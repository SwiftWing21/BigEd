"""
Designer skill — generates visual assets with PIL.
Supports: icon, banner, color_scheme, sd_prompt (Stable Diffusion prompt queue).
Saves outputs to knowledge/design/
"""
import json
from datetime import datetime
from pathlib import Path

SKILL_NAME = "generate_asset"
DESCRIPTION = "Designer skill — generates visual assets with PIL."

FLEET_DIR   = Path(__file__).parent.parent
DESIGN_DIR  = FLEET_DIR / "knowledge" / "design"
SD_QUEUE    = DESIGN_DIR / "sd_prompt_queue.jsonl"


def _make_icon(spec: dict):
    from PIL import Image, ImageDraw, ImageFont
    size    = spec.get("size", 64)
    style   = spec.get("style", "solid")
    color   = spec.get("color", "#b22222")
    bg      = spec.get("bg", "#1a1a1a")
    label   = spec.get("label", "")

    img  = Image.new("RGBA", (size, size), bg)
    draw = ImageDraw.Draw(img)

    if style == "circle":
        pad = size // 8
        draw.ellipse([pad, pad, size - pad, size - pad], fill=color)
    elif style == "rounded":
        r = size // 6
        draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=r, fill=color)
    else:
        draw.rectangle([2, 2, size - 2, size - 2], fill=color)

    if label:
        try:
            font = ImageFont.truetype("arial.ttf", size // 3)
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) // 2, (size - th) // 2), label, fill="#ffffff", font=font)

    return img


def _make_banner(spec: dict):
    from PIL import Image, ImageDraw
    w       = spec.get("width", 400)
    h       = spec.get("height", 80)
    bg      = spec.get("bg", "#1a1a1a")
    accent  = spec.get("accent", "#b22222")
    pattern = spec.get("pattern", "gradient")

    img  = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)

    if pattern == "gradient":
        for x in range(w):
            t = x / w
            r = int(int(bg[1:3], 16) * (1 - t) + int(accent[1:3], 16) * t)
            g = int(int(bg[3:5], 16) * (1 - t) + int(accent[3:5], 16) * t)
            b = int(int(bg[5:7], 16) * (1 - t) + int(accent[5:7], 16) * t)
            draw.line([(x, 0), (x, h)], fill=(r, g, b))
    elif pattern == "stripe":
        for i in range(0, w, 20):
            draw.rectangle([i, 0, i + 10, h], fill=accent)
    else:
        draw.rectangle([0, 0, w, h], fill=bg)
        draw.rectangle([0, h - 4, w, h], fill=accent)

    return img


def _make_color_scheme(spec: dict):
    """Generate a harmonious color scheme and save as JSON + PNG swatch."""
    from PIL import Image, ImageDraw
    import colorsys

    base_hex = spec.get("base", "#b22222")
    r, g, b  = [int(base_hex[i:i+2], 16) / 255 for i in (1, 3, 5)]
    h, s, v  = colorsys.rgb_to_hsv(r, g, b)

    def hsv_hex(hh, ss, vv):
        rr, gg, bb = colorsys.hsv_to_rgb(hh % 1.0, ss, vv)
        return "#{:02x}{:02x}{:02x}".format(int(rr*255), int(gg*255), int(bb*255))

    scheme = {
        "base":        base_hex,
        "dark_bg":     hsv_hex(h, s * 0.2, v * 0.15),
        "mid_bg":      hsv_hex(h, s * 0.15, v * 0.22),
        "accent":      base_hex,
        "accent_light":hsv_hex(h, s * 0.7, min(v * 1.3, 1.0)),
        "text":        "#e2e2e2",
        "text_dim":    "#888888",
        "success":     "#4caf50",
        "warning":     "#ff9800",
        "gold":        hsv_hex(0.11, 0.6, 0.78),
    }

    # Draw swatch PNG
    sw = 60
    pad = 6
    img  = Image.new("RGB", (len(scheme) * (sw + pad) + pad, sw + pad * 2), "#111")
    draw = ImageDraw.Draw(img)
    for i, (name, color) in enumerate(scheme.items()):
        x = pad + i * (sw + pad)
        draw.rectangle([x, pad, x + sw, pad + sw], fill=color, outline="#333")

    return scheme, img


def _queue_sd_prompt(spec: dict):
    """Queue a Stable Diffusion prompt for when SD is available."""
    DESIGN_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "queued_at": datetime.now().isoformat(),
        "prompt":    spec.get("prompt", ""),
        "negative":  spec.get("negative", "blurry, low quality"),
        "width":     spec.get("width", 512),
        "height":    spec.get("height", 512),
        "steps":     spec.get("steps", 20),
        "output_name": spec.get("output_name", f"asset_{datetime.now().strftime('%H%M%S')}"),
        "status":    "queued",
    }
    with open(SD_QUEUE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def run(payload, config):
    asset_type  = payload.get("type", "icon")
    spec        = payload.get("spec", {})
    output_name = payload.get("output_name", f"{asset_type}_{datetime.now().strftime('%H%M%S')}")

    DESIGN_DIR.mkdir(parents=True, exist_ok=True)

    if asset_type == "icon":
        img = _make_icon(spec)
        out = DESIGN_DIR / f"{output_name}.png"
        img.save(out)
        return {"saved_to": str(out), "size": spec.get("size", 64)}

    elif asset_type == "banner":
        img = _make_banner(spec)
        out = DESIGN_DIR / f"{output_name}.png"
        img.save(out)
        return {"saved_to": str(out)}

    elif asset_type == "color_scheme":
        scheme, swatch = _make_color_scheme(spec)
        out_json   = DESIGN_DIR / f"{output_name}.json"
        out_swatch = DESIGN_DIR / f"{output_name}_swatch.png"
        out_json.write_text(json.dumps(scheme, indent=2))
        swatch.save(out_swatch)
        return {"scheme": scheme, "swatch": str(out_swatch), "json": str(out_json)}

    elif asset_type == "sd_prompt":
        entry = _queue_sd_prompt(spec)
        return {"status": "queued", "prompt": entry["prompt"],
                "queue_file": str(SD_QUEUE),
                "note": "SD not yet configured — run ComfyUI/A1111 and process the queue"}

    else:
        return {"error": f"Unknown asset type: {asset_type}. Use: icon, banner, color_scheme, sd_prompt"}