"""
Billing OCR — Process screenshots of API usage dashboards to reconcile local cost tracking.

Supports: Anthropic console, Google AI Studio, Ollama (local verification)
Uses local vision model (llava/qwen-vl) to OCR billing screenshots.

Usage:
    lead_client.py task '{"type": "billing_ocr", "payload": {"image_path": "/path/to/screenshot.png", "provider": "claude"}}'
"""
import json
import os
import base64
import urllib.request
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "billing_ocr"
DESCRIPTION = "OCR billing screenshots from Claude/Gemini dashboards to reconcile local cost tracking."
REQUIRES_NETWORK = False  # Uses local Ollama vision model


def run(payload: dict, config: dict, log) -> dict:
    """Process a billing screenshot and extract cost/usage data."""
    image_path = payload.get("image_path", "")
    provider = payload.get("provider", "auto")  # claude, gemini, auto

    if not image_path or not Path(image_path).exists():
        return {"error": f"Image not found: {image_path}"}

    # Read and encode image
    image_data = Path(image_path).read_bytes()
    b64_image = base64.b64encode(image_data).decode("utf-8")

    # Detect vision model
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    vision_model = config.get("models", {}).get("vision_model", "llava")

    prompt = _build_ocr_prompt(provider)

    # Call Ollama vision API
    try:
        body = json.dumps({
            "model": vision_model,
            "prompt": prompt,
            "images": [b64_image],
            "stream": False,
            "options": {"num_gpu": 0},  # CPU — don't disturb GPU model
        }).encode()
        req = urllib.request.Request(
            f"{host}/api/generate", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
        raw_text = resp.get("response", "")
    except Exception as e:
        return {"error": f"Vision API failed: {e}"}

    # Parse extracted billing data
    billing = _parse_billing_response(raw_text, provider)

    # Optionally update local usage DB
    if billing.get("costs") and payload.get("update_db", False):
        _update_local_usage(billing, config, log)

    return {
        "provider": provider,
        "raw_ocr": raw_text[:500],
        "billing": billing,
        "image": image_path,
    }


def _build_ocr_prompt(provider: str) -> str:
    base = (
        "You are reading a screenshot of an API billing/usage dashboard. "
        "Extract ALL cost and usage data you can see. "
        "Return a JSON object with these fields:\n"
        '{"period": "date range shown", "total_cost_usd": number, '
        '"input_tokens": number, "output_tokens": number, '
        '"calls": number, "breakdown": [{"model": "name", "cost": number, "tokens": number}]}\n'
        "If you can't read a value, use null. Only return the JSON, no other text."
    )
    if provider == "claude":
        return base + "\nThis is the Anthropic Console usage page (console.anthropic.com)."
    elif provider == "gemini":
        return base + "\nThis is the Google AI Studio usage page (aistudio.google.com)."
    return base


def _parse_billing_response(text: str, provider: str) -> dict:
    """Parse the vision model's response into structured billing data."""
    # Try to extract JSON from the response
    try:
        # Find JSON in response (may have surrounding text)
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except json.JSONDecodeError:
        pass

    # Fallback: return raw text for manual review
    return {"raw": text, "parse_failed": True}


def _update_local_usage(billing: dict, config: dict, log):
    """Reconcile OCR billing data with local usage table."""
    try:
        import sys
        sys.path.insert(0, str(FLEET_DIR))
        import db

        total_local = 0
        conn = db.get_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE created_at >= datetime('now', '-30 days')"
        ).fetchone()
        total_local = row[0] if row else 0
        conn.close()

        ocr_total = billing.get("total_cost_usd", 0) or 0
        drift = abs(ocr_total - total_local)

        log.info(f"Billing reconciliation: OCR=${ocr_total:.4f}, Local=${total_local:.4f}, Drift=${drift:.4f}")

        if drift > 0.01:
            log.warning(f"Cost drift detected: ${drift:.4f} difference between OCR and local tracking")
    except Exception as e:
        log.warning(f"Billing reconciliation failed: {e}")
