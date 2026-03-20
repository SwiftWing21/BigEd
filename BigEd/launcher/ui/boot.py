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
                    "dashboard.py", "dispatch_marathon.py", "train.py", "nmap"]
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

# ─── Frozen-exe Python resolver ────────────────────────────────────────────────

def _get_python():
    """Get the correct Python interpreter path.
    When running as frozen .exe, sys.executable is BigEdCC.exe — NOT Python.
    We need the actual Python interpreter for subprocess launches.
    """
    import shutil
    if getattr(sys, 'frozen', False):
        # Frozen .exe — find system Python
        py = shutil.which("python") or shutil.which("python3")
        if py:
            return py
        # Try common locations
        for candidate in [
            Path(sys._MEIPASS).parent / "python.exe",  # PyInstaller temp
            Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python312" / "python.exe",
            Path("C:/Python312/python.exe"),
        ]:
            if candidate.exists():
                return str(candidate)
        # Last resort: try uv
        uv = shutil.which("uv")
        if uv:
            return f"{uv} run python"
        return sys.executable  # fallback (will break but at least we tried)
    return sys.executable  # not frozen — sys.executable IS Python


# ─── Theme (single source of truth) ──────────────────────────────────────────
from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED,
)

# ─── Lazy imports from launcher ──────────────────────────────────────────────

def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


# ─── Ollama host (read from fleet.toml, fallback to default) ─────────────────

def _get_ollama_host():
    try:
        import tomllib
        toml_path = Path(__file__).resolve().parent.parent.parent.parent / "fleet" / "fleet.toml"
        with open(toml_path, "rb") as f:
            cfg = tomllib.load(f)
        return cfg.get("models", {}).get("ollama_host", "http://localhost:11434")
    except Exception:
        return "http://localhost:11434"

OLLAMA_HOST = _get_ollama_host()

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
        # Atomic write: write to temp then replace (safe against crash mid-write)
        tmp = Path(str(_BOOT_HISTORY_FILE) + '.tmp')
        tmp.write_text(json.dumps(history, indent=2), encoding="utf-8")
        tmp.replace(_BOOT_HISTORY_FILE)  # Atomic on same filesystem
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
            with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as r:
                data = json.loads(r.read())
            return any(m["name"] == model_name for m in data.get("models", []))
        except Exception:
            return False

    def _ollama_list_models(self):
        """Get list of installed model names from Ollama."""
        try:
            with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as r:
                data = json.loads(r.read())
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    def _ollama_get_loaded(self):
        """Get list of currently loaded models."""
        try:
            with urllib.request.urlopen(f"{OLLAMA_HOST}/api/ps", timeout=3) as r:
                data = json.loads(r.read())
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    def _pick_fallback_model(self, wanted, available):
        """Pick best available fallback model. Prefers larger models."""
        if not available:
            return None
        # Preference order: larger models first (better quality)
        size_order = ["qwen3:8b", "qwen3:4b", "qwen3:1.7b", "qwen3:0.6b"]
        for m in size_order:
            if m in available and m != wanted:
                return m
        # If none from known tiers, return first available
        return available[0] if available else None

    def _create_model_recovery_action(self, model, *, fallback=None, available=None):
        """Add a HITL action card to pull a missing model from the UI.

        When fallback is set, the card shows which model is being used as a
        stand-in and offers a dropdown to pull the preferred (or any other)
        model for next boot.
        """
        if not hasattr(self, '_action_cards'):
            return
        from ui.theme import BG3, GOLD, TEXT, ORANGE, ACCENT, ACCENT_H, FONT_SM

        card = ctk.CTkFrame(self._actions_scroll, fg_color="#1a2a1a", corner_radius=6)
        card.pack(fill="x", padx=2, pady=(1, 1))
        self._action_cards.append(card)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=6, pady=(4, 0))

        if fallback:
            ctk.CTkLabel(top, text=f"Using fallback: {fallback}",
                         font=("RuneScape Bold 12", 10, "bold"), text_color=ORANGE,
                         anchor="w").pack(anchor="w")
            ctk.CTkLabel(card,
                         text=f"Using {fallback} as fallback. Pull {model} for full performance.",
                         font=FONT_SM, text_color=TEXT, anchor="w",
                         ).pack(fill="x", padx=6, pady=(2, 0))
        else:
            ctk.CTkLabel(top, text=f"Missing model: {model}",
                         font=("RuneScape Bold 12", 10, "bold"), text_color=ORANGE,
                         anchor="w").pack(anchor="w")
            ctk.CTkLabel(card, text="No models installed. Click to download and install.",
                         font=FONT_SM, text_color=TEXT, anchor="w",
                         ).pack(fill="x", padx=6, pady=(2, 0))

        status_lbl = ctk.CTkLabel(card, text="", font=FONT_SM, text_color="#888")
        status_lbl.pack(fill="x", padx=6)

        # Build dropdown options: missing model marked with (pull), plus available
        dropdown_values = [f"{model} (pull)"]
        if available:
            for m in available:
                if m != model:
                    dropdown_values.append(m)
        selected_model = ctk.StringVar(value=dropdown_values[0])

        if len(dropdown_values) > 1:
            # Show a dropdown when there are multiple options
            dropdown_frame = ctk.CTkFrame(card, fg_color="transparent")
            dropdown_frame.pack(fill="x", padx=6, pady=(2, 0))
            ctk.CTkLabel(dropdown_frame, text="Model:", font=FONT_SM,
                         text_color=TEXT).pack(side="left", padx=(0, 4))
            ctk.CTkOptionMenu(
                dropdown_frame, variable=selected_model, values=dropdown_values,
                width=180, height=24, font=FONT_SM,
                fg_color="#2a3a2a", button_color=ACCENT, button_hover_color=ACCENT_H,
            ).pack(side="left")

        def _pull():
            target = selected_model.get().replace(" (pull)", "")
            btn.configure(state="disabled", text="Pulling...")
            status_lbl.configure(text=f"Downloading {target} — this may take a few minutes...")

            def _do_pull():
                if not hasattr(self, '_alive') or not self._alive:
                    return
                import shutil as _shutil
                ollama_exe = _shutil.which("ollama")
                if not ollama_exe and sys.platform == "win32":
                    for env_var, subpath in [
                        ("LOCALAPPDATA", "Programs/Ollama/ollama.exe"),
                        ("LOCALAPPDATA", "Ollama/ollama.exe"),
                        ("PROGRAMFILES", "Ollama/ollama.exe"),
                    ]:
                        base = os.environ.get(env_var, "")
                        if base:
                            p = Path(base) / subpath
                            if p.exists():
                                ollama_exe = str(p)
                                break
                if not ollama_exe:
                    if not hasattr(self, '_alive') or not self._alive:
                        return
                    self._safe_after(0, lambda: status_lbl.configure(
                        text="Ollama not found", text_color="#f44336"))
                    self._safe_after(0, lambda: btn.configure(
                        text="Retry", state="normal"))
                    return
                result = subprocess.run(
                    [ollama_exe, "pull", target],
                    capture_output=True, text=True, timeout=600,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                if not hasattr(self, '_alive') or not self._alive:
                    return
                if result.returncode == 0:
                    self._safe_after(0, lambda: status_lbl.configure(
                        text=f"{target} ready — restart boot to use it", text_color="#4caf50"))
                    self._safe_after(0, lambda: btn.configure(
                        text="Done", state="disabled", fg_color="#2a6a2a"))
                    self._safe_after(0, lambda: self._log_output(f"Model '{target}' pulled"))
                else:
                    self._safe_after(0, lambda: status_lbl.configure(
                        text=f"Pull failed — try manually: ollama pull {target}",
                        text_color="#f44336"))
                    self._safe_after(0, lambda: btn.configure(
                        text="Retry", state="normal"))

            threading.Thread(target=_do_pull, daemon=True).start()

        btn = ctk.CTkButton(
            card, text=f"Pull {model}", width=100, height=24, font=FONT_SM,
            fg_color=ACCENT, hover_color=ACCENT_H, command=_pull)
        btn.pack(anchor="e", padx=6, pady=(2, 6))

        # Update action count
        if hasattr(self, '_actions_count_lbl'):
            try:
                current = self._actions_count_lbl.cget("text")
                n = int(current.split()[0]) if current and current[0].isdigit() else 0
                self._actions_count_lbl.configure(text=f"{n + 1} pending")
            except Exception:
                self._actions_count_lbl.configure(text="1 pending")
        if hasattr(self, '_actions_empty_lbl'):
            self._actions_empty_lbl.pack_forget()

    def _evict_idle_blockers(self, host, target_model):
        """Evict idle VRAM-blocker models before loading a new one.

        Delegates to fleet/debug_models.py — the canonical module for all
        idle-model detection, DB correlation, and eviction logic.

        Returns list of evicted model names.
        """
        try:
            # Import canonical module from fleet/
            fleet_dir = Path(__file__).resolve().parent.parent.parent.parent / "fleet"
            sys.path.insert(0, str(fleet_dir))
            try:
                import debug_models
            finally:
                sys.path.pop(0)

            return debug_models.evict_idle_blockers(
                host=host,
                target_model=target_model,
                db_path=fleet_dir / "fleet.db",
            )
        except Exception as e:
            self._log_output(f"  [warn] evict_idle_blockers: {e}")
            return []

    # ── Boot progress UI with live timers ────────────────────────────────
    def _show_boot_progress(self):
        """Create boot progress line items with live elapsed timers."""
        gpu_model, conductor_model = self._read_fleet_models()
        # Stage definitions with timing keys for history lookup
        self._boot_stage_defs = [
            ("Ollama server",         "ollama",        ""),
            ("Maintainer (CPU)",      "model_load",    "qwen3:0.6b"),
            ("Dr. Ders",              "hw_supervisor", ""),
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
        self._safe_after(250, self._boot_spin)  # update 4x/sec for smooth timer

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

        Order: Ollama → Maintainer (CPU) → Dr. Ders → GPU model →
               Fleet supervisor → Workers → Conductor

        Stability design:
        - Maintainer (smallest CPU model) loads BEFORE Dr. Ders
          so there's always a model available for the fleet
        - Dr. Ders starts with a model already loaded (no empty state)
        - GPU model loads AFTER Dr. Ders is monitoring
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
                self._safe_after(0, lambda: self._log_output("Boot aborted."))
                self._safe_after(0, self._hide_boot_progress)
                return
            self._safe_after(0, lambda i=idx: self._boot_update(i, "active"))
            try:
                detail = fn()
                self._safe_after(0, lambda i=idx, d=detail: self._boot_update(i, "done", d or ""))
            except Exception as e:
                msg = str(e)[:60]
                self._safe_after(0, lambda i=idx, m=msg: self._boot_update(i, "error", m))
                self._safe_after(0, lambda m=msg: self._log_output(f"Boot failed at stage: {m}"))
                self._safe_after(0, lambda m=msg: self._show_toast(f"✗ Boot failed: {m}", RED, duration=8000))
                # Reset system state so button shows Start again
                self._system_running = False
                self._safe_after(0, lambda: self._btn_system_toggle.configure(
                    text="▶  Start", fg_color="#1e3a1e", hover_color="#2a4a2a"))
                # Keep boot progress visible on error — don't auto-hide
                return
        self._safe_after(0, lambda: self._log_output("System boot complete."))
        self._safe_after(0, lambda: self._show_toast("Fleet online — all systems go", GREEN))
        # Switch log view from Dr. Ders to combined after boot
        self._current_log_agent = "all"
        self._safe_after(5000, self._hide_boot_progress)
        # Auto-open dashboard in browser if configured
        self._safe_after(1500, self._auto_open_dashboard)

    def _auto_open_dashboard(self):
        """Open dashboard in default browser after boot, if enabled.

        Respects air_gap_mode (skip) and dashboard.auto_open config.
        Runs in a thread to avoid blocking the UI.
        """
        L = _launcher()
        try:
            import tomllib
            with open(L.FLEET_TOML, "rb") as f:
                cfg = tomllib.load(f)
            fleet = cfg.get("fleet", {})
            dash = cfg.get("dashboard", {})
            # Skip if air-gap or dashboard disabled
            if fleet.get("air_gap_mode", False):
                return
            if not dash.get("enabled", True):
                return
            if not dash.get("auto_open", True):
                return
            port = dash.get("port", 5555)
        except Exception:
            port = 5555

        def _open():
            try:
                import webbrowser
                webbrowser.open(f"http://localhost:{port}")
                self._safe_after(0, lambda: self._log_output(
                    f"Dashboard opened at http://localhost:{port}"))
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    # ── Individual boot stages ───────────────────────────────────────────

    def _boot_ollama(self):
        """Stage 0: Start Ollama server natively, poll until responsive."""
        import shutil
        # Check if already running
        try:
            urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=2)
            return "already up"
        except Exception:
            pass

        # Find and launch ollama natively
        ollama_exe = shutil.which("ollama")
        if not ollama_exe and sys.platform == "win32":
            for env_var, subpath in [
                ("LOCALAPPDATA", "Programs/Ollama/ollama.exe"),
                ("LOCALAPPDATA", "Ollama/ollama.exe"),
                ("PROGRAMFILES", "Ollama/ollama.exe"),
            ]:
                base = os.environ.get(env_var, "")
                if base:
                    p = Path(base) / subpath
                    if p.exists():
                        ollama_exe = str(p)
                        break
        if not ollama_exe:
            raise Exception("ollama not found — install from https://ollama.com")

        # Set eco mode env if needed
        L = _launcher()
        env = os.environ.copy()
        try:
            if self._is_eco_mode():
                env["CUDA_VISIBLE_DEVICES"] = "-1"
        except Exception:
            pass

        self._ollama_proc = subprocess.Popen(
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
                urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=2)
                return "started"
            except Exception:
                time.sleep(2)
        raise Exception("Ollama timed out (30s)")

    def _boot_maintainer(self):
        """Stage 1: Load smallest available model on CPU-only.

        This ensures Dr. Ders always has a model to guard when it starts.
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
                with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as r:
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
            f"{OLLAMA_HOST}/api/generate", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
            return f"{target} (CPU)"
        except Exception as e:
            raise Exception(f"Maintainer {target}: {e}")

    def _boot_hw_supervisor(self):
        """Stage 1: Start Dr. Ders, poll until hw_state.json is fresh.

        Stability: delete stale hw_state.json first, give Dr. Ders
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

        # Launch Dr. Ders NATIVELY on Windows (no WSL needed)
        # It only uses pynvml + psutil + urllib — all cross-platform
        hw_sup_path = L.FLEET_DIR / "hw_supervisor.py"
        # Kill any existing Dr. Ders process
        _kill_fleet_processes(["hw_supervisor.py"])
        time.sleep(1)
        # Start fresh — native Windows Python, no WSL
        subprocess.Popen(
            [_get_python(), str(hw_sup_path)],
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
        # Check model exists — find fallback if missing, only fail if nothing available
        if not self._ollama_model_exists(model):
            # Find best available fallback
            available = self._ollama_list_models()
            fallback = self._pick_fallback_model(model, available)
            if fallback:
                self._safe_after(0, lambda m=model, f=fallback: self._log_output(
                    f"  Model '{m}' not found — using fallback '{f}'"))
                self._safe_after(0, lambda m=model, f=fallback, a=available:
                    self._create_model_recovery_action(m, fallback=f, available=a))
                model = fallback  # continue boot with fallback
            else:
                self._safe_after(0, lambda m=model: self._log_output(
                    f"  No models installed — creating recovery action..."))
                self._safe_after(0, lambda m=model: self._create_model_recovery_action(m))
                raise Exception(f"No models installed — use recovery action to pull '{model}'")

        # Evict idle blocker models before attempting load
        # (models held in VRAM by keep_alive:"24h" with no active fleet tasks)
        host = OLLAMA_HOST
        evicted = self._evict_idle_blockers(host, model)
        if evicted:
            self._log_output(
                f"  Freed VRAM: evicted {len(evicted)} idle model(s): {', '.join(evicted)}"
            )

        # Check if already loaded (after any evictions)
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
            f"{OLLAMA_HOST}/api/generate", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=int(timeout)) as r:
                if r.status != 200:
                    raise Exception(f"{model}: HTTP {r.status}")
                resp_data = r.read()
                # Check for error in response
                try:
                    resp = json.loads(resp_data)
                    if "error" in resp:
                        raise Exception(f"{model}: {resp['error']}")
                except json.JSONDecodeError:
                    pass  # Expected: Ollama /api/generate streams NDJSON, last chunk may not parse
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

        # Launch supervisor natively (like Dr. Ders)
        subprocess.Popen(
            [_get_python(), str(L.FLEET_DIR / "supervisor.py")],
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
        # Terminate Ollama process we started (avoid zombies)
        if hasattr(self, '_ollama_proc') and self._ollama_proc and self._ollama_proc.poll() is None:
            self._ollama_proc.terminate()
            try:
                self._ollama_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._ollama_proc.kill()
            self._ollama_proc = None
        # Kill all fleet processes natively
        killed = _kill_fleet_processes()
        if killed:
            self._log_output(f"Killed: {', '.join(killed)}")
        time.sleep(1)
        _kill_ollama()
        self._log_output("System stopped")
