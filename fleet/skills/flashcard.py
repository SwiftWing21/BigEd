"""Generate Q&A flashcards from existing knowledge summaries."""
import json
import random
from pathlib import Path


def run(payload, config):
    summaries_dir = Path(__file__).parent.parent / "knowledge" / "summaries"
    flashcards_file = Path(__file__).parent.parent / "knowledge" / "flashcards.jsonl"

    candidates = list(summaries_dir.glob("*.md"))
    if not candidates:
        return {"error": "No summaries found — run arxiv_fetch or summarize first"}

    source = random.choice(candidates)
    text = source.read_text()[:3000]

    from skills.summarize import _ollama
    prompt = f"""From this content, generate 3 Q&A flashcard pairs as a JSON array:
[{{"q": "question", "a": "answer"}}, ...]

Content:
{text}

Return only the JSON array."""

    response = _ollama(prompt, config)

    try:
        start, end = response.find("["), response.rfind("]") + 1
        cards = json.loads(response[start:end])
    except Exception:
        return {"error": f"Could not parse JSON from response: {response[:200]}"}

    with open(flashcards_file, "a") as f:
        for card in cards:
            card["source"] = source.name
            f.write(json.dumps(card) + "\n")

    return {"cards_generated": len(cards), "source": source.name}
