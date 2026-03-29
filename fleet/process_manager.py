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

    # ── Ollama optimization ─────────────────────────────────────────

    def _detect_vram_gb(self) -> float:
        """Detect total GPU VRAM in GB using gpu.py backend."""
        try:
            from gpu import detect_gpu, read_telemetry
            backend, has_gpu = detect_gpu()
            if not has_gpu:
                return 0.0
            telem = read_telemetry(backend)
            if telem and telem.get("vram_total_bytes"):
                return telem["vram_total_bytes"] / (1024 ** 3)
        except Exception as e:
            log.debug(f"VRAM detection failed: {e}")
        return 0.0

    def _resolve_ollama_env(self) -> dict[str, str]:
        """Resolve Ollama optimization env vars from fleet.toml + GPU detection.

        Returns dict of env var name → value to inject into Ollama's process env.
        Explicit (non-'auto') values in fleet.toml always override auto-detection.
        """
        opt = self.config.get("ollama", {}).get("optimization", {})
        gpu_mode = self.config.get("gpu", {}).get("mode", "eco")
        vram = self._detect_vram_gb() if gpu_mode == "full" else 0.0

        # VRAM-tier defaults
        if vram <= 0:
            defaults = {"flash": "0", "kv": "f16", "parallel": "2", "models": "1"}
        elif vram < 6:
            defaults = {"flash": "1", "kv": "q4_0", "parallel": "2", "models": "1"}
        elif vram < 8:
            defaults = {"flash": "1", "kv": "q8_0", "parallel": "2", "models": "2"}
        elif vram < 12:
            defaults = {"flash": "1", "kv": "q8_0", "parallel": "4", "models": "2"}
        elif vram < 16:
            defaults = {"flash": "1", "kv": "q8_0", "parallel": "4", "models": "3"}
        else:
            defaults = {"flash": "1", "kv": "f16", "parallel": "6", "models": "4"}

        # Resolve each setting: explicit override or auto-detected default
        flash = opt.get("flash_attention", "auto")
        if flash == "auto":
            flash_val = defaults["flash"]
        elif flash == "on":
            flash_val = "1"
        elif flash == "off":
            flash_val = "0"
        else:
            flash_val = defaults["flash"]

        kv = opt.get("kv_cache_type", "auto")
        kv_val = defaults["kv"] if kv == "auto" else kv

        parallel = opt.get("num_parallel", "auto")
        parallel_val = defaults["parallel"] if parallel == "auto" else str(parallel)

        models = opt.get("max_loaded_models", "auto")
        models_val = defaults["models"] if models == "auto" else str(models)

        env_vars = {
            "OLLAMA_NUM_PARALLEL": parallel_val,
            "OLLAMA_MAX_LOADED_MODELS": models_val,
        }
        # Only set flash/kv if enabled (avoid overriding user's shell env with "0")
        if flash_val == "1":
            env_vars["OLLAMA_FLASH_ATTENTION"] = "1"
        if kv_val != "f16":
            env_vars["OLLAMA_KV_CACHE_TYPE"] = kv_val

        log.info(
            f"Ollama optimization: flash={flash_val}, kv_cache={kv_val}, "
            f"parallel={parallel_val}, max_models={models_val} "
            f"(VRAM={vram:.1f}GB, gpu_mode={gpu_mode})"
        )
        return env_vars

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
        opt_env = self._resolve_ollama_env()
        if self.find_running_ollama():
            loaded = self.discover_loaded_models()
            log.info(f"Ollama already running — adopting ({len(loaded)} models loaded)")
            log.warning(
                "Adopted Ollama was started externally — optimization env vars "
                "(flash_attention, kv_cache_type) only apply to fleet-started Ollama. "
                "Restart Ollama via fleet for optimized settings."
            )
            _json_log("INFO", "ollama_adopt", models_loaded=len(loaded))
            return
        ollama_exe = self._find_ollama()
        env = os.environ.copy()
        if not gpu:
            env["CUDA_VISIBLE_DEVICES"] = "-1"
        elif "CUDA_VISIBLE_DEVICES" in env:
            del env["CUDA_VISIBLE_DEVICES"]
        # Apply resolved optimization settings
        env.update(opt_env)
        mode = "GPU" if gpu else "CPU"
        log.info(f"Starting Ollama ({mode} mode) — {ollama_exe}")
        _json_log("INFO", "ollama_start", mode=mode, exe=ollama_exe, **opt_env)
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
