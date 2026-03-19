"""Summarize text from a URL, file path, or raw description."""
import re
from datetime import date
from pathlib import Path

import httpx

from skills._models import call_complex


def run(payload, config):
    url = payload.get("url", "")
    file_path = payload.get("file_path", "")
    text = payload.get("text", "")
    description = payload.get("description", "")

    if url:
        try:
            resp = httpx.get(url, timeout=15, follow_redirects=True)
            text = resp.text[:8000]
        except Exception as e:
            return {"error": f"Fetch failed for {url}: {e}"}
    elif file_path:
        try:
            text = Path(file_path).read_text(errors="ignore")[:8000]
        except Exception as e:
            return {"error": f"Read failed for {file_path}: {e}"}
    elif description and not text:
        text = description

    if not text:
        return {"error": "No content to summarize"}

    system_prompt = "Summarize the following concisely in 3-5 bullet points."
    summary = call_complex(system_prompt, text[:6000], config, skill_name="summarize")

    source_label = url or file_path or description
    slug = re.sub(r"[^a-z0-9]+", "_", source_label[:40].lower()).strip("_") or "summary"
    out_dir = Path(__file__).parent.parent / "knowledge" / "summaries"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{date.today()}_{slug}.md"
    out_file.write_text(f"# {source_label}\n\n{summary}\n")

    return {"summary": summary, "saved_to": str(out_file)}
