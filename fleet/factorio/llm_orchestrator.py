"""LLM Orchestrator — between-episode diagnostics and narration for Factorio RL agent.

Runs BETWEEN training episodes (never during). Analyzes training progress, diagnoses
stalls, and generates human-readable summaries for the dashboard.
"""
import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("biged.factorio.llm_orchestrator")


@dataclass
class DiagnosticResult:
    """Parsed result from a diagnostic LLM call."""
    diagnosis: str
    suggestions: list[str] = field(default_factory=list)
    raw: str = ""


class LLMOrchestrator:
    """Orchestrates LLM calls between Factorio RL training episodes.

    Responsibilities:
    - Detect training stalls via lesson-progress heuristic
    - Generate diagnostic prompts and parse structured responses
    - Generate human-readable episode narration for the dashboard
    - Call Ollama via HTTP (never blocking the training loop)
    """

    def __init__(self, ollama_url: str, model: str = "gemma4:e4b", timeout: int = 60) -> None:
        self._ollama_url = ollama_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_diagnose(self, history: list[dict], stall_threshold: int = 10) -> bool:
        """Return True if no lesson progress has been made in the last N episodes.

        Args:
            history: List of episode dicts, each expected to contain 'lessons_passed'.
            stall_threshold: Number of trailing episodes to inspect.

        Returns:
            True when every episode in the tail window has the same lessons_passed value
            (i.e. no forward progress), False otherwise.
        """
        if not history:
            return False
        tail = history[-stall_threshold:]
        if len(tail) < stall_threshold:
            return False
        lessons = [ep.get("lessons_passed", 0) for ep in tail]
        # Stall detected when all lesson counts are identical (no progress)
        return len(set(lessons)) == 1

    def diagnose(self, history: list[dict], phase: int) -> DiagnosticResult:
        """Call the LLM to analyse a stalled training run.

        Args:
            history: Full episode history list.
            phase: Current curriculum phase number.

        Returns:
            Parsed DiagnosticResult with diagnosis text and suggestions.
        """
        prompt = self._format_diagnosis_prompt(history, phase)
        try:
            raw = self._call_ollama(prompt)
            return self._parse_diagnostic(raw)
        except Exception:
            log.warning("diagnose: LLM call failed", exc_info=True)
            return DiagnosticResult(
                diagnosis="Diagnosis unavailable — LLM call failed.",
                suggestions=[],
                raw="",
            )

    def narrate(self, episode_summary: dict) -> str:
        """Generate a short human-readable narrative for the given episode.

        Args:
            episode_summary: Dict with keys episode, reward, lessons_passed, steps, actions.

        Returns:
            Narrative string (falls back to a minimal default on failure).
        """
        prompt = self._format_narration_prompt(episode_summary)
        try:
            return self._call_ollama(prompt)
        except Exception:
            log.warning("narrate: LLM call failed", exc_info=True)
            ep = episode_summary.get("episode", "?")
            reward = episode_summary.get("reward", 0.0)
            return f"Episode {ep} completed with reward {reward:.2f}."

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _format_diagnosis_prompt(self, history: list[dict], phase: int) -> str:
        """Build a structured diagnosis prompt from training history."""
        recent = history[-20:] if len(history) > 20 else history
        lines = []
        for ep in recent:
            action_dist = ep.get("action_distribution", {})
            dist_str = ", ".join(f"{k}:{v}" for k, v in action_dist.items()) if action_dist else "N/A"
            lines.append(
                f"  episode={ep.get('episode', '?')}  reward={ep.get('reward', 0):.3f}"
                f"  lessons_passed={ep.get('lessons_passed', 0)}  actions=[{dist_str}]"
            )
        history_block = "\n".join(lines) if lines else "  (no episodes)"

        return (
            f"You are an RL training diagnostician for a Factorio agent.\n"
            f"The agent is currently on Phase {phase} of the curriculum and has stalled.\n\n"
            f"Recent episode history:\n{history_block}\n\n"
            f"Please diagnose why the agent is stuck. Focus on patterns in the action distribution "
            f"(e.g. over-reliance on 'wait', lack of exploration) and reward trends.\n\n"
            f"Respond in this exact format:\n"
            f"DIAGNOSIS: <one-sentence diagnosis>\n"
            f"SUGGESTION: <actionable suggestion 1>\n"
            f"SUGGESTION: <actionable suggestion 2>\n"
        )

    def _format_narration_prompt(self, summary: dict) -> str:
        """Build a narration prompt from a single episode summary."""
        episode = summary.get("episode", "?")
        reward = summary.get("reward", 0.0)
        lessons = summary.get("lessons_passed", 0)
        steps = summary.get("steps", 0)
        actions = summary.get("actions", {})
        action_str = ", ".join(f"{k}: {v}" for k, v in actions.items()) if actions else "none recorded"

        return (
            f"You are narrating a Factorio RL training run for a human dashboard.\n"
            f"Write one concise paragraph (2-3 sentences) summarising Episode {episode}.\n\n"
            f"Stats:\n"
            f"  Episode: {episode}\n"
            f"  Reward: {reward:.3f}\n"
            f"  Lessons passed: {lessons}\n"
            f"  Steps: {steps}\n"
            f"  Top actions: {action_str}\n\n"
            f"Keep it factual, avoid hype. Do not use bullet points."
        )

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_diagnostic(self, raw: str) -> DiagnosticResult:
        """Parse DIAGNOSIS: and SUGGESTION: lines from the LLM response."""
        diagnosis = ""
        suggestions: list[str] = []

        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("DIAGNOSIS:"):
                diagnosis = stripped[len("DIAGNOSIS:"):].strip()
            elif stripped.upper().startswith("SUGGESTION:"):
                suggestion = stripped[len("SUGGESTION:"):].strip()
                if suggestion:
                    suggestions.append(suggestion)

        if not diagnosis:
            # Fallback: use the whole response as the diagnosis
            diagnosis = raw.strip()

        return DiagnosticResult(diagnosis=diagnosis, suggestions=suggestions, raw=raw)

    # ------------------------------------------------------------------
    # Ollama HTTP transport
    # ------------------------------------------------------------------

    def _call_ollama(self, prompt: str) -> str:
        """POST prompt to Ollama /api/generate and return the response text.

        Args:
            prompt: The full prompt string to send.

        Returns:
            The model's response text.

        Raises:
            Exception: Re-raised after logging on any HTTP or JSON error.
        """
        url = f"{self._ollama_url}/api/generate"
        payload = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read()
                data = json.loads(body)
                return data.get("response", "").strip()
        except Exception:
            log.warning("_call_ollama: request to %s failed", url, exc_info=True)
            raise
