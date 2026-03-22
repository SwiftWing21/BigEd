"""
Ingestion Module — File/folder import to RAG for BigEd CC.

Scans configured source directory, supports text/code/data/doc/zip formats,
imports into fleet RAG index via the ingest skill.
Cross-module: feeds into Outputs module (knowledge browser).
"""
import threading
from pathlib import Path

import customtkinter as ctk

BG = BG2 = BG3 = ACCENT = ACCENT_H = GOLD = TEXT = DIM = GREEN = ORANGE = RED = ""
FONT_SM = FONT_STAT = FONT_BOLD = FONT_XS = ("Segoe UI", 10)
FLEET_DIR = None


def _load_settings():
    import launcher
    return launcher._load_settings()


class Module:
    NAME = "ingestion"
    LABEL = "Ingestion"
    VERSION = "0.23"
    DEFAULT_ENABLED = True
    DEPENDS_ON = []

    # Text-extractable formats (direct RAG indexing)
    TEXT_EXTS = {
        # Text & docs
        ".md", ".txt", ".rst", ".log", ".cfg", ".ini", ".toml", ".yaml", ".yml",
        ".rtf", ".ndjson", ".jsonl",
        # Code (all major languages)
        ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp",
        ".h", ".hpp", ".cs", ".rb", ".sh", ".bat", ".ps1", ".sql", ".kt", ".swift",
        ".r", ".lua", ".php", ".pl", ".scala", ".dart", ".zig",
        # Config
        ".json", ".csv", ".tsv", ".xml", ".html", ".htm", ".env", ".properties",
        ".conf", ".tf", ".hcl",
    }
    # Structured docs (need library extraction)
    DOC_EXTS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
                ".odt", ".ods", ".odp", ".epub"}
    # Archives (auto-extracted)
    ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".7z", ".rar", ".tgz"}
    # Media (routed to vision_analyze / speech_to_text skills)
    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}
    AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
    # All recognized
    SUPPORTED_EXTS = TEXT_EXTS | DOC_EXTS | ARCHIVE_EXTS | IMAGE_EXTS | AUDIO_EXTS | VIDEO_EXTS
    # Accept ANY file — unrecognized types shown with "?" color
    ACCEPT_ALL = True

    def __init__(self, app):
        self.app = app
        self._init_theme()
        self._checks = []
        self._widgets = []
        self._file_list = None
        self._count_lbl = None
        self._source_var = None
        self._path_label = None
        self._tag_var = None
        self._maxmb_var = None
        self._btn = None
        self._status = None

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

    def build_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(hdr, text="Source:", font=FONT_SM,
                     text_color=DIM).grid(row=0, column=0, padx=(0, 6))

        default_downloads = str(Path.home() / "Downloads")
        ingest_path = _load_settings().get("ingest_path", default_downloads)
        self._source_var = ctk.StringVar(value=ingest_path)
        ctk.CTkOptionMenu(
            hdr, values=["Downloads", "Custom..."],
            font=FONT_SM, fg_color=BG3, button_color=ACCENT,
            button_hover_color=ACCENT_H, height=26, width=120,
            command=self._source_change,
        ).grid(row=0, column=1, sticky="w")

        self._path_label = ctk.CTkLabel(
            hdr, text=ingest_path, font=FONT_XS, text_color=DIM, anchor="w")
        self._path_label.grid(row=0, column=2, padx=(8, 0), sticky="w")

        self._color_files_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(hdr, text="Color by type", variable=self._color_files_var,
                      font=FONT_XS, text_color=DIM, width=40,
                      fg_color=BG3, progress_color=GOLD,
                      command=self.on_refresh
                      ).grid(row=0, column=3, padx=(8, 0))

        ctk.CTkButton(hdr, text="Refresh", font=FONT_SM, height=26, width=80,
                      fg_color=BG3, hover_color=BG,
                      command=self.on_refresh
                      ).grid(row=0, column=4, padx=(8, 0), sticky="e")

        content = ctk.CTkFrame(parent, fg_color=BG)
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=2)
        content.grid_columnconfigure(1, weight=3)
        content.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content, fg_color=BG2, corner_radius=4)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        sel_bar = ctk.CTkFrame(left, fg_color=BG3, corner_radius=0)
        sel_bar.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(sel_bar, text="Select All", font=FONT_XS,
                      width=70, height=22, fg_color="transparent", hover_color=BG2,
                      text_color=DIM, command=self._select_all
                      ).pack(side="left", padx=4, pady=2)
        ctk.CTkButton(sel_bar, text="Select None", font=FONT_XS,
                      width=75, height=22, fg_color="transparent", hover_color=BG2,
                      text_color=DIM, command=self._select_none
                      ).pack(side="left", padx=0, pady=2)
        self._count_lbl = ctk.CTkLabel(
            sel_bar, text="", font=FONT_XS, text_color=DIM)
        self._count_lbl.pack(side="right", padx=8)

        self._file_list = ctk.CTkScrollableFrame(
            left, fg_color=BG2, corner_radius=0)
        self._file_list.grid(row=1, column=0, sticky="nsew")
        self._file_list.grid_columnconfigure(0, weight=1)

        right = ctk.CTkFrame(content, fg_color=BG2, corner_radius=4)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)

        tag_frame = ctk.CTkFrame(right, fg_color="transparent")
        tag_frame.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(tag_frame, text="Import tag:", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._tag_var = ctk.StringVar(value="import")
        ctk.CTkEntry(tag_frame, textvariable=self._tag_var,
                     font=FONT_SM, fg_color=BG, border_color="#444",
                     text_color=TEXT, height=28, width=160
                     ).pack(side="left", padx=(6, 0))

        max_frame = ctk.CTkFrame(right, fg_color="transparent")
        max_frame.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(max_frame, text="Max file size (MB):", font=FONT_SM,
                     text_color=TEXT).pack(side="left")
        self._maxmb_var = ctk.StringVar(value="50")
        ctk.CTkEntry(max_frame, textvariable=self._maxmb_var,
                     font=FONT_SM, fg_color=BG, border_color="#444",
                     text_color=TEXT, height=28, width=60
                     ).pack(side="left", padx=(6, 0))

        info_frame = ctk.CTkFrame(right, fg_color=BG3, corner_radius=6)
        info_frame.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(info_frame, text="Accepts all files", font=FONT_BOLD,
                     text_color=GOLD).pack(padx=10, pady=(8, 2), anchor="w")
        ctk.CTkLabel(info_frame,
                     text="Text:      .md .txt .rst .log .toml .yaml .json .csv .xml .html\n"
                          "Code:     .py .js .ts .go .rs .java .c .cpp .cs .rb .kt .swift +15 more\n"
                          "Docs:     .pdf .docx .doc .xlsx .pptx .epub .odt\n"
                          "Images:  .png .jpg .gif .webp .svg (routed to vision)\n"
                          "Audio:    .mp3 .wav .flac .ogg (routed to STT)\n"
                          "Archive: .zip .tar .7z .rar (auto-extracted)\n"
                          "Other:    any file — attempts text extraction, flags if unsupported",
                     font=FONT_XS, text_color=DIM, justify="left"
                     ).pack(padx=10, pady=(0, 8), anchor="w")

        self._btn = ctk.CTkButton(
            right, text="Ingest Selected", font=FONT_BOLD,
            height=36, fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._run_ingest)
        self._btn.pack(padx=12, pady=(8, 4), fill="x")

        self._status = ctk.CTkTextbox(
            right, font=FONT_XS, fg_color=BG,
            text_color="#aaa", height=120, corner_radius=4)
        self._status.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._status.insert("end", "Select files and click Ingest to import into RAG.\n")
        self._status.configure(state="disabled")

        self.on_refresh()

    def on_refresh(self):
        # Preserve checked paths before rebuild
        previously_checked = set()
        for var, path in self._checks:
            if var.get():
                previously_checked.add(str(path))

        for w in self._widgets:
            w.destroy()
        self._widgets.clear()
        self._checks.clear()

        source = Path(self._source_var.get()) if self._source_var else Path.home() / "Downloads"
        if not source.exists():
            lbl = ctk.CTkLabel(self._file_list, text="Path not found",
                               font=FONT_SM, text_color=DIM)
            lbl.grid(row=0, column=0, padx=8, pady=20)
            self._widgets.append(lbl)
            self._count_lbl.configure(text="0 files")
            return

        files = []
        for f in sorted(source.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file():
                # Accept all files if ACCEPT_ALL, otherwise filter by SUPPORTED_EXTS
                if self.ACCEPT_ALL or f.suffix.lower() in self.SUPPORTED_EXTS:
                    files.append(f)
            if len(files) >= 200:
                break

        dirs = []
        for d in sorted(source.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                dirs.append(d)
                if len(dirs) >= 50:
                    break

        row = 0
        for d in dirs:
            was_checked = str(d) in previously_checked
            var = ctk.BooleanVar(value=was_checked)
            cb = ctk.CTkCheckBox(
                self._file_list, text=f"[dir] {d.name}/",
                variable=var, font=FONT_XS,
                text_color=GOLD, fg_color=ACCENT, hover_color=ACCENT_H,
                checkbox_width=16, checkbox_height=16, corner_radius=3)
            cb.grid(row=row, column=0, sticky="ew", padx=4, pady=1)
            self._checks.append((var, d))
            self._widgets.append(cb)
            row += 1

        for f in files:
            was_checked = str(f) in previously_checked
            var = ctk.BooleanVar(value=was_checked)
            ext = f.suffix.lower()
            use_colors = self._color_files_var.get() if hasattr(self, '_color_files_var') else False
            if use_colors:
                if ext in self.ARCHIVE_EXTS:
                    color = ORANGE
                elif ext in self.DOC_EXTS:
                    color = "#7aa2f7"  # blue — structured docs
                elif ext in self.IMAGE_EXTS:
                    color = "#ce93d8"  # purple — images
                elif ext in self.AUDIO_EXTS or ext in self.VIDEO_EXTS:
                    color = "#ffb74d"  # amber — media
                elif ext in self.TEXT_EXTS:
                    color = GREEN if ext in (".py", ".js", ".ts", ".go", ".rs", ".java",
                                             ".c", ".cpp", ".cs", ".rb", ".kt", ".swift") else TEXT
                else:
                    color = DIM
            else:
                color = TEXT

            cb = ctk.CTkCheckBox(
                self._file_list,
                text=f"  {f.name}  ({f.stat().st_size / 1024:.0f} KB)",
                variable=var, font=FONT_XS,
                text_color=color, fg_color=ACCENT, hover_color=ACCENT_H,
                checkbox_width=16, checkbox_height=16, corner_radius=3)
            cb.grid(row=row, column=0, sticky="ew", padx=4, pady=1)
            self._checks.append((var, f))
            self._widgets.append(cb)
            row += 1

        total = len(dirs) + len(files)
        self._count_lbl.configure(text=f"{total} items")

        if total == 0:
            lbl = ctk.CTkLabel(self._file_list, text="No supported files found",
                               font=FONT_SM, text_color=DIM)
            lbl.grid(row=0, column=0, padx=8, pady=20)
            self._widgets.append(lbl)

    def on_close(self):
        pass

    def get_settings(self) -> dict:
        return {"enabled": True, "ingest_path": self._source_var.get() if self._source_var else ""}

    def apply_settings(self, cfg: dict):
        if self._source_var and "ingest_path" in cfg:
            self._source_var.set(cfg["ingest_path"])

    def _source_change(self, choice: str):
        if choice == "Custom...":
            from tkinter import filedialog
            chosen = filedialog.askdirectory(
                initialdir=self._source_var.get())
            if chosen:
                self._source_var.set(chosen)
                self._path_label.configure(text=chosen)
                self.on_refresh()
        else:
            default_downloads = str(Path.home() / "Downloads")
            path = _load_settings().get("ingest_path", default_downloads)
            self._source_var.set(path)
            self._path_label.configure(text=path)
            self.on_refresh()

    def _select_all(self):
        for var, _ in self._checks:
            var.set(True)

    def _select_none(self):
        for var, _ in self._checks:
            var.set(False)

    def _log(self, msg: str):
        self._status.configure(state="normal")
        self._status.insert("end", msg + "\n")
        self._status.see("end")
        self._status.configure(state="disabled")

    def _run_ingest(self):
        selected = [path for var, path in self._checks if var.get()]
        if not selected:
            self._log("No files selected.")
            return

        tag = self._tag_var.get().strip() or "import"
        try:
            max_mb = int(self._maxmb_var.get())
        except ValueError:
            max_mb = 50

        self._btn.configure(state="disabled", text="Ingesting...")
        self._log(f"Starting ingest: {len(selected)} items, tag='{tag}'")

        def _do_ingest():
            import importlib.util
            total_files = 0
            total_chunks = 0
            errors = []

            for path in selected:
                try:
                    self.app.after(0, lambda p=path: self._log(f"  Processing: {p.name}"))
                    payload = {
                        "path": str(path),
                        "tag": tag,
                        "max_file_mb": max_mb,
                        "recursive": True,
                    }
                    spec = importlib.util.spec_from_file_location(
                        "ingest", str(FLEET_DIR / "skills" / "ingest.py"))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    result = mod.run(payload, {})

                    if "error" in result:
                        errors.append(f"{path.name}: {result['error']}")
                        self.app.after(0, lambda e=result['error']: self._log(f"    Error: {e}"))
                    else:
                        fi = result.get("files_ingested", 0)
                        ch = result.get("chunks_indexed", 0)
                        total_files += fi
                        total_chunks += ch
                        self.app.after(0, lambda f=fi, c=ch: self._log(
                            f"    Indexed {f} files, {c} chunks"))
                except Exception as e:
                    errors.append(f"{path.name}: {e}")
                    self.app.after(0, lambda e=e: self._log(f"    Error: {e}"))

            summary = f"Done: {total_files} files, {total_chunks} chunks indexed"
            if errors:
                summary += f", {len(errors)} errors"
            self.app.after(0, lambda s=summary: self._log(s))
            self.app.after(0, lambda: self._btn.configure(
                state="normal", text="Ingest Selected"))

        threading.Thread(target=_do_ingest, daemon=True).start()
