"""
Local image generation via Stable Diffusion (HuggingFace diffusers).
No API keys required — runs entirely on local hardware.

Models:
  sd15   — Stable Diffusion 1.5, GPU fp16 (~4GB VRAM, ~30s)  [default]
  sdxl   — Stable Diffusion XL, CPU fp32 (~12GB RAM, ~10-15 min)

Upscale methods (applied after base generation):
  none     — no upscaling (default)
  refine   — img2img re-pass at higher resolution using SD 1.5 (~30s extra)
  x4       — SD x4 upscaler, 512→2048 (~60-90s, ~2.5GB VRAM, ~3GB download)

Pipeline examples:
  512→512            : model=sd15                               (~30s)
  512→768 refine     : model=sd15, upscale=refine               (~60s)
  512→1024 refine x2 : model=sd15, upscale=refine, upscale_passes=2  (~90s)
  512→2048 x4        : model=sd15, upscale=x4                   (~2 min)

Requires: pip install diffusers transformers accelerate torch
Models download from HuggingFace on first run (~5GB SD1.5, ~7GB SDXL, ~3GB x4 upscaler).
"""
import gc
from datetime import datetime
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
IMAGES_DIR = KNOWLEDGE_DIR / "diffusion" / "images"

_MODEL_IDS = {
    "sd15": "stable-diffusion-v1-5/stable-diffusion-v1-5",
    "sdxl": "stabilityai/stable-diffusion-xl-base-1.0",
}

_UPSCALER_ID = "stabilityai/stable-diffusion-x4-upscaler"

_HARDWARE = {
    "sd15": "gpu",
    "sdxl": "cpu",
}

# Cache loaded pipelines to avoid reloading on back-to-back calls
_pipe_cache = {}


def _load_pipeline(model_key: str):
    if model_key in _pipe_cache:
        return _pipe_cache[model_key]

    import torch
    from diffusers import StableDiffusionPipeline, StableDiffusionXLPipeline

    model_id = _MODEL_IDS[model_key]

    if model_key == "sdxl":
        pipe = StableDiffusionXLPipeline.from_pretrained(
            model_id, torch_dtype=torch.float32,
        )
        pipe.to("cpu")
    else:
        pipe = StableDiffusionPipeline.from_pretrained(
            model_id, torch_dtype=torch.float16,
            safety_checker=None, requires_safety_checker=False,
        )
        pipe.to("cuda")
        pipe.enable_attention_slicing()

    _pipe_cache[model_key] = pipe
    return pipe


def _load_img2img_pipeline():
    """Load SD 1.5 img2img pipeline — shares weights with txt2img if cached."""
    if "img2img" in _pipe_cache:
        return _pipe_cache["img2img"]

    import torch
    from diffusers import StableDiffusionImg2ImgPipeline

    # Try to reuse components from cached txt2img pipeline
    if "sd15" in _pipe_cache:
        pipe = StableDiffusionImg2ImgPipeline(**_pipe_cache["sd15"].components)
    else:
        pipe = StableDiffusionImg2ImgPipeline.from_pretrained(
            _MODEL_IDS["sd15"], torch_dtype=torch.float16,
            safety_checker=None, requires_safety_checker=False,
        )
        pipe.to("cuda")
        pipe.enable_attention_slicing()

    _pipe_cache["img2img"] = pipe
    return pipe


def _load_x4_upscaler():
    """Load the SD x4 upscaler pipeline (~2.5GB VRAM fp16)."""
    if "x4" in _pipe_cache:
        return _pipe_cache["x4"]

    import torch
    from diffusers import StableDiffusionUpscalePipeline

    pipe = StableDiffusionUpscalePipeline.from_pretrained(
        _UPSCALER_ID, torch_dtype=torch.float16,
    )
    pipe.to("cuda")
    pipe.enable_attention_slicing()

    _pipe_cache["x4"] = pipe
    return pipe


def _unload(*keys):
    """Free memory for given pipeline keys."""
    for key in keys:
        pipe = _pipe_cache.pop(key, None)
        if pipe is not None:
            del pipe
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _load_gui_settings() -> dict:
    """Read diffusion settings from BigEd CC settings.json."""
    settings_file = Path(__file__).parent.parent.parent / "BigEd" / "launcher" / "data" / "settings.json"
    try:
        if settings_file.exists():
            import json
            return json.loads(settings_file.read_text()).get("diffusion", {})
    except Exception:
        pass
    return {}


def _upscale_refine(image, prompt, negative, steps, guidance, strength, target_w, target_h, seed):
    """Upscale via img2img re-pass — resize then refine at target resolution."""
    import torch

    resized = image.resize((target_w, target_h), resample=3)  # LANCZOS

    # Unload txt2img to free VRAM before loading img2img
    _unload("sd15")
    pipe = _load_img2img_pipeline()

    generator = None
    if seed is not None:
        generator = torch.Generator(device="cuda").manual_seed(seed)

    result = pipe(
        prompt=prompt,
        negative_prompt=negative,
        image=resized,
        strength=strength,
        num_inference_steps=steps,
        guidance_scale=guidance,
        generator=generator,
    )
    return result.images[0]


def _upscale_x4(image, prompt, negative, steps, guidance, seed):
    """Upscale via SD x4 upscaler — 512→2048 with prompt-aware enhancement."""
    import torch

    # Unload base pipeline to free VRAM
    _unload("sd15", "img2img")
    pipe = _load_x4_upscaler()

    generator = None
    if seed is not None:
        generator = torch.Generator(device="cuda").manual_seed(seed)

    result = pipe(
        prompt=prompt,
        negative_prompt=negative,
        image=image,
        num_inference_steps=steps,
        guidance_scale=guidance,
        generator=generator,
    )
    return result.images[0]


def run(payload, config):
    prompt = payload.get("prompt", "")
    if not prompt:
        return {"error": "No prompt provided"}

    gui = _load_gui_settings()
    model_key = payload.get("model", gui.get("default_model", "sd15"))
    if model_key not in _MODEL_IDS:
        return {"error": f"Unknown model '{model_key}'. Options: {list(_MODEL_IDS)}"}

    # Check if model is enabled in GUI settings
    enabled_key = f"{model_key}_enabled"
    if not gui.get(enabled_key, model_key == "sd15"):  # sd15 on by default
        return {"error": f"Model '{model_key}' is disabled in BigEd CC settings",
                "hint": "Enable it in Settings > Models > Image Generation"}

    negative = payload.get(
        "negative_prompt",
        "blurry, low quality, watermark, text, deformed, ugly",
    )
    steps = int(payload.get("steps", gui.get("default_steps", 30)))
    guidance = float(payload.get("guidance_scale", gui.get("default_guidance", 7.5)))
    width = int(payload.get("width", 768 if model_key == "sdxl" else 512))
    height = int(payload.get("height", 768 if model_key == "sdxl" else 512))
    seed = payload.get("seed", None)
    keep_loaded = payload.get("keep_loaded", False)
    output_name = payload.get(
        "output_name",
        f"diff_{model_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )

    # Upscale settings (payload overrides GUI defaults)
    upscale = payload.get("upscale", gui.get("default_upscale", "none"))
    upscale_passes = int(payload.get("upscale_passes", gui.get("default_upscale_passes", 1)))
    upscale_strength = float(payload.get("upscale_strength", gui.get("default_upscale_strength", 0.35)))
    upscale_factor = float(payload.get("upscale_factor", gui.get("default_upscale_factor", 1.5)))
    upscale_steps = int(payload.get("upscale_steps", 25))  # fewer steps ok for refine

    if upscale not in ("none", "refine", "x4"):
        return {"error": f"Unknown upscale method '{upscale}'. Options: none, refine, x4"}

    # x4 upscaler only works with GPU models
    if upscale == "x4" and model_key != "sd15":
        return {"error": "x4 upscaler requires GPU — only works with model='sd15'"}

    # Check dependencies
    try:
        import torch
        from diffusers import StableDiffusionPipeline  # noqa: F401
    except ImportError:
        return {
            "error": "Missing dependencies",
            "hint": "pip install diffusers transformers accelerate torch",
        }

    # GPU availability check for sd15
    if _HARDWARE[model_key] == "gpu" and not torch.cuda.is_available():
        return {
            "error": "CUDA not available — sd15 requires GPU",
            "hint": "Use model='sdxl' for CPU generation, or check torch CUDA install",
        }

    hw = _HARDWARE[model_key]
    device = "cuda" if hw == "gpu" else "cpu"
    pipeline_stages = ["base"]

    try:
        # ── Stage 1: Base generation ─────────────────────────────────────
        pipe = _load_pipeline(model_key)

        generator = None
        if seed is not None:
            generator = torch.Generator(device=device).manual_seed(int(seed))

        result = pipe(
            prompt=prompt,
            negative_prompt=negative,
            num_inference_steps=steps,
            guidance_scale=guidance,
            width=width,
            height=height,
            generator=generator,
        )
        image = result.images[0]
        final_w, final_h = width, height

        # ── Stage 2: Upscale ─────────────────────────────────────────────
        if upscale == "refine" and model_key == "sd15":
            for pass_num in range(upscale_passes):
                target_w = int(final_w * upscale_factor)
                target_h = int(final_h * upscale_factor)
                # Round to nearest 8 (required by SD)
                target_w = (target_w // 8) * 8
                target_h = (target_h // 8) * 8

                image = _upscale_refine(
                    image, prompt, negative, upscale_steps, guidance,
                    upscale_strength, target_w, target_h,
                    seed + pass_num + 1 if seed else None,
                )
                final_w, final_h = target_w, target_h
                pipeline_stages.append(f"refine_{pass_num + 1}({final_w}x{final_h})")

        elif upscale == "x4":
            image = _upscale_x4(
                image, prompt, negative, upscale_steps, guidance,
                seed + 100 if seed else None,
            )
            final_w, final_h = image.size
            pipeline_stages.append(f"x4({final_w}x{final_h})")

    except RuntimeError as e:
        if "out of memory" in str(e).lower():
            _unload(model_key, "img2img", "x4")
            return {
                "error": "GPU out of memory",
                "hint": "Close Ollama or other GPU tasks, reduce upscale_factor, or use model='sdxl' for CPU",
            }
        _unload(model_key, "img2img", "x4")
        return {"error": str(e)}
    except Exception as e:
        _unload(model_key, "img2img", "x4")
        return {"error": str(e)}

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    out = IMAGES_DIR / f"{output_name}.png"
    image.save(out)

    if not keep_loaded:
        _unload(model_key, "img2img", "x4")

    return {
        "saved_to": str(out),
        "prompt": prompt,
        "model": model_key,
        "hardware": hw,
        "resolution": f"{final_w}x{final_h}",
        "base_resolution": f"{width}x{height}",
        "pipeline": " → ".join(pipeline_stages),
        "upscale": upscale,
        "steps": steps,
        "guidance_scale": guidance,
        "seed": seed,
    }
