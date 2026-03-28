"""Agent brain — plan-and-drain loop with Ollama LLM reasoning."""
import json
import logging
import threading
import time
import uuid
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path

from factorio.bridge_config import BridgeConfig
from factorio.world_model import WorldModel, GameEvent
from factorio.state_parser import GameState, state_to_markdown
from factorio.action_translator import translate_action, TranslatedAction, KNOWN_ACTIONS
from factorio.curriculum_manager import CurriculumManager
from factorio.prompt_loader import load_prompt_template, render_prompt

log = logging.getLogger("biged.factorio.brain")

INVALIDATION_EVENTS = {"entity_destroyed", "power_outage", "resource_depleted", "research_complete"}

SYSTEM_PROMPT = """You are a Factorio automation agent controlling a factory through commands.
Respond with ONLY a valid JSON array of action objects. No markdown, no explanation, no text.

Available actions:
- {"action": "place", "entity": "<name>", "position": {"x": N, "y": N}, "direction": "north|east|south|west"}
- {"action": "craft", "recipe": "<name>", "count": N}
- {"action": "research", "technology": "<name>"}
- {"action": "move", "position": {"x": N, "y": N}}
- {"action": "set_recipe", "unit_number": N, "recipe": "<name>"}
- {"action": "connect", "entity": "transport-belt", "from": {"x": N, "y": N}, "to": {"x": N, "y": N}}
- {"action": "remove", "unit_number": N}
- {"action": "wait", "ticks": N}

Decision priority:
1. Fix bottlenecks (idle assemblers, full outputs)
2. Maintain power (build power if none or low)
3. Advance toward current objective
4. Optimize layout

Rules:
- Inserters pick from BEHIND, drop in FRONT (direction matters!)
- Always set_recipe on assemblers after placing
- Check inventory before placing — you can't place what you don't have
- Keep builds compact to minimize belt length
- Electric miners/assemblers need power to work"""


@dataclass
class Directive:
    id: str
    text: str
    sticky: bool
    plans_remaining: int
    created_at: float


@dataclass
class Preset:
    id: str
    label: str
    text: str
    sticky: bool
    plans: int


DEFAULT_PRESETS = [
    Preset(id="preset_focus_power", label="Focus Power", text="Prioritize building and maintaining power infrastructure above all else.", sticky=False, plans=3),
    Preset(id="preset_expand_mining", label="Expand Mining", text="Expand mining operations — place more miners and belt them to the base.", sticky=False, plans=3),
    Preset(id="preset_fix_bottlenecks", label="Fix Bottlenecks", text="Find and fix production bottlenecks — look for idle assemblers and full output chests.", sticky=False, plans=3),
    Preset(id="preset_scale_smelting", label="Scale Smelting", text="Scale up smelting capacity — add more furnaces and ensure they are fed with ore.", sticky=False, plans=3),
    Preset(id="preset_build_defenses", label="Build Defenses", text="Build defensive structures — turrets, walls, and ammo supply lines.", sticky=False, plans=3),
    Preset(id="preset_optimize_layout", label="Optimize Layout", text="Optimize the factory layout for throughput — reduce belt length and crossing paths.", sticky=False, plans=3),
]


def flatten_state(state: GameState) -> dict:
    """Convert GameState to flat dict for curriculum criteria evaluation."""
    entity_counts: dict[str, int] = {}
    for e in state.entities:
        entity_counts[e.name] = entity_counts.get(e.name, 0) + 1
    return {
        "inventory": dict(state.inventory),
        "entities": entity_counts,
        "research": {"name": state.research_name, "progress": state.research_progress},
    }


class AgentBrain:
    """Plan-and-drain reasoning loop powered by local Ollama."""

    def __init__(self, config: BridgeConfig, world_model: WorldModel,
                 curricula_dir: str | None = None, prompts_dir: str | None = None):
        self.config = config
        self.world_model = world_model
        self.curriculum = CurriculumManager(
            current_phase=config.current_phase,
            curricula_dir=curricula_dir or "fleet/factorio/curricula",
        )
        self._prompts_dir = prompts_dir or "fleet/factorio/prompts"
        try:
            self._prompt_template = load_prompt_template(
                config.prompt_template, prompts_dir=self._prompts_dir)
        except (FileNotFoundError, ValueError):
            log.warning("Prompt template '%s' not found, using hardcoded fallback",
                        config.prompt_template)
            self._prompt_template = None
        self._plan: list[dict] = []
        self._plan_index: int = 0
        self._consecutive_failures: int = 0
        self._idle_assembler_count: int = 0
        self._last_results: list[dict] = []
        self._ollama_cooldown_until: float = 0.0
        self._plan_count: int = 0
        # Cumulative counters for experiment scoring
        self.total_actions: int = 0
        self.total_successes: int = 0
        self.total_failures: int = 0
        self._lock = threading.Lock()
        self._paused: bool = False
        self._directives: list = []
        self._presets: list = list(DEFAULT_PRESETS)

    def pause(self) -> None:
        """Pause agent execution — clears current plan."""
        with self._lock:
            self._paused = True
            self._plan = []
            self._plan_index = 0

    def resume(self) -> None:
        """Resume agent execution."""
        with self._lock:
            self._paused = False

    @property
    def is_paused(self) -> bool:
        """Return True if the agent is currently paused."""
        return self._paused

    def add_directive(self, text: str, sticky: bool = False, plans: int = 1) -> str:
        """Add a human directive. Returns the directive id."""
        directive = Directive(
            id=uuid.uuid4().hex[:8],
            text=text,
            sticky=sticky,
            plans_remaining=plans,
            created_at=time.time(),
        )
        with self._lock:
            self._directives.append(directive)
        return directive.id

    def remove_directive(self, directive_id: str) -> bool:
        """Remove a directive by id. Returns True if found and removed."""
        with self._lock:
            before = len(self._directives)
            self._directives = [d for d in self._directives if d.id != directive_id]
            return len(self._directives) < before

    def clear_directives(self) -> int:
        """Clear all directives. Returns number removed."""
        with self._lock:
            count = len(self._directives)
            self._directives = []
        return count

    def get_directives(self) -> list[dict]:
        """Return list of directive dicts."""
        with self._lock:
            return [
                {
                    "id": d.id,
                    "text": d.text,
                    "sticky": d.sticky,
                    "plans_remaining": d.plans_remaining,
                    "created_at": d.created_at,
                }
                for d in self._directives
            ]

    def get_presets(self) -> list[dict]:
        """Return list of preset dicts."""
        with self._lock:
            return [
                {
                    "id": p.id,
                    "label": p.label,
                    "text": p.text,
                    "sticky": p.sticky,
                    "plans": p.plans,
                }
                for p in self._presets
            ]

    def add_preset(self, label: str, text: str, sticky: bool, plans: int) -> str:
        """Add a custom preset. Returns the preset id."""
        preset = Preset(
            id=uuid.uuid4().hex[:8],
            label=label,
            text=text,
            sticky=sticky,
            plans=plans,
        )
        with self._lock:
            self._presets.append(preset)
        return preset.id

    def remove_preset(self, preset_id: str) -> bool:
        """Remove a preset by id. Returns True if found and removed."""
        with self._lock:
            before = len(self._presets)
            self._presets = [p for p in self._presets if p.id != preset_id]
            return len(self._presets) < before

    def _build_prompt(self, state: GameState) -> tuple[str, str]:
        """Build (system_prompt, user_prompt) for Ollama."""
        objective = self.curriculum.get_current_objective()
        state_md = state_to_markdown(state)

        # Build objective text
        obj_text = (
            f"Phase {objective.get('phase', '?')}: {objective.get('phase_name', '')}\n"
            f"Lesson: {objective.get('lesson_name', '?')} — {objective.get('description', '')}\n"
            f"Success criteria: {objective.get('criteria', '?')}\n"
            f"Hint: {objective.get('hint', '')}"
        )

        # Build previous results text
        if self._last_results:
            result_lines = []
            for r in self._last_results:
                status = "OK" if r.get("success") else "FAIL"
                desc = r.get("description", r.get("action", "?"))
                err = f" — {r.get('error', '')}" if r.get("error") else ""
                result_lines.append(f"- [{status}] {desc}{err}")
            results_text = "\n".join(result_lines)
        else:
            results_text = "First plan — no previous results."

        with self._lock:
            active_directives = list(self._directives)

        # Use template if available, else fall back to hardcoded
        if self._prompt_template:
            system, user = render_prompt(self._prompt_template,
                                         state=state_md, objective=obj_text,
                                         previous_results=results_text)
            # Inject directives (templates don't include a {directives} slot)
            if active_directives:
                directive_lines = ["# Human Directives (PRIORITY — follow these)"]
                for d in active_directives:
                    directive_lines.append(f"- {d.text}")
                user = "\n".join(directive_lines) + "\n\n" + user
            return system, user

        # Fallback: original hardcoded format (with directive support)
        lines = [
            "# Current Factory State",
            state_md,
            "",
        ]

        if active_directives:
            lines.append("# Human Directives (PRIORITY — follow these)")
            for d in active_directives:
                lines.append(f"- {d.text}")
            lines.append("")

        lines += [
            "# Current Objective",
            obj_text,
            "",
            "# Previous Plan Results",
            results_text,
            "",
            "Generate 5-20 actions to work toward the objective.",
        ]
        return SYSTEM_PROMPT, "\n".join(lines)

    def _generate_plan(self, state: GameState) -> list[dict]:
        """Call Ollama to generate an action plan."""
        if time.monotonic() < self._ollama_cooldown_until:
            log.info("Ollama in cooldown, skipping plan generation")
            return []

        system_prompt, user_prompt = self._build_prompt(state)

        body_dict = {
            "model": self.config.ollama_model,
            "prompt": user_prompt,
            "system": system_prompt,
            "stream": False,
        }
        # Add temperature/top_p if configured
        options = {}
        if self.config.temperature is not None:
            options["temperature"] = self.config.temperature
        if self.config.top_p is not None:
            options["top_p"] = self.config.top_p
        if options:
            body_dict["options"] = options

        body = json.dumps(body_dict).encode("utf-8")

        req = urllib.request.Request(
            f"{self.config.ollama_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )

        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.config.ollama_timeout) as resp:
                    data = json.loads(resp.read())
                raw_text = data.get("response", "")
                actions = self._parse_response(raw_text)
                if actions:
                    self._plan_count += 1
                    log.info("Plan #%d generated: %d actions", self._plan_count, len(actions))
                    # Expire non-sticky directives
                    with self._lock:
                        updated = []
                        for d in self._directives:
                            if d.sticky:
                                updated.append(d)
                            else:
                                d.plans_remaining -= 1
                                if d.plans_remaining > 0:
                                    updated.append(d)
                        self._directives = updated
                    return actions
                if attempt == 0:
                    log.warning("Parse failed, retrying with shorter prompt")
                    body = json.dumps({
                        "model": self.config.ollama_model,
                        "prompt": "Respond with ONLY a JSON array of Factorio actions.",
                        "system": system_prompt,
                        "stream": False,
                    }).encode("utf-8")
                    req = urllib.request.Request(
                        f"{self.config.ollama_url}/api/generate",
                        data=body,
                        headers={"Content-Type": "application/json"},
                    )
            except (ConnectionRefusedError, urllib.error.URLError, OSError) as e:
                log.warning("Ollama connection failed: %s", e)
                self._ollama_cooldown_until = time.monotonic() + self.config.ollama_cooldown_secs
                return []
            except Exception as e:
                log.warning("Ollama call failed: %s", e)
                return []

        return []

    def _parse_response(self, text: str) -> list[dict]:
        """Parse JSON action array from LLM response text."""
        text = text.strip()
        # Strip markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            log.warning("Failed to parse LLM response as JSON")
            return []

        if not isinstance(parsed, list):
            log.warning("LLM response is not a list")
            return []

        # Filter to known actions and cap
        valid = [a for a in parsed if isinstance(a, dict) and a.get("action") in KNOWN_ACTIONS]
        return valid[:self.config.plan_max_actions]

    def next_action(self, state: GameState, events: list[GameEvent]) -> TranslatedAction | None:
        """Get the next action to execute. May call Ollama if plan is empty."""
        # Check for invalidation events
        has_idle = False
        for event in events:
            if event.event_type in INVALIDATION_EVENTS:
                log.info("Plan invalidated by event: %s", event.event_type)
                self._plan = []
                self._plan_index = 0
                break
            if event.event_type == "idle_assemblers":
                has_idle = True

        # Soft re-plan: idle_assemblers counter tracked across ticks, not per-event
        if has_idle:
            self._idle_assembler_count += 1
            if self._idle_assembler_count >= self.config.idle_assembler_replan:
                log.info("Soft re-plan: %d consecutive idle_assemblers ticks", self._idle_assembler_count)
                self._plan = []
                self._plan_index = 0
                self._idle_assembler_count = 0
        else:
            self._idle_assembler_count = 0

        if self._paused:
            return None

        # Drain current plan
        if self._plan_index < len(self._plan):
            raw = self._plan[self._plan_index]
            self._plan_index += 1
            return translate_action(raw)

        # Plan exhausted — generate new one
        self._plan = self._generate_plan(state)
        self._plan_index = 0
        self._last_results = []

        if not self._plan:
            return None

        raw = self._plan[self._plan_index]
        self._plan_index += 1
        return translate_action(raw)

    def report_result(self, action: TranslatedAction, result: dict) -> None:
        """Track action result. Invalidate plan on consecutive failures."""
        self.total_actions += 1
        result_record = {
            "action": action.action_type,
            "description": action.description,
            "success": result.get("success", False),
        }
        if result.get("error"):
            result_record["error"] = result["error"]
        self._last_results.append(result_record)

        if result.get("success"):
            self.total_successes += 1
            self._consecutive_failures = 0
        else:
            self.total_failures += 1
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.config.plan_invalidation_failures:
                log.warning("Plan invalidated: %d consecutive failures", self._consecutive_failures)
                self._plan = []
                self._plan_index = 0
                self._consecutive_failures = 0

    def check_progress(self, state: GameState) -> dict:
        """Check curriculum progress against current game state."""
        flat = flatten_state(state)
        return self.curriculum.check_progress(flat)

    def get_plan_status(self) -> dict:
        """Return current plan state for the API."""
        with self._lock:
            directives_snapshot = [
                {
                    "id": d.id,
                    "text": d.text,
                    "sticky": d.sticky,
                    "plans_remaining": d.plans_remaining,
                    "created_at": d.created_at,
                }
                for d in self._directives
            ]
        return {
            "plan": list(self._plan),
            "plan_index": self._plan_index,
            "plan_count": self._plan_count,
            "planning": False,
            "consecutive_failures": self._consecutive_failures,
            "paused": self._paused,
            "directives": directives_snapshot,
        }

    def reset_counters(self) -> None:
        """Reset cumulative counters for experiment runner."""
        self.total_actions = 0
        self.total_successes = 0
        self.total_failures = 0
