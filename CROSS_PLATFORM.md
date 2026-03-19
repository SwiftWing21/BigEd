# BigEd CC — Cross-Platform Architecture

> Companion to `FRAMEWORK_BLUEPRINT.md` S8 (Portability) and `ROADMAP_v030_v040.md` Platform Track (PT-1 through PT-4).

---

## 1. Platform Matrix

Component-by-component cross-platform support status:

| Component | Windows | Linux | macOS | Notes |
|-----------|---------|-------|-------|-------|
| **Fleet (`fleet/`)** | via WSL | Native | Native | Already cross-platform — Python, no OS deps |
| **Supervisor** | via WSL | Native | Native | Pure Python + SQLite |
| **HW Supervisor** | via WSL | Native | Native | `pynvml` (NVIDIA only), `psutil` (all) |
| **Workers** | via WSL | Native | Native | Skill dispatch, Ollama HTTP |
| **Dashboard** | via WSL | Native | Native | Flask, SSE — standard HTTP |
| **Skills** | via WSL | Native | Native | Python-only, provider routing via `_models.py` |
| **Launcher GUI** | Native | Planned | Planned | `customtkinter` — cross-platform but untested |
| **Installer** | Native (.exe) | Planned (AppImage) | Planned (.app/DMG) | Uses `winreg` — Windows-only |
| **Uninstaller** | Native (.exe) | Planned | Planned | Uses `winreg` — Windows-only |
| **Updater** | Native (.exe) | Planned | Planned | `.bat` trampoline — Windows-only |
| **Build pipeline** | `build.bat` | Planned (`build.py`) | Planned (`build.py`) | Windows-only `.bat` file |
| **Secrets (`~/.secrets`)** | Cross-platform | Cross-platform | Cross-platform | `Path.home()` — works everywhere |
| **Config (`fleet.toml`)** | Cross-platform | Cross-platform | Cross-platform | TOML parsing, no OS deps |
| **Smoke/soak tests** | via WSL | Native | Native | Pure Python |
| **RAG engine** | via WSL | Native | Native | SQLite FTS5 |

**Key insight:** Fleet is fully portable. Only the launcher ↔ fleet communication bridge and packaging/install tooling are Windows-locked.

---

## 2. FleetBridge Specification

### Interface

```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Callable

class FleetBridge(ABC):
    """Abstract communication layer between launcher (GUI) and fleet (backend)."""

    def __init__(self, fleet_dir: Path):
        self.fleet_dir = fleet_dir

    @abstractmethod
    def run(self, cmd: str, capture: bool = False, timeout: int = 60) -> Optional[str]:
        """Run a command in the fleet environment. Returns stdout if capture=True."""
        ...

    @abstractmethod
    def run_bg(self, cmd: str, callback: Optional[Callable] = None, timeout: int = 60):
        """Run a command in the fleet environment in a background thread."""
        ...

    @abstractmethod
    def fleet_path(self) -> str:
        """Return the fleet directory path as understood by the execution environment."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the bridge's execution environment is reachable."""
        ...
```

### WslBridge (Windows)

```python
class WslBridge(FleetBridge):
    """Windows: runs fleet commands inside WSL Ubuntu."""

    def fleet_path(self) -> str:
        # Convert C:\Users\...\fleet → /mnt/c/Users/.../fleet
        p = str(self.fleet_dir).replace("\\", "/")
        if len(p) > 1 and p[1] == ":":
            return f"/mnt/{p[0].lower()}{p[2:]}"
        return p

    def run(self, cmd, capture=False, timeout=60):
        full = f"cd {self.fleet_path()} && {cmd}"
        result = subprocess.run(
            ["wsl", "-d", "Ubuntu", "--", "bash", "-c", full],
            capture_output=capture, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.stdout.strip() if capture else None

    def run_bg(self, cmd, callback=None, timeout=60):
        threading.Thread(target=self._bg, args=(cmd, callback, timeout), daemon=True).start()

    def is_available(self) -> bool:
        try:
            r = subprocess.run(["wsl", "echo", "ok"], capture_output=True, text=True, timeout=5,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            return r.stdout.strip() == "ok"
        except Exception:
            return False
```

### DirectBridge (Linux / macOS)

```python
class DirectBridge(FleetBridge):
    """Linux/macOS: runs fleet commands natively in the same OS."""

    def fleet_path(self) -> str:
        return str(self.fleet_dir)

    def run(self, cmd, capture=False, timeout=60):
        full = f"cd {self.fleet_path()} && {cmd}"
        result = subprocess.run(
            ["bash", "-c", full],
            capture_output=capture, text=True, timeout=timeout,
        )
        return result.stdout.strip() if capture else None

    def run_bg(self, cmd, callback=None, timeout=60):
        threading.Thread(target=self._bg, args=(cmd, callback, timeout), daemon=True).start()

    def is_available(self) -> bool:
        return True  # Native environment — always available
```

### Detection Logic

```python
import sys

def create_bridge(fleet_dir: Path) -> FleetBridge:
    if sys.platform == "win32":
        return WslBridge(fleet_dir)
    else:
        return DirectBridge(fleet_dir)
```

---

## 3. Platform-Conditional Patterns

Code patterns for platform branching used throughout the codebase:

### Subprocess Flags

```python
import subprocess, sys

_CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

subprocess.run(cmd, creationflags=_CREATE_FLAGS)
```

### Process Management

```python
import psutil  # preferred — cross-platform

# Kill by PID
psutil.Process(pid).kill()

# Find process by name
for p in psutil.process_iter(["name"]):
    if p.info["name"] == "supervisor.py":
        p.kill()
```

### CPU Name Detection

```python
def _cpu_name() -> str:
    if sys.platform == "win32":
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        winreg.CloseKey(key)
        return name.strip()
    elif sys.platform == "linux":
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        return platform.processor() or "Unknown"
    elif sys.platform == "darwin":
        r = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                           capture_output=True, text=True)
        return r.stdout.strip() or platform.processor() or "Unknown"
    return platform.processor() or "Unknown"
```

### Home Directory

```python
from pathlib import Path

# Cross-platform (preferred)
home = Path.home()

# NOT this (Windows-only):
# home = os.environ.get("USERPROFILE", "C:/Users/max")
```

---

## 4. Windows-Specific Code Inventory

Exact locations of all platform-locked code (as of v0.31):

### launcher.py — WSL Bridge

| Line(s) | Pattern | Purpose |
|---------|---------|---------|
| 256-270 | `def wsl(cmd, ...)` | WSL command execution with `CREATE_NO_WINDOW` |
| 259-261 | Path conversion | `C:\...` → `/mnt/c/...` |
| 273-282 | `def wsl_bg(cmd, ...)` | Background WSL execution via threading |
| 1292, 1755, 1759, 1774, 1812, 1830, 1859, 1919, 1925, 1948, 1953, 1989, 1998 | `wsl_bg(...)` calls | Fleet dispatch, status polling |
| 1628 | `wsl("pgrep ...")` | Training detection |
| 3718 | `wsl("cat ~/.secrets ...")` | Secrets reading |
| 4279, 4323, 4383, 4432, 4459 | `wsl(...)` calls | Console key management |

### launcher.py — Other Windows Code

| Line(s) | Pattern | Purpose |
|---------|---------|---------|
| 74 | `USERPROFILE` env var | Home directory (should be `Path.home()`) |
| 266, 269, 1739, 2883, 3326, 3894 | `CREATE_NO_WINDOW` | Subprocess window suppression |
| 2070-2074 | `build.bat` reference | Rebuild trigger |
| 3318-3327 | PowerShell RunAs | nvidia-smi power limit |
| 3944-3948 | `winreg` import + registry read | CPU name detection |

### installer.py

| Line(s) | Pattern | Purpose |
|---------|---------|---------|
| 15, 61-88 | `winreg` | Registry entries for Add/Remove Programs |
| 105-115 | PowerShell `WScript.Shell` | Desktop/Start Menu shortcuts |
| 623-633 | `_cleanup.bat` generation | Self-cleanup trampoline |
| 37, 120, 125-126 | `LOCALAPPDATA`, `USERPROFILE`, `APPDATA` | Windows env vars for paths |

### updater.py

| Line(s) | Pattern | Purpose |
|---------|---------|---------|
| 454-466 | `_swap_updater.bat` generation | Self-replacement trampoline |
| 389, 466 | `CREATE_NO_WINDOW` | Subprocess flags |

### uninstaller.py

| Line(s) | Pattern | Purpose |
|---------|---------|---------|
| 10, 50-61 | `winreg` | Read install location, delete registry key |
| 274-286 | `_cleanup.bat` generation | Self-cleanup trampoline |
| 71, 76 | `USERPROFILE`, `APPDATA` | Windows env vars |

### build.bat

| File | Purpose |
|------|---------|
| `BigEd/launcher/build.bat` | Windows-only build script (PyInstaller with `;` separator) |

---

## 5. Migration Priority

### Phase 1: Platform Abstraction (PT-1) — Highest Priority

**Goal:** Make `launcher.py` run on Linux/macOS without code changes.

1. Implement `FleetBridge` ABC + `WslBridge` + `DirectBridge`
2. Replace all `wsl()` / `wsl_bg()` calls (~20 sites) with `bridge.run()` / `bridge.run_bg()`
3. Conditional `CREATE_NO_WINDOW` flags
4. `Path.home()` over `USERPROFILE`
5. Platform-branched `_cpu_name()`

**Estimated scope:** ~200 lines changed in `launcher.py`, new `fleet_bridge.py` module (~100 lines).

### Phase 2: Cross-Platform Build (PT-2)

**Goal:** Build on any platform with a single command.

1. `build.py` replacing `build.bat`
2. Auto-detect `--add-data` separator
3. GitHub Actions 3-platform CI

**Estimated scope:** New `build.py` (~50 lines), `.github/workflows/build.yml` (~30 lines).

### Phase 3: Platform Packaging (PT-3)

**Goal:** Installable packages for each platform.

1. AppImage tooling for Linux
2. `.app` bundle + DMG for macOS
3. Platform-conditional installer/uninstaller (abstract away `winreg`)
4. Updater: `exec` replacement on Linux/macOS instead of `.bat` trampoline

**Estimated scope:** Largest phase. Refactor `installer.py`, `uninstaller.py`, `updater.py`.

### Phase 4: Platform Testing (PT-4)

**Goal:** Confidence that each platform works.

1. Smoke test in CI per platform (headless)
2. Manual validation on Steam Deck (SteamOS/Arch Linux)
3. Platform troubleshooting matrix in `OPERATIONS.md` (done)

---

## 6. What Already Works Cross-Platform

These components require **zero changes** to run on Linux/macOS:

| Component | Why it works |
|-----------|-------------|
| `fleet/supervisor.py` | Pure Python, SQLite, no OS deps |
| `fleet/hw_supervisor.py` | `psutil` (cross-platform), `pynvml` (NVIDIA — graceful skip if missing) |
| `fleet/worker.py` | Skill dispatch via Python imports, Ollama via HTTP |
| `fleet/db.py` | SQLite WAL mode — works everywhere |
| `fleet/dashboard.py` | Flask HTTP — standard sockets |
| `fleet/rag.py` | SQLite FTS5 — cross-platform |
| `fleet/config.py` | TOML parsing — no OS deps |
| `fleet/lead_client.py` | CLI tool — pure Python |
| `fleet/skills/*` | Python-only, LLM routing via `_models.py` |
| `fleet/smoke_test.py` | Pure Python assertions |
| `fleet/soak_test.py` | Threading + SQLite — cross-platform |
| `fleet.toml` | TOML — text format |
| `~/.secrets` | `Path.home()` — cross-platform |
| `customtkinter` (GUI framework) | Cross-platform (tkinter underneath) |
