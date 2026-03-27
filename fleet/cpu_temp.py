"""
CPU temperature reader — cross-platform (Windows/Linux/macOS).

Strategies per platform:
  Linux:   psutil.sensors_temperatures() via lm-sensors (coretemp, k10temp, acpitz)
  Windows: LibreHardwareMonitor REST API (best) → MSAcpi (fallback) → wmic (last resort)
  macOS:   powermetrics CLI (requires sudo) → osx-cpu-temp if installed

Enable notes:
  Linux:   sudo apt install lm-sensors && sudo sensors-detect
  Windows: Install LibreHardwareMonitor + enable web server (Options > Remote Web Server > Run)
           Auto-install: winget install LibreHardwareMonitor.LibreHardwareMonitor
           Fleet auto-launch: ensure_lhm() finds, starts, and configures LHM on a dynamic port
  macOS:   brew install osx-cpu-temp  (or use sudo powermetrics)

fleet.toml config:
  [thermal]
  cpu_temp_enabled = true   # enable CPU temp reading (may require admin on Windows)
  lhm_port = 8085           # LibreHardwareMonitor REST API port (auto-detected if omitted)
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


_LHM_KNOWN_PATHS = [
    # winget install location
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages"),
    # Common manual install locations
    os.path.join(os.environ.get("PROGRAMFILES", ""), "LibreHardwareMonitor"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "LibreHardwareMonitor"),
]


def find_lhm_exe() -> str | None:
    """Locate LibreHardwareMonitor.exe on disk."""
    import shutil
    # Check PATH first
    path = shutil.which("LibreHardwareMonitor")
    if path:
        return path

    # Search known locations
    for base in _LHM_KNOWN_PATHS:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            if "LibreHardwareMonitor.exe" in files:
                return os.path.join(root, "LibreHardwareMonitor.exe")
            # Don't recurse too deep
            if root.count(os.sep) - base.count(os.sep) > 3:
                _dirs.clear()
    return None


def _find_available_port(start: int = 8085, end: int = 8095) -> int:
    """Find first available port in range."""
    import socket
    for port in range(start, end + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start  # fallback


def _probe_lhm_port(port: int) -> bool:
    """Check if LHM web server responds on the given port."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/data.json", timeout=2):
            return True
    except Exception:
        return False


def detect_lhm_port() -> int | None:
    """Find which port LHM web server is running on (probes 8085-8095)."""
    for port in range(8085, 8096):
        if _probe_lhm_port(port):
            return port
    return None


def is_lhm_running() -> bool:
    """Check if LibreHardwareMonitor process is running."""
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            if proc.info["name"] and "LibreHardwareMonitor" in proc.info["name"]:
                return True
    except Exception:
        pass
    return False


def ensure_lhm() -> dict:
    """Ensure LHM is installed, running, and web server is reachable.

    Returns dict with keys: installed, running, port, exe_path, detail.
    """
    result = {"installed": False, "running": False, "port": None, "exe_path": None, "detail": ""}

    if sys.platform != "win32":
        result["detail"] = "LHM is Windows-only"
        return result

    # 1. Find executable
    exe = find_lhm_exe()
    if not exe:
        result["detail"] = "LHM not installed — winget install LibreHardwareMonitor.LibreHardwareMonitor"
        return result
    result["installed"] = True
    result["exe_path"] = exe

    # 2. Check if already running with web server
    port = detect_lhm_port()
    if port:
        result["running"] = True
        result["port"] = port
        result["detail"] = f"LHM web server on port {port}"
        return result

    # 3. Process is running but web server not enabled
    if is_lhm_running():
        result["running"] = True
        result["detail"] = "LHM running but web server not enabled — enable in Options > Remote Web Server > Run"
        return result

    # 4. Not running — try to start it with elevation
    try:
        import ctypes
        port = _find_available_port()
        # Check fleet.toml for headless preference (default: headless)
        show_lhm = False
        try:
            from config import load_config
            show_lhm = load_config().get("thermal", {}).get("lhm_visible", False)
        except Exception:
            pass

        # ShellExecuteW with "runas" triggers UAC elevation prompt
        show_flag = 1 if show_lhm else 0  # SW_SHOWNORMAL vs SW_HIDE
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe,
            f"--web-server --web-port {port}",
            None, show_flag
        )
        if ret > 32:  # ShellExecute returns >32 on success
            log.info("Started LHM with elevation (port %d)", port)
            # Wait for web server to come up
            import time
            for _ in range(10):
                time.sleep(1)
                if probe_lhm_port(port):
                    result["running"] = True
                    result["port"] = port
                    result["detail"] = f"LHM auto-started on port {port}"
                    return result
            result["running"] = True
            result["detail"] = f"LHM started but web server not responding on {port}"
        else:
            result["detail"] = f"LHM elevation failed (code {ret}) — start manually as admin"
    except Exception as e:
        log.warning("Failed to auto-start LHM: %s", e)
        result["detail"] = f"LHM auto-start failed: {e}"
    return result


def _extract_lhm_cpu_temp(data: dict) -> float:
    """Walk LHM JSON tree to find CPU package/die temperature.

    Looks for "Core (Tctl/Tdie)" (AMD) or "CPU Package" (Intel) under a CPU node.
    Falls back to any temperature sensor under a CPU/processor hardware node.
    """
    best = 0.0

    def _walk(node, is_cpu_hw=False):
        nonlocal best
        text = node.get("Text", "")
        value = node.get("Value", "")

        # Detect CPU hardware node
        hw_id = node.get("HardwareId", "")
        if "/cpu" in hw_id.lower() or "Ryzen" in text or "Core i" in text:
            is_cpu_hw = True

        # Extract temperature value from CPU hardware subtree
        if is_cpu_hw and "°C" in value:
            try:
                val = float(value.replace("°C", "").replace(",", ".").strip())
                # Prefer Tctl/Tdie (AMD) or CPU Package (Intel)
                if "Tctl" in text or "Package" in text:
                    best = val
                    return  # found the best, stop
                elif val > best:
                    best = val
            except (ValueError, TypeError):
                pass

        for child in node.get("Children", []):
            _walk(child, is_cpu_hw)

    _walk(data)
    return best


# ── Windows ──────────────────────────────────────────────────────────────────

def _read_windows() -> int:
    global _detected_method
    _NW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

    # Strategy 2: LibreHardwareMonitor REST API (best — LHM running with web server enabled)
    # Probes ports 8085-8095 or uses fleet.toml [thermal] lhm_port
    try:
        lhm_port = None
        try:
            from config import load_config
            lhm_port = load_config().get("thermal", {}).get("lhm_port")
        except Exception:
            pass
        if lhm_port and _probe_lhm_port(lhm_port):
            pass  # use configured port
        else:
            lhm_port = detect_lhm_port()
        if lhm_port:
            import json
            import urllib.request
            req = urllib.request.Request(f"http://localhost:{lhm_port}/data.json")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
            temp = _extract_lhm_cpu_temp(data)
            if temp and 0 < temp < 120:
                _detected_method = "LHM/REST"
                return round(temp)
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
