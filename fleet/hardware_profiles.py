"""Hardware profile auto-detection and VRAM/model recommendations.

Profiles: cpu_only, igpu, gaming, workstation, datacenter
Replaces scattered hardcoded GPU values (RTX 3080 Ti, 12GB, etc.) with
profile-based lookups so the fleet adapts to any hardware.

Usage:
    from hardware_profiles import detect_profile, get_profile
    profile = detect_profile()   # auto-detects from gpu.py
    cfg = get_profile(profile)   # returns ProfileConfig dataclass
    vram = cfg.vram_total_gb     # safe default if detection fails
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("supervisor")


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    # VRAM budgeting
    vram_total_gb: float        # total VRAM GB (0 for cpu_only/igpu)
    vram_headroom_pct: float    # fraction reserved for OS/display (0.0–1.0)
    # Model sizing
    max_model_size_gb: float    # largest model that fits comfortably
    recommended_quant: str      # e.g. "Q4_K_M", "Q5_K_M", "Q8_0"
    max_concurrent_models: int  # how many models can sit in VRAM simultaneously
    # Descriptive
    description: str


# ── Profile definitions ───────────────────────────────────────────────────────

_PROFILES: dict[str, ProfileConfig] = {
    "cpu_only": ProfileConfig(
        name="cpu_only",
        vram_total_gb=0.0,
        vram_headroom_pct=0.0,
        max_model_size_gb=0.0,
        recommended_quant="Q4_K_M",
        max_concurrent_models=0,
        description="No discrete GPU — CPU inference only",
    ),
    "igpu": ProfileConfig(
        name="igpu",
        vram_total_gb=4.0,          # shared VRAM; assume ≤4GB usable
        vram_headroom_pct=0.35,     # OS + display eat a lot of shared memory
        max_model_size_gb=2.0,
        recommended_quant="Q4_K_M",
        max_concurrent_models=1,
        description="Integrated GPU (Intel Arc, AMD iGPU) — limited shared VRAM",
    ),
    "gaming": ProfileConfig(
        name="gaming",
        vram_total_gb=12.0,         # RTX 30xx/40xx range; updated by detect_profile
        vram_headroom_pct=0.10,
        max_model_size_gb=8.0,
        recommended_quant="Q5_K_M",
        max_concurrent_models=2,
        description="Consumer gaming GPU (RTX 30xx/40xx, RX 7xxx) — 8–24GB VRAM",
    ),
    "workstation": ProfileConfig(
        name="workstation",
        vram_total_gb=24.0,         # A-series / Quadro; updated by detect_profile
        vram_headroom_pct=0.08,
        max_model_size_gb=20.0,
        recommended_quant="Q8_0",
        max_concurrent_models=3,
        description="Workstation GPU (A4000/A5000/A6000, Quadro) — 16–48GB VRAM",
    ),
    "datacenter": ProfileConfig(
        name="datacenter",
        vram_total_gb=80.0,         # A100/H100 80GB; updated by detect_profile
        vram_headroom_pct=0.05,
        max_model_size_gb=70.0,
        recommended_quant="Q8_0",
        max_concurrent_models=4,
        description="Datacenter GPU (A100, H100, L40S) — 40–80GB VRAM",
    ),
}


def get_profile(name: str) -> ProfileConfig:
    """Return a ProfileConfig by name, falling back to cpu_only."""
    return _PROFILES.get(name, _PROFILES["cpu_only"])


# ── GPU name → profile heuristics ────────────────────────────────────────────

_DATACENTER_KEYWORDS = ("a100", "h100", "h200", "l40s", " a40 ", " a40,", " a40\t", "v100", "p100")
_WORKSTATION_KEYWORDS = ("a4000", "a5000", "a6000", "quadro", "rtx a", "rtx6000", "rtx5000")
_IGPU_KEYWORDS = ("intel", "arc", "iris", "vega", "radeon graphics")


def _classify_gpu_name(name: str) -> str:
    """Heuristically map a GPU name string to a profile name.

    Order matters: more-specific patterns checked first so "RTX A4000"
    (workstation) doesn't get caught by the generic "rtx" gaming check.
    """
    n = name.lower()
    if any(k in n for k in _DATACENTER_KEYWORDS):
        return "datacenter"
    if any(k in n for k in _WORKSTATION_KEYWORDS):
        return "workstation"
    if any(k in n for k in _IGPU_KEYWORDS):
        return "igpu"
    # Consumer gaming cards (check after workstation to avoid "RTX A" match)
    if any(k in n for k in ("gtx", "geforce", "radeon rx", "rx 7", "rx 6")):
        return "gaming"
    # Bare "rtx" without "A" prefix is a consumer card
    if "rtx" in n and " a" not in n:
        return "gaming"
    return "gaming"  # best guess for unknown discrete GPU


def detect_profile(gpu_name: Optional[str] = None, vram_total_gb: float = 0.0) -> str:
    """Auto-detect hardware profile.

    If gpu_name is not provided, reads from gpu.py at runtime.
    Returns the profile name string (key into _PROFILES).
    """
    if gpu_name is None:
        try:
            from gpu import detect_gpu, read_telemetry
            backend, has_gpu = detect_gpu()
            if not has_gpu:
                return "cpu_only"
            gpu_name = backend.get_name()
            if vram_total_gb == 0.0:
                mem = backend.get_memory_info()
                if mem:
                    vram_total_gb = round(mem.total_bytes / (1024 ** 3), 1)
        except Exception as exc:
            log.warning("hardware_profiles: GPU detection failed: %s", exc)
            return "cpu_only"

    if not gpu_name or gpu_name.lower() == "none":
        return "cpu_only"

    profile_name = _classify_gpu_name(gpu_name)
    return profile_name


def get_detected_profile(gpu_name: Optional[str] = None, vram_total_gb: float = 0.0) -> ProfileConfig:
    """Detect hardware profile and return a ProfileConfig with VRAM patched from live reading.

    The static ProfileConfig vram_total_gb is a reasonable default; this function
    replaces it with the actual detected VRAM when available.
    """
    actual_vram = vram_total_gb
    actual_name = gpu_name

    if actual_name is None or actual_vram == 0.0:
        try:
            from gpu import detect_gpu, read_telemetry
            backend, has_gpu = detect_gpu()
            if has_gpu:
                if actual_name is None:
                    actual_name = backend.get_name()
                if actual_vram == 0.0:
                    mem = backend.get_memory_info()
                    if mem:
                        actual_vram = round(mem.total_bytes / (1024 ** 3), 1)
        except Exception as exc:
            log.warning("hardware_profiles: live VRAM detection failed: %s", exc)

    profile_name = detect_profile(actual_name, actual_vram)
    base = get_profile(profile_name)

    # Patch vram_total_gb with actual reading if available
    if actual_vram > 0.0 and actual_vram != base.vram_total_gb:
        # Return a copy with the real VRAM value
        base = ProfileConfig(
            name=base.name,
            vram_total_gb=actual_vram,
            vram_headroom_pct=base.vram_headroom_pct,
            max_model_size_gb=min(base.max_model_size_gb, actual_vram * (1 - base.vram_headroom_pct)),
            recommended_quant=base.recommended_quant,
            max_concurrent_models=base.max_concurrent_models,
            description=base.description,
        )
    return base


def usable_vram_gb(profile: Optional[ProfileConfig] = None) -> float:
    """Return usable VRAM in GB after headroom deduction.

    Convenience wrapper used by marathon.py and hw_supervisor.py.
    """
    if profile is None:
        profile = get_detected_profile()
    return max(0.0, profile.vram_total_gb * (1.0 - profile.vram_headroom_pct))
