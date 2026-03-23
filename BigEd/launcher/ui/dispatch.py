"""
BigEd CC — Dispatch, prompt-queue, marathon, and search logic.
Extracted from launcher.py to reduce god-object complexity (TECH_DEBT 4.1).

Provides a DispatchMixin that is mixed into BigEdCC:
- Prompt Queue: _pq_toggle_add_row, _pq_add_item, _pq_remove_item,
                _pq_refresh_list, _pq_start, _pq_stop, _pq_run
- Dispatch:     _dispatch_task, _dispatch_raw
- Marathon:     _start_marathon, _show_marathon_log, _stop_marathon
- Idle:         _enable_idle, _disable_idle
- Search:       _open_search_dialog, _show_results
"""
import base64
import json
import subprocess
import threading
import time

import customtkinter as ctk
import psutil

from ui.theme import (
    BG, BG3, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED,
    FONT_SM, FONT_XS,
)


# ─── Lazy imports from launcher ──────────────────────────────────────────────

def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


# ─── Dispatch Mixin ─────────────────────────────────────────────────────────

class DispatchMixin:
    """Mixin providing dispatch, prompt-queue, marathon, and search for BigEdCC."""

    # ── Prompt Queue (Command Center sidebar) ───────────────────────────
    def _pq_toggle_add_row(self):
        """Show/hide the inline add row for the prompt queue."""
        if self._pq_add_visible:
            self._pq_add_frame.grid_forget()
            self._pq_add_visible = False
        else:
            self._pq_add_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=(2, 0))
            self._pq_add_visible = True
            self._pq_prompt_entry.focus_set()

    def _pq_add_item(self):
        """Add a prompt to the queue."""
        text = self._pq_prompt_entry.get().strip()
        if not text:
            return
        self._pq_items.append({"prompt": text, "skill": self._pq_skill_var.get()})
        self._pq_prompt_entry.delete(0, "end")
        self._pq_refresh_list()

    def _pq_remove_item(self, idx):
        """Remove item at index from the queue."""
        if 0 <= idx < len(self._pq_items):
            self._pq_items.pop(idx)
            self._pq_refresh_list()

    def _pq_refresh_list(self):
        """Rebuild the queue list display."""
        for w in self._pq_list_frame.winfo_children():
            w.destroy()
        if not self._pq_items:
            self._pq_empty_lbl = ctk.CTkLabel(
                self._pq_list_frame, text="Queue empty — click + Add",
                font=FONT_XS, text_color=DIM)
            self._pq_empty_lbl.pack(pady=8)
            return
        for i, item in enumerate(self._pq_items):
            row = ctk.CTkFrame(self._pq_list_frame, fg_color="transparent")
            row.pack(fill="x", padx=2, pady=1)
            row.grid_columnconfigure(2, weight=1)
            ctk.CTkLabel(row, text=f"{i+1}.", font=FONT_XS, text_color=DIM,
                         width=16).grid(row=0, column=0, padx=(2, 1))
            ctk.CTkLabel(row, text=f"[{item['skill']}]", font=FONT_XS,
                         text_color=GOLD, width=80, anchor="w"
                         ).grid(row=0, column=1, padx=(0, 2))
            ctk.CTkLabel(row, text=item['prompt'][:50], font=FONT_XS,
                         text_color=TEXT, anchor="w"
                         ).grid(row=0, column=2, sticky="ew")
            ctk.CTkButton(row, text="x", width=18, height=16, font=FONT_XS,
                          fg_color=BG, hover_color="#5a2020",
                          command=lambda idx=i: self._pq_remove_item(idx)
                          ).grid(row=0, column=3, padx=(2, 2))

    def _pq_start(self):
        """Start dispatching the prompt queue in a background thread."""
        if not self._pq_items:
            return
        try:
            loops = int(self._pq_loop_var.get())
        except ValueError:
            loops = 1
        self._pq_max_loops = loops
        self._pq_loop_count = 0
        self._pq_running = True
        self._pq_start_btn.configure(state="disabled")
        self._pq_stop_btn.configure(state="normal")
        threading.Thread(target=self._pq_run, daemon=True).start()

    def _pq_stop(self):
        """Stop the running prompt queue."""
        self._pq_running = False
        self._pq_start_btn.configure(state="normal")
        self._pq_stop_btn.configure(state="disabled")
        self._safe_after(0, lambda: self._pq_status_lbl.configure(text="Stopped"))

    def _pq_run(self):
        """Background thread: dispatch prompts round-robin via db.post_task."""
        try:
            import db
        except ImportError:
            self._pq_running = False
            self._safe_after(0, lambda: self._pq_status_lbl.configure(
                text="Error: db not available"))
            return

        idx = 0
        while self._pq_running:
            if not self._pq_items:
                break
            n_items = len(self._pq_items)
            item = self._pq_items[idx % n_items]
            loop_num = self._pq_loop_count + 1
            item_num = (idx % n_items) + 1
            try:
                db.post_task(item['skill'], json.dumps({"prompt": item['prompt']}))
                status_text = f"Running {item_num}/{n_items}, loop {loop_num}"
                if self._pq_max_loops > 0:
                    status_text += f"/{self._pq_max_loops}"
                self._safe_after(0, lambda t=status_text: self._pq_status_lbl.configure(
                    text=t, text_color=GREEN))
            except Exception as e:
                err = f"Dispatch error: {str(e)[:30]}"
                self._safe_after(0, lambda t=err: self._pq_status_lbl.configure(
                    text=t, text_color=RED))

            idx += 1
            if idx % n_items == 0:
                self._pq_loop_count += 1
                if self._pq_max_loops > 0 and self._pq_loop_count >= self._pq_max_loops:
                    break

            time.sleep(2)  # Pace between dispatches

        self._pq_running = False
        final = f"Done ({self._pq_loop_count} loop{'s' if self._pq_loop_count != 1 else ''})"
        self._safe_after(0, lambda: (
            self._pq_start_btn.configure(state="normal"),
            self._pq_stop_btn.configure(state="disabled"),
            self._pq_status_lbl.configure(text=final, text_color=DIM),
        ))

    # ── Search ────────────────────────────────────────────────────────────
    def _open_search_dialog(self):
        dialog = ctk.CTkInputDialog(
            text="Search query:", title="Web Search")
        q = dialog.get_input()
        if q:
            self._dispatch_raw("web_search", json.dumps({"query": q, "source": "gui"}),
                               None, f"Searching: {q}")

    def _show_results(self):
        L = _launcher()
        if not L.REPORTS_DIR.exists():
            self._log_output("No reports yet.")
            return
        files = sorted(L.REPORTS_DIR.glob("*.md"), reverse=True)[:3]
        if not files:
            self._log_output("No reports in knowledge/reports/")
            return
        text = ""
        for f in files:
            text += f"\n{'─'*60}\n{f.name}\n{'─'*60}\n"
            text += f.read_text(encoding="utf-8", errors="ignore")[:600] + "\n"
        self._log_output(text)

    # ── Marathon ──────────────────────────────────────────────────────────
    def _start_marathon(self):
        L = _launcher()
        # Check if already running first via psutil
        def _bg():
            marathon_pid = None
            for proc in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline') or []
                    if 'dispatch_marathon.py' in ' '.join(cmdline):
                        marathon_pid = proc.info['pid']
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if marathon_pid:
                self._safe_after(0, lambda: self._log_output(
                    f"Marathon already running (PID {marathon_pid}).\n"
                    f"Use '\U0001f4cb Marathon Log' to see progress, or '\u23f9 Stop Marathon' first."))
                return
            # Not running — launch it natively
            try:
                import shutil
                uv = shutil.which("uv")
                if uv:
                    cmd = [uv, "run", "python", "dispatch_marathon.py"]
                else:
                    cmd = [L._get_fleet_python(), "dispatch_marathon.py"]
                log_path = L.FLEET_DIR / "logs" / "marathon.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a") as log_f:
                    proc = subprocess.Popen(
                        cmd, cwd=str(L.FLEET_DIR),
                        stdout=log_f, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
                self._safe_after(0, lambda: self._log_output(
                    f"Marathon launched (PID: {proc.pid}).\n"
                    f"Phases: wait-for-idle \u2192 8 discussion rounds (40 min apart) "
                    f"\u2192 lead research \u2192 synthesis.\n"
                    f"Use '\U0001f4cb Marathon Log' to monitor progress."))
            except Exception as e:
                self._safe_after(0, lambda: self._log_output(f"Marathon launch error: {e}"))
        threading.Thread(target=_bg, daemon=True).start()

    def _show_marathon_log(self):
        """Tail the marathon log and show it in the output panel (native file read)."""
        L = _launcher()
        def _bg():
            log_path = L.FLEET_DIR / "logs" / "marathon.log"
            try:
                if not log_path.exists():
                    raise FileNotFoundError
                text = log_path.read_text(encoding="utf-8", errors="ignore")
                lines = text.strip().splitlines()[-80:]  # last 80 lines
            except Exception:
                self._safe_after(0, lambda: self._log_output(
                    "marathon.log is empty or not found.\n"
                    "Start marathon first, or check fleet/logs/marathon.log."))
                return
            if not lines:
                self._safe_after(0, lambda: self._log_output(
                    "marathon.log is empty or not found.\n"
                    "Start marathon first, or check fleet/logs/marathon.log."))
                return
            key = [l for l in lines if any(
                kw in l for kw in ("Phase", "round", "Round", "Task", "Waiting",
                                   "Sleeping", "synthesis", "Marathon", "=====",
                                   "Error", "Traceback", "\u2713"))]
            summary = "\n".join(key[-10:]) if key else ""
            tail    = "\n".join(lines[-20:])
            sep     = "\u2500" * 40
            self._safe_after(0, lambda: self._log_output(
                f"{'=' * 40}\nMARATHON LOG\n{'=' * 40}\n"
                + (f"[Key events]\n{summary}\n\n{sep}\n" if summary else "")
                + f"[Last 20 lines]\n{tail}"))
        threading.Thread(target=_bg, daemon=True).start()

    def _stop_marathon(self):
        def _bg():
            from ui.boot import _kill_fleet_processes
            killed = _kill_fleet_processes(["dispatch_marathon.py"])
            if killed:
                msg = f"Stopped marathon: {', '.join(killed)}"
            else:
                msg = "No marathon process found"
            self._safe_after(0, lambda: self._log_output(msg))
        threading.Thread(target=_bg, daemon=True).start()

    # ── Idle control ──────────────────────────────────────────────────────
    def _enable_idle(self):
        self._dispatch_raw("summarize", '{"description":"idle enabled confirmation"}',
                           None, None)
        self._log_output("Enable idle: edit fleet.toml \u2192 set idle_enabled = true, then restart fleet.")

    def _disable_idle(self):
        self._log_output("Idle already disabled by default (idle_enabled = false in fleet.toml).")

    # ── Task dispatch ─────────────────────────────────────────────────────
    def _dispatch_task(self):
        L = _launcher()
        text = self._task_entry.get("1.0", "end-1c").strip()
        if not text:
            return
        self._task_entry.delete("1.0", "end")
        self._task_status.configure(text="\u231b dispatching...", text_color=ORANGE)
        self._log_output(f"\u2192 {text}")

        def _bg():
            r = None
            try:
                import shutil
                uv = shutil.which("uv")
                if uv:
                    cmd = [uv, "run", "python", "lead_client.py", "task", text, "--wait"]
                else:
                    cmd = [L._get_fleet_python(), "lead_client.py", "task", text, "--wait"]
                r = subprocess.run(
                    cmd, cwd=str(L.FLEET_DIR),
                    capture_output=True, text=True, timeout=300,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                out, err = r.stdout.strip(), r.stderr.strip()
            except Exception as e:
                out, err = "", str(e)
            def _update():
                result = out or err or "(no output)"
                self._log_output(f"\u2190 {result[:1200]}")
                self._task_status.configure(text="\u2713 done", text_color=GREEN)
                self._safe_after(3000, lambda: self._task_status.configure(text=""))
                # Toast notification for task completion
                if r and r.returncode == 0:
                    self._show_toast(f"\u2713 Task done: {text[:40]}", GREEN)
                else:
                    self._show_toast(f"\u2717 Task failed: {text[:40]}", RED, duration=8000)
            self._safe_after(0, _update)
        threading.Thread(target=_bg, daemon=True).start()

    def _dispatch_raw(self, skill: str, payload_json: str, assigned_to=None, msg=None):
        L = _launcher()
        if msg:
            self._log_output(msg)
        b64 = base64.b64encode(payload_json.encode()).decode()
        def _bg():
            try:
                import shutil
                uv = shutil.which("uv")
                base = uv if uv else L._get_fleet_python()
                cmd = [base] + (["run", "python"] if uv else [])
                cmd += ["lead_client.py", "dispatch", skill, b64, "--b64", "--priority", "9"]
                if assigned_to:
                    cmd += ["--assigned-to", assigned_to]
                r = subprocess.run(
                    cmd, cwd=str(L.FLEET_DIR),
                    capture_output=True, text=True, timeout=60,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                out, err = r.stdout.strip(), r.stderr.strip()
            except Exception as e:
                out, err = "", str(e)
            self._safe_after(0, lambda: self._log_output(out or err))
        threading.Thread(target=_bg, daemon=True).start()
