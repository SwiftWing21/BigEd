"""v0.49: Synthetic dataset generation for ML training (autoresearch pipeline)."""
import json
import sys
from datetime import datetime
from pathlib import Path
from skills._models import call_complex, get_last_provider

SKILL_NAME = "dataset_synthesize"
DESCRIPTION = "Generate synthetic JSONL training datasets for the autoresearch ML pipeline"
REQUIRES_NETWORK = False  # can use local Ollama

FLEET_DIR = Path(__file__).parent.parent
DATASETS_DIR = FLEET_DIR / "knowledge" / "datasets"


# ---------------------------------------------------------------------------
# ToS: Gemini-sourced content excluded — Google ToS prohibits using Gemini
# output to train competing models. Defense-in-depth: check both thread-local
# provider tag (Approach B) and usage table (Approach A).
# ---------------------------------------------------------------------------

def _is_gemini_sourced_by_provider() -> bool:
    """Check thread-local: did the last call_complex() use Gemini?"""
    return get_last_provider() == "gemini"


def _is_gemini_sourced_by_db(task_id) -> bool:
    """Check usage table: was this task served by Gemini?"""
    if task_id is None:
        return False
    try:
        import db
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT model, provider FROM usage WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,)
            ).fetchone()
            if row:
                if (row["provider"] or "").lower() == "gemini":
                    return True
                if "gemini" in (row["model"] or "").lower():
                    return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# System prompts for each format
# ---------------------------------------------------------------------------

_CONVERSATION_SYSTEM = """You are a training-data generator. Produce a single multi-turn conversation between a user and a helpful assistant on the given topic.

Rules:
- Exactly 2-4 turns (user then assistant, alternating).
- The user asks a clear question; the assistant gives a concise, accurate answer.
- Each message should be 1-3 sentences. No filler.
- Output ONLY valid JSON — no markdown, no commentary.

Output format (strict):
{"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]}"""

_INSTRUCTION_SYSTEM = """You are a training-data generator. Produce a single instruction-following example on the given topic.

Rules:
- "instruction": a clear task description (1-2 sentences).
- "input": optional context or empty string if not needed.
- "output": a correct, concise response (2-5 sentences).
- Output ONLY valid JSON — no markdown, no commentary.

Output format (strict):
{"instruction": "...", "input": "...", "output": "..."}"""

_TINYSTORIES_SYSTEM = """You are a training-data generator producing short stories for language-model pretraining.

Rules:
- Write a self-contained story of 3-6 sentences on the given topic.
- Use simple, clear language (target reading level: grade 3-5).
- The story should have a beginning, a small event, and a resolution.
- Output ONLY valid JSON — no markdown, no commentary.

Output format (strict):
{"text": "..."}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_response(raw: str) -> dict | None:
    """Extract a JSON object from the model response, tolerating minor wrapper text."""
    raw = raw.strip()
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Try to find JSON object boundaries
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def _gen_conversation(topic: str, index: int, config: dict) -> dict | None:
    """Generate a multi-turn chat example: {"messages": [{"role":..., "content":...}, ...]}."""
    user_prompt = (
        f"Topic: {topic}\n"
        f"Example #{index + 1}. Generate a unique multi-turn conversation. "
        f"Vary the angle — do not repeat earlier examples."
    )
    raw = call_complex(
        _CONVERSATION_SYSTEM, user_prompt, config,
        max_tokens=512, skill_name="dataset_synthesize",
    )
    # ToS: Gemini-sourced content excluded
    if _is_gemini_sourced_by_provider():
        return None
    entry = _parse_json_response(raw)
    if entry is None or "messages" not in entry:
        return None
    # Validate structure
    msgs = entry["messages"]
    if not isinstance(msgs, list) or len(msgs) < 2:
        return None
    for msg in msgs:
        if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
            return None
    return {"messages": msgs}


def _gen_instruction(topic: str, index: int, config: dict) -> dict | None:
    """Generate an instruction-following example: {"instruction":..., "input":..., "output":...}."""
    user_prompt = (
        f"Topic: {topic}\n"
        f"Example #{index + 1}. Generate a unique instruction-following pair. "
        f"Vary the task type — do not repeat earlier examples."
    )
    raw = call_complex(
        _INSTRUCTION_SYSTEM, user_prompt, config,
        max_tokens=512, skill_name="dataset_synthesize",
    )
    # ToS: Gemini-sourced content excluded
    if _is_gemini_sourced_by_provider():
        return None
    entry = _parse_json_response(raw)
    if entry is None or "instruction" not in entry or "output" not in entry:
        return None
    return {
        "instruction": entry["instruction"],
        "input": entry.get("input", ""),
        "output": entry["output"],
    }


def _gen_tinystories(topic: str, index: int, config: dict) -> dict | None:
    """Generate a short story for pretraining: {"text": ...}."""
    user_prompt = (
        f"Topic: {topic}\n"
        f"Story #{index + 1}. Write a unique short story. "
        f"Vary the characters and setting — do not repeat earlier stories."
    )
    raw = call_complex(
        _TINYSTORIES_SYSTEM, user_prompt, config,
        max_tokens=512, skill_name="dataset_synthesize",
    )
    # ToS: Gemini-sourced content excluded
    if _is_gemini_sourced_by_provider():
        return None
    entry = _parse_json_response(raw)
    if entry is None or "text" not in entry:
        return None
    return {"text": entry["text"]}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(payload: dict, config: dict) -> str:
    format_type = payload.get("format", "conversation")  # conversation | instruction | tinystories
    topic = payload.get("topic", "general knowledge")
    count = min(payload.get("count", 10), 50)  # cap at 50 per call
    output_name = payload.get("output", f"synthetic_{datetime.now().strftime('%Y%m%d_%H%M')}")

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DATASETS_DIR / f"{output_name}.jsonl"

    generators = {
        "conversation": _gen_conversation,
        "instruction": _gen_instruction,
        "tinystories": _gen_tinystories,
    }

    gen = generators.get(format_type, _gen_instruction)

    entries = []
    gemini_excluded = 0
    for i in range(count):
        try:
            entry = gen(topic, i, config)
            if entry is None and _is_gemini_sourced_by_provider():
                # ToS: Gemini-sourced content excluded
                gemini_excluded += 1
            elif entry:
                entries.append(entry)
        except Exception as e:
            entries.append({"error": str(e), "index": i})

    if gemini_excluded > 0:
        print(f"[ToS] dataset_synthesize: excluded {gemini_excluded}/{count} samples "
              f"(Gemini-sourced, cannot train competing models)", file=sys.stderr)

    # Write JSONL
    with open(output_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    # Optional: copy training data to autoresearch pipeline
    autoresearch_dir = FLEET_DIR.parent / "autoresearch" / "data"
    copied_to_autoresearch = False
    if autoresearch_dir.exists():
        import shutil
        shutil.copy2(output_path, autoresearch_dir / output_path.name)
        print(f"[dataset_synthesize] Copied training data to autoresearch: {output_path.name}", file=sys.stderr)
        copied_to_autoresearch = True

    return json.dumps({
        "status": "ok",
        "format": format_type,
        "count": len(entries),
        "gemini_excluded": gemini_excluded,
        "output": str(output_path),
        "topic": topic,
        "copied_to_autoresearch": copied_to_autoresearch,
    })
