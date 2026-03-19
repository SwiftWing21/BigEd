"""
Marketing campaign skill — orchestrates copy + image + optional video.

Payload:
  company        str   "Acme Dental"
  service        str   "local AI patient scheduling assistant"
  audience       str   "dental practice owners in Santa Cruz County"
  tone           str   "professional and approachable"  (default)
  generate_image bool  True  — create hero image via Stability AI
  generate_video bool  False — create short video clip via Replicate
  video_model    str   "wan"  — wan | minimax | ltx
  image_style    str   "photographic"

Output: knowledge/marketing/<campaign_id>/
  copy.json     — 3 copy variants (email, linkedin, video script)
  campaign.md   — human-readable summary
  hero.png      — generated image (if generate_image=True)
  clip.mp4      — generated video (if generate_video=True)
"""
import importlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

FLEET_DIR     = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
MARKETING_DIR = KNOWLEDGE_DIR / "marketing"
REQUIRES_NETWORK = True

sys.path.insert(0, str(Path(__file__).parent))


def _write_copy(company: str, service: str, audience: str, tone: str, config: dict) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    system = (
        "You are a concise B2B marketing copywriter specializing in local AI services. "
        "Write trustworthy, practical copy. No hype. Focus on ROI, privacy, and ease of adoption. "
        "Respond with valid JSON only — no markdown, no explanation."
    )
    user = (
        f"Company being pitched: {company}\n"
        f"Service / offer: {service}\n"
        f"Target audience: {audience}\n"
        f"Tone: {tone}\n\n"
        "Return JSON with exactly these keys:\n"
        '{"email": {"subject": "...", "body": "..."},\n'
        ' "linkedin": {"headline": "...", "pitch": "..."},\n'
        ' "video_script": "..."}\n\n'
        "Constraints:\n"
        "- email body: under 80 words\n"
        "- linkedin pitch: under 50 words\n"
        "- video_script: under 40 words, include visual cues in [brackets]"
    )

    model = config.get("models", {}).get("complex", "claude-haiku-4-5-20251001")
    resp = client.messages.create(
        model=model,
        max_tokens=700,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = resp.content[0].text.strip()
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except Exception:
            pass
    return {"raw": text}


def run(payload, config):
    company     = payload.get("company", "Your Company")
    service     = payload.get("service", "Local AI implementation")
    audience    = payload.get("audience", "Small business owners")
    tone        = payload.get("tone", "professional and approachable")
    do_image    = payload.get("generate_image", True)
    do_video    = payload.get("generate_video", False)
    image_style = payload.get("image_style", "photographic")
    video_model = payload.get("video_model", "wan")

    safe_name   = company.lower().replace(" ", "_")[:24]
    campaign_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    out_dir     = MARKETING_DIR / campaign_id
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {"campaign_id": campaign_id, "company": company, "saved_to": str(out_dir)}

    # ── 1. Copy ───────────────────────────────────────────────────────────────
    copy = _write_copy(company, service, audience, tone, config)
    result["copy"] = copy
    (out_dir / "copy.json").write_text(json.dumps(copy, indent=2))

    # ── 2. Image ──────────────────────────────────────────────────────────────
    if do_image:
        gi = importlib.import_module("generate_image")
        img_prompt = (
            f"Professional marketing photo for {service} aimed at {audience}. "
            f"Clean, modern business environment. No text, no logos, no watermarks."
        )
        img_result = gi.run({
            "prompt":       img_prompt,
            "aspect_ratio": "16:9",
            "style_preset": image_style,
            "output_name":  f"{campaign_id}_hero",
        }, config)
        result["image"] = img_result

    # ── 3. Video ──────────────────────────────────────────────────────────────
    if do_video:
        gv = importlib.import_module("generate_video")
        script = copy.get("video_script") or f"Professional AI services demo for {audience}."
        vid_result = gv.run({
            "prompt":      f"Professional business video: {script}",
            "model":       video_model,
            "duration":    5,
            "output_name": f"{campaign_id}_clip",
        }, config)
        result["video"] = vid_result

    # ── 4. Campaign summary ───────────────────────────────────────────────────
    md_lines = [
        f"# Campaign: {company}",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Service:** {service}",
        f"**Audience:** {audience}",
        "",
    ]
    if "email" in copy:
        md_lines += [
            "## Cold Email",
            f"**Subject:** {copy['email'].get('subject', '')}",
            "",
            copy["email"].get("body", ""),
            "",
        ]
    if "linkedin" in copy:
        md_lines += [
            "## LinkedIn",
            f"**{copy['linkedin'].get('headline', '')}**",
            "",
            copy["linkedin"].get("pitch", ""),
            "",
        ]
    if "video_script" in copy:
        md_lines += ["## Video Script", copy["video_script"], ""]

    if "image" in result:
        saved = result["image"].get("saved_to", "")
        if saved:
            md_lines.append(f"## Assets\n- Hero image: `{saved}`")
    if "video" in result:
        saved = result["video"].get("saved_to", "")
        if saved:
            md_lines.append(f"- Video clip: `{saved}`")

    (out_dir / "campaign.md").write_text("\n".join(md_lines))
    return result
