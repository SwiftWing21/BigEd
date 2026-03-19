"""
Short video generation — Replicate REST API (no replicate package needed, just httpx).
Saves MP4 to knowledge/marketing/videos/.
Requires REPLICATE_API_TOKEN in ~/.secrets.

Models (text-to-video):
  wan       — WAN 2.1 480p, fast & cheap (default)
  minimax   — Hailuo Video-01, HD 6s, higher quality
  ltx       — LTX-Video, very fast (real-time-ish), good for iteration

Models (image-to-video):
  svd       — Stable Video Diffusion; payload must include "image_url"
"""
import os
import time
from datetime import datetime
from pathlib import Path

import httpx

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
VIDEOS_DIR    = KNOWLEDGE_DIR / "marketing" / "videos"
REPLICATE_API = "https://api.replicate.com/v1"
REQUIRES_NETWORK = True

# Owner/name slugs — Replicate resolves to latest version automatically
_MODEL_SLUGS = {
    "wan":     "wavespeedai/wan-2.1-t2v-480p",
    "minimax": "minimax/video-01",
    "ltx":     "lightricks/ltx-video",
    "svd":     "stability-ai/stable-video-diffusion",
}


def _predict(model_slug: str, input_data: dict, token: str, timeout: int = 240) -> bytes:
    owner, name = model_slug.split("/", 1)

    # Submit prediction
    resp = httpx.post(
        f"{REPLICATE_API}/models/{owner}/{name}/predictions",
        headers={"Authorization": f"Token {token}", "Content-Type": "application/json"},
        json={"input": input_data},
        timeout=30,
    )
    resp.raise_for_status()
    pred = resp.json()
    poll_url = pred["urls"]["get"]

    # Poll until done
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(6)
        check = httpx.get(
            poll_url,
            headers={"Authorization": f"Token {token}"},
            timeout=15,
        )
        check.raise_for_status()
        data   = check.json()
        status = data.get("status")

        if status == "succeeded":
            output = data.get("output")
            if isinstance(output, list):
                output = output[0]
            video = httpx.get(output, timeout=60)
            video.raise_for_status()
            return video.content

        if status in ("failed", "canceled"):
            raise RuntimeError(f"Prediction {status}: {data.get('error', 'unknown')}")

    raise TimeoutError(f"Video generation timed out after {timeout}s")


def run(payload, config):
    token = os.environ.get("REPLICATE_API_TOKEN", "")
    if not token:
        return {
            "error": "REPLICATE_API_TOKEN not set",
            "hint": "export REPLICATE_API_TOKEN=r8_... in ~/.secrets",
        }

    prompt = payload.get("prompt", "")
    if not prompt:
        return {"error": "No prompt provided"}

    model_key   = payload.get("model", "wan")
    output_name = payload.get(
        "output_name", f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    if model_key not in _MODEL_SLUGS:
        return {"error": f"Unknown model '{model_key}'. Options: {list(_MODEL_SLUGS)}"}

    # Build model-specific input
    duration = int(payload.get("duration", 5))
    if model_key == "wan":
        input_data = {
            "prompt":              prompt,
            "num_frames":          min(duration * 8, 81),
            "sample_guide_scale":  5.0,
        }
    elif model_key == "minimax":
        input_data = {
            "prompt":           prompt,
            "prompt_optimizer": True,
        }
    elif model_key == "ltx":
        input_data = {
            "prompt":          prompt,
            "negative_prompt": "blurry, low quality, worst quality",
            "num_frames":      min(duration * 8, 97),
            "frame_rate":      24,
        }
    elif model_key == "svd":
        image_url = payload.get("image_url", "")
        if not image_url:
            return {"error": "svd model requires 'image_url' in payload"}
        input_data = {
            "input_image":       image_url,
            "video_length":      "14_frames_with_svd",
            "sizing_strategy":   "maintain_aspect_ratio",
            "motion_bucket_id":  127,
            "cond_aug":          0.02,
        }
    else:
        input_data = {"prompt": prompt}

    try:
        video_bytes = _predict(_MODEL_SLUGS[model_key], input_data, token,
                               timeout=payload.get("timeout", 240))
    except Exception as e:
        return {"error": str(e), "model": model_key}

    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    out = VIDEOS_DIR / f"{output_name}.mp4"
    out.write_bytes(video_bytes)

    return {
        "saved_to":    str(out),
        "prompt":      prompt,
        "model":       model_key,
        "output_name": output_name,
    }
