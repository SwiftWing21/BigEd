"""Tests for fleet/hardware_profiles.py auto-detection and profile lookup."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "fleet"))

from hardware_profiles import (
    ProfileConfig,
    _PROFILES,
    _classify_gpu_name,
    detect_profile,
    get_profile,
    get_detected_profile,
    usable_vram_gb,
)


def _make_gpu_module(name="NVIDIA GeForce RTX 3080 Ti", vram_bytes=12 * 1024**3, has_gpu=True):
    """Build a fake gpu module for injection into sys.modules."""
    gpu_mod = types.ModuleType("gpu")

    class MemInfo:
        def __init__(self):
            self.used_bytes = vram_bytes // 4
            self.total_bytes = vram_bytes

    class FakeBackend:
        def get_name(self):
            return name

        def get_memory_info(self):
            return MemInfo() if has_gpu else None

    def detect_gpu():
        b = FakeBackend()
        return b, has_gpu

    gpu_mod.detect_gpu = detect_gpu
    gpu_mod.read_telemetry = lambda b: None
    return gpu_mod


class TestProfileLookup(unittest.TestCase):
    def test_all_profiles_present(self):
        for name in ("cpu_only", "igpu", "gaming", "workstation", "datacenter"):
            self.assertIn(name, _PROFILES)

    def test_get_profile_returns_correct(self):
        p = get_profile("gaming")
        self.assertEqual(p.name, "gaming")
        self.assertGreater(p.vram_total_gb, 0)

    def test_get_profile_unknown_falls_back_to_cpu_only(self):
        p = get_profile("nonexistent")
        self.assertEqual(p.name, "cpu_only")

    def test_profile_is_frozen(self):
        p = get_profile("datacenter")
        with self.assertRaises((AttributeError, TypeError)):
            p.vram_total_gb = 999  # type: ignore[misc]

    def test_usable_vram_deducts_headroom(self):
        p = get_profile("gaming")
        expected = p.vram_total_gb * (1.0 - p.vram_headroom_pct)
        self.assertAlmostEqual(usable_vram_gb(p), expected, places=3)

    def test_usable_vram_cpu_only_is_zero(self):
        p = get_profile("cpu_only")
        self.assertEqual(usable_vram_gb(p), 0.0)


class TestClassifyGpuName(unittest.TestCase):
    def test_rtx_3080_is_gaming(self):
        self.assertEqual(_classify_gpu_name("NVIDIA GeForce RTX 3080 Ti"), "gaming")

    def test_rtx_4090_is_gaming(self):
        self.assertEqual(_classify_gpu_name("NVIDIA GeForce RTX 4090"), "gaming")

    def test_a100_is_datacenter(self):
        self.assertEqual(_classify_gpu_name("NVIDIA A100-SXM4-80GB"), "datacenter")

    def test_h100_is_datacenter(self):
        self.assertEqual(_classify_gpu_name("NVIDIA H100 PCIe"), "datacenter")

    def test_a4000_is_workstation(self):
        self.assertEqual(_classify_gpu_name("NVIDIA RTX A4000"), "workstation")

    def test_quadro_is_workstation(self):
        self.assertEqual(_classify_gpu_name("Quadro P6000"), "workstation")

    def test_intel_arc_is_igpu(self):
        self.assertEqual(_classify_gpu_name("Intel Arc A770"), "igpu")

    def test_amd_radeon_rx_is_gaming(self):
        self.assertEqual(_classify_gpu_name("AMD Radeon RX 7900 XTX"), "gaming")

    def test_unknown_defaults_to_gaming(self):
        self.assertEqual(_classify_gpu_name("Unknown GPU XYZ"), "gaming")


class TestDetectProfile(unittest.TestCase):
    def _with_gpu(self, name, vram_bytes, has_gpu=True):
        """Context: inject fake gpu module."""
        return patch.dict(
            sys.modules,
            {"gpu": _make_gpu_module(name=name, vram_bytes=vram_bytes, has_gpu=has_gpu)},
        )

    def test_detect_with_name_rtx3080(self):
        result = detect_profile(gpu_name="NVIDIA GeForce RTX 3080 Ti", vram_total_gb=12.0)
        self.assertEqual(result, "gaming")

    def test_detect_with_name_a100(self):
        result = detect_profile(gpu_name="NVIDIA A100", vram_total_gb=80.0)
        self.assertEqual(result, "datacenter")

    def test_detect_no_gpu_live(self):
        with self._with_gpu("none", 0, has_gpu=False):
            name = detect_profile()
        self.assertEqual(name, "cpu_only")

    def test_detect_gaming_gpu_live(self):
        with self._with_gpu("NVIDIA GeForce RTX 3080 Ti", 12 * 1024**3):
            name = detect_profile()
        self.assertEqual(name, "gaming")

    def test_detect_import_error_falls_back_to_cpu_only(self):
        # Patch the gpu module's detect_gpu to raise so hardware_profiles falls back
        import gpu as real_gpu
        original = real_gpu.detect_gpu

        def raise_import(*a, **kw):
            raise ImportError("no pynvml")

        real_gpu.detect_gpu = raise_import
        try:
            name = detect_profile()
        finally:
            real_gpu.detect_gpu = original
        self.assertEqual(name, "cpu_only")


class TestGetDetectedProfile(unittest.TestCase):
    def _with_gpu(self, name, vram_bytes, has_gpu=True):
        return patch.dict(
            sys.modules,
            {"gpu": _make_gpu_module(name=name, vram_bytes=vram_bytes, has_gpu=has_gpu)},
        )

    def test_patches_vram_from_live_detection(self):
        with self._with_gpu("NVIDIA GeForce RTX 4090", 24 * 1024**3):
            profile = get_detected_profile()
        self.assertEqual(profile.name, "gaming")
        self.assertAlmostEqual(profile.vram_total_gb, 24.0, places=1)

    def test_max_model_size_respects_headroom(self):
        with self._with_gpu("NVIDIA GeForce RTX 4090", 24 * 1024**3):
            profile = get_detected_profile()
        base = get_profile("gaming")
        expected_max = 24.0 * (1 - base.vram_headroom_pct)
        self.assertLessEqual(profile.max_model_size_gb, expected_max)

    def test_cpu_only_when_no_gpu(self):
        with self._with_gpu("none", 0, has_gpu=False):
            profile = get_detected_profile()
        self.assertEqual(profile.name, "cpu_only")
        self.assertEqual(profile.vram_total_gb, 0.0)

    def test_workstation_profile_detected(self):
        with self._with_gpu("NVIDIA RTX A5000", 24 * 1024**3):
            profile = get_detected_profile()
        self.assertEqual(profile.name, "workstation")

    def test_datacenter_profile_detected(self):
        with self._with_gpu("NVIDIA A100-SXM4-80GB", 80 * 1024**3):
            profile = get_detected_profile()
        self.assertEqual(profile.name, "datacenter")
        self.assertAlmostEqual(profile.vram_total_gb, 80.0, places=1)


if __name__ == "__main__":
    unittest.main()
