"""
Big Edge Compute Command — Uninstaller
Reads install location from registry, removes shortcuts, registry entry, and files.
"""
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

if sys.platform == "win32":
    import winreg

import customtkinter as ctk
from PIL import Image

# ─── Bundle path resolution (same pattern as installer) ──────────────────────
if getattr(sys, "frozen", False):
    BUNDLE   = Path(sys._MEIPASS)
    SELF_EXE = Path(sys.executable)
else:
    BUNDLE   = Path(__file__).parent / "dist"
    SELF_EXE = Path(__file__)

BANNER_PNG = BUNDLE / "icon_1024.png"
ICON_ICO   = BUNDLE / "brick.ico"

APP_NAME  = "Big Edge Compute Command"
PUBLISHER = "Max's Home Lab"
REG_KEY   = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\BigEdCC"

# ─── Theme (identical to installer / updater) ─────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

BG       = "#1a1a1a"
BG2      = "#242424"
BG3      = "#2d2d2d"
ACCENT   = "#b22222"
ACCENT_H = "#8b0000"
GOLD     = "#c8a84b"
TEXT     = "#e2e2e2"
DIM      = "#888888"
GREEN    = "#4caf50"
RED      = "#f44336"


# ─── Registry helpers ─────────────────────────────────────────────────────────
def _read_install_dir() -> Path | None:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY)
        val, _ = winreg.QueryValueEx(key, "InstallLocation")
        winreg.CloseKey(key)
        p = Path(val)
        return p if p.exists() else None
    except Exception:
        return None


def _remove_registry() -> str:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
        return "Removed from Apps & Features"
    except FileNotFoundError:
        return "Registry entry not found — already clean"
    except Exception as e:
        return f"Registry removal failed: {e}"


def _remove_shortcuts() -> list[str]:
    removed = []
    desktop = Path(os.environ.get("USERPROFILE", "~")) / "Desktop" / "BigEdCC.lnk"
    if desktop.exists():
        desktop.unlink(missing_ok=True)
        removed.append("Desktop shortcut")
    programs = (
        Path(os.environ.get("APPDATA", "~"))
        / "Microsoft/Windows/Start Menu/Programs/Big Edge Compute Command"
    )
    if programs.exists():
        shutil.rmtree(programs, ignore_errors=True)
        removed.append("Start Menu folder")
    return removed


# ─── App ──────────────────────────────────────────────────────────────────────
class Uninstaller(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — Uninstall")
        self.geometry("520x390")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        if ICON_ICO.exists():
            try:
                self.iconbitmap(str(ICON_ICO))
            except Exception:
                pass

        self._install_dir = _read_install_dir()
        self._frames = {}
        self._build_header()
        self._build_pages()
        self._show("confirm")

    # ── Header (persistent) ───────────────────────────────────────────────────
    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=70, corner_radius=0)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        if BANNER_PNG.exists():
            try:
                img    = Image.open(BANNER_PNG)
                ctkimg = ctk.CTkImage(light_image=img, dark_image=img, size=(45, 60))
                ctk.CTkLabel(hdr, image=ctkimg, text="").pack(side="left", padx=(12, 8), pady=5)
            except Exception:
                ctk.CTkLabel(hdr, text="🧱", font=("RuneScape Plain 12", 32)).pack(side="left", padx=(12, 8))
        else:
            ctk.CTkLabel(hdr, text="🧱", font=("RuneScape Plain 12", 32)).pack(side="left", padx=(12, 8))

        info = ctk.CTkFrame(hdr, fg_color="transparent")
        info.pack(side="left", fill="y", pady=10)
        ctk.CTkLabel(info, text=APP_NAME, font=("RuneScape Bold 12", 16, "bold"),
                     text_color=GOLD, anchor="w").pack(anchor="w")
        ctk.CTkLabel(info, text=f"Uninstall  ·  {PUBLISHER}",
                     font=("RuneScape Plain 11", 10), text_color=DIM, anchor="w").pack(anchor="w")

    # ── Pages ─────────────────────────────────────────────────────────────────
    def _build_pages(self):
        container = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        container.pack(fill="both", expand=True)
        for name, builder in [
            ("confirm",  self._page_confirm),
            ("removing", self._page_removing),
            ("complete", self._page_complete),
        ]:
            frame = ctk.CTkFrame(container, fg_color=BG, corner_radius=0)
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            builder(frame)
            self._frames[name] = frame

    def _show(self, name: str):
        for n, f in self._frames.items():
            f.lift() if n == name else f.lower()

    # ── Page: Confirm ─────────────────────────────────────────────────────────
    def _page_confirm(self, parent):
        ctk.CTkLabel(parent, text="Uninstall Big Edge Compute Command",
                     font=("RuneScape Bold 12", 14, "bold"), text_color=GOLD
                     ).pack(pady=(24, 10))

        if self._install_dir:
            body = (
                "The following will be permanently removed:\n\n"
                f"  •  Install directory:  {self._install_dir}\n"
                "  •  Desktop shortcut (if created)\n"
                "  •  Start Menu entry (if created)\n"
                "  •  Windows registry entry\n\n"
                "Your fleet data (fleet.db, config, knowledge/) is NOT affected."
            )
        else:
            body = (
                "Install location not found in registry.\n\n"
                "Shortcuts and registry entries will still be cleaned up.\n"
                "No files will be deleted."
            )

        ctk.CTkLabel(parent, text=body, font=("RuneScape Plain 12", 11),
                     text_color=TEXT, justify="left", anchor="w"
                     ).pack(padx=32, pady=4, anchor="w")

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=24, pady=16)
        ctk.CTkButton(btn_row, text="Uninstall", width=120, height=34,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._start_uninstall
                      ).pack(side="right")
        ctk.CTkButton(btn_row, text="Cancel", width=80, height=34,
                      fg_color=BG2, hover_color=BG,
                      command=self.destroy
                      ).pack(side="right", padx=(0, 8))

    # ── Page: Removing ────────────────────────────────────────────────────────
    def _page_removing(self, parent):
        ctk.CTkLabel(parent, text="Uninstalling...",
                     font=("RuneScape Bold 12", 14, "bold"), text_color=GOLD
                     ).pack(pady=(28, 10))

        self._prog = ctk.CTkProgressBar(parent, height=14, corner_radius=4,
                                        fg_color=BG3, progress_color=ACCENT)
        self._prog.set(0)
        self._prog.pack(fill="x", padx=32, pady=(4, 12))

        self._prog_lbl = ctk.CTkLabel(parent, text="Preparing...",
                                      font=("RuneScape Plain 11", 10), text_color=DIM)
        self._prog_lbl.pack(anchor="w", padx=34)

        self._log_box = ctk.CTkTextbox(parent, font=("Consolas", 10),
                                       fg_color=BG2, text_color="#aaa",
                                       height=130, corner_radius=4)
        self._log_box.pack(fill="x", padx=28, pady=8)
        self._log_box.configure(state="disabled")

    def _log(self, msg: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.configure(state="normal")
        self._log_box.insert("end", f"[{ts}] {msg}\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _set_prog(self, pct: float, label: str):
        self._prog.set(pct)
        self._prog_lbl.configure(text=label)

    # ── Page: Complete ────────────────────────────────────────────────────────
    def _page_complete(self, parent):
        ctk.CTkLabel(parent, text="✓", font=("RuneScape Plain 12", 52),
                     text_color=GREEN).pack(pady=(20, 4))
        ctk.CTkLabel(parent, text="Uninstall Complete",
                     font=("RuneScape Bold 12", 15, "bold"), text_color=GOLD
                     ).pack()
        self._complete_note = ctk.CTkLabel(
            parent, text="", font=("RuneScape Plain 11", 10), text_color=DIM)
        self._complete_note.pack(pady=6)

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=24, pady=20)
        ctk.CTkButton(btn_row, text="Close", width=100, height=36,
                      fg_color=BG2, hover_color=BG,
                      command=self.destroy
                      ).pack(side="right")

    # ── Uninstall logic ───────────────────────────────────────────────────────
    def _start_uninstall(self):
        self._show("removing")
        threading.Thread(target=self._run_uninstall, daemon=True).start()

    def _run_uninstall(self):
        steps = [
            (0.25, "Removing shortcuts...",      self._do_shortcuts),
            (0.55, "Removing registry entry...", self._do_registry),
            (0.90, "Scheduling file removal...", self._do_files),
            (1.00, "Done.",                      lambda: None),
        ]
        for pct, label, fn in steps:
            self.after(0, lambda p=pct, l=label: self._set_prog(p, l))
            self.after(0, lambda l=label: self._log(l))
            try:
                note = fn()
                if note:
                    self.after(0, lambda n=note: self._log(f"  {n}"))
            except Exception as e:
                self.after(0, lambda e=e: self._log(f"  ⚠ {e}"))

        self.after(0, self._on_complete)

    def _do_shortcuts(self) -> str:
        removed = _remove_shortcuts()
        return ", ".join(removed) if removed else "No shortcuts found"

    def _do_registry(self) -> str:
        return _remove_registry()

    def _do_files(self) -> str:
        if not self._install_dir or not self._install_dir.exists():
            return "Install directory not found — nothing to delete"
        self._schedule_dir_removal(self._install_dir)
        return f"Will remove {self._install_dir} after close"

    def _schedule_dir_removal(self, target: Path):
        """Bat that waits for this process to exit then deletes the install dir."""
        bat = target / "_cleanup.bat"
        bat.write_text(
            "@echo off\n"
            ":wait\n"
            'tasklist /FI "IMAGENAME eq Uninstaller.exe" 2>nul | find /I "Uninstaller.exe" >nul\n'
            "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\n"
            f'rmdir /s /q "{target}"\n'
            'del "%~f0"\n',
            encoding="utf-8",
        )
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _on_complete(self):
        self._prog.configure(progress_color=GREEN)
        if self._install_dir:
            note = "Install directory will be removed after this window closes."
        else:
            note = "Shortcuts and registry entries cleaned up."
        self._complete_note.configure(text=note)
        self._show("complete")


# ─── Entry ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    Uninstaller().mainloop()
