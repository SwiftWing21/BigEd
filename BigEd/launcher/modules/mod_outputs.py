"""
Outputs Module — Knowledge browser for BigEd CC.

Browses fleet knowledge outputs: code reviews, security, quality, drafts, reports.
Cross-module: fed by Ingestion module via RAG index.
Human feedback: approve/reject agent outputs with optional notes.
"""
import sys
import threading
from pathlib import Path

import customtkinter as ctk

BG = BG2 = BG3 = ACCENT = ACCENT_H = GOLD = TEXT = DIM = GREEN = ORANGE = RED = ""
FONT_SM = FONT_STAT = FONT_BOLD = FONT_XS = ("Segoe UI", 10)
FLEET_DIR = None

# Feedback badge characters
_BADGE_APPROVED = "\u2714 "   # checkmark
_BADGE_REJECTED = "\u2718 "   # X mark


class Module:
    NAME = "outputs"
    LABEL = "Outputs"
    VERSION = "0.24"
    DEFAULT_ENABLED = True
    DEPENDS_ON = []

    _DIRS = {
        "All": None,
        "Code Reviews": "code_reviews",
        "Security": "security",
        "Quality": "quality",
        "Drafts": "code_drafts",
        "Reports": "reports",
        "Evaluations": "evaluations",
        "Stability": "stability",
        "Summaries": "summaries",
        "Refactors": "refactors",
        "FMA Reviews": "fma_reviews",
        "DITL": "ditl",
        "Evolution": "evolution",
        "Chains": "chains",
    }

    def __init__(self, app):
        self.app = app
        self._init_theme()
        self._items = []
        self._cat_var = None
        self._list = None
        self._preview = None
        self._preview_label = None
        self._current_path = None
        # Feedback bar widgets
        self._fb_bar = None
        self._fb_status = None
        self._fb_notes = None
        self._fb_approve_btn = None
        self._fb_reject_btn = None

    def _init_theme(self):
        global BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM, GREEN, ORANGE, RED
        global FONT_SM, FONT_STAT, FONT_BOLD, FONT_XS, FLEET_DIR
        from ui.theme import (BG as _BG, BG2 as _BG2, BG3 as _BG3,
                              ACCENT as _ACC, ACCENT_H as _AH, GOLD as _GOLD,
                              TEXT as _TEXT, DIM as _DIM, GREEN as _GR, ORANGE as _OR, RED as _RED,
                              FONT_SM as _FSM, FONT_STAT as _FST, FONT_BOLD as _FB, FONT_XS as _FXS)
        BG = _BG; BG2 = _BG2; BG3 = _BG3
        ACCENT = _ACC; ACCENT_H = _AH; GOLD = _GOLD
        TEXT = _TEXT; DIM = _DIM; GREEN = _GR; ORANGE = _OR; RED = _RED
        FONT_SM = _FSM; FONT_STAT = _FST; FONT_BOLD = _FB; FONT_XS = _FXS
        import launcher
        FLEET_DIR = launcher.FLEET_DIR

    # ── DB helpers (lazy import, background writes) ──────────────────────────

    @staticmethod
    def _get_db():
        """Lazy import of fleet db module."""
        if str(FLEET_DIR) not in sys.path:
            sys.path.insert(0, str(FLEET_DIR))
        import db
        return db

    def _submit_feedback_bg(self, path_str, verdict, notes):
        """Submit feedback in a background thread, update UI on completion."""
        def _worker():
            try:
                db = self._get_db()
                db.submit_feedback(path_str, verdict, feedback_text=notes)
                self.app.after(0, lambda: self._update_feedback_status(verdict))
                self.app.after(0, self._refresh_list_badges)
            except Exception as e:
                self.app.after(0, lambda: self._update_feedback_status(
                    "error", error_msg=str(e)))
        threading.Thread(target=_worker, daemon=True).start()

    def _load_feedback_bg(self, path_str):
        """Load existing feedback for a path in the background."""
        def _worker():
            try:
                db = self._get_db()
                fb = db.get_feedback(path_str)
                self.app.after(0, lambda: self._apply_loaded_feedback(fb))
            except Exception:
                self.app.after(0, lambda: self._apply_loaded_feedback(None))
        threading.Thread(target=_worker, daemon=True).start()

    # ── Tab layout ───────────────────────────────────────────────────────────

    def build_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(hdr, text="Category:", font=FONT_SM,
                     text_color=DIM).grid(row=0, column=0, padx=(0, 6))

        categories = list(self._DIRS.keys())
        self._cat_var = ctk.StringVar(value="All")
        ctk.CTkOptionMenu(
            hdr, values=categories, variable=self._cat_var,
            font=FONT_SM, fg_color=BG3, button_color=ACCENT,
            button_hover_color=ACCENT_H, height=26, width=140,
            command=lambda _: self.on_refresh()
        ).grid(row=0, column=1, sticky="w")

        ctk.CTkButton(hdr, text="Refresh", font=FONT_SM, height=26, width=80,
                      fg_color=BG3, hover_color=BG,
                      command=self.on_refresh
                      ).grid(row=0, column=2, sticky="e")

        content = ctk.CTkFrame(parent, fg_color=BG)
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=3)
        content.grid_rowconfigure(0, weight=1)

        self._list = ctk.CTkScrollableFrame(content, fg_color=BG2, corner_radius=4)
        self._list.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._list.grid_columnconfigure(0, weight=1)

        preview_frame = ctk.CTkFrame(content, fg_color=BG2, corner_radius=4)
        preview_frame.grid(row=0, column=1, sticky="nsew")
        preview_frame.grid_rowconfigure(1, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)

        self._preview_label = ctk.CTkLabel(
            preview_frame, text="Select a file to preview", font=FONT_SM,
            text_color=DIM, anchor="w")
        self._preview_label.grid(row=0, column=0, padx=8, pady=(4, 2), sticky="w")

        self._preview = ctk.CTkTextbox(
            preview_frame, font=FONT_STAT, fg_color=BG2,
            text_color="#c8c8c8", wrap="word", corner_radius=0)
        self._preview.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 0))

        # ── Feedback bar (row 2 inside preview_frame) ────────────────────
        self._fb_bar = ctk.CTkFrame(preview_frame, fg_color=BG3, corner_radius=4,
                                    height=36)
        self._fb_bar.grid(row=2, column=0, sticky="ew", padx=4, pady=(2, 4))
        self._fb_bar.grid_columnconfigure(2, weight=1)

        self._fb_approve_btn = ctk.CTkButton(
            self._fb_bar, text="\u2714 Approve", font=FONT_SM,
            fg_color="#2e7d32", hover_color="#388e3c", text_color="#ffffff",
            width=90, height=26, corner_radius=4,
            command=lambda: self._on_feedback("approved"))
        self._fb_approve_btn.grid(row=0, column=0, padx=(6, 3), pady=5)

        self._fb_reject_btn = ctk.CTkButton(
            self._fb_bar, text="\u2718 Reject", font=FONT_SM,
            fg_color="#c62828", hover_color="#d32f2f", text_color="#ffffff",
            width=90, height=26, corner_radius=4,
            command=lambda: self._on_feedback("rejected"))
        self._fb_reject_btn.grid(row=0, column=1, padx=(3, 6), pady=5)

        self._fb_notes = ctk.CTkEntry(
            self._fb_bar, font=FONT_XS, fg_color=BG2, text_color=TEXT,
            placeholder_text="Feedback notes (optional)...",
            height=26, corner_radius=4)
        self._fb_notes.grid(row=0, column=2, sticky="ew", padx=(0, 6), pady=5)

        self._fb_status = ctk.CTkLabel(
            self._fb_bar, text="", font=FONT_XS, text_color=DIM, width=100,
            anchor="e")
        self._fb_status.grid(row=0, column=3, padx=(0, 8), pady=5)

        # Hide feedback bar until a file is selected
        self._fb_bar.grid_remove()

        self._items = []
        self.on_refresh()

    # ── File list with feedback badges ───────────────────────────────────────

    def on_refresh(self):
        for w in self._items:
            w.destroy()
        self._items.clear()

        cat = self._cat_var.get() if self._cat_var else "All"
        knowledge = FLEET_DIR / "knowledge"
        subdir = self._DIRS.get(cat)

        files = []
        if subdir:
            target = knowledge / subdir
            if target.exists():
                files = sorted(target.rglob("*.md"), key=lambda f: f.stat().st_mtime,
                               reverse=True)[:50]
        else:
            if knowledge.exists():
                files = sorted(knowledge.rglob("*.md"), key=lambda f: f.stat().st_mtime,
                               reverse=True)[:50]

        # Store file list for badge refresh
        self._file_list = files
        self._file_knowledge_root = knowledge

        # Query feedback status in background, render list immediately
        self._render_file_list(files, knowledge, {})
        if files:
            self._load_badges_bg(files)

    def _render_file_list(self, files, knowledge, badge_map):
        """Render the file list with optional badge indicators."""
        for w in self._items:
            w.destroy()
        self._items.clear()

        for i, f in enumerate(files):
            rel = f.relative_to(knowledge)
            path_str = str(f)
            bg = BG3 if i % 2 == 0 else BG2

            verdict = badge_map.get(path_str)
            if verdict == "approved":
                label = _BADGE_APPROVED + str(rel)
                txt_color = GREEN
            elif verdict == "rejected":
                label = _BADGE_REJECTED + str(rel)
                txt_color = RED
            else:
                label = str(rel)
                txt_color = TEXT

            btn = ctk.CTkButton(
                self._list, text=label, font=FONT_XS,
                fg_color=bg, hover_color=ACCENT, text_color=txt_color,
                anchor="w", height=22, corner_radius=2,
                command=lambda path=f: self._show_file(path))
            btn.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            self._items.append(btn)

        if not files:
            lbl = ctk.CTkLabel(self._list, text="No files found",
                               font=FONT_SM, text_color=DIM)
            lbl.grid(row=0, column=0, padx=8, pady=20)
            self._items.append(lbl)

    def _load_badges_bg(self, files):
        """Load feedback verdicts for all listed files in background."""
        path_strings = [str(f) for f in files]
        def _worker():
            try:
                db = self._get_db()
                badge_map = db.get_feedback_bulk(path_strings)
            except Exception:
                badge_map = {}
            self.app.after(0, lambda: self._render_file_list(
                self._file_list, self._file_knowledge_root, badge_map))
        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_list_badges(self):
        """Re-query badges after a feedback submission."""
        if hasattr(self, '_file_list') and self._file_list:
            self._load_badges_bg(self._file_list)

    # ── Preview + feedback ───────────────────────────────────────────────────

    def _show_file(self, path: Path):
        self._current_path = path
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")[:8000]
        except Exception as e:
            content = f"Error reading file: {e}"
        self._preview_label.configure(text=path.name)
        self._preview.configure(state="normal")
        self._preview.delete("1.0", "end")
        self._preview.insert("end", content)
        self._preview.see("1.0")
        self._preview.configure(state="disabled")

        # Show feedback bar and load existing feedback
        self._fb_bar.grid()
        self._fb_notes.delete(0, "end")
        self._fb_status.configure(text="Loading...", text_color=DIM)
        self._fb_approve_btn.configure(state="normal")
        self._fb_reject_btn.configure(state="normal")
        self._load_feedback_bg(str(path))

    def _apply_loaded_feedback(self, fb):
        """Apply loaded feedback state to the feedback bar widgets."""
        if fb and fb.get("verdict"):
            verdict = fb["verdict"]
            self._update_feedback_status(verdict)
            if fb.get("feedback_text"):
                self._fb_notes.delete(0, "end")
                self._fb_notes.insert(0, fb["feedback_text"])
        else:
            self._fb_status.configure(text="Unreviewed", text_color=DIM)

    def _on_feedback(self, verdict):
        """Handle approve/reject button click."""
        if not self._current_path:
            return
        notes = self._fb_notes.get().strip()
        self._fb_approve_btn.configure(state="disabled")
        self._fb_reject_btn.configure(state="disabled")
        self._fb_status.configure(text="Saving...", text_color=GOLD)
        self._submit_feedback_bg(str(self._current_path), verdict, notes)

    def _update_feedback_status(self, verdict, error_msg=None):
        """Update the feedback status label after a submit or load."""
        if error_msg:
            self._fb_status.configure(text="Error", text_color=RED)
            self._fb_approve_btn.configure(state="normal")
            self._fb_reject_btn.configure(state="normal")
            return
        if verdict == "approved":
            self._fb_status.configure(text="\u2714 Approved", text_color=GREEN)
        elif verdict == "rejected":
            self._fb_status.configure(text="\u2718 Rejected", text_color=RED)
        else:
            self._fb_status.configure(text="Unreviewed", text_color=DIM)
        self._fb_approve_btn.configure(state="normal")
        self._fb_reject_btn.configure(state="normal")

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def on_close(self):
        pass

    def get_settings(self) -> dict:
        return {"enabled": True}

    def apply_settings(self, cfg: dict):
        pass
