"""
AI image generation — Stability AI v2beta REST API.
Saves PNG to knowledge/marketing/images/.
Requires STABILITY_API_KEY in ~/.secrets.

Models:  core   — fast, $0.003/image (default)
         ultra  — highest quality, $0.008/image
         sd3    — SD3 Medium, $0.035/image

Aspect ratios: 1:1, 16:9, 9:16, 4:3, 3:4, 21:9
Style presets: photographic, digital-art, cinematic, anime, comic-book, fantasy-art,
               line-art, analog-film, neon-punk, enhance, tile-texture, 3d-model
"""
import os
from datetime import datetime
from pathlib import Path

import httpx

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
IMAGES_DIR    = KNOWLEDGE_DIR / "marketing" / "images"
API_BASE      = "https://api.stability.ai/v2beta/stable-image/generate"


def run(payload, config):
    key = os.environ.get("STABILITY_API_KEY", "")
    if not key:
        return {
            "error": "STABILITY_API_KEY not set",
            "hint": "export STABILITY_API_KEY=sk-... in ~/.secrets",
        }

    prompt = payload.get("prompt", "")
    if not prompt:
        return {"error": "No prompt provided"}

    model       = payload.get("model", "core")
    aspect      = payload.get("aspect_ratio", "1:1")
    negative    = payload.get("negative_prompt", "blurry, low quality, watermark, text overlay")
    style       = payload.get("style_preset", None)
    output_name = payload.get(
        "output_name", f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    form = {
        "prompt":          prompt,
        "negative_prompt": negative,
        "aspect_ratio":    aspect,
        "output_format":   "png",
    }
    if style:
        form["style_preset"] = style

    try:
        resp = httpx.post(
            f"{API_BASE}/{model}",
            headers={"Authorization": f"Bearer {key}", "Accept": "image/*"},
            data=form,
            timeout=60,
        )
        if resp.status_code == 403:
            return {"error": "Invalid STABILITY_API_KEY or insufficient credits"}
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    out = IMAGES_DIR / f"{output_name}.png"
    out.write_bytes(resp.content)

    return {
        "saved_to":   str(out),
        "prompt":     prompt,
        "model":      model,
        "aspect_ratio": aspect,
        "style_preset": style,
    }
