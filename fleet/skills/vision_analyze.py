"""
Local vision analysis — send images to multimodal models via Ollama.

Models: llava, minicpm-v, qwen-vl (configured in fleet.toml [models] vision_model)
All inference runs locally — no API keys needed.

Actions:
  describe     — general image description
  ocr          — extract text from image
  analyze_chart — interpret chart/graph data

Payload:
  image_path   str   path to image file (required)
  action       str   describe | ocr | analyze_chart (default: describe)
  prompt       str   optional custom prompt override
  model        str   override vision model (default: from config)

Returns: {action, model, analysis, image_path}

VRAM note: Vision models are large (~4-7GB). hw_supervisor handles VRAM rotation —
it will evict the text model if needed, load vision, run inference, then restore.
This skill sets a flag in hw_state.json to signal the request.
"""
import base64
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
VISION_DIR = KNOWLEDGE_DIR / "vision"
HW_STATE_FILE = FLEET_DIR / "hw_state.json"

# Not strictly network — Ollama is localhost. But needs Ollama running.
REQUIRES_NETWORK = False

DEFAULT_PROMPTS = {
    "describe": "Describe this image in detail. Include objects, colors, layout, text, and any notable features.",
    "ocr": "Extract ALL text visible in this image. Return the text exactly as it appears, preserving layout where possible.",
    "analyze_chart": "Analyze this chart/graph. Identify: chart type, axes/labels, data trends, key values, and any conclusions that can be drawn.",
}


def _encode_image(image_path):
    """Read image file and return base64 string."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _call_vision(host, model, prompt, image_b64, timeout=120):
    """Call Ollama multimodal API with image."""
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "options": {"num_predict": 1024},
    }).encode()
    req = urllib.request.Request(
        f"{host}/api/generate", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["response"]


def _signal_vision_request(model):
    """Write a vision request flag to hw_state.json for hw_supervisor VRAM rotation."""
    try:
        state = {}
        if HW_STATE_FILE.exists():
            state = json.loads(HW_STATE_FILE.read_text(encoding="utf-8"))
        state["vision_request"] = {
            "model": model,
            "requested_at": datetime.now().isoformat(),
        }
        HW_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _clear_vision_request():
    """Clear the vision request flag after inference completes."""
    try:
        if HW_STATE_FILE.exists():
            state = json.loads(HW_STATE_FILE.read_text(encoding="utf-8"))
            state.pop("vision_request", None)
            HW_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def run(payload, config):
    image_path = payload.get("image_path", "")
    if not image_path:
        return {"error": "image_path required"}

    action = payload.get("action", "describe")
    host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
    model = payload.get("model", config.get("models", {}).get("vision_model", "llava"))
    prompt = payload.get("prompt", DEFAULT_PROMPTS.get(action, DEFAULT_PROMPTS["describe"]))

    try:
        image_b64 = _encode_image(image_path)
    except FileNotFoundError as e:
        return {"error": str(e)}

    # Signal hw_supervisor that we need the vision model loaded
    _signal_vision_request(model)

    try:
        analysis = _call_vision(host, model, prompt, image_b64)

        # Save result
        VISION_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        result = {
            "action": action,
            "model": model,
            "image_path": str(image_path),
            "analysis": analysis,
            "timestamp": ts,
        }
        out_file = VISION_DIR / f"vision_{action}_{ts}.json"
        out_file.write_text(json.dumps(result, indent=2))

        return result

    except Exception as e:
        return {"error": f"Vision inference failed: {e}", "model": model, "action": action}
    finally:
        _clear_vision_request()
