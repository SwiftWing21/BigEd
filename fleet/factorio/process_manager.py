"""Factorio Process Manager — standalone GUI for server + bridge lifecycle.

Usage: python fleet/factorio/process_manager.py
"""
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

FLEET_DIR = Path(__file__).resolve().parent.parent
PID_FILE = Path(__file__).parent / "server_data" / "pids.json"
LOCK_FILE = Path(__file__).parent / "server_data" / ".lock"


def _find_processes():
    """Find running Factorio processes."""
    import psutil
    procs = {"server": [], "bridge": [], "launcher": []}
    for p in psutil.process_iter(["pid", "cmdline", "name", "create_time"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            name = (p.info.get("name") or "").lower()
            if "factorio.bridge" in cmd:
                procs["bridge"].append(p)
            elif "factorio.setup_and_launch" in cmd:
                procs["launcher"].append(p)
            elif name.startswith("factorio") and "--start-server" in cmd:
                procs["server"].append(p)
        except Exception:
            pass
    return procs


def _kill_all():
    """Kill all Factorio processes and clean up."""
    import psutil
    killed = []
    # Graceful bridge shutdown
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:27016/api/shutdown", method="POST")
        urllib.request.urlopen(req, timeout=2)
        time.sleep(1)
    except Exception:
        pass
    # Kill by scan
    procs = _find_processes()
    for category in ["bridge", "launcher", "server"]:
        for p in procs[category]:
            try:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except psutil.TimeoutExpired:
                    p.kill()
                killed.append(f"{category} PID {p.pid}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    # Clean lock + PID file
    for f in [LOCK_FILE, PID_FILE]:
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass
    return killed


def _start_all():
    """Start server + bridge via setup_and_launch."""
    # Clean first
    _kill_all()
    time.sleep(2)
    # Clean lock
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    proc = subprocess.Popen(
        [sys.executable, "-m", "factorio.setup_and_launch"],
        cwd=str(FLEET_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    return proc.returncode


def _restart_bridge():
    """Restart just the bridge (server stays up)."""
    import psutil
    # Kill bridge
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:27016/api/shutdown", method="POST")
        urllib.request.urlopen(req, timeout=2)
        time.sleep(1)
    except Exception:
        pass
    for p in _find_processes()["bridge"]:
        try:
            p.kill()
        except Exception:
            pass
    time.sleep(1)
    # Start bridge
    subprocess.Popen(
        [sys.executable, "-m", "factorio.bridge"],
        cwd=str(FLEET_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _get_bridge_status():
    """Check bridge API status."""
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:27016/api/status", timeout=2)
        return json.loads(resp.read())
    except Exception:
        return None


def _get_training_status():
    """Check training status."""
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:27016/api/training/status", timeout=2)
        return json.loads(resp.read())
    except Exception:
        return None


def _get_plan_status():
    """Check plan queue + history from bridge."""
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:27016/api/plan/queue", timeout=2)
        queue_data = json.loads(resp.read())
        resp2 = urllib.request.urlopen("http://127.0.0.1:27016/api/plan/history", timeout=2)
        history_data = json.loads(resp2.read())
        queued = len(queue_data.get("queued", []))
        has_current = 1 if queue_data.get("current") else 0
        completed = len(history_data.get("history", []))
        return {"queued": queued, "active": has_current, "completed": completed}
    except Exception:
        return None


class ProcessManagerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Factorio Process Manager")
        self.root.geometry("420x400")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        self._build_ui()
        self._refresh()

    def _build_ui(self):
        bg = "#1a1a2e"
        fg = "#e0e0e0"
        green = "#00d26a"
        red = "#ff4444"
        yellow = "#ffbb33"
        btn_bg = "#16213e"

        # Title
        tk.Label(self.root, text="Factorio Processes", font=("Consolas", 14, "bold"),
                 bg=bg, fg=fg).pack(pady=(10, 5))

        # Status frame
        status_frame = tk.Frame(self.root, bg="#0f3460", padx=10, pady=8)
        status_frame.pack(fill="x", padx=10, pady=5)

        self.server_label = tk.Label(status_frame, text="Server: ...", font=("Consolas", 11),
                                     bg="#0f3460", fg=fg, anchor="w")
        self.server_label.pack(fill="x")

        self.bridge_label = tk.Label(status_frame, text="Bridge: ...", font=("Consolas", 11),
                                     bg="#0f3460", fg=fg, anchor="w")
        self.bridge_label.pack(fill="x")

        self.rcon_label = tk.Label(status_frame, text="RCON: ...", font=("Consolas", 11),
                                   bg="#0f3460", fg=fg, anchor="w")
        self.rcon_label.pack(fill="x")

        self.training_label = tk.Label(status_frame, text="Training: ...", font=("Consolas", 11),
                                       bg="#0f3460", fg=fg, anchor="w")
        self.training_label.pack(fill="x")

        self.lesson_label = tk.Label(status_frame, text="", font=("Consolas", 10),
                                     bg="#0f3460", fg=yellow, anchor="w")
        self.lesson_label.pack(fill="x")

        self.plans_label = tk.Label(status_frame, text="", font=("Consolas", 10),
                                    bg="#0f3460", fg=fg, anchor="w")
        self.plans_label.pack(fill="x")

        # Orphan warning
        self.orphan_label = tk.Label(self.root, text="", font=("Consolas", 10),
                                     bg=bg, fg=red)
        self.orphan_label.pack(pady=2)

        # Buttons
        btn_frame = tk.Frame(self.root, bg=bg)
        btn_frame.pack(pady=10)

        btn_style = {"font": ("Consolas", 11), "width": 16, "height": 1,
                     "bg": btn_bg, "fg": fg, "activebackground": "#1a1a3e",
                     "activeforeground": fg, "relief": "flat", "cursor": "hand2"}

        tk.Button(btn_frame, text="Start All", command=self._on_start,
                  **btn_style).grid(row=0, column=0, padx=5, pady=3)
        tk.Button(btn_frame, text="Stop All", command=self._on_stop,
                  **btn_style).grid(row=0, column=1, padx=5, pady=3)
        tk.Button(btn_frame, text="Restart Bridge", command=self._on_restart_bridge,
                  **btn_style).grid(row=1, column=0, padx=5, pady=3)
        tk.Button(btn_frame, text="Restart All", command=self._on_restart,
                  **btn_style).grid(row=1, column=1, padx=5, pady=3)

        # Log
        self.log_text = tk.Text(self.root, height=4, font=("Consolas", 9),
                                bg="#0a0a1a", fg="#888", insertbackground=fg,
                                relief="flat", state="disabled")
        self.log_text.pack(fill="x", padx=10, pady=(5, 10))

    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')} {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh(self):
        """Poll process status every 3 seconds."""
        try:
            procs = _find_processes()
            green = "#00d26a"
            red = "#ff4444"
            gray = "#666666"

            # Server
            n_server = len(procs["server"])
            if n_server == 1:
                pid = procs["server"][0].pid
                self.server_label.configure(text=f"Server:  PID {pid}", fg=green)
            elif n_server > 1:
                self.server_label.configure(text=f"Server:  {n_server} instances!", fg=red)
            else:
                self.server_label.configure(text="Server:  stopped", fg=gray)

            # Bridge
            n_bridge = len(procs["bridge"])
            if n_bridge == 1:
                pid = procs["bridge"][0].pid
                self.bridge_label.configure(text=f"Bridge:  PID {pid}", fg=green)
            elif n_bridge > 1:
                self.bridge_label.configure(text=f"Bridge:  {n_bridge} instances!", fg=red)
            else:
                self.bridge_label.configure(text="Bridge:  stopped", fg=gray)

            # Orphan warning
            n_launcher = len(procs["launcher"])
            total_orphans = max(0, n_bridge - 1) + max(0, n_server - 1) + n_launcher
            if total_orphans > 0:
                self.orphan_label.configure(
                    text=f"! {total_orphans} orphan process(es) — use Stop All")
            else:
                self.orphan_label.configure(text="")

            # RCON + training
            bridge_status = _get_bridge_status()
            if bridge_status:
                running = bridge_status.get("running", False)
                tick = bridge_status.get("tick", 0)
                comps = bridge_status.get("components", {})
                rcon_ok = comps.get("rcon", running)
                self.rcon_label.configure(
                    text=f"RCON:    {'connected' if rcon_ok else 'disconnected'} (tick {tick:,})",
                    fg=green if rcon_ok else red)
            else:
                self.rcon_label.configure(text="RCON:    no bridge API", fg=gray)

            ts = _get_training_status()
            if ts and ts.get("mode"):
                mode = ts.get("mode", "?")
                ep = ts.get("episode", 0)
                step = ts.get("step", 0)
                lessons = ts.get("lessons_completed", 0)
                total = ts.get("lessons_total", 0)
                self.training_label.configure(
                    text=f"Train:   {mode} ep{ep} step{step:,}",
                    fg=green)
                self.lesson_label.configure(
                    text=f"         Lessons: {lessons}/{total}")
            else:
                self.training_label.configure(text="Train:   inactive", fg=gray)
                self.lesson_label.configure(text="")

            # Plan queue
            ps = _get_plan_status()
            if ps is not None:
                active = ps["active"]
                queued = ps["queued"]
                done = ps["completed"]
                self.plans_label.configure(
                    text=f"Plans:   {queued} queued / {active} active — {done} done",
                    fg=green if active else gray)
            else:
                self.plans_label.configure(text="Plans:   n/a", fg=gray)

        except Exception as e:
            self.server_label.configure(text=f"Error: {e}", fg="#ff4444")

        self.root.after(3000, self._refresh)

    def _run_in_thread(self, fn, label):
        """Run a blocking operation in a thread."""
        self._log(f"{label}...")

        def worker():
            try:
                result = fn()
                self.root.after(0, lambda: self._log(f"{label}: done ({result})"))
            except Exception as e:
                self.root.after(0, lambda: self._log(f"{label}: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_start(self):
        self._run_in_thread(_start_all, "Start All")

    def _on_stop(self):
        self._run_in_thread(lambda: _kill_all(), "Stop All")

    def _on_restart_bridge(self):
        self._run_in_thread(_restart_bridge, "Restart Bridge")

    def _on_restart(self):
        self._run_in_thread(_start_all, "Restart All")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    sys.path.insert(0, str(FLEET_DIR))
    ProcessManagerApp().run()
