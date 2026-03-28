"""Agent brain — plan-and-drain loop with Ollama LLM reasoning."""
import json
import logging
import time
import urllib.request
import urllib.error
from pathlib import Path

from factorio.bridge_config import BridgeConfig
from factorio.world_model import WorldModel, GameEvent
from factorio.state_parser import GameState, state_to_markdown
from factorio.action_translator import translate_action, TranslatedAction, KNOWN_ACTIONS
from factorio.curriculum_manager import CurriculumManager

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
                 curricula_dir: str | None = None):
        self.config = config
        self.world_model = world_model
        self.curriculum = CurriculumManager(
            current_phase=config.current_phase,
            curricula_dir=curricula_dir or "fleet/factorio/curricula",
        )
        self._plan: list[dict] = []
        self._plan_index: int = 0
        self._consecutive_failures: int = 0
        self._idle_assembler_count: int = 0
        self._last_results: list[dict] = []
        self._ollama_cooldown_until: float = 0.0
        self._plan_count: int = 0

    def _build_prompt(self, state: GameState) -> tuple[str, str]:
        """Build (system_prompt, user_prompt) for Ollama."""
        objective = self.curriculum.get_current_objective()
        state_md = state_to_markdown(state)

        lines = [
            "# Current Factory State",
            state_md,
            "",
            "# Current Objective",
            f"Phase {objective.get('phase', '?')}: {objective.get('phase_name', '')}",
            f"Lesson: {objective.get('lesson_name', '?')} — {objective.get('description', '')}",
            f"Success criteria: {objective.get('criteria', '?')}",
            f"Hint: {objective.get('hint', '')}",
            "",
            "# Previous Plan Results",
        ]

        if self._last_results:
            for r in self._last_results:
                status = "OK" if r.get("success") else "FAIL"
                desc = r.get("description", r.get("action", "?"))
                err = f" — {r.get('error', '')}" if r.get("error") else ""
                lines.append(f"- [{status}] {desc}{err}")
        else:
            lines.append("First plan — no previous results.")

        lines.append("")
        lines.append("Generate 5-20 actions to work toward the objective.")

        return SYSTEM_PROMPT, "\n".join(lines)

    def _generate_plan(self, state: GameState) -> list[dict]:
        """Call Ollama to generate an action plan."""
        if time.monotonic() < self._ollama_cooldown_until:
            log.info("Ollama in cooldown, skipping plan generation")
            return []

        system_prompt, user_prompt = self._build_prompt(state)

        body = json.dumps({
            "model": self.config.ollama_model,
            "prompt": user_prompt,
            "system": system_prompt,
            "stream": False,
        }).encode("utf-8")

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
            if self._idle_assembler_count >= 3:
                log.info("Soft re-plan: %d consecutive idle_assemblers ticks", self._idle_assembler_count)
                self._plan = []
                self._plan_index = 0
                self._idle_assembler_count = 0
        else:
            self._idle_assembler_count = 0

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
        result_record = {
            "action": action.action_type,
            "description": action.description,
            "success": result.get("success", False),
        }
        if result.get("error"):
            result_record["error"] = result["error"]
        self._last_results.append(result_record)

        if result.get("success"):
            self._consecutive_failures = 0
        else:
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
        return {
            "plan": list(self._plan),
            "plan_index": self._plan_index,
            "plan_count": self._plan_count,
            "planning": False,
            "consecutive_failures": self._consecutive_failures,
        }
