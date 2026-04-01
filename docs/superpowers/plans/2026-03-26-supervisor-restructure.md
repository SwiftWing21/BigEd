# Supervisor Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose `fleet/supervisor.py` (1890 lines) into 5 focused modules + a thin orchestrator, preserving all existing behavior.

**Architecture:** Extract code from supervisor.py into process_manager.py, health_monitor.py, scheduler.py, federation_manager.py, and boot_sequence.py. Each module is a class (or function set) with a clear boundary. Supervisor.py becomes a ~150-line orchestrator that instantiates the modules and runs the main loop. self_healing.py and diagnostics.py become re-export shims pointing to health_monitor.py.

**Tech Stack:** Python 3.11+, sqlite3, psutil (optional), subprocess, urllib.request

**Spec:** `docs/superpowers/specs/2026-03-26-supervisor-restructure-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `fleet/process_manager.py` | **Create** | All subprocess lifecycle: Ollama, workers, dashboard, Dr. Ders, Discord, OpenClaw |
| `fleet/health_monitor.py` | **Create** | Health sweeps, memory watchdog, circuit breakers, diagnostics, stale task recovery, watchdog, cache/RAG/context cleanup, reinforcement/ML router |
| `fleet/scheduler.py` | **Create** | Dynamic scaling, auto-triggers, manual mode, event triggers, cost anomaly, capacity bonus, training detection, config reload |
| `fleet/federation_manager.py` | **Create** | Peer heartbeat, overflow routing announcement, mTLS, discovery |
| `fleet/boot_sequence.py` | **Create** | Ordered startup: PID, logs, DB, Ollama, dashboard, workers, federation, backup, views |
| `fleet/supervisor.py` | **Rewrite** | Thin orchestrator: main loop, signal handlers, status writes, sup-channel, heartbeat file |
| `fleet/self_healing.py` | **Replace** | Re-export shim pointing to health_monitor.py |
| `fleet/diagnostics.py` | **Replace** | Re-export shim pointing to health_monitor.py |
| `fleet/tests/test_supervisor_restructure.py` | **Create** | Unit + integration tests for all new modules |

---

## Task 1: Create `fleet/tests/test_supervisor_restructure.py` — test scaffold

**Files:**
- Create: `fleet/tests/test_supervisor_restructure.py`

This test file grows across all subsequent tasks. We create the scaffold first so every module task follows TDD.

- [ ] **Step 1: Write the test scaffold with ProcessManager tests**

```python
#!/usr/bin/env python3
"""Tests for supervisor restructure — process_manager, health_monitor,
scheduler, federation_manager, boot_sequence.

Usage:
    python -m pytest fleet/tests/test_supervisor_restructure.py -v
    python fleet/tests/test_supervisor_restructure.py
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

FLEET_DIR = str(Path(__file__).resolve().parent.parent)
if FLEET_DIR not in sys.path:
    sys.path.insert(0, FLEET_DIR)

os.environ.setdefault("FLEET_TEST_DB", ":memory:")


# ── ProcessManager ──────────────────────────────────────────────────

def test_process_manager_imports():
    """ProcessManager class can be imported."""
    from process_manager import ProcessManager
    assert ProcessManager is not None


def test_process_manager_init():
    """ProcessManager initializes with config dict."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {"eco_mode": False}, "models": {}, "workers": {}})
    assert pm.worker_procs == {}
    assert pm.ollama_proc is None
    assert pm.dashboard_proc is None
    assert pm.hw_supervisor_proc is None
    assert pm.discord_proc is None
    assert pm.openclaw_proc is None
    assert pm.training_active is False


def test_process_manager_get_running_workers_empty():
    """get_running_workers returns empty set when no workers."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    assert pm.get_running_workers() == set()


def test_process_manager_find_ollama_returns_string():
    """find_ollama always returns a string (path or fallback)."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    result = pm._find_ollama()
    assert isinstance(result, str)
    assert len(result) > 0


def test_process_manager_read_hw_state_missing_file():
    """read_hw_state returns None when hw_state.json doesn't exist."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    # Temporarily point HW_STATE_FILE to nonexistent path
    import process_manager as pm_mod
    original = pm_mod.HW_STATE_FILE
    pm_mod.HW_STATE_FILE = Path("/tmp/nonexistent_hw_state_test.json")
    try:
        assert pm.read_hw_state() is None
    finally:
        pm_mod.HW_STATE_FILE = original


def test_process_manager_shutdown_all_no_procs():
    """shutdown_all completes without error when no processes running."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    pm.shutdown_all()  # should not raise


def test_process_manager_check_alive_no_workers():
    """check_alive completes without error when worker_procs is empty."""
    from process_manager import ProcessManager
    pm = ProcessManager({"fleet": {"disabled_agents": []}, "models": {}, "workers": {}})
    pm.check_alive()  # should not raise


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
```

- [ ] **Step 2: Run the tests to confirm they fail (module not found)**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v -x 2>&1 | head -20`
Expected: FAIL with `ModuleNotFoundError: No module named 'process_manager'`

- [ ] **Step 3: Commit the test scaffold**

```bash
git add fleet/tests/test_supervisor_restructure.py
git commit -m "test: add test scaffold for supervisor restructure"
```

---

## Task 2: Create `fleet/process_manager.py`

**Files:**
- Create: `fleet/process_manager.py`
- Test: `fleet/tests/test_supervisor_restructure.py` (tests from Task 1)

This is the largest extraction. All subprocess lifecycle moves here.

- [ ] **Step 1: Create process_manager.py with the ProcessManager class**

Extract the following from `fleet/supervisor.py` into a `ProcessManager` class:
- `_find_ollama()` (lines 367-382) -> `_find_ollama()` method
- `get_best_available_model()` (lines 385-414) -> `get_best_available_model()` method
- `_find_running_ollama()` (lines 417-426) -> `find_running_ollama()` method
- `_discover_loaded_models()` (lines 429-442) -> `discover_loaded_models()` method
- `start_ollama()` (lines 445-479) -> `start_ollama()` method
- `stop_ollama()` (lines 482-500) -> `stop_ollama()` method
- `start_discord_bot()` (lines 503-516) -> `start_discord_bot()` method
- `stop_discord_bot()` (lines 519-528) -> `stop_discord_bot()` method
- `start_openclaw()` (lines 531-543) -> `start_openclaw()` method
- `stop_openclaw()` (lines 546-555) -> `stop_openclaw()` method
- `_dashboard_port_alive()` (lines 558-568) -> `dashboard_port_alive()` method
- `start_dashboard()` (lines 571-591) -> `start_dashboard()` method
- `stop_dashboard()` (lines 594-603) -> `stop_dashboard()` method
- `start_hw_supervisor()` (lines 606-614) -> `start_hw_supervisor()` method
- `start_worker()` (lines 617-663) -> `start_worker()` method
- `_stop_worker()` (lines 353-364) -> `stop_worker()` method
- `_apply_resource_limits()` (lines 666-734) -> `_apply_resource_limits()` method
- `_get_running_workers()` (lines 180-186) -> `get_running_workers()` method
- `_ping_ollama_keepalive()` (lines 755-772) -> `ping_ollama_keepalive()` method
- `_warmup_conductor()` (lines 775-792) -> `warmup_conductor()` method
- `read_hw_state()` (lines 744-751) -> `read_hw_state()` method
- `HW_STATE_FILE` constant (line 737) -> module-level constant
- Dead worker respawn logic (lines 1353-1399) -> `check_alive()` method
- `shutdown()` teardown body (lines 847-875) -> `shutdown_all()` method

Key conventions to follow:
- Replace all `global X_proc` with `self.X_proc`
- Replace `worker_procs` dict access with `self.worker_procs`
- Replace `_last_busy` with `self.last_busy`
- Every `subprocess.Popen` keeps `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)`
- Every `urllib.request.urlopen` keeps `timeout=` parameter
- `except Exception:` never bare `except:`
- Use `self.config` for config access; `update_config()` method to swap in new config

```python
#!/usr/bin/env python3
"""Process manager — owns all fleet subprocess lifecycle.

Extracted from supervisor.py during restructure. Manages Ollama, workers,
dashboard, Dr. Ders, Discord, and OpenClaw processes.
"""

import gc
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("supervisor")

FLEET_DIR = Path(__file__).parent
PYTHON = sys.executable
HW_STATE_FILE = FLEET_DIR / "hw_state.json"


def _json_log(level, event, **kwargs):
    """Structured JSON log line for fleet processes."""
    import json as _json
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **kwargs}
    print(_json.dumps(entry), flush=True)


class ProcessManager:
    """Owns all fleet subprocess lifecycle."""

    def __init__(self, config: dict):
        self.config = config
        self.worker_procs: dict[str, subprocess.Popen | None] = {}
        self.ollama_proc: subprocess.Popen | None = None
        self.discord_proc: subprocess.Popen | None = None
        self.openclaw_proc: subprocess.Popen | None = None
        self.dashboard_proc: subprocess.Popen | None = None
        self.hw_supervisor_proc: subprocess.Popen | None = None
        self.training_active: bool = False
        self.ollama_evicted_for_training: bool = False
        self.last_busy: dict[str, float] = {}
        self._worker_next_start: dict[str, float] = {}

    def update_config(self, config: dict) -> None:
        """Hot-swap config (called after fleet.toml reload)."""
        self.config = config

    # ── Ollama ──────────────────────────────────────────────────────

    def _find_ollama(self) -> str:
        """Find the ollama executable — PATH, Windows default, or WSL."""
        path = shutil.which("ollama")
        if path:
            return path
        if sys.platform == "win32":
            for p in [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "ollama.exe",
                Path(os.environ.get("PROGRAMFILES", "")) / "Ollama" / "ollama.exe",
            ]:
                if p.exists():
                    return str(p)
        return "ollama"

    def find_running_ollama(self) -> bool:
        """Check if Ollama is already running (any process, not just ours)."""
        try:
            host = self.config.get("models", {}).get("ollama_host", "http://localhost:11434")
            with urllib.request.urlopen(f"{host}/api/tags", timeout=2):
                return True
        except Exception:
            return False

    def discover_loaded_models(self) -> list[str]:
        """Query Ollama for currently loaded models."""
        try:
            host = self.config.get("models", {}).get("ollama_host", "http://localhost:11434")
            with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
                data = json.loads(r.read())
            models = [m["name"] for m in data.get("models", [])]
            if models:
                log.info(f"Discovered loaded models: {', '.join(models)}")
            return models
        except Exception:
            return []

    def get_best_available_model(self) -> str:
        """Return the best available local model, falling back from configured default."""
        from config import load_config
        config = load_config()
        configured = config.get("models", {}).get("local", "qwen3:8b")
        try:
            host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
            with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as r:
                data = json.loads(r.read())
            available = [m["name"] for m in data.get("models", [])]
        except Exception:
            return configured
        if configured in available:
            return configured
        preference = ["qwen3:8b", "qwen3:4b", "qwen3:1.7b", "qwen3:0.6b"]
        for m in preference:
            if m in available:
                log.warning(f"Configured model '{configured}' not available, using fallback '{m}'")
                return m
        if available:
            log.warning(f"No preferred model available, using '{available[0]}'")
            return available[0]
        return configured

    def start_ollama(self, gpu: bool = True) -> None:
        """Start or adopt Ollama."""
        if self.find_running_ollama():
            loaded = self.discover_loaded_models()
            log.info(f"Ollama already running — adopting ({len(loaded)} models loaded)")
            _json_log("INFO", "ollama_adopt", models_loaded=len(loaded))
            return
        ollama_exe = self._find_ollama()
        env = os.environ.copy()
        if not gpu:
            env["CUDA_VISIBLE_DEVICES"] = "-1"
        elif "CUDA_VISIBLE_DEVICES" in env:
            del env["CUDA_VISIBLE_DEVICES"]
        env.setdefault("OLLAMA_NUM_PARALLEL", "4")
        env.setdefault("OLLAMA_MAX_LOADED_MODELS", "3")
        mode = "GPU" if gpu else "CPU"
        log.info(f"Starting Ollama ({mode} mode) — {ollama_exe}")
        _json_log("INFO", "ollama_start", mode=mode, exe=ollama_exe)
        try:
            self.ollama_proc = subprocess.Popen(
                [ollama_exe, "serve"], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            time.sleep(3)
        except FileNotFoundError:
            log.error(f"Ollama not found at '{ollama_exe}' — install from https://ollama.com")
            _json_log("ERROR", "ollama_not_found", exe=ollama_exe)
            self.ollama_proc = None

    def stop_ollama(self) -> None:
        """Stop Ollama (fleet-started) or unload models (adopted)."""
        if self.ollama_proc and self.ollama_proc.poll() is None:
            log.info("Stopping Ollama (fleet-started)")
            _json_log("INFO", "ollama_stop")
            self.ollama_proc.terminate()
            try:
                self.ollama_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ollama_proc.kill()
        elif not self.ollama_proc:
            log.info("External Ollama — unloading fleet models only (not stopping process)")
            try:
                from hw_supervisor import unload_all_models
                unload_all_models()
            except Exception:
                pass
        self.ollama_proc = None

    def ping_ollama_keepalive(self, keep_alive: str = None, model: str = None) -> None:
        """Load model into VRAM and keep it there."""
        host = self.config.get("models", {}).get("ollama_host", "http://localhost:11434")
        model = model or self.config.get("models", {}).get("local", "qwen3:8b")
        if keep_alive is None:
            keep_alive = f"{self.config.get('models', {}).get('keep_alive_mins', 30)}m"
        body = json.dumps({"model": model, "keep_alive": keep_alive,
                           "options": {"num_gpu": 99}}).encode()
        req = urllib.request.Request(
            f"{host}/api/generate", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
            log.debug(f"Ollama keep-alive ping sent (keep_alive={keep_alive})")
        except Exception as e:
            log.warning(f"Ollama keep-alive ping failed: {e}")

    def warmup_conductor(self) -> None:
        """Pre-load the conductor model on CPU for user chat."""
        host = self.config.get("models", {}).get("ollama_host", "http://localhost:11434")
        model = self.config.get("models", {}).get("conductor_model")
        if not model:
            return
        ka = f"{self.config.get('models', {}).get('keep_alive_mins', 30)}m"
        body = json.dumps({"model": model, "keep_alive": ka, "options": {"num_gpu": 0}}).encode()
        req = urllib.request.Request(
            f"{host}/api/generate", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30):
                pass
            log.info(f"Conductor model '{model}' warmed up on CPU")
        except Exception as e:
            log.warning(f"Conductor warmup failed: {e}")

    # ── Workers ─────────────────────────────────────────────────────

    def start_worker(self, role: str) -> None:
        """Start a worker process with CPU affinity and resource limits."""
        cmd = [PYTHON, str(FLEET_DIR / "worker.py"), "--role", role]
        if sys.platform != "win32":
            nice = self.config.get("workers", {}).get("nice_level", 10)
            cpu_limit = self.config.get("workers", {}).get("cpu_limit_percent", 80)
            cmd = ["nice", f"-n{nice}"] + cmd
            if shutil.which("cpulimit"):
                cmd = ["cpulimit", f"--limit={cpu_limit}", "--"] + cmd

        log.info(f"Starting worker: {role}")
        self.worker_procs[role] = subprocess.Popen(
            cmd, cwd=str(FLEET_DIR),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

        # CPU affinity
        if sys.platform != "darwin":
            try:
                import psutil
                proc = psutil.Process(self.worker_procs[role].pid)
                available = proc.cpu_affinity()
                num_cores = len(available) if available else (psutil.cpu_count(logical=True) or 4)
                worker_idx = len(self.worker_procs) - 1
                if num_cores >= 4:
                    stride = max(2, num_cores // max(len(self.worker_procs), 1))
                    core_a = available[(worker_idx * stride) % num_cores]
                    core_b = available[(worker_idx * stride + 1) % num_cores]
                    proc.cpu_affinity([core_a, core_b])
                    log.info(f"Worker {role}: pinned to cores {core_a},{core_b} (of {num_cores})")
                else:
                    log.debug(f"Worker {role}: only {num_cores} cores, skipping affinity")
            except AttributeError:
                log.debug(f"CPU affinity for {role}: not supported on this platform")
            except Exception as e:
                log.debug(f"CPU affinity for {role} skipped: {e}")

        memory_limit = self.config.get("workers", {}).get("memory_limit_mb", 0)
        if memory_limit > 0:
            self._apply_resource_limits(self.worker_procs[role], memory_limit)

    def stop_worker(self, role: str) -> None:
        """Gracefully stop a single worker process."""
        proc = self.worker_procs.get(role)
        if proc and proc.poll() is None:
            log.info(f"Stopping worker: {role}")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.worker_procs.pop(role, None)
        self.last_busy.pop(role, None)

    def get_running_workers(self) -> set:
        """Get names of currently running worker processes."""
        running = set()
        for name, proc in list(self.worker_procs.items()):
            if proc and proc.poll() is None:
                running.add(name)
        return running

    def _apply_resource_limits(self, proc, memory_limit_mb):
        """Apply OS-level resource limits to a worker process."""
        try:
            if sys.platform == "linux":
                log.info(f"Worker {proc.pid}: memory limit {memory_limit_mb}MB (advisory — cgroups recommended)")
            elif sys.platform == "win32":
                try:
                    import ctypes
                    from ctypes import wintypes

                    kernel32 = ctypes.windll.kernel32
                    job = kernel32.CreateJobObjectW(None, None)
                    if job:
                        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                            _fields_ = [
                                ("PerProcessUserTimeLimit", ctypes.c_int64),
                                ("PerJobUserTimeLimit", ctypes.c_int64),
                                ("LimitFlags", wintypes.DWORD),
                                ("MinimumWorkingSetSize", ctypes.c_size_t),
                                ("MaximumWorkingSetSize", ctypes.c_size_t),
                                ("ActiveProcessLimit", wintypes.DWORD),
                                ("Affinity", ctypes.c_size_t),
                                ("PriorityClass", wintypes.DWORD),
                                ("SchedulingClass", wintypes.DWORD),
                            ]

                        class IO_COUNTERS(ctypes.Structure):
                            _fields_ = [("ReadOperationCount", ctypes.c_uint64)] * 6

                        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                            _fields_ = [
                                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                                ("IoInfo", IO_COUNTERS),
                                ("ProcessMemoryLimit", ctypes.c_size_t),
                                ("JobMemoryLimit", ctypes.c_size_t),
                                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                                ("PeakJobMemoryUsed", ctypes.c_size_t),
                            ]

                        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                        info.BasicLimitInformation.LimitFlags = 0x00000100
                        info.ProcessMemoryLimit = memory_limit_mb * 1024 * 1024
                        kernel32.SetInformationJobObject(
                            job, 9, ctypes.byref(info), ctypes.sizeof(info)
                        )
                        handle = kernel32.OpenProcess(0x0001, False, proc.pid)
                        if handle:
                            kernel32.AssignProcessToJobObject(job, handle)
                            kernel32.CloseHandle(handle)
                            log.info(f"Worker {proc.pid}: Windows Job Object memory limit {memory_limit_mb}MB")
                except Exception as e:
                    log.debug(f"Windows Job Object limit failed: {e}")
            else:
                log.info(f"Worker {proc.pid}: memory limit {memory_limit_mb}MB (platform: advisory only)")
        except Exception as e:
            log.debug(f"Resource limit failed for {proc.pid}: {e}")

    # ── Services ────────────────────────────────────────────────────

    def start_hw_supervisor(self) -> None:
        """Start Dr. Ders (hw_supervisor)."""
        log.info("Starting Dr. Ders (hw_supervisor)")
        self.hw_supervisor_proc = subprocess.Popen(
            [PYTHON, str(FLEET_DIR / "hw_supervisor.py")],
            cwd=str(FLEET_DIR),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

    def start_dashboard(self) -> None:
        """Start the dashboard web server."""
        if not self.config.get("dashboard", {}).get("enabled", False):
            log.info("Dashboard disabled in fleet.toml")
            return
        if self.dashboard_port_alive():
            log.info("Dashboard already running — skipping launch")
            return
        dash_cfg = self.config.get("dashboard", {})
        port = dash_cfg.get("port", 5555)
        host = dash_cfg.get("bind_address", "127.0.0.1")
        dash_script = "web_app.py" if (FLEET_DIR / "web_app.py").exists() else "dashboard.py"
        log.info(f"Starting dashboard ({dash_script}) on http://{host}:{port}")
        self.dashboard_proc = subprocess.Popen(
            [PYTHON, str(FLEET_DIR / dash_script), "--port", str(port), "--host", host],
            cwd=str(FLEET_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

    def stop_dashboard(self) -> None:
        """Stop the dashboard."""
        if self.dashboard_proc and self.dashboard_proc.poll() is None:
            log.info("Stopping dashboard")
            self.dashboard_proc.terminate()
            try:
                self.dashboard_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.dashboard_proc.kill()
        self.dashboard_proc = None

    def dashboard_port_alive(self) -> bool:
        """Check if the dashboard port is already responding."""
        dash_cfg = self.config.get("dashboard", {})
        port = dash_cfg.get("port", 5555)
        host = dash_cfg.get("bind_address", "127.0.0.1")
        try:
            urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=2)
            return True
        except Exception:
            return False

    def start_discord_bot(self) -> None:
        """Start the Discord bot."""
        if not self.config.get("fleet", {}).get("discord_bot_enabled", True):
            log.info("Discord bot disabled in fleet.toml")
            return
        if not os.environ.get("DISCORD_BOT_TOKEN"):
            log.info("DISCORD_BOT_TOKEN not set — Discord bot disabled")
            return
        log.info("Starting Discord bot")
        self.discord_proc = subprocess.Popen(
            [PYTHON, str(FLEET_DIR / "discord_bot.py")],
            cwd=str(FLEET_DIR),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

    def stop_discord_bot(self) -> None:
        """Stop the Discord bot."""
        if self.discord_proc and self.discord_proc.poll() is None:
            log.info("Stopping Discord bot")
            self.discord_proc.terminate()
            try:
                self.discord_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.discord_proc.kill()
        self.discord_proc = None

    def start_openclaw(self) -> None:
        """Start OpenClaw gateway (manual API use only, not called during boot)."""
        if not self.config.get("fleet", {}).get("openclaw_enabled", False):
            log.info("OpenClaw disabled in fleet.toml (set openclaw_enabled=true to enable)")
            return
        port = self.config.get("openclaw", {}).get("port", 18789)
        log.info(f"Starting OpenClaw gateway on port {port}")
        self.openclaw_proc = subprocess.Popen(
            ["openclaw", "gateway", "--port", str(port)],
            cwd=str(FLEET_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

    def stop_openclaw(self) -> None:
        """Stop OpenClaw gateway."""
        if self.openclaw_proc and self.openclaw_proc.poll() is None:
            log.info("Stopping OpenClaw gateway")
            self.openclaw_proc.terminate()
            try:
                self.openclaw_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.openclaw_proc.kill()
        self.openclaw_proc = None

    # ── State ───────────────────────────────────────────────────────

    def read_hw_state(self) -> dict | None:
        """Read Dr. Ders state — returns dict or None."""
        try:
            if HW_STATE_FILE.exists():
                return json.loads(HW_STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.debug(f"[read_hw_state] failed to read hw_state.json: {e}")
        return None

    # ── Lifecycle ───────────────────────────────────────────────────

    def check_alive(self) -> None:
        """Respawn dead workers, Dr. Ders, dashboard, Discord, OpenClaw."""
        from config import is_offline, is_air_gap
        now = time.time()
        offline = is_offline(self.config)
        air_gap = is_air_gap(self.config)
        disabled = set(self.config.get("fleet", {}).get("disabled_agents", []))

        # Dead workers: mark None, schedule respawn after 15s cooldown
        for role in list(self.worker_procs.keys()):
            proc = self.worker_procs.get(role)
            if proc and proc.poll() is not None:
                if role in disabled:
                    log.info(f"Worker '{role}' exited and is disabled — removing from worker_procs")
                    del self.worker_procs[role]
                    continue
                log.warning(f"Worker '{role}' died (exit={proc.returncode}) — entering 15s cool-down")
                _json_log("WARNING", "worker_crash", worker=role, exit_code=proc.returncode)
                self.worker_procs[role] = None
                self._worker_next_start[role] = now + 15

        for role, next_time in list(self._worker_next_start.items()):
            if role in disabled:
                self._worker_next_start.pop(role, None)
                continue
            if self.worker_procs.get(role) is None and now >= next_time:
                log.info(f"Cool-down complete. Respawning worker '{role}'")
                _json_log("INFO", "worker_respawn", worker=role)
                self.start_worker(role)
                self._worker_next_start.pop(role, None)

        # Restart messaging bridges (skip when offline/air-gapped)
        if not offline:
            if self.discord_proc and self.discord_proc.poll() is not None:
                log.warning(f"Discord bot died (exit={self.discord_proc.returncode}) — restarting")
                self.start_discord_bot()
            if self.openclaw_proc and self.openclaw_proc.poll() is not None:
                log.warning(f"OpenClaw died (exit={self.openclaw_proc.returncode}) — restarting")
                self.start_openclaw()

        # Dashboard respawn
        if not air_gap:
            dp = self.dashboard_proc
            if dp and dp.poll() is not None:
                if not self.dashboard_port_alive():
                    log.warning(f"Dashboard died (exit={dp.returncode}) — restarting")
                    self.start_dashboard()
                else:
                    self.stop_dashboard()

        # Dr. Ders respawn
        if self.hw_supervisor_proc and self.hw_supervisor_proc.poll() is not None:
            log.warning("Dr. Ders crashed — respawning")
            self.start_hw_supervisor()

    def shutdown_all(self) -> None:
        """Clean teardown of all fleet processes."""
        _json_log("INFO", "supervisor_shutdown")
        # Stop discovery first
        try:
            import discovery
            discovery.stop_discovery()
        except Exception:
            pass
        self.stop_dashboard()
        self.stop_openclaw()
        self.stop_discord_bot()
        for role, proc in list(self.worker_procs.items()):
            if proc and proc.poll() is None:
                proc.terminate()
        for role, proc in list(self.worker_procs.items()):
            if proc:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        # Dr. Ders after workers, before Ollama
        if self.hw_supervisor_proc and self.hw_supervisor_proc.poll() is None:
            self.hw_supervisor_proc.terminate()
            try:
                self.hw_supervisor_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.hw_supervisor_proc.kill()
        self.stop_ollama()
        log.info("Fleet stopped.")
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v`
Expected: 7 tests PASS

- [ ] **Step 3: Commit**

```bash
git add fleet/process_manager.py
git commit -m "refactor: extract ProcessManager from supervisor.py"
```

---

## Task 3: Create `fleet/health_monitor.py`

**Files:**
- Create: `fleet/health_monitor.py`
- Modify: `fleet/tests/test_supervisor_restructure.py` (add health monitor tests)

Absorbs all of self_healing.py (585 lines), all of diagnostics.py (88 lines), plus supervisor.py health-related blocks.

- [ ] **Step 1: Add health monitor tests to the test file**

Append these tests to `fleet/tests/test_supervisor_restructure.py`:

```python
# ── HealthMonitor ───────────────────────────────────────────────────

def test_health_monitor_imports():
    """HealthMonitor class can be imported."""
    from health_monitor import HealthMonitor
    assert HealthMonitor is not None


def test_health_monitor_standalone_functions_importable():
    """All module-level functions from self_healing + diagnostics are importable."""
    from health_monitor import (
        check_agent_health,
        recover_agent,
        retry_failed_task,
        circuit_breaker_record_failure,
        circuit_breaker_is_open,
        get_circuit_breaker_status,
        run_health_sweep,
        detect_skill_regression,
        get_rollback_candidates,
        rollback_skill,
        get_agent_health_summary,
        get_skill_health_summary,
        get_recovery_log,
        quarantine_agent,
        clear_quarantine,
        get_failure_streaks,
        get_stuck_reviews,
    )
    # All should be callable
    assert callable(check_agent_health)
    assert callable(quarantine_agent)


def test_health_monitor_circuit_breaker_initially_closed():
    """Circuit breaker is closed for unknown skills."""
    from health_monitor import circuit_breaker_is_open
    assert circuit_breaker_is_open("nonexistent_skill_xyz") is False


def test_health_monitor_tick_no_crash():
    """HealthMonitor.tick() completes without error when PM has no procs."""
    from process_manager import ProcessManager
    from health_monitor import HealthMonitor
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    hm = HealthMonitor({"self_healing": {"enabled": False}}, pm)
    hm.tick(0.0)  # should not raise


def test_health_monitor_recovery_log_empty():
    """Recovery log starts empty."""
    from health_monitor import get_recovery_log
    # Note: this may have entries from other tests, but should be a list
    result = get_recovery_log()
    assert isinstance(result, list)
```

- [ ] **Step 2: Run tests to verify they fail (module not found)**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_health_monitor_imports -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'health_monitor'`

- [ ] **Step 3: Create health_monitor.py**

This file combines:
1. All of `self_healing.py` (standalone functions at module level, NOT class methods)
2. All of `diagnostics.py` (standalone functions at module level)
3. `HealthMonitor` class with `tick()` that calls into supervisor health blocks
4. Memory watchdog extracted from supervisor.py (lines 878-960)
5. Stale task recovery (lines 1508-1525)
6. Semantic watchdog dispatch (lines 1538-1573)
7. Context cleanup (lines 1527-1536)
8. Reinforcement/ML router (lines 1686-1706)
9. RAG stale cleanup (lines 1673-1684)
10. Cache invalidation (lines 1662-1671)
11. Health sweep dispatch (lines 1765-1781)

```python
#!/usr/bin/env python3
"""Unified health monitoring, recovery, and protection.

Absorbs self_healing.py and diagnostics.py. Provides:
- HealthMonitor class (tick-based, called from supervisor main loop)
- Module-level standalone functions (backward-compatible with self_healing/diagnostics imports)
"""

import gc
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("self_healing")

FLEET_DIR = Path(__file__).parent

# ── Memory watchdog constants ─────────────────────────────────────────
_WORKER_RSS_WARN_MB = 300
_WORKER_RSS_CRITICAL_MB = 600
_HW_SUP_RSS_CRITICAL_MB = 400
_SUP_SELF_RSS_WARN_MB = 200
_MEMORY_WATCHDOG_INTERVAL = 300

# ── Supervisor health intervals ───────────────────────────────────────
STALE_TASK_RECOVERY_INTERVAL = 300
STALE_TASK_TIMEOUT = 900
WATCHDOG_INTERVAL = 60
WATCHDOG_FULL_INTERVAL = 600

# ── In-memory circuit breaker state ──────────────────────────────────
_breakers = {}
_breaker_lock = threading.Lock()

# ── Recovery action log ──────────────────────────────────────────────
_recovery_log = []
_recovery_lock = threading.Lock()
_MAX_RECOVERY_LOG = 200


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Config helpers (from self_healing.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _cfg():
    """Load [self_healing] config from fleet.toml with safe defaults."""
    try:
        from config import load_config
        cfg = load_config()
        return cfg.get("self_healing", {})
    except Exception:
        return {}


def _default(key, fallback):
    return _cfg().get(key, fallback)


def _log_recovery(action: str, target: str, detail: str = ""):
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "action": action,
        "target": target,
        "detail": detail,
    }
    with _recovery_lock:
        _recovery_log.append(entry)
        if len(_recovery_log) > _MAX_RECOVERY_LOG:
            _recovery_log[:] = _recovery_log[-_MAX_RECOVERY_LOG:]
    try:
        from audit_log import log_event
        log_event("self_healing", "self_healing", entry, severity="warning")
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Standalone functions — from self_healing.py (unchanged signatures)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_agent_health(agent_name: str) -> dict:
    """Check if an agent is responsive based on heartbeat and error rate."""
    import db
    result = {
        "agent": agent_name, "healthy": True, "last_heartbeat": None,
        "error_rate": 0.0, "active_task": None, "idle_secs": 0, "issues": [],
    }
    try:
        with db.get_conn() as conn:
            agent = conn.execute(
                "SELECT status, last_heartbeat, current_task_id, pid "
                "FROM agents WHERE name = ?", (agent_name,)
            ).fetchone()
            if not agent:
                result["healthy"] = False
                result["issues"].append("agent_not_found")
                return result
            result["last_heartbeat"] = agent["last_heartbeat"]
            result["active_task"] = agent["current_task_id"]
            if agent["last_heartbeat"]:
                try:
                    hb = datetime.fromisoformat(agent["last_heartbeat"])
                    delta = (datetime.utcnow() - hb).total_seconds()
                    result["idle_secs"] = int(delta)
                    stuck_timeout = _default("agent_stuck_timeout", 300)
                    if delta > stuck_timeout:
                        result["healthy"] = False
                        result["issues"].append(f"no_heartbeat_{int(delta)}s")
                except Exception:
                    pass
            recent = conn.execute(
                "SELECT status FROM tasks WHERE assigned_to = ? "
                "AND classification != 'synthetic_prefix' "
                "ORDER BY id DESC LIMIT 30", (agent_name,)
            ).fetchall()
            if recent:
                failed = sum(1 for r in recent if r["status"] == "FAILED")
                result["error_rate"] = round(failed / len(recent), 3)
                if result["error_rate"] > 0.5:
                    result["healthy"] = False
                    result["issues"].append(f"high_error_rate_{result['error_rate']}")
            if agent["pid"]:
                try:
                    import psutil
                    if not psutil.pid_exists(agent["pid"]):
                        result["healthy"] = False
                        result["issues"].append("pid_dead")
                except ImportError:
                    pass
    except Exception as e:
        log.warning("check_agent_health failed for %s: %s", agent_name, e)
        result["healthy"] = False
        result["issues"].append(f"check_error: {e}")
    return result


def recover_agent(agent_name: str) -> dict:
    """Kill and restart an unresponsive agent by resetting its DB state."""
    import db
    result = {"agent": agent_name, "recovered": False, "detail": ""}
    try:
        pid = None
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT pid FROM agents WHERE name = ?", (agent_name,)
            ).fetchone()
            if row:
                pid = row["pid"]
        if pid:
            try:
                import psutil
                proc = psutil.Process(pid)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                result["detail"] = f"terminated pid {pid}"
            except ImportError:
                import signal as _sig
                try:
                    os.kill(pid, _sig.SIGTERM)
                    result["detail"] = f"sent SIGTERM to pid {pid}"
                except OSError:
                    result["detail"] = f"pid {pid} already dead"
            except Exception as e:
                result["detail"] = f"kill failed: {e}"

        def _reset():
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE agents SET status='IDLE', current_task_id=NULL, pid=NULL "
                    "WHERE name = ?", (agent_name,))
                conn.execute(
                    "UPDATE tasks SET status='PENDING', assigned_to=NULL "
                    "WHERE assigned_to = ? AND status = 'RUNNING'", (agent_name,))
        db._retry_write(_reset)
        result["recovered"] = True
        _log_recovery("recover_agent", agent_name, result["detail"])
        log.info("Recovered agent %s: %s", agent_name, result["detail"])
    except Exception as e:
        log.warning("recover_agent failed for %s: %s", agent_name, e)
        result["detail"] = f"error: {e}"
    return result


def retry_failed_task(task_id: int, max_retries: int = 3) -> dict:
    """Requeue a failed task with exponential backoff tracking."""
    import db
    result = {"task_id": task_id, "retried": False, "detail": ""}
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT status, type, payload_json, assigned_to "
                "FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not row:
                result["detail"] = "task_not_found"
                return result
            if row["status"] != "FAILED":
                result["detail"] = f"task_status_is_{row['status']}"
                return result
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            retry_count = payload.get("_retry_count", 0)
            if retry_count >= max_retries:
                result["detail"] = f"max_retries_exceeded ({retry_count}/{max_retries})"
                return result
            payload["_retry_count"] = retry_count + 1
            payload["_last_retry_ts"] = datetime.utcnow().isoformat()

        def _requeue():
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE tasks SET status='PENDING', assigned_to=NULL, "
                    "error=NULL, result_json=NULL, payload_json=? WHERE id=?",
                    (json.dumps(payload), task_id))
        db._retry_write(_requeue)
        result["retried"] = True
        result["detail"] = f"retry {retry_count + 1}/{max_retries}"
        _log_recovery("retry_task", f"task_{task_id} ({row['type']})", result["detail"])
        log.info("Retried task %d (%s): %s", task_id, row["type"], result["detail"])
    except Exception as e:
        log.warning("retry_failed_task failed for %d: %s", task_id, e)
        result["detail"] = f"error: {e}"
    return result


def circuit_breaker_record_failure(skill_name: str, error: str = ""):
    """Record a skill failure for circuit breaker evaluation."""
    now = time.time()
    with _breaker_lock:
        if skill_name not in _breakers:
            _breakers[skill_name] = {"failures": [], "tripped_at": None}
        _breakers[skill_name]["failures"].append((now, error[:200]))


def circuit_breaker_is_open(skill_name: str) -> bool:
    """Check if a skill's circuit breaker is tripped (open)."""
    threshold = _default("circuit_breaker_threshold", 3)
    window = _default("circuit_breaker_window", 300)
    now = time.time()
    with _breaker_lock:
        state = _breakers.get(skill_name)
        if not state:
            return False
        if state["tripped_at"]:
            if now - state["tripped_at"] > window:
                state["tripped_at"] = None
                state["failures"] = []
                log.info("Circuit breaker reset for skill %s", skill_name)
                _log_recovery("circuit_breaker_reset", skill_name)
                return False
            return True
        recent = [(ts, err) for ts, err in state["failures"] if now - ts <= window]
        state["failures"] = recent
        if len(recent) >= threshold:
            state["tripped_at"] = now
            log.warning("Circuit breaker TRIPPED for skill %s (%d failures in %ds)",
                        skill_name, len(recent), window)
            _log_recovery("circuit_breaker_trip", skill_name,
                          f"{len(recent)} failures in {window}s")
            return True
    return False


def get_circuit_breaker_status() -> list:
    """Return current state of all circuit breakers for dashboard."""
    now = time.time()
    window = _default("circuit_breaker_window", 300)
    result = []
    with _breaker_lock:
        for skill_name, state in _breakers.items():
            recent = [f for f in state["failures"] if now - f[0] <= window]
            result.append({
                "skill": skill_name,
                "tripped": state["tripped_at"] is not None,
                "tripped_at": datetime.utcfromtimestamp(state["tripped_at"]).isoformat()
                    if state["tripped_at"] else None,
                "recent_failures": len(recent),
                "last_error": recent[-1][1] if recent else "",
                "cooldown_remaining": max(0, int(window - (now - state["tripped_at"])))
                    if state["tripped_at"] else 0,
            })
    return result


def run_health_sweep() -> dict:
    """Check all agents and recover any that are stuck."""
    if not _default("enabled", True):
        return {"skipped": True, "reason": "self_healing disabled"}
    import db
    max_retries = _default("max_task_retries", 3)
    summary = {"checked": 0, "recovered_agents": [], "retried_tasks": [], "errors": []}
    try:
        with db.get_conn() as conn:
            agents = conn.execute("SELECT name FROM agents").fetchall()
        for row in agents:
            name = row["name"]
            summary["checked"] += 1
            health = check_agent_health(name)
            if not health["healthy"]:
                log.warning("Unhealthy agent %s: %s", name, health["issues"])
                result = recover_agent(name)
                if result["recovered"]:
                    summary["recovered_agents"].append(name)
        _NO_RETRY_TYPES = {"skill_draft", "skill_test", "skill_evolve", "skill_promote",
                           "deploy_skill", "skill_lifecycle_suite", "evolution_coordinator"}
        with db.get_conn() as conn:
            failed = conn.execute(
                "SELECT id, type, payload_json FROM tasks "
                "WHERE status = 'FAILED' "
                "AND created_at >= datetime('now', '-1 hour') "
                "ORDER BY id DESC LIMIT 20"
            ).fetchall()
        for task in failed:
            if task["type"] in _NO_RETRY_TYPES:
                continue
            try:
                payload = json.loads(task["payload_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            retry_count = payload.get("_retry_count", 0)
            if retry_count < max_retries:
                result = retry_failed_task(task["id"], max_retries)
                if result["retried"]:
                    summary["retried_tasks"].append(task["id"])
    except Exception as e:
        log.warning("Health sweep error: %s", e)
        summary["errors"].append(str(e))
    if summary["recovered_agents"] or summary["retried_tasks"]:
        log.info("Health sweep: recovered %d agents, retried %d tasks",
                 len(summary["recovered_agents"]), len(summary["retried_tasks"]))
    return summary


def detect_skill_regression(skill_name: str, window_hours: int = 6) -> bool:
    """Compare recent success rate vs 7-day baseline."""
    import db
    threshold = _default("regression_threshold", 0.20)
    try:
        with db.get_conn() as conn:
            recent = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done "
                "FROM tasks WHERE type = ? "
                "AND created_at >= datetime('now', ?)",
                (skill_name, f"-{window_hours} hours")
            ).fetchone()
            baseline = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done "
                "FROM tasks WHERE type = ? "
                "AND created_at >= datetime('now', '-7 days') "
                "AND created_at < datetime('now', ?)",
                (skill_name, f"-{window_hours} hours")
            ).fetchone()
            if not baseline or baseline["total"] < 5:
                return False
            if not recent or recent["total"] < 3:
                return False
            baseline_rate = baseline["done"] / baseline["total"]
            recent_rate = recent["done"] / recent["total"]
            drop = baseline_rate - recent_rate
            if drop > threshold:
                log.warning("Skill regression: %s success rate dropped %.1f%% "
                            "(baseline: %.1f%% -> recent: %.1f%%)",
                            skill_name, drop * 100, baseline_rate * 100,
                            recent_rate * 100)
                return True
    except Exception as e:
        log.warning("detect_skill_regression error for %s: %s", skill_name, e)
    return False


def get_rollback_candidates() -> list:
    """Find skills with >regression_threshold success rate drop in last 6 hours."""
    import db
    candidates = []
    try:
        with db.get_conn() as conn:
            skills = conn.execute(
                "SELECT DISTINCT type FROM tasks "
                "WHERE created_at >= datetime('now', '-6 hours') "
                "AND type IS NOT NULL"
            ).fetchall()
        for row in skills:
            skill_name = row["type"]
            if detect_skill_regression(skill_name):
                drafts_dir = FLEET_DIR / "knowledge" / "code_drafts"
                has_backup = False
                backup_file = None
                if drafts_dir.exists():
                    matches = sorted(drafts_dir.glob(f"{skill_name}_draft_*.py"), reverse=True)
                    if matches:
                        has_backup = True
                        backup_file = str(matches[0])
                candidates.append({
                    "skill": skill_name, "has_backup": has_backup,
                    "backup_file": backup_file,
                    "detected_at": datetime.utcnow().isoformat(),
                })
    except Exception as e:
        log.warning("get_rollback_candidates error: %s", e)
    return candidates


def rollback_skill(skill_name: str) -> dict:
    """Restore a skill from its most recent code_drafts backup."""
    result = {"skill": skill_name, "rolled_back": False, "detail": ""}
    if not _default("auto_rollback_enabled", True):
        result["detail"] = "auto_rollback_disabled"
        return result
    skill_file = FLEET_DIR / "skills" / f"{skill_name}.py"
    drafts_dir = FLEET_DIR / "knowledge" / "code_drafts"
    if not skill_file.exists():
        result["detail"] = "skill_file_not_found"
        return result
    if not drafts_dir.exists():
        result["detail"] = "no_code_drafts_directory"
        return result
    matches = sorted(drafts_dir.glob(f"{skill_name}_draft_*.py"), reverse=True)
    if not matches:
        result["detail"] = "no_draft_backup_available"
        return result
    backup_source = matches[0]
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_rollback = drafts_dir / f"{skill_name}_pre_rollback_{ts}.py"
        shutil.copy2(str(skill_file), str(pre_rollback))
        shutil.copy2(str(backup_source), str(skill_file))
        result["rolled_back"] = True
        result["detail"] = f"restored from {backup_source.name}, pre-rollback saved to {pre_rollback.name}"
        _log_recovery("rollback_skill", skill_name, result["detail"])
        log.info("Rolled back skill %s: %s", skill_name, result["detail"])
    except Exception as e:
        log.warning("rollback_skill failed for %s: %s", skill_name, e)
        result["detail"] = f"error: {e}"
    return result


def get_agent_health_summary() -> list:
    """Per-agent health status for dashboard."""
    import db
    agents = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute("SELECT name FROM agents").fetchall()
        for row in rows:
            agents.append(check_agent_health(row["name"]))
    except Exception as e:
        log.warning("get_agent_health_summary error: %s", e)
    return agents


def get_skill_health_summary() -> list:
    """Skill success rates with regression flags for dashboard."""
    import db
    skills = []
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT type as skill, COUNT(*) as total, "
                "SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) as done, "
                "SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed, "
                "ROUND(AVG(intelligence_score), 3) as avg_iq "
                "FROM tasks WHERE created_at >= datetime('now', '-24 hours') "
                "AND type IS NOT NULL GROUP BY type ORDER BY total DESC"
            ).fetchall()
        for row in rows:
            total = row["total"] or 1
            success_rate = round((row["done"] or 0) / total, 3)
            regressed = detect_skill_regression(row["skill"])
            breaker_open = circuit_breaker_is_open(row["skill"])
            skills.append({
                "skill": row["skill"], "total_24h": total,
                "success_rate": success_rate, "failed_24h": row["failed"] or 0,
                "avg_iq": row["avg_iq"], "regressed": regressed,
                "circuit_breaker_open": breaker_open,
            })
    except Exception as e:
        log.warning("get_skill_health_summary error: %s", e)
    return skills


def get_recovery_log() -> list:
    """Return recent recovery actions for dashboard."""
    with _recovery_lock:
        return list(_recovery_log)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Standalone functions — from diagnostics.py (unchanged signatures)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def quarantine_agent(name: str, reason: str) -> None:
    """Set agent status to QUARANTINED with reason stored in messages."""
    import db
    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE agents SET status='QUARANTINED' WHERE name=?", (name,))
            conn.execute("""
                INSERT INTO messages (from_agent, to_agent, body_json, channel)
                VALUES ('watchdog', ?, ?, 'fleet')
            """, (name, json.dumps({"type": "quarantine", "reason": reason})))
    db._retry_write(_do)


def clear_quarantine(name: str) -> None:
    """Remove quarantine status — agent returns to IDLE."""
    import db
    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE agents SET status='IDLE' WHERE name=? AND status='QUARANTINED'",
                (name,))
    db._retry_write(_do)


def get_failure_streaks(threshold: int = 3) -> list:
    """Find agents with N+ consecutive recent task failures."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT assigned_to as agent,
                   COUNT(*) as fail_count,
                   MAX(error) as last_error
            FROM (
                SELECT assigned_to, error, status,
                       ROW_NUMBER() OVER (PARTITION BY assigned_to ORDER BY id DESC) as rn
                FROM tasks
                WHERE assigned_to IS NOT NULL AND status IN ('FAILED', 'DONE')
                  AND classification != 'synthetic_prefix'
            )
            WHERE rn <= ? AND status = 'FAILED'
            GROUP BY assigned_to
            HAVING fail_count >= ?
        """, (threshold + 2, threshold)).fetchall()
        if not rows:
            rows = conn.execute("""
                SELECT assigned_to as agent, COUNT(*) as fail_count,
                       MAX(error) as last_error
                FROM (
                    SELECT * FROM tasks
                    WHERE assigned_to IS NOT NULL AND status = 'FAILED'
                      AND classification != 'synthetic_prefix'
                    ORDER BY id DESC LIMIT ?
                )
                GROUP BY assigned_to
                HAVING fail_count >= ?
            """, (threshold * 20, threshold)).fetchall()
        return [dict(r) for r in rows]


def get_stuck_reviews(timeout_minutes: int = 30) -> list:
    """Find tasks stuck in REVIEW status for too long."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT id, type, assigned_to
            FROM tasks
            WHERE status = 'REVIEW'
              AND (julianday('now') - julianday(created_at)) * 1440 > ?
        """, (timeout_minutes,)).fetchall()
        return [dict(r) for r in rows]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HealthMonitor class — tick-based supervisor integration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _json_log(level, event, **kwargs):
    """Structured JSON log line."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **kwargs}
    print(json.dumps(entry), flush=True)


class HealthMonitor:
    """Unified health monitoring, recovery, and protection."""

    def __init__(self, config: dict, pm):
        self.config = config
        self.pm = pm  # ProcessManager instance
        self._last_health_sweep: float = 0
        self._last_memory_watchdog: float = 0
        self._last_stale_check: float = 0
        self._last_watchdog: float = 0
        self._last_watchdog_full: float = 0
        self._last_context_cleanup: float = 0
        self._last_feedback_check: float = 0
        self._last_cache_cleanup: float = 0
        self._last_rag_cleanup: float = 0

    def update_config(self, config: dict) -> None:
        self.config = config

    def tick(self, now: float) -> None:
        """Called every 5s from main loop. Runs all health checks."""
        try:
            self._run_health_sweep(now)
        except Exception:
            log.warning("Health sweep failed", exc_info=True)
        try:
            self._run_memory_watchdog(now)
        except Exception:
            log.warning("Memory watchdog failed", exc_info=True)
        try:
            self._recover_stale_tasks(now)
        except Exception:
            log.warning("Stale task recovery failed", exc_info=True)
        try:
            self._run_watchdog(now)
        except Exception:
            log.warning("Watchdog failed", exc_info=True)
        try:
            self._cleanup_contexts(now)
        except Exception:
            log.warning("Context cleanup failed", exc_info=True)
        try:
            self._check_feedback(now)
        except Exception:
            log.warning("Feedback check failed", exc_info=True)
        try:
            self._cleanup_caches(now)
        except Exception:
            log.warning("Cache cleanup failed", exc_info=True)
        try:
            self._cleanup_rag(now)
        except Exception:
            log.warning("RAG cleanup failed", exc_info=True)

    def _run_health_sweep(self, now: float) -> None:
        heal_cfg = self.config.get("self_healing", {})
        heal_interval = heal_cfg.get("health_sweep_interval", 60)
        if not heal_cfg.get("enabled", True):
            return
        if now - self._last_health_sweep < heal_interval:
            return
        self._last_health_sweep = now
        sweep = run_health_sweep()
        if sweep.get("recovered_agents") or sweep.get("retried_tasks"):
            log.info("Health sweep: recovered %d agents, retried %d tasks",
                     len(sweep.get("recovered_agents", [])),
                     len(sweep.get("retried_tasks", [])))
            _json_log("INFO", "health_sweep",
                      recovered=len(sweep.get("recovered_agents", [])),
                      retried=len(sweep.get("retried_tasks", [])))

    def _run_memory_watchdog(self, now: float) -> None:
        if now - self._last_memory_watchdog < _MEMORY_WATCHDOG_INTERVAL:
            return
        self._last_memory_watchdog = now
        try:
            import psutil
        except ImportError:
            return
        import db
        actions = []
        # 1. Self-check
        try:
            own = psutil.Process(os.getpid())
            own_rss = own.memory_info().rss / (1024 * 1024)
            if own_rss > _SUP_SELF_RSS_WARN_MB:
                collected = gc.collect()
                log.warning(f"Supervisor self RSS: {own_rss:.0f} MB — gc collected {collected}")
                actions.append(f"sup_gc:{collected}")
            else:
                gc.collect(0)
        except Exception:
            pass
        # 2. Worker RSS
        for role, proc in list(self.pm.worker_procs.items()):
            if proc is None or proc.poll() is not None:
                continue
            try:
                p = psutil.Process(proc.pid)
                rss = p.memory_info().rss / (1024 * 1024)
                if rss > _WORKER_RSS_CRITICAL_MB:
                    log.warning(f"Worker '{role}' RSS {rss:.0f} MB > {_WORKER_RSS_CRITICAL_MB} MB — restarting")
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    self.pm.worker_procs[role] = None
                    actions.append(f"restart:{role}:{rss:.0f}MB")
                elif rss > _WORKER_RSS_WARN_MB:
                    log.info(f"Worker '{role}' RSS: {rss:.0f} MB (elevated)")
                    actions.append(f"warn:{role}:{rss:.0f}MB")
            except Exception:
                pass
        # 3. Dr. Ders cross-check
        try:
            hw_state = self.pm.read_hw_state()
            if hw_state:
                hw_rss = hw_state.get("memory", {}).get("hw_sup_rss_mb", 0)
                if hw_rss > _HW_SUP_RSS_CRITICAL_MB:
                    log.warning(f"Dr. Ders RSS {hw_rss:.0f} MB > {_HW_SUP_RSS_CRITICAL_MB} MB — flagging for restart")
                    actions.append(f"dr_ders_leak:{hw_rss:.0f}MB")
                    try:
                        db.post_note("sup", "supervisor", json.dumps({
                            "type": "memory_alert",
                            "title": f"Dr. Ders memory leak: {hw_rss:.0f} MB",
                            "content": "RSS exceeds threshold. Consider restarting Dr. Ders.",
                            "tags": ["memory", "dr_ders"],
                        }))
                    except Exception:
                        pass
        except Exception:
            pass
        if actions:
            log.info(f"Memory watchdog: {', '.join(actions)}")

    def _recover_stale_tasks(self, now: float) -> None:
        if now - self._last_stale_check < STALE_TASK_RECOVERY_INTERVAL:
            return
        self._last_stale_check = now
        import db
        recovered = db.recover_stale_tasks(STALE_TASK_TIMEOUT)
        for t in recovered:
            log.warning(f"Recovered stale task {t['id']} ({t['type']}) from {t['assigned_to']}")
            _json_log("WARNING", "stale_task_recovered", task_id=t["id"],
                      task_type=t["type"], agent=t["assigned_to"])
        if recovered:
            try:
                db.post_note("sup", "supervisor", json.dumps({
                    "type": "stale_recovery",
                    "title": f"Recovered {len(recovered)} stale tasks",
                    "tasks": [{"id": t["id"], "type": t["type"]} for t in recovered[:5]],
                    "tags": ["recovery"],
                }))
            except Exception as e:
                log.warning(f"[stale-recovery] failed to post recovery note: {e}")

    def _run_watchdog(self, now: float) -> None:
        if now - self._last_watchdog < WATCHDOG_INTERVAL:
            return
        self._last_watchdog = now
        try:
            from skills._watchdog import run_cycle, run_full_cycle
            if now - self._last_watchdog_full >= WATCHDOG_FULL_INTERVAL:
                self._last_watchdog_full = now
                alerts = run_full_cycle(log.info)
                try:
                    from integrity import verify_integrity, save_manifest
                    result = verify_integrity()
                    if result.get("status") == "tampered":
                        log.warning(f"INTEGRITY: {len(result.get('modified',[]))} modified, "
                                   f"{len(result.get('missing',[]))} missing files")
                        try:
                            from audit_log import log_event
                            log_event("integrity_alert", "supervisor",
                                     {"modified": result.get("modified", [])[:5],
                                      "missing": result.get("missing", [])[:5]},
                                     severity="warning")
                        except Exception:
                            pass
                    elif result.get("status") == "no_manifest":
                        save_manifest()
                        log.info("INTEGRITY: Initial manifest created")
                except ImportError:
                    pass
                except Exception as e:
                    log.debug(f"Integrity check error: {e}")
            else:
                alerts = run_cycle(log.info)
            for a in alerts:
                log.warning(f"Watchdog alert: {a['message']}")
        except Exception as e:
            log.warning(f"Watchdog error: {e}")

    def _cleanup_contexts(self, now: float) -> None:
        if now - self._last_context_cleanup < 1800:
            return
        self._last_context_cleanup = now
        try:
            from context_manager import clear_stale_contexts
            cleared = clear_stale_contexts()
            if cleared:
                log.info(f"Cleared {cleared} stale agent contexts")
        except Exception:
            pass

    def _check_feedback(self, now: float) -> None:
        if now - self._last_feedback_check < 600:
            return
        self._last_feedback_check = now
        try:
            from reinforcement import age_out_unreviewed
            aged = age_out_unreviewed()
            if aged:
                log.debug(f"Feedback: aged out {aged} unreviewed outputs")
        except Exception:
            pass
        try:
            from ml_router import retrain_if_stale
            retrain_result = retrain_if_stale()
            if retrain_result and not retrain_result.get("error"):
                log.info("ML router retrained: accuracy=%.3f, samples=%d",
                         retrain_result.get("accuracy", 0),
                         retrain_result.get("sample_count", 0))
        except Exception:
            pass

    def _cleanup_caches(self, now: float) -> None:
        if now - self._last_cache_cleanup < 300:
            return
        self._last_cache_cleanup = now
        try:
            from cache_manager import invalidate_stale
            stale = invalidate_stale()
            if stale:
                log.debug(f"Cache: invalidated {stale} stale caches")
        except Exception:
            pass

    def _cleanup_rag(self, now: float) -> None:
        if now - self._last_rag_cleanup < 1800:
            return
        self._last_rag_cleanup = now
        try:
            from rag import RAGIndex
            idx = RAGIndex()
            result = idx.cleanup_stale()
            removed = result.get("stale_removed", 0)
            if removed:
                log.info(f"RAG: cleaned {removed} stale index entries")
        except Exception:
            pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v -k health_monitor`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/health_monitor.py fleet/tests/test_supervisor_restructure.py
git commit -m "refactor: extract HealthMonitor from supervisor.py + self_healing.py + diagnostics.py"
```

---

## Task 4: Create `fleet/scheduler.py`

**Files:**
- Create: `fleet/scheduler.py`
- Modify: `fleet/tests/test_supervisor_restructure.py` (add scheduler tests)

Extracts dynamic scaling, auto-triggers, manual mode, event triggers, cost anomaly, capacity bonus, training detection, and config reload.

- [ ] **Step 1: Add scheduler tests**

Append to `fleet/tests/test_supervisor_restructure.py`:

```python
# ── Scheduler ───────────────────────────────────────────────────────

def test_scheduler_imports():
    """Scheduler class can be imported."""
    from scheduler import Scheduler
    assert Scheduler is not None


def test_scheduler_init():
    """Scheduler initializes with config and PM."""
    from process_manager import ProcessManager
    from scheduler import Scheduler
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    sched = Scheduler({"fleet": {"training_check_interval_secs": 30}, "scaling": {}}, pm)
    assert sched is not None


def test_scheduler_build_roles():
    """build_roles returns a list of role strings."""
    from process_manager import ProcessManager
    from scheduler import Scheduler
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    cfg = {"fleet": {"disabled_agents": [], "training_check_interval_secs": 30},
           "workers": {"coder_count": 2}, "scaling": {}}
    sched = Scheduler(cfg, pm)
    roles = sched.build_roles()
    assert isinstance(roles, list)
    assert "researcher" in roles
    assert "coder_1" in roles
    assert "coder_2" in roles


def test_scheduler_count_pending_tasks():
    """count_pending_tasks returns an integer >= 0."""
    from process_manager import ProcessManager
    from scheduler import Scheduler
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    sched = Scheduler({"fleet": {"training_check_interval_secs": 30}, "scaling": {}}, pm)
    result = sched.count_pending_tasks()
    assert isinstance(result, int)
    assert result >= 0


def test_scheduler_tick_no_crash():
    """Scheduler.tick() completes without error."""
    from process_manager import ProcessManager
    from scheduler import Scheduler
    pm = ProcessManager({"fleet": {"eco_mode": False, "disabled_agents": [],
                                    "training_check_interval_secs": 30,
                                    "ram_ceiling_pct": 95, "max_workers": 10},
                          "models": {}, "workers": {}, "scaling": {}})
    sched = Scheduler(pm.config, pm)
    sched.tick(0.0)  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_scheduler_imports -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create scheduler.py**

Extract from supervisor.py:
- `BASE_ROLES`, `CORE_AGENTS`, `SCALE_ORDER`, scaling constants (lines 58-70)
- `_build_roles()` (lines 135-147)
- `_count_pending_tasks()` (lines 165-177)
- `_pending_tasks_by_type()` (lines 189-203)
- `_skill_to_role()` (lines 206-211)
- `_load_affinity_map()` (lines 214-220)
- `_next_instance_name()` (lines 223-231)
- `_predict_queue_growth()` (lines 234-261)
- `_get_ram_usage_pct()` (lines 264-270)
- `_should_scale_up()` (lines 273-337)
- `_should_scale_down()` (lines 340-350)
- `_check_manual_mode_schedule()` (lines 83-132)
- `_capacity_state` class (lines 160-162)
- Main loop scaling block (lines 1178-1351)
- Auto-triggers (lines 1575-1655)
- Event triggers (lines 1708-1717)
- Config reload (lines 1719-1726)
- Cost anomaly (lines 1728-1763)
- Capacity bonus (lines 1783-1798)
- Training detection (lines 1412-1479)

```python
#!/usr/bin/env python3
"""Scheduler — dynamic scaling, periodic triggers, and task scheduling.

Extracted from supervisor.py during restructure. Decides what work to
do and when: agent scaling, auto-triggers, training detection.
"""

import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("supervisor")

FLEET_DIR = Path(__file__).parent

# ── Role and scaling constants ────────────────────────────────────────
BASE_ROLES = [
    "researcher", "coder", "archivist", "analyst", "sales", "onboarding",
    "implementation", "security", "planner", "legal", "account_manager",
    "ds_rag", "ds_fleet", "ds_research",
]
CORE_AGENTS = {"coder_1", "researcher", "planner", "archivist"}
SCALE_ORDER = ["coder_2", "coder_3", "analyst", "security", "coder"]
SCALE_UP_QUEUE_DEPTH = 2
SCALE_DOWN_IDLE_SECS = 300
MAX_DYNAMIC_PER_ROLE = 4

# ── Auto-triggered pipeline intervals ────────────────────────────────
RESEARCH_INTERVAL = 86400
EVOLUTION_INTERVAL = 604800
_SCHED_CHECK_INTERVAL = 60
SCALE_CHECK_INTERVAL = 30
MODEL_RECOMMEND_INTERVAL = 6 * 3600
CONFIG_RELOAD_INTERVAL = 300
FEEDBACK_CHECK_INTERVAL = 600
COST_ANOMALY_INTERVAL = 600


def _json_log(level, event, **kwargs):
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **kwargs}
    print(json.dumps(entry), flush=True)


class _CapacityState:
    """Tracks Claude capacity bonus window state."""
    active = False


class Scheduler:
    """Dynamic scaling, periodic triggers, and task scheduling."""

    def __init__(self, config: dict, pm):
        self.config = config
        self.pm = pm  # ProcessManager
        self._capacity_state = _CapacityState()
        # Interval trackers
        self._last_scale_check: float = 0
        self._last_research_trigger: float = 0
        self._last_evolution_trigger: float = 0
        self._last_results_mtime: float = 0
        self._last_model_recommend: float = 0
        self._last_sched_check: float = 0
        self._last_trigger_check: float = 0
        self._last_config_reload: float = 0
        self._last_cost_anomaly_check: float = 0
        self._last_capacity_check: float = 0
        self._last_training_check: float = 0

    def update_config(self, config: dict) -> None:
        self.config = config

    # ── Public interface ────────────────────────────────────────────

    def build_roles(self) -> list[str]:
        """Expand BASE_ROLES, replacing 'coder' with coder_1..coder_N and filtering disabled."""
        disabled = set(self.config.get("fleet", {}).get("disabled_agents", []))
        roles = []
        for r in BASE_ROLES:
            if r in disabled:
                continue
            if r == "coder":
                n = max(1, int(self.config.get("workers", {}).get("coder_count", 1)))
                roles.extend(f"coder_{i}" for i in range(1, n + 1))
            else:
                roles.append(r)
        return roles

    def count_pending_tasks(self) -> int:
        """Count pending tasks in the queue."""
        try:
            import sqlite3
            db_path = FLEET_DIR / "fleet.db"
            conn = sqlite3.connect(str(db_path), timeout=5)
            try:
                row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='PENDING'").fetchone()
                return row[0] if row else 0
            finally:
                conn.close()
        except Exception:
            return 0

    def get_core_agents(self) -> set[str]:
        return set(CORE_AGENTS)

    def get_disabled_agents(self) -> set[str]:
        return set(self.config.get("fleet", {}).get("disabled_agents", []))

    # ── tick ────────────────────────────────────────────────────────

    def tick(self, now: float) -> None:
        """Called every 5s from main loop."""
        try:
            self._check_scaling(now)
        except Exception:
            log.warning("Scaling check failed", exc_info=True)
        try:
            self._check_training(now)
        except Exception:
            log.warning("Training check failed", exc_info=True)
        try:
            self._check_auto_triggers(now)
        except Exception:
            log.warning("Auto-trigger check failed", exc_info=True)
        try:
            self._check_manual_mode(now)
        except Exception:
            log.warning("Manual mode check failed", exc_info=True)
        try:
            self._check_event_triggers(now)
        except Exception:
            log.warning("Event trigger check failed", exc_info=True)
        try:
            self._check_cost_anomaly(now)
        except Exception:
            log.warning("Cost anomaly check failed", exc_info=True)
        try:
            self._check_capacity_bonus(now)
        except Exception:
            log.warning("Capacity bonus check failed", exc_info=True)
        try:
            self._reload_config_if_stale(now)
        except Exception:
            log.warning("Config reload failed", exc_info=True)

    # ── Internal: Scaling ───────────────────────────────────────────

    def _pending_tasks_by_type(self) -> dict:
        try:
            import sqlite3
            db_path = FLEET_DIR / "fleet.db"
            conn = sqlite3.connect(str(db_path), timeout=5)
            try:
                rows = conn.execute(
                    "SELECT type, COUNT(*) as n FROM tasks WHERE status='PENDING' GROUP BY type"
                ).fetchall()
                return {r[0]: r[1] for r in rows}
            finally:
                conn.close()
        except Exception:
            return {}

    def _skill_to_role(self, skill: str, affinity_map: dict) -> str | None:
        for role, skills in affinity_map.items():
            if skill in skills:
                return role
        return None

    def _load_affinity_map(self) -> dict:
        try:
            from config import load_config
            return load_config().get("affinity", {})
        except Exception:
            return {}

    def _next_instance_name(self, base_role: str, running: set) -> str | None:
        for i in range(1, MAX_DYNAMIC_PER_ROLE + 1):
            name = f"{base_role}_{i}" if i > 1 or base_role == "coder" else base_role
            if base_role == "coder":
                name = f"coder_{i}"
            if name not in running:
                return name
        return None

    def _predict_queue_growth(self) -> int:
        try:
            import sqlite3
            db_path = FLEET_DIR / "fleet.db"
            conn = sqlite3.connect(str(db_path), timeout=5)
            try:
                recent = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE created_at >= datetime('now', '-5 minutes')"
                ).fetchone()[0]
                prior = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE created_at >= datetime('now', '-10 minutes') "
                    "AND created_at < datetime('now', '-5 minutes')"
                ).fetchone()[0]
            finally:
                conn.close()
            if recent > prior * 1.5 and recent > 3:
                return recent - prior
        except Exception:
            pass
        return 0

    def _get_ram_usage_pct(self) -> float:
        try:
            import psutil
            return psutil.virtual_memory().percent
        except Exception:
            return 0.0

    def _should_scale_up(self, pending: int, running: set) -> list:
        to_start = []
        if pending < SCALE_UP_QUEUE_DEPTH:
            return to_start
        ram_ceiling = self.config.get("fleet", {}).get("ram_ceiling_pct", 95)
        if ram_ceiling > 0:
            ram_pct = self._get_ram_usage_pct()
            if ram_pct >= ram_ceiling:
                log.info(f"Scale-up blocked: RAM {ram_pct:.1f}% >= ceiling {ram_ceiling}%")
                return to_start
        max_total = self.config.get("fleet", {}).get("max_workers", 16)
        if max_total <= 0:
            try:
                from system_info import get_worker_limits
                max_total = get_worker_limits()["max_workers"]
            except Exception:
                max_total = 16
        if len(running) >= max_total:
            return to_start
        by_type = self._pending_tasks_by_type()
        affinity = self._load_affinity_map()
        role_demand = {}
        for skill, count in by_type.items():
            role = self._skill_to_role(skill, affinity)
            if role:
                role_demand[role] = role_demand.get(role, 0) + count
        for role, demand in sorted(role_demand.items(), key=lambda x: -x[1]):
            if demand < 2:
                continue
            name = self._next_instance_name(role, running | set(to_start))
            if name and name not in running and len(to_start) + len(running) < max_total:
                to_start.append(name)
                log.info(f"Type-aware scale: {name} for {demand} pending {role} tasks")
        if not to_start and pending >= SCALE_UP_QUEUE_DEPTH:
            for agent in SCALE_ORDER:
                if agent not in running and len(to_start) + len(running) < max_total:
                    to_start.append(agent)
                    if pending // SCALE_UP_QUEUE_DEPTH <= len(to_start):
                        break
        return to_start

    def _should_scale_down(self, running: set) -> list:
        now = time.time()
        to_stop = []
        for name in running:
            if name in CORE_AGENTS:
                continue
            idle_since = self.pm.last_busy.get(name, now)
            if now - idle_since > SCALE_DOWN_IDLE_SECS:
                to_stop.append(name)
        return to_stop

    def _check_scaling(self, now: float) -> None:
        if now - self._last_scale_check < SCALE_CHECK_INTERVAL:
            return
        self._last_scale_check = now
        import db

        pending = self.count_pending_tasks()
        running = self.pm.get_running_workers()
        disabled = self.get_disabled_agents()

        # Update last-busy timestamps
        try:
            with db.get_conn() as conn:
                busy_agents = conn.execute(
                    "SELECT name FROM agents WHERE status='BUSY' "
                    "AND (julianday('now') - julianday(last_heartbeat)) * 86400 < 60"
                ).fetchall()
            for row in busy_agents:
                self.pm.last_busy[row["name"]] = now
        except Exception:
            pass

        # ML predictor or heuristic
        _ml_predictor_used = False
        try:
            scaling_cfg = self.config.get("scaling", {})
            if scaling_cfg.get("ml_predictor_enabled", True):
                from predictive_scaler import (
                    predict_optimal_agents as _ml_predict,
                    record_scaling_event as _record_scaling,
                    _get_task_rate,
                )
                _rate_5m = _get_task_rate(5)
                _rate_15m = _get_task_rate(15)
                _optimal = _ml_predict(pending, len(running), _rate_5m, _rate_15m)
                if _optimal > len(running):
                    pending += (_optimal - len(running)) * SCALE_UP_QUEUE_DEPTH
                    log.info(f"ML predictor: optimal={_optimal}, inflating pending to {pending}")
                _ml_predictor_used = True
        except Exception:
            pass

        if not _ml_predictor_used:
            predicted = self._predict_queue_growth()
            if predicted > 0:
                log.info(f"Predictive scaling: {predicted} additional tasks expected")
                pending += predicted

        # Build dynamic pool
        all_roles = self.build_roles()
        dynamic_pool = [r for r in all_roles if r not in CORE_AGENTS and r not in disabled]

        # Scale up
        to_start = self._should_scale_up(pending, running)
        to_start = [r for r in to_start if r not in disabled and r in dynamic_pool]
        for role in to_start:
            log.info(f"Scaling up: starting {role} ({pending} pending tasks)")
            _json_log("INFO", "scale_up", worker=role, pending=pending)
            self.pm.start_worker(role)
            self.pm.last_busy[role] = now

        # Scale down
        to_stop = self._should_scale_down(running)
        for role in to_stop:
            idle_secs = int(now - self.pm.last_busy.get(role, now))
            log.info(f"Scaling down: stopping {role} (idle {idle_secs // 60}m{idle_secs % 60}s)")
            _json_log("INFO", "scale_down", worker=role, idle_secs=idle_secs)
            self.pm.stop_worker(role)

        # Record ML training data
        try:
            if _ml_predictor_used:
                _action = "scale_up" if to_start else ("scale_down" if to_stop else "none")
                _target = len(running) + len(to_start) - len(to_stop)
                _record_scaling(
                    queue_depth=self.count_pending_tasks(),
                    agent_count=len(running),
                    task_rate_5m=_rate_5m,
                    task_rate_15m=_rate_15m,
                    action=_action,
                    target_agents=_target,
                )
        except Exception:
            pass

        # Federation overflow routing
        self._check_federation_overflow(pending, running)

    def _check_federation_overflow(self, pending: int, running: set) -> None:
        """Federation overflow routing when queue is deep."""
        import db
        federation_cfg = self.config.get("federation", {})
        if not federation_cfg.get("enabled") or pending <= 10:
            return

        overflow_threshold = federation_cfg.get("overflow_threshold", 0.85)
        max_capacity = self.config.get("fleet", {}).get("max_workers", 10) * 5
        if pending / max(max_capacity, 1) <= overflow_threshold:
            return

        # Auto-discovered + manual peers
        try:
            import discovery
            _all_peers = discovery.get_all_peers()
            fed_peers = [p["url"] for p in _all_peers if p.get("online", False)]
        except Exception:
            fed_peers = federation_cfg.get("peers", [])

        _hb_ssl = None
        try:
            from fleet_tls import is_tls_enabled, get_ssl_context
            if is_tls_enabled():
                _hb_ssl = get_ssl_context("client")
        except Exception:
            pass

        for peer_url in fed_peers:
            try:
                _of_kwargs = {"timeout": 3}
                if _hb_ssl:
                    _of_kwargs["context"] = _hb_ssl
                with urllib.request.urlopen(
                    f"{peer_url}/api/federation/peers", **_of_kwargs
                ) as r:
                    peer_data = json.loads(r.read())  # noqa: F841
                log.info(f"Federation overflow: {pending} pending, routing to {peer_url}")
                _json_log("INFO", "federation_overflow", pending=pending, peer=peer_url)
                break
            except Exception:
                pass

        # Cross-fleet task routing via federation_router
        try:
            from federation_router import should_route_remotely, find_best_peer, route_to_peer, record_local_route
            if federation_cfg.get("enabled") and pending > 0:
                _routed_count = 0
                if should_route_remotely("", priority=5):
                    try:
                        with db.get_conn() as _fc:
                            _pending_rows = _fc.execute(
                                "SELECT id, type, payload_json, priority FROM tasks "
                                "WHERE status='PENDING' ORDER BY priority DESC, id ASC LIMIT 5"
                            ).fetchall()
                        best_peer = find_best_peer("")
                        if best_peer:
                            for _pr in _pending_rows:
                                _task_priority = _pr["priority"] or 5
                                local_priority_min = int(federation_cfg.get("local_priority_min", 9))
                                if _task_priority >= local_priority_min:
                                    continue
                                task_dict = {
                                    "type": _pr["type"],
                                    "payload": json.loads(_pr["payload_json"] or "{}"),
                                    "priority": _task_priority,
                                }
                                result = route_to_peer(best_peer, task_dict)
                                if result.get("ok"):
                                    def _mark_forwarded(_tid=_pr["id"], _peer=best_peer["url"],
                                                        _remote_id=result.get("task_id")):
                                        with db.get_conn() as _mf:
                                            _mf.execute(
                                                "UPDATE tasks SET status='FORWARDED', "
                                                "result_json=? WHERE id=? AND status='PENDING'",
                                                (json.dumps({
                                                    "forwarded_to": _peer,
                                                    "remote_task_id": _remote_id,
                                                }), _tid))
                                    db._retry_write(_mark_forwarded)
                                    _routed_count += 1
                                    log.info(f"Federation: routed task {_pr['id']} ({_pr['type']}) to {best_peer['url']}")
                                    _json_log("INFO", "federation_route", task_id=_pr["id"],
                                              task_type=_pr["type"], peer=best_peer["url"])
                                else:
                                    log.debug(f"Federation: route failed for task {_pr['id']}: {result.get('error')}")
                                    break
                    except Exception as e:
                        log.debug(f"Federation routing error: {e}")
                if _routed_count == 0:
                    record_local_route()
        except ImportError:
            pass

    # ── Internal: Training ──────────────────────────────────────────

    def _check_training(self, now: float) -> None:
        training_interval = self.config.get("fleet", {}).get("training_check_interval_secs", 30)
        if now - self._last_training_check < training_interval:
            return
        self._last_training_check = now

        from marathon import is_training_running, _check_training_checkpoints, _evict_gpu_models, training_needs_eviction
        import db

        training_now, training_profile = is_training_running()
        if training_now and not self.pm.training_active:
            needs_eviction, reason = training_needs_eviction(self.config, training_profile)
            log.info(f"train.py detected (profile={training_profile or 'unknown'}) — {reason}")
            _json_log("INFO", "training_detected", profile=training_profile or "unknown", reason=reason)
            self.pm.training_active = True

            if needs_eviction:
                _evict_gpu_models(self.config)
                time.sleep(2)
                self.pm.stop_ollama()
                self.pm.start_ollama(gpu=False)
                self.pm.ollama_evicted_for_training = True
                mode_msg = "Ollama CPU-only"
            else:
                self.pm.ollama_evicted_for_training = False
                mode_msg = "Ollama stays on GPU (training fits in remaining VRAM)"

            try:
                db.post_note("sup", "supervisor", json.dumps({
                    "type": "training_state",
                    "title": f"Training started — {mode_msg}",
                    "tags": ["training"],
                }))
            except Exception as e:
                log.warning(f"[training] failed to post training-started note: {e}")
            try:
                checkpoint_info = _check_training_checkpoints()
                db.post_task("marathon_log", json.dumps({
                    "session_id": "autoresearch",
                    "goal": "ML training session",
                    "completed_steps": ["Training detected", mode_msg],
                    "next_step": "Monitor checkpoints",
                    "notes": f"Profile: {training_profile or 'unknown'}. Checkpoints: {checkpoint_info}" if checkpoint_info else f"Profile: {training_profile or 'unknown'}. No checkpoints yet",
                }), priority=2)
            except Exception as e:
                log.warning(f"[training] failed to post marathon_log (start): {e}")

        elif not training_now and self.pm.training_active:
            self.pm.training_active = False
            if self.pm.ollama_evicted_for_training:
                log.info("Training finished — restoring Ollama to GPU mode")
                self.pm.stop_ollama()
                self.pm.start_ollama(gpu=not self.config.get("fleet", {}).get("eco_mode", False))
                self.pm.ollama_evicted_for_training = False
            else:
                log.info("Training finished — Ollama was already on GPU, no restart needed")
            try:
                db.post_note("sup", "supervisor", json.dumps({
                    "type": "training_state",
                    "title": "Training finished — Ollama restored",
                    "tags": ["training"],
                }))
            except Exception as e:
                log.warning(f"[training] failed to post training-finished note: {e}")
            try:
                checkpoint_info = _check_training_checkpoints()
                db.post_task("marathon_log", json.dumps({
                    "session_id": "autoresearch",
                    "goal": "ML training session",
                    "completed_steps": ["Training completed", "Ollama restored to GPU",
                                       f"Final checkpoints: {checkpoint_info['count']}" if checkpoint_info else "No checkpoints"],
                    "next_step": "Evaluate training results",
                }), priority=2)
            except Exception as e:
                log.warning(f"[training] failed to post marathon_log (end): {e}")

    # ── Internal: Auto-triggers ─────────────────────────────────────

    def _check_auto_triggers(self, now: float) -> None:
        # Daily research
        if now - self._last_research_trigger > RESEARCH_INTERVAL:
            try:
                import sqlite3
                db_path = FLEET_DIR / "fleet.db"
                conn = sqlite3.connect(str(db_path), timeout=5)
                try:
                    pending_research = conn.execute(
                        "SELECT COUNT(*) FROM tasks WHERE type='research_loop' AND status='PENDING'"
                    ).fetchone()[0]
                    if pending_research == 0:
                        conn.execute(
                            "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                            "VALUES ('research_loop', 'PENDING', 3, ?, datetime('now'))",
                            (json.dumps({"action": "detect_gaps"}),))
                        conn.commit()
                        log.info("Auto-triggered daily research cycle")
                finally:
                    conn.close()
                self._last_research_trigger = now
            except Exception as e:
                log.debug(f"Research trigger failed: {e}")

        # Weekly evolution
        if now - self._last_evolution_trigger > EVOLUTION_INTERVAL:
            try:
                import sqlite3
                db_path = FLEET_DIR / "fleet.db"
                conn = sqlite3.connect(str(db_path), timeout=5)
                try:
                    pending_evo = conn.execute(
                        "SELECT COUNT(*) FROM tasks WHERE type='evolution_coordinator' AND status='PENDING'"
                    ).fetchone()[0]
                    if pending_evo == 0:
                        conn.execute(
                            "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                            "VALUES ('evolution_coordinator', 'PENDING', 2, ?, datetime('now'))",
                            (json.dumps({"action": "evolve"}),))
                        conn.commit()
                        log.info("Auto-triggered weekly evolution sweep")
                finally:
                    conn.close()
                self._last_evolution_trigger = now
            except Exception as e:
                log.debug(f"Evolution trigger failed: {e}")

        # ML bridge import (watch results.tsv)
        results_tsv = FLEET_DIR.parent / "autoresearch" / "results.tsv"
        if results_tsv.exists():
            mtime = results_tsv.stat().st_mtime
            if mtime > self._last_results_mtime and self._last_results_mtime > 0:
                try:
                    import sqlite3
                    db_path = FLEET_DIR / "fleet.db"
                    conn = sqlite3.connect(str(db_path), timeout=5)
                    try:
                        conn.execute(
                            "INSERT INTO tasks (type, status, priority, payload_json, created_at) "
                            "VALUES ('ml_bridge', 'PENDING', 4, ?, datetime('now'))",
                            (json.dumps({"action": "import_results"}),))
                        conn.commit()
                        log.info("Auto-triggered ml_bridge import (new results.tsv entries)")
                    finally:
                        conn.close()
                except Exception:
                    pass
            self._last_results_mtime = mtime

        # Model recommendation (every 6h)
        if now - self._last_model_recommend >= MODEL_RECOMMEND_INTERVAL:
            self._last_model_recommend = now
            try:
                import db
                db.post_task("model_recommend", json.dumps({"action": "analyze"}), priority=3)
                log.info("Dispatched model_recommend analysis task")
            except Exception as e:
                log.debug(f"Model recommend dispatch error: {e}")

    def _check_manual_mode(self, now: float) -> None:
        if now - self._last_sched_check < _SCHED_CHECK_INTERVAL:
            return
        self._last_sched_check = now
        try:
            sys.path.insert(0, str(FLEET_DIR))
            from manual_mode import ManualModeEngine
            from datetime import datetime, timezone, timedelta

            engine = ManualModeEngine()
            sched = engine.get_scheduler()
            if not sched.get("enabled"):
                return
            next_run = sched.get("next_run", "")
            if not next_run:
                return
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            if now_str >= next_run:
                queue = engine.get_queue()
                if not queue:
                    log.info("[SCHED] Manual Mode scheduler fired but queue is empty — skipping")
                else:
                    log.info("[SCHED] Manual Mode scheduler firing: %d items", len(queue))
                    try:
                        engine.run_queue(queue)
                        log.info("[SCHED] Manual Mode scheduled run complete")
                    except Exception as exc:
                        log.warning("[SCHED] Manual Mode scheduled run error: %s", exc)
                if sched.get("mode") == "recurring":
                    interval = int(sched.get("interval_days", 1))
                    new_next = (
                        datetime.now(timezone.utc) + timedelta(days=interval)
                    ).strftime("%Y-%m-%d %H:%M")
                    sched["next_run"] = new_next
                    engine.set_scheduler(sched)
                    log.info("[SCHED] Next Manual Mode run scheduled for %s", new_next)
                else:
                    sched["enabled"] = False
                    engine.set_scheduler(sched)
                    log.info("[SCHED] One-time Manual Mode run complete — scheduler disabled")
        except Exception as exc:
            log.debug("[SCHED] Manual Mode schedule check error: %s", exc)

    def _check_event_triggers(self, now: float) -> None:
        if now - self._last_trigger_check < 30:
            return
        self._last_trigger_check = now
        try:
            from event_triggers import check_all_triggers
            dispatched = check_all_triggers(self.config)
            if dispatched:
                log.info(f"Triggers: dispatched {dispatched} task(s)")
        except Exception:
            pass

    def _check_cost_anomaly(self, now: float) -> None:
        if now - self._last_cost_anomaly_check < COST_ANOMALY_INTERVAL:
            return
        self._last_cost_anomaly_check = now
        try:
            from cost_tracking import detect_cost_anomaly
            import db
            anomaly = detect_cost_anomaly()
            throttle_flag = FLEET_DIR / ".cost_anomaly_throttle"
            if anomaly:
                log.warning(f"Cost anomaly: ${anomaly['today_cost']} today vs "
                            f"${anomaly['avg_cost']} avg ({anomaly['multiplier']}x)")
                _json_log("WARNING", "cost_anomaly_throttle", **anomaly)
                throttle_flag.write_text(json.dumps({
                    "ts": time.time(),
                    "today_cost": anomaly["today_cost"],
                    "avg_cost": anomaly["avg_cost"],
                    "multiplier": anomaly["multiplier"],
                }), encoding="utf-8")
                try:
                    db.post_note("sup", "supervisor", json.dumps({
                        "type": "cost_anomaly",
                        "title": f"Cost anomaly: ${anomaly['today_cost']} today "
                                 f"({anomaly['multiplier']}x avg ${anomaly['avg_cost']})",
                        "tags": ["cost", "anomaly"],
                    }))
                except Exception:
                    pass
            else:
                if throttle_flag.exists():
                    throttle_flag.unlink(missing_ok=True)
                    log.info("Cost anomaly cleared — idle evolution resumed")
                    _json_log("INFO", "cost_anomaly_cleared")
        except Exception:
            pass

    def _check_capacity_bonus(self, now: float) -> None:
        if now - self._last_capacity_check < 300:
            return
        self._last_capacity_check = now
        try:
            from skills.claude_efficiency import is_in_bonus_window
            in_bonus = is_in_bonus_window(self.config)
            if in_bonus and not self._capacity_state.active:
                self._capacity_state.active = True
                log.info("Claude capacity bonus window active")
                _json_log("INFO", "capacity_bonus_start")
            elif not in_bonus and self._capacity_state.active:
                self._capacity_state.active = False
                log.info("Claude capacity bonus window ended")
                _json_log("INFO", "capacity_bonus_end")
        except Exception:
            pass

    def _reload_config_if_stale(self, now: float) -> None:
        if now - self._last_config_reload < CONFIG_RELOAD_INTERVAL:
            return
        self._last_config_reload = now
        try:
            from config import reload_config
            reload_config()
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v -k scheduler`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/scheduler.py fleet/tests/test_supervisor_restructure.py
git commit -m "refactor: extract Scheduler from supervisor.py"
```

---

## Task 5: Create `fleet/federation_manager.py`

**Files:**
- Create: `fleet/federation_manager.py`
- Modify: `fleet/tests/test_supervisor_restructure.py` (add federation tests)

- [ ] **Step 1: Add federation tests**

Append to `fleet/tests/test_supervisor_restructure.py`:

```python
# ── FederationManager ───────────────────────────────────────────────

def test_federation_manager_imports():
    """FederationManager class can be imported."""
    from federation_manager import FederationManager
    assert FederationManager is not None


def test_federation_manager_init():
    """FederationManager initializes with config and PM."""
    from process_manager import ProcessManager
    from federation_manager import FederationManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    fm = FederationManager({"federation": {"enabled": False}}, pm)
    assert fm is not None


def test_federation_manager_tick_disabled():
    """tick() completes without error when federation is disabled."""
    from process_manager import ProcessManager
    from federation_manager import FederationManager
    pm = ProcessManager({"fleet": {}, "models": {}, "workers": {}})
    fm = FederationManager({"federation": {"enabled": False}}, pm)
    fm.tick(0.0)  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_federation_manager_imports -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create federation_manager.py**

```python
#!/usr/bin/env python3
"""Federation manager — cross-fleet peer communication.

Extracted from supervisor.py during restructure. Handles heartbeat
broadcast, rejoin announcement, mesh discovery, and mTLS setup.
"""

import json
import logging
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("supervisor")

FLEET_DIR = Path(__file__).parent


class FederationManager:
    """Cross-fleet peer communication."""

    def __init__(self, config: dict, pm):
        self.config = config
        self.pm = pm  # ProcessManager
        self._last_heartbeat: float = 0

    def update_config(self, config: dict) -> None:
        self.config = config

    def tick(self, now: float) -> None:
        """Broadcast status to peers (every 60s)."""
        try:
            self._broadcast_heartbeat(now)
        except Exception:
            log.warning("Federation heartbeat failed", exc_info=True)

    def announce_rejoin(self, roles: list) -> None:
        """Announce rejoin to peers on startup (crash recovery)."""
        from config import is_offline
        federation_cfg = self.config.get("federation", {})
        if not federation_cfg.get("enabled") or is_offline(self.config):
            return

        device_name = self.config.get("naming", {}).get("device_name", "unknown")
        peers = federation_cfg.get("peers", [])

        ssl_ctx = self._get_ssl_context()
        for peer_url in peers:
            try:
                rejoin_data = json.dumps({
                    "fleet_id": device_name,
                    "agents": len(roles),
                    "pending": self._count_pending(),
                    "event": "rejoin",
                    "timestamp": time.time(),
                }).encode()
                req = urllib.request.Request(
                    f"{peer_url}/api/federation/heartbeat",
                    data=rejoin_data, method="POST",
                    headers={"Content-Type": "application/json"})
                if ssl_ctx:
                    urllib.request.urlopen(req, timeout=5, context=ssl_ctx)
                else:
                    urllib.request.urlopen(req, timeout=5)
                log.info(f"Federation: rejoined peer {peer_url}")
            except Exception:
                log.debug(f"Federation: peer {peer_url} unreachable (will retry in heartbeat loop)")

    def start_discovery(self) -> None:
        """Start mesh auto-discovery (UDP broadcast + mDNS)."""
        federation_cfg = self.config.get("federation", {})
        if not federation_cfg.get("discovery_enabled", True):
            return
        try:
            import discovery
            dashboard_port = self.config.get("dashboard", {}).get("port", 5555)
            discovery.start_discovery(port=dashboard_port)
            log.info("Federation: mesh auto-discovery started")
        except Exception:
            log.warning("Federation: auto-discovery failed to start", exc_info=True)

    def setup_tls(self) -> None:
        """Deferred mTLS auto-setup."""
        try:
            from fleet_tls import auto_setup as _tls_auto_setup
            _tls_auto_setup()
        except Exception:
            pass

    # ── Internal ────────────────────────────────────────────────────

    def _get_ssl_context(self):
        try:
            from fleet_tls import is_tls_enabled, get_ssl_context
            if is_tls_enabled():
                return get_ssl_context("client")
        except Exception:
            pass
        return None

    def _count_pending(self) -> int:
        try:
            import sqlite3
            db_path = FLEET_DIR / "fleet.db"
            conn = sqlite3.connect(str(db_path), timeout=5)
            try:
                row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='PENDING'").fetchone()
                return row[0] if row else 0
            finally:
                conn.close()
        except Exception:
            return 0

    def _broadcast_heartbeat(self, now: float) -> None:
        if now - self._last_heartbeat < 60:
            return
        self._last_heartbeat = now

        federation_cfg = self.config.get("federation", {})
        if not federation_cfg.get("enabled"):
            return

        # GPU capacity info
        gpu_count = 0
        total_vram = 0.0
        try:
            from hw_supervisor import detect_gpu_config
            gpu_info = detect_gpu_config()
            gpu_count = gpu_info.get("gpu_count", 0)
            total_vram = gpu_info.get("total_vram_gb", 0.0)
        except Exception:
            pass

        # Peer list: auto-discovered + manual
        try:
            import discovery
            all_peers = discovery.get_all_peers()
            peer_urls = [p["url"] for p in all_peers]
        except Exception:
            peer_urls = federation_cfg.get("peers", [])

        ssl_ctx = self._get_ssl_context()
        for peer_url in peer_urls:
            try:
                status = {
                    "fleet_id": self.config.get("naming", {}).get("device_name", ""),
                    "agents": len(self.pm.get_running_workers()),
                    "pending": self._count_pending(),
                    "gpu_count": gpu_count,
                    "total_vram_gb": total_vram,
                    "timestamp": time.time(),
                }
                body = json.dumps(status).encode()
                req = urllib.request.Request(
                    f"{peer_url}/api/federation/heartbeat",
                    data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                if ssl_ctx:
                    urllib.request.urlopen(req, timeout=3, context=ssl_ctx)
                else:
                    urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v -k federation_manager`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/federation_manager.py fleet/tests/test_supervisor_restructure.py
git commit -m "refactor: extract FederationManager from supervisor.py"
```

---

## Task 6: Create `fleet/boot_sequence.py`

**Files:**
- Create: `fleet/boot_sequence.py`
- Modify: `fleet/tests/test_supervisor_restructure.py` (add boot tests)

- [ ] **Step 1: Add boot sequence tests**

Append to `fleet/tests/test_supervisor_restructure.py`:

```python
# ── BootSequence ────────────────────────────────────────────────────

def test_boot_sequence_imports():
    """boot function can be imported."""
    from boot_sequence import boot
    assert callable(boot)


def test_boot_load_secrets():
    """_load_secrets function can be imported and called safely."""
    from boot_sequence import _load_secrets
    _load_secrets()  # should not raise even if ~/.secrets doesn't exist
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_boot_sequence_imports -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create boot_sequence.py**

```python
#!/usr/bin/env python3
"""Boot sequence — ordered startup for the supervisor.

Extracted from supervisor.py main() pre-loop setup. Runs once at
supervisor launch, returns initialized module instances for the main loop.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("supervisor")

FLEET_DIR = Path(__file__).parent


def _load_secrets():
    """Source ~/.secrets into env so workers inherit API keys."""
    secrets = Path.home() / ".secrets"
    if not secrets.exists():
        return
    for line in secrets.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def _register_views():
    """Register supervisor data source for Hybrid ViewPort."""
    try:
        import view_registry
        view_registry.register_source(
            name="supervisor",
            category="fleet",
            node_types=["supervisor", "agent", "worker"],
            edge_types=["manages", "dispatches", "heartbeat"],
            data_endpoint="/api/fleet/graph",
            icon="cpu",
            layout_hint="radial",
            metrics=["uptime_s", "worker_count", "task_queue_depth"],
        )
    except Exception as e:
        log.debug(f"ViewPort registration failed: {e}")


def boot(config: dict = None):
    """Execute the full supervisor boot sequence.

    Order:
    1. PID acquire (exit if duplicate)
    2. Log rotation
    3. DB init + register supervisor agent
    4. DAG queue start
    5. Load config + secrets (skip in air-gap)
    6. Start Ollama (adopt or launch)
    7. Resolve best model + export override
    8. Initial keepalive ping
    9. Start dashboard (background thread)
    10. Start Dr. Ders
    11. Start core workers (no stagger)
    12. Start Discord (if online)
    13. Deferred federation (background thread)
    14. Start backup manager
    15. Register ViewPort data sources
    16. Write STATUS.md

    Returns (pm, scheduler, health_monitor, federation_manager, config, roles)
    or None if boot fails (duplicate supervisor).
    """
    import db
    from config import load_config, is_offline, is_air_gap
    from process_manager import ProcessManager, _json_log
    from scheduler import Scheduler, CORE_AGENTS
    from health_monitor import HealthMonitor
    from federation_manager import FederationManager

    # Ensure directories
    (FLEET_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (FLEET_DIR / "knowledge" / "summaries").mkdir(parents=True, exist_ok=True)
    (FLEET_DIR / "knowledge" / "reports").mkdir(parents=True, exist_ok=True)

    # 1. Log rotation
    try:
        from log_manager import rotate_logs
        rotation = rotate_logs()
        if rotation.get("files_archived"):
            print(f"[SUPERVISOR] Logs rotated: {rotation['files_archived']} files archived to sessions/{rotation['session_id']}")
    except Exception as e:
        print(f"[SUPERVISOR] Log rotation skipped: {e}")

    # 2. PID acquire
    try:
        from pid_manager import acquire_pid, release_pid
        if not acquire_pid("supervisor"):
            log.warning("Another supervisor is already running — exiting")
            return None
        import atexit
        atexit.register(lambda: release_pid("supervisor"))
    except Exception as e:
        log.warning("PID manager unavailable: %s", e)

    # 3. DB init
    db.init_db()
    db.register_agent("supervisor", "supervisor", os.getpid())

    # 4. DAG queue
    try:
        from dag_queue import start as start_dag_queue
        start_dag_queue()
    except ImportError as e:
        log.debug(f"[main] DAG queue not available (optional): {e}")

    # 5. Config + secrets
    if config is None:
        config = load_config()

    air_gap = is_air_gap(config)
    offline = is_offline(config)

    if not air_gap:
        _load_secrets()
    else:
        log.info("AIR-GAP mode — secrets loading disabled")

    if air_gap:
        log.info("AIR-GAP mode enabled — dashboard, Discord, OpenClaw disabled")
    elif offline:
        log.info("OFFLINE mode enabled — Discord, OpenClaw disabled")

    # Create module instances
    pm = ProcessManager(config)
    sched = Scheduler(config, pm)
    hm = HealthMonitor(config, pm)
    fm = FederationManager(config, pm)

    # Build roles
    all_roles = sched.build_roles()
    disabled = sched.get_disabled_agents()
    core_roles = [r for r in all_roles if r in CORE_AGENTS and r not in disabled]
    dynamic_pool = [r for r in all_roles if r not in CORE_AGENTS and r not in disabled]
    log.info(f"Dynamic scaling: booting {len(core_roles)} core agents, {len(dynamic_pool)} on-demand")
    log.info(f"Core: {', '.join(core_roles)} | Pool: {', '.join(dynamic_pool)}")

    # 6. Start Ollama
    pm.start_ollama(gpu=not config.get("fleet", {}).get("eco_mode", False))

    # 7. Resolve model
    resolved_model = pm.get_best_available_model()
    configured_model = config.get("models", {}).get("local", "qwen3:8b")
    if resolved_model != configured_model:
        log.info(f"Model fallback: '{configured_model}' -> '{resolved_model}'")
        _json_log("INFO", "model_fallback", configured=configured_model, resolved=resolved_model)
    else:
        log.info(f"Using configured model: {resolved_model}")
    os.environ["FLEET_MODEL_OVERRIDE"] = resolved_model

    # 8. Initial keepalive
    if not air_gap:
        pm.ping_ollama_keepalive(model=resolved_model)

    # 9. Dashboard (background thread)
    if not air_gap:
        threading.Thread(target=pm.start_dashboard, daemon=True).start()

    # 10. Dr. Ders
    pm.start_hw_supervisor()

    # 11. Core workers
    for role in core_roles:
        pm.start_worker(role)
        pm.last_busy[role] = time.time()

    # 12. Discord (if online)
    if not offline:
        pm.start_discord_bot()
        # OpenClaw not in boot per spec, but was in original — keep for compat
        # pm.start_openclaw() is NOT called during boot (disabled in fleet.toml)

    # 13. Federation (deferred background thread)
    def _deferred_federation():
        fm.setup_tls()

    federation_cfg = config.get("federation", {})
    if federation_cfg.get("enabled") and not offline:
        fm.start_discovery()
        fm.announce_rejoin(core_roles)
    threading.Thread(target=_deferred_federation, daemon=True).start()

    # 14. Backup manager
    try:
        from backup_manager import BackupManager
        _backup = BackupManager(config)
        _backup.perform_backup(trigger="fleet_startup")
        _backup.start_auto_save()
        log.info(f"Auto-save enabled: every {_backup.interval}s to {_backup.location}")
    except Exception as e:
        log.warning(f"Backup manager failed to start: {e}")

    # 15. ViewPort
    _register_views()

    mode_label = " [AIR-GAP]" if air_gap else " [OFFLINE]" if offline else ""
    log.info(f"Fleet up — {len(core_roles)} core workers (dynamic scaling enabled), "
             f"eco={config.get('fleet', {}).get('eco_mode', False)}{mode_label}")
    _json_log("INFO", "supervisor_startup", workers=len(core_roles),
              eco=config.get("fleet", {}).get("eco_mode", False),
              mode=mode_label.strip() or "normal",
              scaling="dynamic", core=len(core_roles), pool=len(dynamic_pool))

    return pm, sched, hm, fm, config, core_roles
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v -k boot_sequence`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/boot_sequence.py fleet/tests/test_supervisor_restructure.py
git commit -m "refactor: extract boot_sequence from supervisor.py main()"
```

---

## Task 7: Rewrite `fleet/supervisor.py` as thin orchestrator

**Files:**
- Rewrite: `fleet/supervisor.py`
- Modify: `fleet/tests/test_supervisor_restructure.py` (add orchestrator test)

- [ ] **Step 1: Add orchestrator test**

Append to `fleet/tests/test_supervisor_restructure.py`:

```python
# ── Supervisor Orchestrator ─────────────────────────────────────────

def test_supervisor_imports():
    """supervisor.py can be imported without side effects."""
    # Just verify the module-level imports don't crash
    import importlib
    mod = importlib.import_module("supervisor")
    assert hasattr(mod, "main")
    assert hasattr(mod, "write_status_md")
```

- [ ] **Step 2: Run test to verify it currently passes (existing supervisor.py)**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py::test_supervisor_imports -v`
Expected: PASS (existing supervisor.py has main)

- [ ] **Step 3: Rewrite supervisor.py as thin orchestrator (~150 lines)**

Replace the entire file with a thin orchestrator that:
- Keeps: `main()`, `write_status_md()`, `_json_log()`, signal handlers, logging setup
- Keeps: sup-channel inbox reading, heartbeat file write, write_status_md call (30s interval)
- Delegates: everything else to the 5 modules

```python
#!/usr/bin/env python3
"""Fleet supervisor — thin orchestrator over 5 focused modules.

Modules:
  process_manager.py  — subprocess lifecycle (Ollama, workers, dashboard, etc.)
  scheduler.py        — dynamic scaling, auto-triggers, training detection
  health_monitor.py   — health sweeps, memory watchdog, circuit breakers
  federation_manager.py — peer heartbeat, discovery, mTLS
  boot_sequence.py    — ordered startup sequence
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

import db
from config import load_config
from marathon import _check_training_checkpoints

# ── Logging ─────────────────────────────────────────────────────────
(FLEET_DIR / "logs").mkdir(parents=True, exist_ok=True)
from logging.handlers import RotatingFileHandler
_sup_handler = RotatingFileHandler(
    FLEET_DIR / "logs" / "supervisor.log",
    maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
)
_sup_handler.setFormatter(logging.Formatter("%(asctime)s [SUPERVISOR] %(message)s"))
logging.basicConfig(
    level=logging.INFO,
    handlers=[_sup_handler, logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("supervisor")


def _json_log(level, event, **kwargs):
    """Structured JSON log line for fleet processes."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **kwargs}
    print(json.dumps(entry), flush=True)


# ── Module-level state (set during boot) ────────────────────────────
_pm = None     # ProcessManager
_sched = None  # Scheduler
_hm = None     # HealthMonitor
_fm = None     # FederationManager
_config = None


def write_status_md():
    """Write fleet status snapshot to STATUS.md."""
    try:
        status = db.get_fleet_status()
        task_lookup = {}
        try:
            with db.get_conn() as conn:
                for a in status["agents"]:
                    tid = a.get("current_task_id")
                    if tid:
                        row = conn.execute("SELECT type FROM tasks WHERE id=?", (tid,)).fetchone()
                        if row:
                            task_lookup[a["name"]] = row["type"]
        except Exception as e:
            log.debug(f"[write_status_md] task type lookup failed: {e}")

        training_active = _pm.training_active if _pm else False
        ollama_evicted = _pm.ollama_evicted_for_training if _pm else False
        eco_mode = _config.get("fleet", {}).get("eco_mode", False) if _config else False

        lines = [
            f"# Fleet Status — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Agents",
            "| Name | Role | Status | Task | Last Heartbeat |",
            "|------|------|--------|------|----------------|",
        ]
        for a in status["agents"]:
            hb = db.utc_to_local(a.get("last_heartbeat"))
            task_type = task_lookup.get(a["name"], "—")
            lines.append(f"| {a['name']} | {a['role']} | {a['status']} | {task_type} | {hb} |")
        t = status["tasks"]
        lines += [
            "",
            "## Tasks",
            f"- Pending: {t['PENDING']}  Running: {t['RUNNING']}  Done: {t['DONE']}  Failed: {t['FAILED']}",
            "",
            "## GPU",
            f"- Training detected: {training_active}",
            f"- Ollama mode: {'CPU-only (training evicted models)' if ollama_evicted else 'GPU + training coexist' if training_active else 'eco CPU' if eco_mode else 'GPU'}",
        ]
        checkpoint_info = _check_training_checkpoints()
        if checkpoint_info:
            lines += [
                "",
                "## Marathon",
                f"- Latest checkpoint: {checkpoint_info['latest']} ({checkpoint_info['size_mb']} MB)",
                f"- Total checkpoints: {checkpoint_info['count']}",
            ]
        (FLEET_DIR / "STATUS.md").write_text("\n".join(lines))
    except Exception as e:
        log.warning(f"STATUS.md write failed: {e}")


def shutdown(sig, frame):
    """Signal handler — clean fleet shutdown."""
    log.info("Shutting down fleet...")
    if _pm:
        _pm.shutdown_all()
    sys.exit(0)


def main():
    global _pm, _sched, _hm, _fm, _config

    from boot_sequence import boot

    result = boot()
    if result is None:
        return  # Duplicate supervisor

    _pm, _sched, _hm, _fm, _config, roles = result

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Write STATUS.md immediately so boot.py doesn't wait
    write_status_md()

    last_status = time.time()
    last_sup_notes_ts = None

    while True:
      try:
        now = time.time()

        # Module ticks (each handles its own intervals internally)
        _sched.tick(now)
        _hm.tick(now)
        _fm.tick(now)
        _pm.check_alive()

        # Log Dr. Ders transitions
        hw_state = _pm.read_hw_state()
        if hw_state and hw_state.get("status") == "transitioning":
            log.info(f"Dr. Ders transitioning to {hw_state.get('model')} — workers pausing claims")

        # 30s status interval: sup-channel inbox, heartbeat, STATUS.md
        if now - last_status >= 30:
            last_status = now

            # Sup-channel inbox
            try:
                sup_msgs = db.get_messages("supervisor", unread_only=True,
                                           limit=5, channels=["sup"])
                for m in sup_msgs:
                    try:
                        body = json.loads(m["body_json"])
                        log.info(f"Sup msg from {m['from_agent']}: {body.get('type', '?')}")
                    except Exception as e:
                        log.debug(f"[sup-channel] failed to parse sup message: {e}")
                sup_notes = db.get_notes("sup", since=last_sup_notes_ts, limit=10)
                for n in sup_notes:
                    try:
                        body = json.loads(n["body_json"])
                        log.info(f"Sup note [{n['from_agent']}]: {body.get('title', '?')}")
                    except Exception as e:
                        log.debug(f"[sup-channel] failed to parse sup note: {e}")
                    last_sup_notes_ts = n.get("created_at", last_sup_notes_ts)
            except Exception as e:
                log.debug(f"Sup channel read error: {e}")

            write_status_md()

            # Heartbeat file for Dr. Ders
            try:
                hb_file = FLEET_DIR / ".supervisor_heartbeat"
                hb_file.write_text(json.dumps({
                    "pid": os.getpid(), "ts": time.time(),
                    "workers": len(_pm.get_running_workers()),
                    "model": _config.get("models", {}).get("local", ""),
                }), encoding="utf-8")
            except Exception:
                pass

        time.sleep(5)
      except Exception:
        log.warning("Main loop iteration failed", exc_info=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add fleet/supervisor.py fleet/tests/test_supervisor_restructure.py
git commit -m "refactor: rewrite supervisor.py as thin orchestrator over 5 modules"
```

---

## Task 8: Replace `self_healing.py` and `diagnostics.py` with shims

**Files:**
- Rewrite: `fleet/self_healing.py`
- Rewrite: `fleet/diagnostics.py`
- Modify: `fleet/tests/test_supervisor_restructure.py` (add shim tests)

- [ ] **Step 1: Add shim backward-compatibility tests**

Append to `fleet/tests/test_supervisor_restructure.py`:

```python
# ── Backward Compatibility Shims ────────────────────────────────────

def test_self_healing_shim_imports():
    """self_healing.py re-exports all functions from health_monitor."""
    from self_healing import (
        check_agent_health,
        recover_agent,
        retry_failed_task,
        circuit_breaker_record_failure,
        circuit_breaker_is_open,
        get_circuit_breaker_status,
        run_health_sweep,
        detect_skill_regression,
        get_rollback_candidates,
        rollback_skill,
        get_agent_health_summary,
        get_skill_health_summary,
        get_recovery_log,
    )
    assert callable(check_agent_health)
    assert callable(run_health_sweep)


def test_diagnostics_shim_imports():
    """diagnostics.py re-exports all functions from health_monitor."""
    from diagnostics import (
        quarantine_agent,
        clear_quarantine,
        get_failure_streaks,
        get_stuck_reviews,
    )
    assert callable(quarantine_agent)
    assert callable(get_stuck_reviews)


def test_self_healing_shim_same_objects():
    """self_healing and health_monitor export the same function objects."""
    from self_healing import check_agent_health as sh_fn
    from health_monitor import check_agent_health as hm_fn
    assert sh_fn is hm_fn


def test_diagnostics_shim_same_objects():
    """diagnostics and health_monitor export the same function objects."""
    from diagnostics import quarantine_agent as diag_fn
    from health_monitor import quarantine_agent as hm_fn
    assert diag_fn is hm_fn
```

- [ ] **Step 2: Replace self_healing.py with shim**

```python
"""Self-healing compatibility shim — imports from health_monitor.py.

All functionality has moved to health_monitor.py. This file exists
only for backward compatibility with existing imports.
"""
from health_monitor import (
    check_agent_health,
    recover_agent,
    retry_failed_task,
    circuit_breaker_record_failure,
    circuit_breaker_is_open,
    get_circuit_breaker_status,
    run_health_sweep,
    detect_skill_regression,
    get_rollback_candidates,
    rollback_skill,
    get_agent_health_summary,
    get_skill_health_summary,
    get_recovery_log,
)

__all__ = [
    "check_agent_health",
    "recover_agent",
    "retry_failed_task",
    "circuit_breaker_record_failure",
    "circuit_breaker_is_open",
    "get_circuit_breaker_status",
    "run_health_sweep",
    "detect_skill_regression",
    "get_rollback_candidates",
    "rollback_skill",
    "get_agent_health_summary",
    "get_skill_health_summary",
    "get_recovery_log",
]
```

- [ ] **Step 3: Replace diagnostics.py with shim**

```python
"""Diagnostics compatibility shim — imports from health_monitor.py.

All functionality has moved to health_monitor.py. This file exists
only for backward compatibility with existing imports.
"""
from health_monitor import (
    quarantine_agent,
    clear_quarantine,
    get_failure_streaks,
    get_stuck_reviews,
)

__all__ = [
    "quarantine_agent",
    "clear_quarantine",
    "get_failure_streaks",
    "get_stuck_reviews",
]
```

- [ ] **Step 4: Run all tests**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_supervisor_restructure.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run existing integration tests to verify no regressions**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/test_integration.py -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add fleet/self_healing.py fleet/diagnostics.py fleet/tests/test_supervisor_restructure.py
git commit -m "refactor: replace self_healing.py and diagnostics.py with shims to health_monitor"
```

---

## Task 9: Integration test — full smoke test

**Files:**
- No new files — run existing smoke_test.py and verify fleet boots

- [ ] **Step 1: Run the smoke test suite**

Run: `cd /c/Users/max/Projects/Education && python fleet/smoke_test.py --fast`
Expected: 33/33 PASS (no regressions from restructure)

- [ ] **Step 2: Run the full test suite**

Run: `cd /c/Users/max/Projects/Education && python -m pytest fleet/tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Verify supervisor boots and fleet starts**

Run: `cd /c/Users/max/Projects/Education && timeout 15 python fleet/supervisor.py 2>&1 | head -30`
Expected: Output shows boot sequence completing — "Fleet up" message with core workers starting. The supervisor should log its normal startup sequence through all 16 boot steps.

- [ ] **Step 4: Verify file sizes match spec targets**

Run the following to count lines in each new module:
```bash
wc -l fleet/process_manager.py fleet/health_monitor.py fleet/scheduler.py fleet/federation_manager.py fleet/boot_sequence.py fleet/supervisor.py fleet/self_healing.py fleet/diagnostics.py
```
Expected targets (approximate):
- process_manager.py: ~400 lines
- health_monitor.py: ~700 lines
- scheduler.py: ~450 lines
- federation_manager.py: ~200 lines
- boot_sequence.py: ~200 lines
- supervisor.py: ~150 lines
- self_healing.py: ~20 lines (shim)
- diagnostics.py: ~10 lines (shim)

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "test: verify supervisor restructure — smoke tests + integration passing"
```
