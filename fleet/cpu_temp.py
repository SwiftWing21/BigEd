"""
CPU temperature reader — cross-platform (Windows/Linux/macOS).

Strategies per platform:
  Linux:   psutil.sensors_temperatures() via lm-sensors (coretemp, k10temp, acpitz)
  Windows: LibreHardwareMonitor WMI (best) → MSAcpi_ThermalZoneTemperature (fallback)
  macOS:   powermetrics CLI (requires sudo) → osx-cpu-temp if installed

Enable notes:
  Linux:   sudo apt install lm-sensors && sudo sensors-detect
  Windows: Install LibreHardwareMonitor, run as admin (or enable "Run as service")
           https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
  macOS:   brew install osx-cpu-temp  (or use sudo powermetrics)

fleet.toml config:
  [thermal]
  cpu_temp_enabled = true   # enable CPU temp reading (may require admin on Windows)
"""
import os
import subprocess
import sys
import time

_cache_temp = 0
_cache_time = 0.0
_CACHE_TTL = 5  # seconds — subprocess calls are expensive
_detected_method = None  # cached detection: "psutil" | "lhm" | "wmi" | "powermetrics" | "osx-cpu-temp" | None


def read_cpu_temp() -> int:
    """Return CPU temp in Celsius. Returns 0 if unavailable."""
    global _cache_temp, _cache_time
    now = time.time()
    if now - _cache_time < _CACHE_TTL:
        return _cache_temp

    temp = _read_cpu_temp_impl()
    _cache_temp = temp
    _cache_time = now
    return temp


def get_cpu_temp_method() -> str:
    """Return which method is being used (for dashboard display)."""
    if _detected_method:
        return _detected_method
    # Trigger detection
    read_cpu_temp()
    return _detected_method or "unavailable"


def _read_cpu_temp_impl() -> int:
    global _detected_method

    # ── Strategy 1: psutil (Linux, some macOS) ───────────────────────────
    try:
        import psutil
        if hasattr(psutil, 'sensors_temperatures'):
            temps = psutil.sensors_temperatures()
            if temps:
                # Intel
                for chip in ['coretemp', 'k10temp', 'zenpower']:
                    if chip in temps:
                        for entry in temps[chip]:
                            if entry.current > 0:
                                _detected_method = f"psutil/{chip}"
                                return round(entry.current)
                # AMD
                if 'amdgpu' in temps:
                    for entry in temps['amdgpu']:
                        if 'edge' in (entry.label or '').lower() and entry.current > 0:
                            _detected_method = "psutil/amdgpu"
                            return round(entry.current)
                # Generic ACPI
                for chip in ['acpitz', 'thermal_zone0']:
                    if chip in temps:
                        for entry in temps[chip]:
                            if entry.current > 0:
                                _detected_method = f"psutil/{chip}"
                                return round(entry.current)
                # Any sensor with a positive reading
                for chip_name, entries in temps.items():
                    for entry in entries:
                        if entry.current > 0:
                            _detected_method = f"psutil/{chip_name}"
                            return round(entry.current)
    except Exception:
        pass

    # ── Platform-specific fallbacks ──────────────────────────────────────

    if sys.platform == "win32":
        return _read_windows()
    elif sys.platform == "darwin":
        return _read_macos()

    return 0


# ── Windows ──────────────────────────────────────────────────────────────────

def _read_windows() -> int:
    global _detected_method
    _NW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

    # Strategy 2: LibreHardwareMonitor WMI (best — requires LHM running as admin/service)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor "
             "| Where-Object { $_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU' } "
             "| Select -First 1 -ExpandProperty Value"],
            capture_output=True, text=True, timeout=5, creationflags=_NW,
        )
        if r.returncode == 0 and r.stdout.strip():
            val = float(r.stdout.strip())
            if 0 < val < 120:
                _detected_method = "LibreHardwareMonitor"
                return round(val)
    except Exception:
        pass

    # Strategy 3: MSAcpi_ThermalZoneTemperature (built-in, may need admin)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace root/wmi 2>$null "
             "| Select -First 1 -ExpandProperty CurrentTemperature"],
            capture_output=True, text=True, timeout=5, creationflags=_NW,
        )
        if r.returncode == 0 and r.stdout.strip():
            raw = int(r.stdout.strip())
            celsius = round((raw / 10) - 273.15)
            if 0 < celsius < 120:
                _detected_method = "WMI/ACPI"
                return celsius
    except Exception:
        pass

    # Strategy 4: wmic fallback (deprecated but still works on Win 10/11)
    try:
        r = subprocess.run(
            ["wmic", "/namespace:\\\\root\\wmi", "PATH",
             "MSAcpi_ThermalZoneTemperature", "get", "CurrentTemperature"],
            capture_output=True, text=True, timeout=5, creationflags=_NW,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    raw = int(line)
                    celsius = round((raw / 10) - 273.15)
                    if 0 < celsius < 120:
                        _detected_method = "wmic/ACPI"
                        return celsius
    except Exception:
        pass

    _detected_method = None
    return 0


# ── macOS ────────────────────────────────────────────────────────────────────

def _read_macos() -> int:
    global _detected_method

    # Strategy 2: osx-cpu-temp (brew install osx-cpu-temp — no sudo needed)
    try:
        import shutil
        if shutil.which("osx-cpu-temp"):
            r = subprocess.run(
                ["osx-cpu-temp"], capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                # Output like "65.0°C"
                val = float(r.stdout.strip().replace("°C", "").strip())
                if 0 < val < 120:
                    _detected_method = "osx-cpu-temp"
                    return round(val)
    except Exception:
        pass

    # Strategy 3: powermetrics (requires sudo — only works if run elevated)
    if os.geteuid() == 0 if hasattr(os, 'geteuid') else False:
        try:
            r = subprocess.run(
                ["powermetrics", "-s", "smc", "-i", "1", "-n", "1"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    if "CPU die temperature" in line:
                        # "CPU die temperature: 45.32 C"
                        parts = line.split(":")
                        if len(parts) == 2:
                            val = float(parts[1].strip().replace("C", "").strip())
                            if 0 < val < 120:
                                _detected_method = "powermetrics"
                                return round(val)
        except Exception:
            pass

    # Strategy 4: thermal framework via ioreg (Apple Silicon)
    try:
        r = subprocess.run(
            ["ioreg", "-rc", "AppleSmartBattery"],
            capture_output=True, text=True, timeout=5,
        )
        # This doesn't reliably give CPU temp but can give battery temp
        # Left as a placeholder for future Apple Silicon thermal APIs
    except Exception:
        pass

    _detected_method = None
    return 0
