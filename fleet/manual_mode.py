"""Manual Mode engine — operator-controlled audit queue and scheduler.

Backend (not a skill). Manages queue persistence, scheduler config,
and direct Claude API calls for the Manual Mode launcher module.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FLEET_DIR = Path(__file__).parent
FLEET_TOML = FLEET_DIR / "fleet.toml"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"

# Model display-name → API ID mapping
CLAUDE_MODELS = {
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-haiku":  "claude-haiku-4-5",
    "claude-opus":   "claude-opus-4-6",
}

# Pricing per million tokens (input / output)
_PRICING = {
    "claude-haiku-4-5":  {"input": 0.80,  "output": 4.00},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":   {"input": 15.00, "output": 75.00},
}


class ManualModeEngine:
    """Backend for Manual Mode — queue management, scheduling, and run history."""

    def __init__(self):
        # Ensure fleet/ is importable
        if str(FLEET_DIR) not in sys.path:
            sys.path.insert(0, str(FLEET_DIR))

    # ── TOML I/O ──────────────────────────────────────────────────────────────

    def _load_toml(self):
        """Return parsed fleet.toml as a tomlkit document."""
        import tomlkit
        try:
            return tomlkit.loads(FLEET_TOML.read_text(encoding="utf-8"))
        except Exception:
            return tomlkit.document()

    def _save_toml(self, doc):
        """Write tomlkit document back to fleet.toml."""
        import tomlkit
        try:
            FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
        except Exception:
            pass

    def _ensure_section(self, doc, section: str):
        """Ensure [section] exists in the document."""
        import tomlkit
        if section not in doc:
            doc.add(tomlkit.nl())
            doc.add(tomlkit.comment(f" Manual Mode operator queue and scheduler"))
            doc[section] = tomlkit.table()
        return doc[section]

    # ── Queue ─────────────────────────────────────────────────────────────────

    def get_queue(self) -> list:
        """Return the audit queue from fleet.toml [manual_mode] queue."""
        try:
            doc = self._load_toml()
            raw = doc.get("manual_mode", {}).get("queue", [])
            return [dict(item) for item in raw]
        except Exception:
            return []

    def set_queue(self, queue: list):
        """Persist the audit queue to fleet.toml [manual_mode] queue."""
        doc = self._load_toml()
        sec = self._ensure_section(doc, "manual_mode")
        sec["queue"] = queue
        self._save_toml(doc)

    # ── Scheduler ─────────────────────────────────────────────────────────────

    def get_scheduler(self) -> dict:
        """Return scheduler config from fleet.toml [manual_mode] scheduler."""
        try:
            doc = self._load_toml()
            raw = doc.get("manual_mode", {}).get("scheduler", {})
            return {
                "enabled":       bool(raw.get("enabled", False)),
                "mode":          str(raw.get("mode", "one-time")),
                "run_at":        str(raw.get("run_at", "")),
                "interval_days": int(raw.get("interval_days", 1)),
                "next_run":      str(raw.get("next_run", "")),
            }
        except Exception:
            return {
                "enabled": False, "mode": "one-time",
                "run_at": "", "interval_days": 1, "next_run": "",
            }

    def set_scheduler(self, scheduler: dict):
        """Persist scheduler config to fleet.toml [manual_mode] scheduler."""
        doc = self._load_toml()
        sec = self._ensure_section(doc, "manual_mode")
        sec["scheduler"] = scheduler
        self._save_toml(doc)

    # ── Run Queue ─────────────────────────────────────────────────────────────

    def run_queue(self, queue: list, on_progress=None) -> dict:
        """Execute all prompts in the queue via the Claude API.

        Args:
            queue:       list of dicts — keys: prompt, model, max_tokens, repeat
            on_progress: optional callback(i, total, result_dict)

        Returns:
            dict with total_tokens, total_cost, results list
        """
        import db as _db

        results = []
        total_tokens = 0
        total_cost = 0.0

        # Expand repeat counts into individual calls
        expanded = []
        for item in queue:
            repeat = max(1, min(10, int(item.get("repeat", 1))))
            for r in range(repeat):
                expanded.append({
                    "prompt":     item.get("prompt", ""),
                    "model":      item.get("model", "claude-sonnet"),
                    "max_tokens": int(item.get("max_tokens", 4096)),
                })

        total = len(expanded)
        for i, item in enumerate(expanded):
            model_key = item["model"]
            model_id  = CLAUDE_MODELS.get(model_key, "claude-sonnet-4-6")
            prompt    = item["prompt"]
            max_tok   = item["max_tokens"]

            result = {
                "prompt":        prompt[:200],
                "model":         model_key,
                "model_id":      model_id,
                "max_tokens":    max_tok,
                "status":        "error",
                "response":      "",
                "input_tokens":  0,
                "output_tokens": 0,
                "cost":          0.0,
                "error":         "",
            }

            try:
                text, in_tok, out_tok, cost = self._call_claude(prompt, model_id, max_tok)
                result.update({
                    "status":        "done",
                    "response":      text,
                    "input_tokens":  in_tok,
                    "output_tokens": out_tok,
                    "cost":          cost,
                })
                total_tokens += in_tok + out_tok
                total_cost   += cost
            except Exception as exc:
                result["error"] = str(exc)

            results.append(result)

            if on_progress:
                try:
                    on_progress(i + 1, total, result)
                except Exception:
                    pass

        # Persist run to DB
        try:
            _db.log_audit_run(
                prompts=[{"prompt": r["prompt"], "model": r["model"]} for r in results],
                results=results,
                total_tokens=total_tokens,
                total_cost=total_cost,
            )
        except Exception:
            pass

        return {
            "total_tokens": total_tokens,
            "total_cost":   total_cost,
            "results":      results,
        }

    def _call_claude(self, prompt: str, model_id: str, max_tokens: int) -> tuple:
        """Call Claude API. Returns (text, input_tokens, output_tokens, cost_usd)."""
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)
        time.sleep(0.3)  # 300 ms minimum between requests (rate-limit headroom)

        resp = client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": "You are a helpful AI audit assistant.",
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
        )

        in_tok  = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
        rates   = _PRICING.get(model_id, _PRICING["claude-sonnet-4-6"])
        cost    = round(
            in_tok  * rates["input"]  / 1_000_000
            + out_tok * rates["output"] / 1_000_000,
            6,
        )
        return resp.content[0].text, in_tok, out_tok, cost

    # ── History ───────────────────────────────────────────────────────────────

    def get_run_history(self, limit: int = 20) -> list:
        """Return recent audit runs from DB, newest first."""
        try:
            import db as _db
            return _db.get_audit_runs(limit=limit)
        except Exception:
            return []
