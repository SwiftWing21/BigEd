"""BigEd CC -- Submit Issue dialog.

Full-featured issue reporter with type selection (Bug, Feature, Feedback,
Module Submission), automatic system-info population, error-log loading,
API-key scrubbing, and dual submission path (gh CLI -> browser fallback).

Module submissions route to SwiftWing21/BigEd-ModuleHub.
All other issues route to SwiftWing21/BigEd.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, FONT, FONT_SM, FONT_H, FONT_XS,
    BTN_RADIUS, CARD_RADIUS, MONO,
)

# ── Late-bound refs (injected by launcher.py) ────────────────────────────────
HERE: Optional[Path] = None
FLEET_DIR: Optional[Path] = None
LOGS_DIR: Optional[Path] = None
MANIFEST_PATH: Optional[Path] = None
_get_version = None  # callable -> str


def _init_submit_issue_refs(here, fleet_dir, logs_dir, manifest_path,
                            get_version_fn):
    """Called once from launcher.py to inject paths without circular imports."""
    global HERE, FLEET_DIR, LOGS_DIR, MANIFEST_PATH, _get_version
    HERE = here
    FLEET_DIR = fleet_dir
    LOGS_DIR = logs_dir
    MANIFEST_PATH = manifest_path
    _get_version = get_version_fn


# ── Constants ─────────────────────────────────────────────────────────────────
GITHUB_OWNER = "SwiftWing21"
REPO_BIGED = "BigEd"
REPO_MODULEHUB = "BigEd-ModuleHub"

ISSUE_TYPES = ["Bug Report", "Feature Request", "Feedback", "Module Submission"]

KEY_PATTERNS = [
    r'sk-ant-[A-Za-z0-9_-]{20,}',           # Anthropic
    r'sk-[A-Za-z0-9_-]{20,}',                # OpenAI-style
    r'AI[A-Za-z0-9_-]{20,}',                 # Gemini
    r'ghp_[A-Za-z0-9]{36}',                  # GitHub PAT
    r'github_pat_[A-Za-z0-9_]{20,}',         # GitHub fine-grained
    r'xoxb-[A-Za-z0-9-]+',                   # Slack
    r'BSA[A-Za-z0-9]{20,}',                  # Brave
    r'(?:KEY|TOKEN|SECRET)=[A-Za-z0-9_-]{32,}',  # Generic
]
_KEY_RE = re.compile("|".join(f"({p})" for p in KEY_PATTERNS))

REPRO_TEMPLATE = (
    "1. \n"
    "2. \n"
    "3. \n"
    "Expected: \n"
    "Actual: \n"
)

# Star labels for the feedback rating
_STAR_LABELS = {1: "Poor", 2: "Fair", 3: "Good", 4: "Great", 5: "Excellent"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scrub_keys(text: str) -> str:
    """Replace any API-key-shaped tokens with [REDACTED]."""
    return _KEY_RE.sub("[REDACTED]", text)


def _load_recent_log(logs_dir: Optional[Path], lines: int = 50) -> str:
    """Return the last *lines* of the most recently modified log file."""
    if not logs_dir or not logs_dir.is_dir():
        return "(no logs directory found)"
    log_files = sorted(
        [f for f in logs_dir.iterdir() if f.suffix == ".log" and f.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not log_files:
        return "(no log files found)"
    try:
        all_lines = log_files[0].read_text(encoding="utf-8", errors="replace").splitlines()
        tail = all_lines[-lines:]
        return f"[{log_files[0].name}]\n" + "\n".join(tail)
    except Exception as exc:
        return f"(error reading log: {exc})"


def _collect_system_info() -> str:
    """Build a compact system-info block for bug reports."""
    parts: list[str] = []
    try:
        import platform as _plat
        parts.append(f"OS: {_plat.system()} {_plat.version()} ({_plat.machine()})")
        parts.append(f"Python: {_plat.python_version()}")
    except Exception:
        parts.append("OS/Python: unknown")
    # BigEd version
    try:
        ver = _get_version() if _get_version else "unknown"
        parts.append(f"BigEd CC: {ver}")
    except Exception:
        parts.append("BigEd CC: unknown")
    # Ollama
    try:
        import urllib.request
        import json as _json
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
            data = _json.loads(r.read())
            models = [m["name"] for m in data.get("models", [])]
            parts.append(f"Ollama: running ({len(models)} model(s))")
    except Exception:
        parts.append("Ollama: not detected")
    # RAM / GPU
    try:
        import psutil
        ram = psutil.virtual_memory()
        parts.append(f"RAM: {round(ram.total / (1024**3), 1)} GB")
    except Exception:
        pass
    return "\n".join(parts)


def _load_modules() -> list[str]:
    """Read module names from manifest.json."""
    if not MANIFEST_PATH or not MANIFEST_PATH.is_file():
        return []
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        return [m["name"] for m in data.get("modules", []) if isinstance(m, dict)]
    except Exception:
        return []


def _save_local_copy(issue_data: dict, fleet_dir: Optional[Path]) -> Optional[Path]:
    """Persist a JSON copy under fleet/knowledge/reports/."""
    target_dir = (fleet_dir / "knowledge" / "reports") if fleet_dir else None
    if not target_dir:
        return None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = target_dir / f"issue_{ts}.json"
        path.write_text(json.dumps(issue_data, indent=2, default=str),
                        encoding="utf-8")
        return path
    except Exception:
        return None


def _take_screenshot() -> Optional[Path]:
    """Capture a screenshot and return the saved path (temp file)."""
    try:
        from PIL import ImageGrab
        import tempfile
        img = ImageGrab.grab()
        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="biged_issue_")
        os.close(fd)
        img.save(tmp)
        return Path(tmp)
    except Exception:
        return None


# ── Dialog ────────────────────────────────────────────────────────────────────

class SubmitIssueDialog(ctk.CTkToplevel):
    """Modal dialog for submitting GitHub issues from the launcher."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC -- Submit Issue")
        self.geometry("640x700")
        self.minsize(580, 620)
        self.configure(fg_color=BG)
        self.grab_set()
        self.focus_force()

        # Icon
        if HERE:
            ico = HERE / "brick.ico"
            if ico.exists():
                try:
                    self.iconbitmap(str(ico))
                except Exception:
                    pass

        self._screenshot_path: Optional[Path] = None
        self._submitting = False

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI Construction ───────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Header ────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=48, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        ctk.CTkLabel(
            hdr, text="SUBMIT ISSUE",
            font=FONT_H, text_color=GOLD,
        ).pack(side="left", padx=14, pady=10)

        # ── Scrollable body ───────────────────────────────────────────────
        body = ctk.CTkScrollableFrame(self, fg_color=BG, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        body.grid_columnconfigure(0, weight=1)

        row = 0

        # ── Type selector ─────────────────────────────────────────────────
        ctk.CTkLabel(body, text="Issue Type", font=FONT_SM, text_color=DIM
                     ).grid(row=row, column=0, sticky="w", padx=14, pady=(12, 2))
        row += 1

        self._type_var = ctk.StringVar(value=ISSUE_TYPES[0])
        seg = ctk.CTkSegmentedButton(
            body, values=ISSUE_TYPES, variable=self._type_var,
            font=FONT_SM, corner_radius=BTN_RADIUS,
            fg_color=BG2, selected_color=ACCENT, selected_hover_color=ACCENT_H,
            unselected_color=BG3, unselected_hover_color=BG2,
            text_color=TEXT, command=self._on_type_change,
        )
        seg.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 8))
        row += 1

        # ── Title ─────────────────────────────────────────────────────────
        ctk.CTkLabel(body, text="Title", font=FONT_SM, text_color=DIM
                     ).grid(row=row, column=0, sticky="w", padx=14, pady=(8, 2))
        row += 1

        self._title_entry = ctk.CTkEntry(
            body, placeholder_text="Brief summary of the issue",
            font=FONT_SM, fg_color=BG2, border_color=BG3, text_color=TEXT,
            corner_radius=BTN_RADIUS, height=32,
        )
        self._title_entry.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 8))
        row += 1

        # ── Description ───────────────────────────────────────────────────
        ctk.CTkLabel(body, text="Description", font=FONT_SM, text_color=DIM
                     ).grid(row=row, column=0, sticky="w", padx=14, pady=(8, 2))
        row += 1

        self._desc_text = ctk.CTkTextbox(
            body, height=130, font=FONT_SM,
            fg_color=BG2, border_color=BG3, text_color=TEXT,
            corner_radius=BTN_RADIUS,
        )
        self._desc_text.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 8))
        row += 1

        # ── Bug-specific section ──────────────────────────────────────────
        self._bug_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._bug_frame.grid(row=row, column=0, sticky="ew", padx=0, pady=0)
        self._bug_frame.grid_columnconfigure(0, weight=1)
        self._bug_row = row
        row += 1

        self._build_bug_section(self._bug_frame)

        # ── Feedback-specific section ─────────────────────────────────────
        self._feedback_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._feedback_frame.grid(row=row, column=0, sticky="ew", padx=0, pady=0)
        self._feedback_frame.grid_columnconfigure(0, weight=1)
        self._feedback_row = row
        row += 1

        self._build_feedback_section(self._feedback_frame)

        # ── Module submission section ─────────────────────────────────────
        self._module_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._module_frame.grid(row=row, column=0, sticky="ew", padx=0, pady=0)
        self._module_frame.grid_columnconfigure(0, weight=1)
        self._module_row = row
        row += 1

        self._build_module_section(self._module_frame)

        # ── Screenshot ────────────────────────────────────────────────────
        ss_row = ctk.CTkFrame(body, fg_color="transparent")
        ss_row.grid(row=row, column=0, sticky="ew", padx=14, pady=(8, 4))
        ss_row.grid_columnconfigure(1, weight=1)
        row += 1

        self._btn_screenshot = ctk.CTkButton(
            ss_row, text="Attach Screenshot", width=140, height=28,
            font=FONT_SM, fg_color=BG3, hover_color=BG2, text_color=TEXT,
            corner_radius=BTN_RADIUS, command=self._attach_screenshot,
        )
        self._btn_screenshot.grid(row=0, column=0, sticky="w")

        self._screenshot_lbl = ctk.CTkLabel(
            ss_row, text="No screenshot attached", font=FONT_XS,
            text_color=DIM,
        )
        self._screenshot_lbl.grid(row=0, column=1, sticky="w", padx=(8, 0))

        # ── Footer (status + submit) ──────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        footer.grid(row=2, column=0, sticky="sew")
        footer.grid_propagate(False)
        footer.grid_columnconfigure(0, weight=1)

        self._status_lbl = ctk.CTkLabel(
            footer, text="", font=FONT_XS, text_color=DIM,
        )
        self._status_lbl.grid(row=0, column=0, sticky="w", padx=14, pady=10)

        self._btn_submit = ctk.CTkButton(
            footer, text="Submit", width=110, height=32,
            font=FONT_SM, fg_color=ACCENT, hover_color=ACCENT_H,
            text_color=TEXT, corner_radius=BTN_RADIUS,
            command=self._submit,
        )
        self._btn_submit.grid(row=0, column=1, sticky="e", padx=14, pady=10)

        # Show correct sections for initial type
        self._on_type_change(ISSUE_TYPES[0])

    # ── Bug section ───────────────────────────────────────────────────────

    def _build_bug_section(self, parent):
        r = 0
        # System info checkbox
        self._sysinfo_var = ctk.BooleanVar(value=True)
        self._sysinfo_cb = ctk.CTkCheckBox(
            parent, text="Include System Info", variable=self._sysinfo_var,
            font=FONT_SM, text_color=TEXT, fg_color=ACCENT,
            hover_color=ACCENT_H, corner_radius=BTN_RADIUS,
        )
        self._sysinfo_cb.grid(row=r, column=0, sticky="w", padx=14, pady=(8, 4))
        r += 1

        # System info preview
        ctk.CTkLabel(parent, text="System Info (auto-detected)", font=FONT_XS,
                     text_color=DIM).grid(row=r, column=0, sticky="w", padx=14, pady=(4, 2))
        r += 1

        self._sysinfo_text = ctk.CTkTextbox(
            parent, height=80, font=MONO, fg_color=BG2,
            border_color=BG3, text_color=DIM, corner_radius=BTN_RADIUS,
        )
        self._sysinfo_text.grid(row=r, column=0, sticky="ew", padx=14, pady=(0, 8))
        self._sysinfo_text.insert("1.0", _collect_system_info())
        self._sysinfo_text.configure(state="disabled")
        r += 1

        # Repro steps
        ctk.CTkLabel(parent, text="Reproduction Steps", font=FONT_SM,
                     text_color=DIM).grid(row=r, column=0, sticky="w", padx=14, pady=(4, 2))
        r += 1

        self._repro_text = ctk.CTkTextbox(
            parent, height=100, font=FONT_SM, fg_color=BG2,
            border_color=BG3, text_color=TEXT, corner_radius=BTN_RADIUS,
        )
        self._repro_text.grid(row=r, column=0, sticky="ew", padx=14, pady=(0, 4))
        self._repro_text.insert("1.0", REPRO_TEMPLATE)
        r += 1

        # Error log viewer
        ctk.CTkLabel(parent, text="Recent Error Log (last 50 lines)", font=FONT_XS,
                     text_color=DIM).grid(row=r, column=0, sticky="w", padx=14, pady=(8, 2))
        r += 1

        self._log_text = ctk.CTkTextbox(
            parent, height=100, font=MONO, fg_color=BG2,
            border_color=BG3, text_color=DIM, corner_radius=BTN_RADIUS,
        )
        self._log_text.grid(row=r, column=0, sticky="ew", padx=14, pady=(0, 8))
        log_content = _load_recent_log(LOGS_DIR)
        self._log_text.insert("1.0", log_content)
        self._log_text.configure(state="disabled")

    # ── Feedback section ──────────────────────────────────────────────────

    def _build_feedback_section(self, parent):
        r = 0
        ctk.CTkLabel(parent, text="Rating", font=FONT_SM, text_color=DIM
                     ).grid(row=r, column=0, sticky="w", padx=14, pady=(8, 2))
        r += 1

        rating_frame = ctk.CTkFrame(parent, fg_color="transparent")
        rating_frame.grid(row=r, column=0, sticky="w", padx=14, pady=(0, 8))

        self._rating_var = ctk.IntVar(value=0)
        self._star_btns: list[ctk.CTkButton] = []
        for i in range(1, 6):
            btn = ctk.CTkButton(
                rating_frame, text=str(i), width=40, height=32,
                font=FONT_SM, fg_color=BG3, hover_color=BG2,
                text_color=TEXT, corner_radius=BTN_RADIUS,
                command=lambda val=i: self._set_rating(val),
            )
            btn.pack(side="left", padx=2)
            self._star_btns.append(btn)

        self._rating_lbl = ctk.CTkLabel(
            rating_frame, text="(select a rating)", font=FONT_XS,
            text_color=DIM,
        )
        self._rating_lbl.pack(side="left", padx=(10, 0))

    def _set_rating(self, value: int):
        self._rating_var.set(value)
        for i, btn in enumerate(self._star_btns):
            if i < value:
                btn.configure(fg_color=GOLD, text_color=BG)
            else:
                btn.configure(fg_color=BG3, text_color=TEXT)
        label = _STAR_LABELS.get(value, "")
        self._rating_lbl.configure(text=label)

    # ── Module submission section ─────────────────────────────────────────

    def _build_module_section(self, parent):
        r = 0
        ctk.CTkLabel(parent, text="Module", font=FONT_SM, text_color=DIM
                     ).grid(row=r, column=0, sticky="w", padx=14, pady=(8, 2))
        r += 1

        modules = _load_modules()
        choices = modules + ["New Module"] if modules else ["New Module"]
        self._module_var = ctk.StringVar(value=choices[0])
        self._module_dropdown = ctk.CTkOptionMenu(
            parent, values=choices, variable=self._module_var,
            font=FONT_SM, fg_color=BG2, button_color=BG3,
            button_hover_color=ACCENT_H, text_color=TEXT,
            dropdown_fg_color=BG2, dropdown_text_color=TEXT,
            dropdown_hover_color=BG3, corner_radius=BTN_RADIUS,
            width=300,
        )
        self._module_dropdown.grid(row=r, column=0, sticky="w", padx=14, pady=(0, 4))
        r += 1

        self._module_note = ctk.CTkLabel(
            parent,
            text="Module submissions are filed on the BigEd-ModuleHub repository.",
            font=FONT_XS, text_color=ORANGE,
        )
        self._module_note.grid(row=r, column=0, sticky="w", padx=14, pady=(0, 8))

    # ── Visibility toggling ───────────────────────────────────────────────

    def _on_type_change(self, value: str):
        """Show/hide type-specific sections."""
        is_bug = value == "Bug Report"
        is_feedback = value == "Feedback"
        is_module = value == "Module Submission"

        if is_bug:
            self._bug_frame.grid()
        else:
            self._bug_frame.grid_remove()

        if is_feedback:
            self._feedback_frame.grid()
        else:
            self._feedback_frame.grid_remove()

        if is_module:
            self._module_frame.grid()
        else:
            self._module_frame.grid_remove()

    # ── Screenshot ────────────────────────────────────────────────────────

    def _attach_screenshot(self):
        """Capture a screenshot in a background thread."""
        self._btn_screenshot.configure(state="disabled", text="Capturing...")
        threading.Thread(target=self._do_screenshot, daemon=True).start()

    def _do_screenshot(self):
        path = _take_screenshot()
        self.after(0, self._screenshot_done, path)

    def _screenshot_done(self, path: Optional[Path]):
        self._btn_screenshot.configure(state="normal", text="Attach Screenshot")
        if path and path.exists():
            self._screenshot_path = path
            self._screenshot_lbl.configure(
                text=f"Attached: {path.name}", text_color=GREEN,
            )
        else:
            self._screenshot_lbl.configure(
                text="Screenshot capture failed", text_color=RED,
            )

    # ── Submission ────────────────────────────────────────────────────────

    def _submit(self):
        """Validate, scrub, and submit the issue."""
        if self._submitting:
            return

        title = self._title_entry.get().strip()
        if not title:
            self._set_status("Title is required.", RED)
            return

        desc = self._desc_text.get("1.0", "end").strip()
        issue_type = self._type_var.get()

        # Build body
        body_parts: list[str] = []

        if issue_type == "Bug Report":
            body_parts.append(f"**Type:** Bug Report\n")
            if self._sysinfo_var.get():
                si = self._sysinfo_text.get("1.0", "end").strip()
                body_parts.append(f"### System Info\n```\n{si}\n```\n")
            body_parts.append(f"### Description\n{desc}\n")
            repro = self._repro_text.get("1.0", "end").strip()
            if repro and repro != REPRO_TEMPLATE.strip():
                body_parts.append(f"### Reproduction Steps\n{repro}\n")
            log = self._log_text.get("1.0", "end").strip()
            if log and not log.startswith("(no log"):
                # Truncate to avoid hitting URL length limits
                truncated = log[-2000:] if len(log) > 2000 else log
                body_parts.append(
                    f"### Error Log (tail)\n```\n{truncated}\n```\n")

        elif issue_type == "Feature Request":
            body_parts.append(f"**Type:** Feature Request\n")
            body_parts.append(f"### Description\n{desc}\n")

        elif issue_type == "Feedback":
            rating = self._rating_var.get()
            label = _STAR_LABELS.get(rating, "unrated")
            body_parts.append(f"**Type:** Feedback\n")
            body_parts.append(f"**Rating:** {rating}/5 ({label})\n")
            body_parts.append(f"### Feedback\n{desc}\n")

        elif issue_type == "Module Submission":
            module = self._module_var.get()
            body_parts.append(f"**Type:** Module Submission\n")
            body_parts.append(f"**Module:** {module}\n")
            body_parts.append(f"### Description\n{desc}\n")

        body = "\n".join(body_parts)

        # ── Scrub ALL text fields ─────────────────────────────────────────
        title = _scrub_keys(title)
        body = _scrub_keys(body)

        # Determine target repo
        is_module = issue_type == "Module Submission"
        repo = f"{GITHUB_OWNER}/{REPO_MODULEHUB}" if is_module else f"{GITHUB_OWNER}/{REPO_BIGED}"

        # Label mapping
        label_map = {
            "Bug Report": "bug",
            "Feature Request": "enhancement",
            "Feedback": "feedback",
            "Module Submission": "module",
        }
        label = label_map.get(issue_type, "")

        # Local copy data
        issue_data = {
            "timestamp": datetime.now().isoformat(),
            "type": issue_type,
            "title": title,
            "body": body,
            "repo": repo,
            "label": label,
            "screenshot": str(self._screenshot_path) if self._screenshot_path else None,
        }

        # Save local copy first (always)
        local_path = _save_local_copy(issue_data, FLEET_DIR)

        # Submit in background
        self._submitting = True
        self._btn_submit.configure(state="disabled", text="Submitting...")
        self._set_status("Submitting issue...", DIM)

        threading.Thread(
            target=self._do_submit,
            args=(title, body, repo, label, local_path),
            daemon=True,
        ).start()

    def _do_submit(self, title: str, body: str, repo: str, label: str,
                   local_path: Optional[Path]):
        """Background: try gh CLI, fallback to browser."""
        success = False
        url = ""

        # Attempt 1: gh CLI
        try:
            cmd = [
                "gh", "issue", "create",
                "--repo", repo,
                "--title", title,
                "--body", body,
            ]
            if label:
                cmd.extend(["--label", label])
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                success = True
        except Exception:
            pass

        # Attempt 2: browser fallback
        if not success:
            try:
                params = {
                    "title": title[:200],  # URL length safety
                    "body": body[:4000],   # GitHub URL limit ~8KB
                }
                if label:
                    params["labels"] = label
                query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
                url = f"https://github.com/{repo}/issues/new?{query}"
                webbrowser.open(url)
                success = True  # We opened the browser; user will submit
            except Exception:
                pass

        self.after(0, self._submit_done, success, url, local_path)

    def _submit_done(self, success: bool, url: str, local_path: Optional[Path]):
        """UI callback after submission attempt."""
        self._submitting = False
        self._btn_submit.configure(state="normal", text="Submit")

        if success and url and url.startswith("http"):
            if "issues/new" in url:
                msg = "Opened in browser. Complete submission there."
            else:
                msg = f"Issue created: {url}"
            self._set_status(msg, GREEN)
        elif success:
            self._set_status("Opened in browser for submission.", GREEN)
        else:
            local_note = f" Local copy: {local_path}" if local_path else ""
            self._set_status(f"Submission failed. {local_note}", RED)

    # ── Utility ───────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str = DIM):
        self._status_lbl.configure(text=text, text_color=color)

    def _on_close(self):
        self.grab_release()
        self.destroy()
