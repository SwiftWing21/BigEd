"""
Outputs Module — Knowledge browser for BigEd CC.

Browses fleet knowledge outputs: code reviews, security, quality, drafts, reports.
Cross-module: fed by Ingestion module via RAG index.
"""
from pathlib import Path

import customtkinter as ctk

BG = BG2 = BG3 = ACCENT = ACCENT_H = GOLD = TEXT = DIM = GREEN = ORANGE = RED = ""
FONT_SM = ("RuneScape Plain 11", 10)
FLEET_DIR = None


class Module:
    NAME = "outputs"
    LABEL = "Outputs"
    VERSION = "0.23"
    DEFAULT_ENABLED = True
    DEPENDS_ON = []

    _DIRS = {
        "All": None,
        "Code Reviews": "code_reviews",
        "Security": "security",
        "Quality": "quality",
        "Drafts": "code_drafts",
        "Reports": "reports",
        "Chains": "chains",
        "FMA Reviews": "fma_reviews",
    }

    def __init__(self, app):
        self.app = app
        self._init_theme()
        self._items = []
        self._cat_var = None
        self._list = None
        self._preview = None
        self._preview_label = None

    def _init_theme(self):
        global BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM, GREEN, ORANGE, RED, FONT_SM, FLEET_DIR
        import launcher
        BG = launcher.BG; BG2 = launcher.BG2; BG3 = launcher.BG3
        ACCENT = launcher.ACCENT; ACCENT_H = launcher.ACCENT_H
        GOLD = launcher.GOLD; TEXT = launcher.TEXT; DIM = launcher.DIM
        GREEN = launcher.GREEN; ORANGE = launcher.ORANGE; RED = launcher.RED
        FONT_SM = launcher.FONT_SM
        FLEET_DIR = launcher.FLEET_DIR

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
            preview_frame, font=("Consolas", 10), fg_color=BG2,
            text_color="#c8c8c8", wrap="word", corner_radius=0)
        self._preview.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))

        self._items = []
        self.on_refresh()

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

        for i, f in enumerate(files):
            rel = f.relative_to(knowledge)
            bg = BG3 if i % 2 == 0 else BG2
            btn = ctk.CTkButton(
                self._list, text=str(rel), font=("Consolas", 9),
                fg_color=bg, hover_color=ACCENT, text_color=TEXT,
                anchor="w", height=22, corner_radius=2,
                command=lambda path=f: self._show_file(path))
            btn.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            self._items.append(btn)

        if not files:
            lbl = ctk.CTkLabel(self._list, text="No files found",
                               font=FONT_SM, text_color=DIM)
            lbl.grid(row=0, column=0, padx=8, pady=20)
            self._items.append(lbl)

    def on_close(self):
        pass

    def get_settings(self) -> dict:
        return {"enabled": True}

    def apply_settings(self, cfg: dict):
        pass

    def _show_file(self, path: Path):
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
