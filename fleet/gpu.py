"""
GPU abstraction layer — vendor-agnostic telemetry for NVIDIA, AMD, and Intel GPUs.

Detection order:
  1. pynvml  (NVIDIA)       — full: temp, VRAM, power, fan
  2. pyamdgpuinfo (AMD)     — temp, VRAM, power (no fan)
  3. rocm-smi CLI (AMD)     — temp, VRAM, power (no fan)
  4. sysfs hwmon (Linux)    — temp (possibly power/fan via hwmon)
  5. NullBackend            — CPU-only mode, all readings None

Usage:
    from gpu import detect_gpu, read_telemetry
    backend, has_gpu = detect_gpu()
    data = read_telemetry(backend)  # dict or None
"""
from __future__ import annotations

import subprocess
import sys
from typing import NamedTuple, Optional, Protocol


class MemInfo(NamedTuple):
    used_bytes: int
    total_bytes: int


class GPUBackend(Protocol):
    def get_name(self) -> str: ...
    def get_temperature(self) -> Optional[int]: ...
    def get_memory_info(self) -> Optional[MemInfo]: ...
    def get_power_usage(self) -> Optional[float]: ...
    def get_fan_speed(self) -> Optional[int]: ...


# ── NVIDIA (pynvml) ──────────────────────────────────────────────────────────

class NvidiaBackend:
    def __init__(self):
        import pynvml
        pynvml.nvmlInit()
        self._pynvml = pynvml
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)

    def get_name(self) -> str:
        name = self._pynvml.nvmlDeviceGetName(self._handle)
        return name if isinstance(name, str) else name.decode()

    def get_temperature(self) -> Optional[int]:
        try:
            return self._pynvml.nvmlDeviceGetTemperature(
                self._handle, self._pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            return None

    def get_memory_info(self) -> Optional[MemInfo]:
        try:
            mem = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
            return MemInfo(mem.used, mem.total)
        except Exception:
            return None

    def get_power_usage(self) -> Optional[float]:
        try:
            return self._pynvml.nvmlDeviceGetPowerUsage(self._handle) / 1000.0
        except Exception:
            return None

    def get_fan_speed(self) -> Optional[int]:
        try:
            return self._pynvml.nvmlDeviceGetFanSpeed(self._handle)
        except Exception:
            return None


# ── AMD (pyamdgpuinfo) ───────────────────────────────────────────────────────

class AmdBackend:
    def __init__(self):
        if sys.platform == "win32":
            raise RuntimeError("AMD GPU monitoring not supported on Windows")
        # Try pyamdgpuinfo first
        try:
            import pyamdgpuinfo
            if pyamdgpuinfo.detect_gpus() < 1:
                raise RuntimeError("no AMD GPUs found")
            self._dev = pyamdgpuinfo.get_gpu(0)
            self._lib = "pyamdgpuinfo"
        except (ImportError, RuntimeError):
            # Fall back to rocm-smi CLI
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showtemp", "--json"],
                    capture_output=True, text=True, timeout=5)
                if r.returncode != 0:
                    raise RuntimeError("rocm-smi failed")
                self._lib = "rocm-smi"
                self._dev = None
            except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired):
                raise RuntimeError("no AMD GPU backend available")

    def get_name(self) -> str:
        if self._lib == "pyamdgpuinfo":
            return self._dev.name or "AMD GPU"
        return "AMD GPU (rocm-smi)"

    def get_temperature(self) -> Optional[int]:
        try:
            if self._lib == "pyamdgpuinfo":
                return int(self._dev.query_temperature())
            return self._rocm_query("temperature")
        except Exception:
            return None

    def get_memory_info(self) -> Optional[MemInfo]:
        try:
            if self._lib == "pyamdgpuinfo":
                total = self._dev.memory_info["vram_size"]
                used = total - self._dev.query_vram_usage()
                return MemInfo(used, total)
            return self._rocm_query_vram()
        except Exception:
            return None

    def get_power_usage(self) -> Optional[float]:
        try:
            if self._lib == "pyamdgpuinfo":
                return self._dev.query_power()
            return self._rocm_query("power")
        except Exception:
            return None

    def get_fan_speed(self) -> Optional[int]:
        return None  # neither backend exposes fan reliably

    def _rocm_query(self, metric: str) -> Optional[int | float]:
        """Query a single metric from rocm-smi --json."""
        import json
        flag = {"temperature": "--showtemp", "power": "--showpower"}.get(metric)
        if not flag:
            return None
        r = subprocess.run(
            ["rocm-smi", flag, "--json"],
            capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        # rocm-smi JSON: {"card0": {"Temperature (Sensor edge) (C)": "45.0", ...}}
        card = next(iter(data.values()), {})
        for key, val in card.items():
            if metric == "temperature" and "emperature" in key and "edge" in key.lower():
                return int(float(val))
            if metric == "power" and "ower" in key and "average" in key.lower():
                return float(val)
        return None

    def _rocm_query_vram(self) -> Optional[MemInfo]:
        """Query VRAM from rocm-smi."""
        import json
        r = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        card = next(iter(data.values()), {})
        total = used = None
        for key, val in card.items():
            if "otal" in key:
                total = int(val)
            elif "sed" in key:
                used = int(val)
        if total is not None and used is not None:
            return MemInfo(used, total)
        return None


# ── Sysfs hwmon (Linux — AMD/Intel fallback) ─────────────────────────────────

class SysfsBackend:
    def __init__(self):
        if sys.platform != "linux":
            raise RuntimeError("sysfs only available on Linux")
        from pathlib import Path
        # Find first GPU hwmon with a temp sensor
        self._hwmon = None
        self._drm_card = None
        for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
            hwmon_dir = card / "device" / "hwmon"
            if not hwmon_dir.exists():
                continue
            for hwmon in sorted(hwmon_dir.iterdir()):
                if (hwmon / "temp1_input").exists():
                    self._hwmon = hwmon
                    self._drm_card = card
                    break
            if self._hwmon:
                break
        if not self._hwmon:
            raise RuntimeError("no GPU hwmon found in sysfs")

    def get_name(self) -> str:
        try:
            name_file = self._hwmon / "name"
            if name_file.exists():
                return name_file.read_text().strip()
        except Exception:
            pass
        return f"GPU ({self._drm_card.name})"

    def get_temperature(self) -> Optional[int]:
        try:
            raw = (self._hwmon / "temp1_input").read_text().strip()
            return int(raw) // 1000  # millidegrees -> degrees
        except Exception:
            return None

    def get_memory_info(self) -> Optional[MemInfo]:
        try:
            dev = self._drm_card / "device"
            total = int((dev / "mem_info_vram_total").read_text().strip())
            used = int((dev / "mem_info_vram_used").read_text().strip())
            return MemInfo(used, total)
        except Exception:
            return None

    def get_power_usage(self) -> Optional[float]:
        try:
            raw = (self._hwmon / "power1_average").read_text().strip()
            return int(raw) / 1_000_000  # microwatts -> watts
        except Exception:
            return None

    def get_fan_speed(self) -> Optional[int]:
        try:
            raw = (self._hwmon / "pwm1").read_text().strip()
            return round(int(raw) / 255 * 100)  # 0-255 -> 0-100%
        except Exception:
            return None


# ── Null (CPU-only fallback) ─────────────────────────────────────────────────

class NullBackend:
    def get_name(self) -> str:
        return "none"

    def get_temperature(self) -> Optional[int]:
        return None

    def get_memory_info(self) -> Optional[MemInfo]:
        return None

    def get_power_usage(self) -> Optional[float]:
        return None

    def get_fan_speed(self) -> Optional[int]:
        return None


# ── Factory & telemetry ──────────────────────────────────────────────────────

_BACKENDS = [NvidiaBackend, AmdBackend, SysfsBackend, NullBackend]


def detect_gpu() -> tuple[GPUBackend, bool]:
    """Try each backend in priority order. Returns (backend, has_gpu)."""
    for cls in _BACKENDS:
        try:
            backend = cls()
            has_gpu = not isinstance(backend, NullBackend)
            label = f"{backend.get_name()} via {cls.__name__}"
            if has_gpu:
                print(f"[GPU] Detected: {label}")
            else:
                print("[GPU] No GPU detected, CPU-only mode")
            return backend, has_gpu
        except Exception:
            continue
    # Should never reach here (NullBackend always succeeds), but just in case
    return NullBackend(), False


def read_telemetry(backend: GPUBackend) -> Optional[dict]:
    """Read all GPU metrics into the dict format expected by hw_supervisor."""
    if isinstance(backend, NullBackend):
        return None
    try:
        temp = backend.get_temperature()
        if temp is None:
            return None
        mem = backend.get_memory_info()
        power = backend.get_power_usage()
        fan = backend.get_fan_speed()
        result = {
            "gpu_temp_c": temp,
            "gpu_power_w": round(power, 1) if power is not None else 0.0,
            "gpu_fan_pct": fan if fan is not None else -1,
        }
        if mem is not None:
            result["vram_used_gb"] = round(mem.used_bytes / (1024**3), 2)
            result["vram_total_gb"] = round(mem.total_bytes / (1024**3), 2)
            result["vram_pct"] = round(mem.used_bytes / mem.total_bytes, 3)
        else:
            result["vram_used_gb"] = 0.0
            result["vram_total_gb"] = 0.0
            result["vram_pct"] = 0.0
        return result
    except Exception:
        return None
