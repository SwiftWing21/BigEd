"""
BigEd CC — Boot sequence and system start/stop logic.
Extracted from launcher.py to reduce god-object complexity (TECH_DEBT 4.1).

Provides a BootManager mixin that is mixed into BigEdCC:
- _show_boot_progress / _hide_boot_progress  (staged boot UI)
- _start_system / _stop_system               (lifecycle)
- _boot_sequence and individual stage methods (_boot_ollama, etc.)
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import customtkinter as ctk


# ─── Cross-platform process management (psutil) ──────────────────────────────

def _kill_fleet_processes(targets=None):
    """Kill fleet processes by name using psutil (cross-platform).

    targets: list of script names to kill, e.g. ["supervisor.py", "worker.py"]
    If None, kills all fleet processes.
    """
    if targets is None:
        targets = ["supervisor.py", "hw_supervisor.py", "worker.py",
                    "dispatch_marathon.py", "train.py", "nmap"]
    import psutil
    killed = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            cmd_str = ' '.join(cmdline)
            for target in targets:
                if target in cmd_str and proc.pid != os.getpid():
                    proc.kill()
                    killed.append(f"{target}(pid={proc.pid})")
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


def _kill_ollama():
    """Kill Ollama process using psutil."""
    import psutil
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info.get('name', '').lower().startswith('ollama'):
                proc.kill()
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False

# ─── Theme constants (copied from launcher.py — boot module is standalone) ────
BG       = "#1a1a1a"
BG2      = "#242424"
BG3      = "#2d2d2d"
ACCENT   = "#b22222"
ACCENT_H = "#8b0000"
GOLD     = "#c8a84b"
TEXT     = "#e2e2e2"
DIM      = "#888888"
GREEN    = "#4caf50"
ORANGE   = "#ff9800"
RED      = "#f44336"

# ─── Lazy imports from launcher ──────────────────────────────────────────────

def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


# ─── Boot spinner characters ─────────────────────────────────────────────────
_SPIN = "⣾⣽⣻⢿⡿⣟⣯⣷"

# ─── Adaptive Boot Timing ────────────────────────────────────────────────────
_BOOT_HISTORY_FILE = Path(__file__).parent / "data" / "boot_timing.json"

def _load_boot_history() -> dict:
    """Load historical boot stage timings."""
    try:
        if _BOOT_HISTORY_FILE.exists():
            return json.loads(_BOOT_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_boot_timing(stage: str, duration: float, model: str = ""):
    """Record how long a boot stage took for adaptive timeouts."""
    try:
        _BOOT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        history = _load_boot_history()
        key = f"{stage}:{model}" if model else stage
        if key not in history:
            history[key] = {"times": [], "avg": 0}
        times = history[key]["times"]
        times.append(round(duration, 1))
        # Keep last 10 measurements
        if len(times) > 10:
            times[:] = times[-10:]
        history[key]["avg"] = round(sum(times) / len(times), 1)
        _BOOT_HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except Exception:
        pass

def _get_adaptive_timeout(stage: str, model: str = "", default: float = 40) -> float:
    """Get timeout for a boot stage based on history.

    First boot or model change: use generous default (120s).
    Subsequent boots: avg + 60s headroom (minimum 30s).
    """
    history = _load_boot_history()
    key = f"{stage}:{model}" if model else stage
    if key not in history or not history[key].get("times"):
        return 120  # first boot — very generous
    avg = history[key]["avg"]
    return max(30, avg + 60)  # avg + 60s headroom, minimum 30s


# ─── Boot Manager Mixin ─────────────────────────────────────────────────────

class BootManagerMixin:
    """Mixin providing staged boot sequence and system start/stop for BigEdCC."""

    # ── Fleet model names ────────────────────────────────────────────────
    def _read_fleet_models(self):
        """Read GPU + conductor model names from fleet.toml using tomlkit."""
        L = _launcher()
        try:
            import tomllib
            with open(L.FLEET_TOML, "rb") as f:
                cfg = tomllib.load(f)
            models = cfg.get("models", {})
            return (models.get("local", "qwen3:8b"),
                    models.get("conductor_model", "qwen3:4b"))
        except Exception:
            return "qwen3:8b", "qwen3:4b"

    # ── Ollama model helpers ─────────────────────────────────────────────
    def _ollama_model_exists(self, model_name):
        """Check if a model is installed in Ollama."""
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
                data = json.loads(r.read())
            return any(m["name"] == model_name for m in data.get("models", []))
        except Exception:
            return False

    def _ollama_get_loaded(self):
        """Get list of currently loaded models."""
        try:
            with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=3) as r:
                data = json.loads(r.read())
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    # ── Boot progress UI with live timers ────────────────────────────────
    def _show_boot_progress(self):
        """Create boot progress line items with live elapsed timers."""
        gpu_model, conductor_model = self._read_fleet_models()
        # Stage definitions with timing keys for history lookup
        self._boot_stage_defs = [
            ("Ollama server",         "ollama",        ""),
            ("Maintainer (CPU)",      "model_load",    "qwen3:0.6b"),
            ("HW Supervisor",         "hw_supervisor", ""),
            (f"GPU model  {gpu_model}","model_load",   gpu_model),
            ("Fleet supervisor",       "supervisor",    ""),
            ("Workers",                "workers",       ""),
            (f"Conductor  {conductor_model}", "model_load", conductor_model),
        ]
        self._boot_active = True
        self._boot_abort.clear()
        self._boot_widgets = []

        for i, (name, timing_key, timing_model) in enumerate(self._boot_stage_defs):
            row = ctk.CTkFrame(self._agents_frame_inner, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=1)
            row.grid_columnconfigure(1, weight=1)

            dot = ctk.CTkLabel(row, text="○", font=("Consolas", 11),
                               text_color=DIM, width=14)
            dot.grid(row=0, column=0, padx=(2, 3))

            lbl = ctk.CTkLabel(row, text=name, font=("Consolas", 10),
                               text_color=DIM, anchor="w", width=160)
            lbl.grid(row=0, column=1, sticky="w")

            # Live timer label (shows elapsed / expected)
            timer = ctk.CTkLabel(row, text="", font=("Consolas", 9),
                                 text_color=DIM, anchor="e", width=70)
            timer.grid(row=0, column=2, sticky="e")

            # Status label (done detail or error)
            st = ctk.CTkLabel(row, text="", font=("Consolas", 9),
                              text_color=DIM, anchor="e", width=90)
            st.grid(row=0, column=3, sticky="e", padx=(0, 4))

            # Get expected time from history
            expected = _get_adaptive_timeout(timing_key, timing_model, default=30)
            history = _load_boot_history()
            hkey = f"{timing_key}:{timing_model}" if timing_model else timing_key
            avg = history.get(hkey, {}).get("avg", 0)

            self._boot_widgets.append({
                "frame": row, "dot": dot, "label": lbl, "timer": timer,
                "status": st, "_state": "waiting",
                "_start_time": 0, "_expected": avg,
            })

        self._boot_spin_idx = 0
        self._boot_spin()

    def _boot_spin(self):
        """Animate spinner + update live timers for active stages."""
        if not self._boot_active:
            return
        self._boot_spin_idx = (self._boot_spin_idx + 1) % len(_SPIN)
        char = _SPIN[self._boot_spin_idx]
        for w in self._boot_widgets:
            if w["_state"] == "active":
                w["dot"].configure(text=char)
                # Update live timer
                elapsed = time.time() - w["_start_time"]
                expected = w["_expected"]
                if expected > 0:
                    timer_text = f"{elapsed:.0f}s / ~{expected:.0f}s"
                    # Color: green if under expected, orange if close, red if over
                    if elapsed < expected * 0.8:
                        w["timer"].configure(text=timer_text, text_color=GREEN)
                    elif elapsed < expected * 1.2:
                        w["timer"].configure(text=timer_text, text_color=ORANGE)
                    else:
                        w["timer"].configure(text=timer_text, text_color=RED)
                else:
                    w["timer"].configure(text=f"{elapsed:.0f}s", text_color=DIM)
        self.after(250, self._boot_spin)  # update 4x/sec for smooth timer

    def _boot_update(self, idx, state, detail=""):
        """Update boot stage visual state. Must be called from main thread."""
        if idx >= len(self._boot_widgets):
            return
        w = self._boot_widgets[idx]
        w["_state"] = state
        if state == "waiting":
            w["dot"].configure(text="○", text_color=DIM)
            w["label"].configure(text_color=DIM)
            w["timer"].configure(text="", text_color=DIM)
            w["status"].configure(text="", text_color=DIM)
        elif state == "active":
            w["_start_time"] = time.time()
            w["dot"].configure(text_color=ACCENT)
            w["label"].configure(text_color=TEXT)
            w["status"].configure(text="starting...", text_color=ACCENT)
        elif state == "done":
            elapsed = time.time() - w["_start_time"] if w["_start_time"] else 0
            w["dot"].configure(text="●", text_color=GREEN)
            w["label"].configure(text_color=TEXT)
            w["timer"].configure(text=f"{elapsed:.1f}s", text_color=GREEN)
            w["status"].configure(text=detail or "ONLINE", text_color=GREEN)
        elif state == "error":
            elapsed = time.time() - w["_start_time"] if w["_start_time"] else 0
            w["dot"].configure(text="✗", text_color=RED)
            w["label"].configure(text_color=RED)
            w["timer"].configure(text=f"{elapsed:.1f}s", text_color=RED)
            w["status"].configure(text=detail or "FAILED", text_color=RED)

    def _hide_boot_progress(self):
        """Remove boot progress widgets, let normal agent display take over."""
        for w in self._boot_widgets:
            w["frame"].destroy()
        self._boot_widgets = []
        self._boot_active = False

    # ── System start / stop ──────────────────────────────────────────────
    def _start_system(self):
        self._system_intentional_stop = False
        self._system_running = True
        self._ever_seen_roles.clear()  # clear stale agents from previous sessions
        self._btn_system_toggle.configure(
            text="■  Stop", fg_color="#3a1e1e", hover_color="#4a2a2a")
        self._show_boot_progress()
        self._log_output("Staged boot starting...")
        threading.Thread(target=self._boot_sequence, daemon=True).start()

    def _boot_sequence(self):
        """Staged boot — 7 stages, smallest CPU model loaded FIRST.

        Order: Ollama → Maintainer (CPU) → HW Supervisor → GPU model →
               Fleet supervisor → Workers → Conductor

        Stability design:
        - Maintainer (smallest CPU model) loads BEFORE hw_supervisor
          so there's always a model available for the fleet
        - hw_supervisor starts with a model already loaded (no empty state)
        - GPU model loads AFTER hw_supervisor is monitoring
        - Each stage has explicit timeouts with generous margins
        - Model existence validated before load attempts
        - Failures reset button to Start and clean up boot UI
        """
        gpu_model, conductor_model = self._read_fleet_models()

        stages = [
            (0, self._boot_ollama),
            (1, self._boot_maintainer),
            (2, self._boot_hw_supervisor),
            (3, lambda: self._boot_model(gpu_model, gpu=True)),
            (4, self._boot_supervisor),
            (5, self._boot_workers),
            (6, lambda: self._boot_model(conductor_model, gpu=False)),
        ]
        for idx, fn in stages:
            if self._boot_abort.is_set():
                self.after(0, lambda: self._log_output("Boot aborted."))
                self.after(0, self._hide_boot_progress)
                return
            self.after(0, lambda i=idx: self._boot_update(i, "active"))
            try:
                detail = fn()
                self.after(0, lambda i=idx, d=detail: self._boot_update(i, "done", d or ""))
            except Exception as e:
                msg = str(e)[:60]
                self.after(0, lambda i=idx, m=msg: self._boot_update(i, "error", m))
                self.after(0, lambda m=msg: self._log_output(f"Boot failed at stage: {m}"))
                # Reset system state so button shows Start again
                self._system_running = False
                self.after(0, lambda: self._btn_system_toggle.configure(
                    text="▶  Start", fg_color="#1e3a1e", hover_color="#2a4a2a"))
                self.after(3000, self._hide_boot_progress)
                return
        self.after(0, lambda: self._log_output("System boot complete."))
        # Switch log view from hw_supervisor to combined after boot
        self._current_log_agent = "all"
        self.after(5000, self._hide_boot_progress)

    # ── Individual boot stages ───────────────────────────────────────────

    def _boot_ollama(self):
        """Stage 0: Start Ollama server natively, poll until responsive."""
        import shutil
        # Check if already running
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
            return "already up"
        except Exception:
            pass

        # Find and launch ollama natively
        ollama_exe = shutil.which("ollama")
        if not ollama_exe:
            raise Exception("ollama not found on PATH")

        # Set eco mode env if needed
        L = _launcher()
        env = os.environ.copy()
        try:
            if self._is_eco_mode():
                env["CUDA_VISIBLE_DEVICES"] = "-1"
        except Exception:
            pass

        subprocess.Popen(
            [ollama_exe, "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

        # Poll with generous timeout (30s)
        for _ in range(15):
            if self._boot_abort.is_set():
                raise Exception("aborted")
            try:
                urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
                return "started"
            except Exception:
                time.sleep(2)
        raise Exception("Ollama timed out (30s)")

    def _boot_maintainer(self):
        """Stage 1: Load smallest available model on CPU-only.

        This ensures hw_supervisor always has a model to guard when it starts.
        The maintainer model is lightweight (~0.5GB RAM) and runs on CPU,
        never touching GPU VRAM.
        """
        # Find smallest available model (prefer 0.6b → 1.7b → 4b)
        preferred = ["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"]
        target = None
        for model in preferred:
            if self._ollama_model_exists(model):
                target = model
                break

        if not target:
            # Fallback: use whatever is installed
            try:
                with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
                    data = json.loads(r.read())
                models = [m["name"] for m in data.get("models", [])]
                if models:
                    # Pick smallest by name heuristic (lower param count first)
                    target = sorted(models)[0]
            except Exception:
                pass

        if not target:
            raise Exception("No models installed")

        # Check if already loaded
        loaded = self._ollama_get_loaded()
        if target in loaded:
            return f"{target} (cached)"

        # Load on CPU-only (num_gpu=0) — never touch GPU
        body = json.dumps({
            "model": target, "prompt": "", "keep_alive": "24h",
            "options": {"num_gpu": 0},
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
            return f"{target} (CPU)"
        except Exception as e:
            raise Exception(f"Maintainer {target}: {e}")

    def _boot_hw_supervisor(self):
        """Stage 1: Start hw_supervisor, poll until hw_state.json is fresh.

        Stability: delete stale hw_state.json first, give hw_supervisor
        5s to boot Python + detect GPU before first poll.
        """
        L = _launcher()
        hw_state = L.FLEET_DIR / "hw_state.json"

        # Delete stale hw_state.json so we only detect fresh writes
        try:
            if hw_state.exists():
                hw_state.unlink()
        except Exception:
            pass

        # Launch hw_supervisor NATIVELY on Windows (no WSL needed)
        # It only uses pynvml + psutil + urllib — all cross-platform
        hw_sup_path = L.FLEET_DIR / "hw_supervisor.py"
        # Kill any existing hw_supervisor process
        _kill_fleet_processes(["hw_supervisor.py"])
        time.sleep(1)
        # Start fresh — native Windows Python, no WSL
        subprocess.Popen(
            [sys.executable, str(hw_sup_path)],
            cwd=str(L.FLEET_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

        # Adaptive timeout — uses historical boot times
        timeout_secs = _get_adaptive_timeout("hw_supervisor")
        max_polls = max(10, int(timeout_secs / 2))
        start_time = time.time()

        for i in range(max_polls):
            if self._boot_abort.is_set():
                raise Exception("aborted")
            try:
                if hw_state.exists():
                    data = json.loads(hw_state.read_text(encoding="utf-8"))
                    updated = data.get("updated_at", 0)
                    age = time.time() - updated
                    if age < 30:
                        status = data.get("status", "unknown")
                        if status in ("starting", "ready", "transitioning", "degraded"):
                            elapsed = time.time() - start_time
                            _save_boot_timing("hw_supervisor", elapsed)
                            return f"{status} ({elapsed:.0f}s)"
            except Exception:
                pass
            time.sleep(2)
        elapsed = time.time() - start_time
        raise Exception(f"hw_state not updating ({elapsed:.0f}s)")

    def _boot_model(self, model, gpu=True):
        """Stage 2/5: Load a model into Ollama.

        Stability: validate model exists before attempting load.
        Shorter timeout for missing models.
        """
        # Check model exists first
        if not self._ollama_model_exists(model):
            # Try to find what IS available
            try:
                with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
                    data = json.loads(r.read())
                available = [m["name"] for m in data.get("models", [])]
            except Exception:
                available = []
            raise Exception(f"'{model}' not installed. Have: {available}")

        # Check if already loaded
        loaded = self._ollama_get_loaded()
        if model in loaded:
            return f"{model} (cached)"

        timeout = _get_adaptive_timeout("model_load", model)
        start_time = time.time()

        body = json.dumps({
            "model": model, "prompt": "", "keep_alive": "24h",
            **({"options": {"num_gpu": 0}} if not gpu else {}),
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=int(timeout)) as r:
                resp_data = r.read()
                # Check for error in response
                try:
                    resp = json.loads(resp_data)
                    if "error" in resp:
                        raise Exception(f"{model}: {resp['error']}")
                except json.JSONDecodeError:
                    pass  # streaming response, not JSON — that's fine
            elapsed = time.time() - start_time
            _save_boot_timing("model_load", elapsed, model)
            return f"{model} ({elapsed:.0f}s)"
        except urllib.error.URLError as e:
            raise Exception(f"{model}: {e}")

    def _boot_supervisor(self):
        """Stage 3: Start supervisor.py, poll until STATUS.md is fresh.

        Stability: supervisor writes STATUS.md every 30s, so we use a 60s
        freshness window and poll for up to 45s.
        """
        L = _launcher()

        # Delete stale STATUS.md too
        try:
            if L.STATUS_MD.exists():
                L.STATUS_MD.unlink()
        except Exception:
            pass

        # Kill any existing supervisor process natively
        _kill_fleet_processes(["supervisor.py"])
        time.sleep(1)

        # Ensure required directories exist
        for d in ["logs", "knowledge/summaries", "knowledge/reports"]:
            (L.FLEET_DIR / d).mkdir(parents=True, exist_ok=True)

        # Launch supervisor natively (like hw_supervisor)
        subprocess.Popen(
            [sys.executable, str(L.FLEET_DIR / "supervisor.py")],
            cwd=str(L.FLEET_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )

        # Poll for fresh STATUS.md (45s total: 22 iterations × 2s)
        for _ in range(22):
            if self._boot_abort.is_set():
                raise Exception("aborted")
            time.sleep(2)
            try:
                if L.STATUS_MD.exists():
                    age = time.time() - L.STATUS_MD.stat().st_mtime
                    if age < 60:  # written within last 60s
                        return "ONLINE"
            except Exception:
                pass
        raise Exception("STATUS.md stale (45s)")

    def _boot_workers(self):
        """Stage 4: Poll until agents appear in STATUS.md.

        Stability: workers register within ~5s of supervisor start.
        Poll for up to 40s.
        """
        L = _launcher()
        for _ in range(20):
            if self._boot_abort.is_set():
                raise Exception("aborted")
            time.sleep(2)
            try:
                status = L.parse_status()
                agents = [a for a in status.get("agents", [])
                          if a.get("status") not in ("OFFLINE", None)]
                if agents:
                    return f"{len(agents)} online"
            except Exception:
                pass
        raise Exception("no workers (40s)")

    def _stop_system(self):
        self._system_intentional_stop = True
        self._system_running = False
        self._boot_abort.set()  # abort staged boot if in progress
        if self._boot_active:
            self._hide_boot_progress()
        self._btn_system_toggle.configure(
            text="▶  Start", fg_color="#1e3a1e", hover_color="#2a4a2a")
        self._log_output("Stopping fleet...")
        # Kill all fleet processes natively
        killed = _kill_fleet_processes()
        if killed:
            self._log_output(f"Killed: {', '.join(killed)}")
        time.sleep(1)
        _kill_ollama()
        self._log_output("System stopped")
