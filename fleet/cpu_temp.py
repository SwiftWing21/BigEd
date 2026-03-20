"""CPU temperature reader — cross-platform with Windows WMI fallback."""
import subprocess
import sys
import time

_cache_temp = 0
_cache_time = 0.0
_CACHE_TTL = 5  # seconds — CPU temp via subprocess is expensive

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

def _read_cpu_temp_impl() -> int:
    # Strategy 1: psutil (Linux/macOS)
    try:
        import psutil
        if hasattr(psutil, 'sensors_temperatures'):
            temps = psutil.sensors_temperatures()
            if temps:
                for chip in ['coretemp', 'k10temp', 'acpitz']:
                    if chip in temps:
                        for entry in temps[chip]:
                            if entry.current > 0:
                                return round(entry.current)
                for entries in temps.values():
                    for entry in entries:
                        if entry.current > 0:
                            return round(entry.current)
    except Exception:
        pass

    if sys.platform != "win32":
        return 0

    # Strategy 2: PowerShell WMI (Windows)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace root/wmi 2>$null | Select -First 1 -ExpandProperty CurrentTemperature"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if r.returncode == 0 and r.stdout.strip():
            # CurrentTemperature is in tenths of Kelvin
            raw = int(r.stdout.strip())
            celsius = round((raw / 10) - 273.15)
            if 0 < celsius < 120:
                return celsius
    except Exception:
        pass

    # Strategy 3: wmic fallback (deprecated but still works)
    try:
        r = subprocess.run(
            ["wmic", "/namespace:\\\\root\\wmi", "PATH", "MSAcpi_ThermalZoneTemperature", "get", "CurrentTemperature"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    raw = int(line)
                    celsius = round((raw / 10) - 273.15)
                    if 0 < celsius < 120:
                        return celsius
    except Exception:
        pass

    return 0
