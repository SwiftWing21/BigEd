"""
BigEd CC — Boot sequence and system start/stop logic.
Extracted from launcher.py to reduce god-object complexity (TECH_DEBT 4.1).

Provides a BootManager mixin that is mixed into BigEdCC:
- _show_boot_progress / _hide_boot_progress  (staged boot UI)
- _start_system / _stop_system               (lifecycle)
- _boot_sequence and individual stage methods (_boot_ollama, etc.)
"""
import json
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import customtkinter as ctk

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


# ─── Boot Manager Mixin ─────────────────────────────────────────────────────
# These are intended to be called as methods of BigEdCC (self = app instance).
# They are injected into BigEdCC at module level in launcher.py.

class BootManagerMixin:
    """Mixin providing staged boot sequence and system start/stop for BigEdCC."""

    # ── Fleet model names ────────────────────────────────────────────────
    def _read_fleet_models(self):
        """Read GPU + conductor model names from fleet.toml."""
        L = _launcher()
        try:
            text = L.FLEET_TOML.read_text(encoding="utf-8")
            gpu_m = re.search(r'^local\s*=\s*["\']([^"\']+)["\']', text, re.M)
            cond_m = re.search(r'^conductor_model\s*=\s*["\']([^"\']+)["\']', text, re.M)
            return (gpu_m.group(1) if gpu_m else "qwen3:8b",
                    cond_m.group(1) if cond_m else "qwen3:4b")
        except Exception:
            return "qwen3:8b", "qwen3:4b"

    # ── Boot progress UI ─────────────────────────────────────────────────
    def _show_boot_progress(self):
        """Create boot progress line items in the agents panel."""
        gpu_model, conductor_model = self._read_fleet_models()
        stages = [
            "Ollama server",
            "HW Supervisor",
            f"GPU model  {gpu_model}",
            "Fleet supervisor",
            "Workers",
            f"Conductor  {conductor_model}",
        ]
        self._boot_active = True
        self._boot_abort.clear()
        self._boot_widgets = []

        for i, name in enumerate(stages):
            row = ctk.CTkFrame(self._agents_frame_inner, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=1)
            row.grid_columnconfigure(1, weight=1)
            dot = ctk.CTkLabel(row, text="○", font=("Consolas", 11),
                               text_color=DIM, width=14)
            dot.grid(row=0, column=0, padx=(2, 3))
            lbl = ctk.CTkLabel(row, text=name, font=("Consolas", 10),
                               text_color=DIM, anchor="w", width=180)
            lbl.grid(row=0, column=1, sticky="w")
            st = ctk.CTkLabel(row, text="", font=("Consolas", 9),
                              text_color=DIM, anchor="e", width=80)
            st.grid(row=0, column=2, sticky="e", padx=(0, 4))
            self._boot_widgets.append({
                "frame": row, "dot": dot, "label": lbl, "status": st, "_state": "waiting",
            })

        self._boot_spin_idx = 0
        self._boot_spin()

    def _boot_spin(self):
        """Animate spinner for active boot stages."""
        if not self._boot_active:
            return
        self._boot_spin_idx = (self._boot_spin_idx + 1) % len(_SPIN)
        char = _SPIN[self._boot_spin_idx]
        for w in self._boot_widgets:
            if w["_state"] == "active":
                w["dot"].configure(text=char)
        self.after(120, self._boot_spin)

    def _boot_update(self, idx, state, detail=""):
        """Update boot stage visual state. Must be called from main thread."""
        if idx >= len(self._boot_widgets):
            return
        w = self._boot_widgets[idx]
        w["_state"] = state
        if state == "waiting":
            w["dot"].configure(text="○", text_color=DIM)
            w["label"].configure(text_color=DIM)
            w["status"].configure(text="", text_color=DIM)
        elif state == "active":
            w["dot"].configure(text_color=ACCENT)
            w["label"].configure(text_color=TEXT)
            w["status"].configure(text="starting...", text_color=ACCENT)
        elif state == "done":
            w["dot"].configure(text="●", text_color=GREEN)
            w["label"].configure(text_color=TEXT)
            w["status"].configure(text=detail or "ONLINE", text_color=GREEN)
        elif state == "error":
            w["dot"].configure(text="✗", text_color=RED)
            w["label"].configure(text_color=RED)
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
        """Staged boot — Ollama → HW sup → GPU model → supervisor → workers → conductor."""
        gpu_model, conductor_model = self._read_fleet_models()
        stages = [
            (0, self._boot_ollama),
            (1, self._boot_hw_supervisor),
            (2, lambda: self._boot_model(gpu_model, gpu=True)),
            (3, self._boot_supervisor),
            (4, self._boot_workers),
            (5, lambda: self._boot_model(conductor_model, gpu=False)),
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
                msg = str(e)[:40]
                self.after(0, lambda i=idx, m=msg: self._boot_update(i, "error", m))
                self.after(0, lambda m=msg: self._log_output(f"Boot failed at stage: {m}"))
                # Reset system state so button shows Start again
                self._system_running = False
                self.after(0, lambda: self._btn_system_toggle.configure(
                    text="▶  Start", fg_color="#1e3a1e", hover_color="#2a4a2a"))
                self.after(0, self._hide_boot_progress)
                return
        self.after(0, lambda: self._log_output("System boot complete."))
        self.after(5000, self._hide_boot_progress)

    # ── Individual boot stages ───────────────────────────────────────────
    def _boot_ollama(self):
        """Start Ollama server, poll until responsive."""
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
            return "already up"
        except Exception:
            pass
        # Write and execute start script via WSL
        tmp = Path(tempfile.gettempdir()) / "fleet_ollama_start.sh"
        tmp.write_text(self._ollama_script(), encoding="utf-8", newline="\n")
        drive = tmp.drive.rstrip(":").lower()
        rest = str(tmp).replace("\\", "/")[2:]
        wsl_path = f"/mnt/{drive}{rest}"
        subprocess.run(
            ["wsl", "-d", "Ubuntu", "/bin/bash", wsl_path],
            capture_output=True, text=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for _ in range(15):
            if self._boot_abort.is_set():
                raise Exception("aborted")
            try:
                urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
                return "started"
            except Exception:
                time.sleep(2)
        raise Exception("Ollama timed out")

    def _boot_hw_supervisor(self):
        """Start hw_supervisor, poll until hw_state.json is fresh."""
        L = _launcher()
        L.wsl(
            "pkill -f hw_supervisor.py 2>/dev/null; sleep 1; "
            "nohup ~/.local/bin/uv run python hw_supervisor.py >> logs/hw_supervisor.log 2>&1 &",
            capture=True, timeout=15,
        )
        hw_state = L.FLEET_DIR / "hw_state.json"
        for _ in range(10):
            if self._boot_abort.is_set():
                raise Exception("aborted")
            time.sleep(2)
            try:
                if hw_state.exists():
                    data = json.loads(hw_state.read_text(encoding="utf-8"))
                    if time.time() - data.get("updated_at", 0) < 15:
                        return "monitoring"
            except Exception:
                pass
        raise Exception("hw_state not updating")

    def _boot_model(self, model, gpu=True):
        """Load a model into Ollama. gpu=True for GPU, False for CPU-only."""
        body = json.dumps({
            "model": model, "prompt": "", "keep_alive": "24h",
            **({"options": {"num_gpu": 0}} if not gpu else {}),
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                r.read()
            return model
        except Exception as e:
            raise Exception(f"{model}: {e}")

    def _boot_supervisor(self):
        """Start supervisor.py, poll until STATUS.md is fresh."""
        L = _launcher()
        L.wsl(
            "pkill -f supervisor.py 2>/dev/null; sleep 1; "
            "mkdir -p logs knowledge/summaries knowledge/reports && "
            "nohup ~/.local/bin/uv run python supervisor.py >> logs/supervisor.log 2>&1 &",
            capture=True, timeout=15,
        )
        for _ in range(15):
            if self._boot_abort.is_set():
                raise Exception("aborted")
            time.sleep(2)
            try:
                if L.STATUS_MD.exists() and (time.time() - L.STATUS_MD.stat().st_mtime < 45):
                    return "ONLINE"
            except Exception:
                pass
        raise Exception("STATUS.md stale")

    def _boot_workers(self):
        """Poll until agents appear in STATUS.md."""
        L = _launcher()
        for _ in range(20):
            if self._boot_abort.is_set():
                raise Exception("aborted")
            time.sleep(2)
            status = L.parse_status()
            agents = [a for a in status.get("agents", []) if a.get("status") != "OFFLINE"]
            if agents:
                return f"{len(agents)} online"
        raise Exception("no workers")

    def _stop_system(self):
        L = _launcher()
        self._system_intentional_stop = True
        self._system_running = False
        self._boot_abort.set()  # abort staged boot if in progress
        if self._boot_active:
            self._hide_boot_progress()
        self._btn_system_toggle.configure(
            text="▶  Start", fg_color="#1e3a1e", hover_color="#2a4a2a")
        self._log_output("Stopping fleet + Ollama...")
        stop_cmd = (
            "pkill -f supervisor.py 2>/dev/null; "
            "pkill -f hw_supervisor.py 2>/dev/null; "
            "pkill -f 'worker.py' 2>/dev/null; "
            "pkill -f 'dispatch_marathon.py' 2>/dev/null; "
            "pkill -f 'train\\.py' 2>/dev/null; "
            "pkill -f 'nmap' 2>/dev/null; "
            "sleep 1; "
            "pkill -f ollama 2>/dev/null; "
            "echo 'System stopped'"
        )
        L.wsl_bg(stop_cmd, lambda o, e: self.after(0, lambda: self._log_output(o or e or "System stopped")))
