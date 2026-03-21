"""
Big Edge Compute Command — Unified Setup
Auto-detects installation state on launch:
  • Not installed  →  Install only
  • Installed      →  Reinstall  |  Uninstall

Built as Setup.exe via build.bat.
The installed copy is what Windows calls from Apps & Features.
"""
import os
import shutil
import subprocess
import sys
import threading
import winreg
from pathlib import Path

import customtkinter as ctk
from PIL import Image

# ─── Bundle paths ─────────────────────────────────────────────────────────────
# PyInstaller extracts --add-data assets to sys._MEIPASS (temp dir).
# Sibling exe files (BigEdCC, Updater) live next to Setup.exe, not in _MEIPASS.
if getattr(sys, "frozen", False):
    ASSETS   = Path(sys._MEIPASS)          # icons, images (--add-data)
    EXE_DIR  = Path(sys.executable).parent  # sibling executables
    SELF_EXE = Path(sys.executable)
else:
    ASSETS   = Path(__file__).parent / "dist"
    EXE_DIR  = Path(__file__).parent / "dist"
    SELF_EXE = Path(__file__)

FLEET_EXE   = EXE_DIR / "BigEdCC.exe"
UPDATER_EXE = EXE_DIR / "Updater.exe"
BANNER_PNG  = ASSETS  / "brick_banner.png"
ICON_ICO    = ASSETS  / "brick.ico"

APP_NAME    = "Big Edge Compute Command"
APP_VERSION = "0.42.00b"
PUBLISHER   = "Max's Home Lab"
DEFAULT_DIR = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "BigEdCC"
REG_KEY     = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\BigEdCC"

# ─── Theme ────────────────────────────────────────────────────────────────────
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
ORANGE   = "#ff9800"
RED      = "#f44336"


# ─── Registry helpers ─────────────────────────────────────────────────────────
def detect_install() -> dict | None:
    """Return registry install info dict if installed and dir exists, else None."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY)
        info = {}
        for field in ("InstallLocation", "DisplayVersion", "DisplayName"):
            try:
                info[field], _ = winreg.QueryValueEx(key, field)
            except Exception:
                pass
        winreg.CloseKey(key)
        loc_str = info.get("InstallLocation") or ""
        if not loc_str:
            return None
        loc = Path(loc_str)
        if loc.exists():
            return info
    except Exception:
        pass
    return None


def register_app(install_dir: Path, setup_exe: Path):
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY)
        winreg.SetValueEx(key, "DisplayName",     0, winreg.REG_SZ,    APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion",  0, winreg.REG_SZ,    APP_VERSION)
        winreg.SetValueEx(key, "Publisher",       0, winreg.REG_SZ,    PUBLISHER)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ,    str(install_dir))
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ,    str(setup_exe))
        winreg.SetValueEx(key, "DisplayIcon",     0, winreg.REG_SZ,    str(install_dir / "BigEdCC.exe"))
        winreg.SetValueEx(key, "NoModify",        0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair",        0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
    except Exception:
        pass


def remove_registry() -> str:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
        return "Removed from Apps & Features"
    except FileNotFoundError:
        return "Registry entry already clean"
    except Exception as e:
        return f"Registry removal failed: {e}"


def create_shortcut(target: Path, shortcut_path: Path, icon: Path = None):
    icon_str = str(icon) if icon and icon.exists() else str(target)
    ps = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$s = $ws.CreateShortcut("{shortcut_path}"); '
        f'$s.TargetPath = "{target}"; '
        f'$s.IconLocation = "{icon_str}"; '
        f'$s.Save()'
    )
    subprocess.run(
        ["powershell", "-NonInteractive", "-Command", ps],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def remove_shortcuts() -> list[str]:
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
class Setup(ctk.CTk):
    def __init__(self):
        super().__init__()

        self._install_info = detect_install()
        self._is_installed = self._install_info is not None
        self._install_dir  = ctk.StringVar(
            value=self._install_info.get("InstallLocation", str(DEFAULT_DIR))
            if self._install_info else str(DEFAULT_DIR)
        )
        self._desktop_sc   = ctk.BooleanVar(value=True)
        self._startmenu_sc = ctk.BooleanVar(value=True)
        self._diffusion    = ctk.BooleanVar(value=False)
        self._install_ollama = ctk.BooleanVar(value=False)
        self._mode         = None   # "install" | "reinstall" | "uninstall"

        # Detect existing installs
        self._python_found = self._detect_python()
        self._ollama_found = self._detect_ollama()

        self.title(APP_NAME)
        self.geometry("580x540")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        if ICON_ICO.exists():
            try:
                self.iconbitmap(str(ICON_ICO))
            except Exception:
                pass

        self._frames = {}
        self._build_header()
        self._build_pages()
        self._show("installed" if self._is_installed else "welcome")

    # ── Header ────────────────────────────────────────────────────────────────
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
        ctk.CTkLabel(info, text=APP_NAME,
                     font=("RuneScape Bold 12", 16, "bold"), text_color=GOLD, anchor="w").pack(anchor="w")
        self._header_sub = ctk.CTkLabel(
            info,
            text=f"Version {APP_VERSION}  ·  {PUBLISHER}",
            font=("RuneScape Plain 11", 10), text_color=DIM, anchor="w",
        )
        self._header_sub.pack(anchor="w")

        self._step_lbl = ctk.CTkLabel(hdr, text="", font=("RuneScape Plain 11", 10), text_color=DIM)
        self._step_lbl.pack(side="right", padx=16)

    # ── Pages ─────────────────────────────────────────────────────────────────
    def _build_pages(self):
        container = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        container.pack(fill="both", expand=True)
        for name, builder in [
            ("welcome",   self._page_welcome),
            ("installed", self._page_installed),
            ("options",   self._page_options),
            ("working",   self._page_working),
            ("complete",  self._page_complete),
        ]:
            frame = ctk.CTkFrame(container, fg_color=BG, corner_radius=0)
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            builder(frame)
            self._frames[name] = frame

    def _show(self, name: str):
        subtitles = {
            "welcome":   f"Version {APP_VERSION}  ·  {PUBLISHER}",
            "installed": "Manage Installation",
            "options":   "Configure",
            "working":   "",
            "complete":  "",
        }
        steps = {
            "welcome":   "Step 1 of 3",
            "options":   "Step 2 of 3",
            "working":   "Step 3 of 3",
            "installed": "",
            "complete":  "",
        }
        self._header_sub.configure(text=subtitles.get(name, ""))
        self._step_lbl.configure(text=steps.get(name, ""))
        for n, f in self._frames.items():
            f.lift() if n == name else f.lower()

    # ── Dependency detection ─────────────────────────────────────────────────
    def _detect_python(self) -> str | None:
        """Return python version string if found, else None."""
        for name in ("python", "python3"):
            exe = shutil.which(name)
            if exe:
                try:
                    r = subprocess.run(
                        [exe, "--version"], capture_output=True, text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    ver = r.stdout.strip() or r.stderr.strip()
                    return ver  # e.g. "Python 3.12.1"
                except Exception:
                    return exe
        return None

    def _detect_ollama(self) -> str | None:
        """Return ollama path if found, else None. Checks PATH + common Windows locations."""
        exe = shutil.which("ollama")
        if exe:
            return exe
        if sys.platform == "win32":
            for env_var, subpath in [
                ("LOCALAPPDATA", "Programs/Ollama/ollama.exe"),
                ("LOCALAPPDATA", "Ollama/ollama.exe"),
                ("PROGRAMFILES", "Ollama/ollama.exe"),
                ("USERPROFILE", "AppData/Local/Ollama/ollama.exe"),
            ]:
                base = os.environ.get(env_var, "")
                if base:  # Only check if env var has a value
                    p = Path(base) / subpath
                    if p.exists():
                        return str(p)
        return None

    # ── Page: Welcome (not installed) ─────────────────────────────────────────
    def _page_welcome(self, parent):
        ctk.CTkLabel(parent, text="Welcome to Big Edge Compute Command Setup",
                     font=("RuneScape Bold 12", 14, "bold"), text_color=GOLD,
                     ).pack(pady=(26, 10))

        desc = (
            "This will install the following components:\n\n"
            "  •  BigEdCC.exe   —  main dashboard & agent launcher\n"
            "  •  Updater.exe        —  one-click rebuild tool\n"
            "  •  Setup.exe          —  reinstall / uninstall\n\n"
            f"Default location:  {DEFAULT_DIR}\n\n"
            "Click Next to configure the installation."
        )
        ctk.CTkLabel(parent, text=desc, font=("RuneScape Plain 12", 11),
                     text_color=TEXT, justify="left", anchor="w",
                     ).pack(padx=32, pady=4, anchor="w")

        if not FLEET_EXE.exists() or not UPDATER_EXE.exists():
            ctk.CTkLabel(
                parent,
                text="⚠  BigEdCC.exe or Updater.exe not found in bundle.\n"
                     "Run build.bat first, or use Re-install to build from source.",
                font=("RuneScape Plain 11", 10), text_color=RED,
            ).pack(padx=32, pady=6, anchor="w")

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=24, pady=16)
        ctk.CTkButton(btn_row, text="Next →", width=110, height=34,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=lambda: (setattr(self, "_mode", "install"), self._show("options")),
                      ).pack(side="right")
        ctk.CTkButton(btn_row, text="Cancel", width=80, height=34,
                      fg_color=BG2, hover_color=BG,
                      command=self.destroy,
                      ).pack(side="right", padx=(0, 8))

    # ── Page: Installed (manage) ───────────────────────────────────────────────
    def _page_installed(self, parent):
        ctk.CTkLabel(parent, text="Big Edge Compute Command is installed",
                     font=("RuneScape Bold 12", 14, "bold"), text_color=GOLD,
                     ).pack(pady=(22, 4))

        loc = (self._install_info or {}).get("InstallLocation", "Unknown")
        ver = (self._install_info or {}).get("DisplayVersion", APP_VERSION)
        ctk.CTkLabel(parent,
                     text=f"Version {ver}  ·  {loc}",
                     font=("RuneScape Plain 11", 10), text_color=DIM,
                     ).pack()

        # ── Action cards ──────────────────────────────────────────────────────
        cards = ctk.CTkFrame(parent, fg_color="transparent")
        cards.pack(fill="x", padx=28, pady=18)
        cards.grid_columnconfigure((0, 1), weight=1)

        # Reinstall card
        reinstall_card = ctk.CTkFrame(cards, fg_color=BG2, corner_radius=8)
        reinstall_card.grid(row=0, column=0, padx=(0, 8), sticky="nsew")

        ctk.CTkLabel(reinstall_card, text="⟳  Reinstall",
                     font=("RuneScape Bold 12", 13, "bold"), text_color=GOLD,
                     ).pack(pady=(18, 6), padx=16)
        ctk.CTkLabel(reinstall_card,
                     text="Rebuild from source and\nrefresh all shortcuts.\nYour data is not affected.",
                     font=("RuneScape Plain 11", 10), text_color=DIM, justify="center",
                     ).pack(padx=16, pady=(0, 14))
        ctk.CTkButton(reinstall_card, text="Reinstall", width=110, height=32,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._go_reinstall,
                      ).pack(pady=(0, 18))

        # Uninstall card
        uninstall_card = ctk.CTkFrame(cards, fg_color=BG2, corner_radius=8)
        uninstall_card.grid(row=0, column=1, padx=(8, 0), sticky="nsew")

        ctk.CTkLabel(uninstall_card, text="🗑  Uninstall",
                     font=("RuneScape Bold 12", 13, "bold"), text_color=RED,
                     ).pack(pady=(18, 6), padx=16)
        ctk.CTkLabel(uninstall_card,
                     text="Remove shortcuts, files,\nand registry entries.\nFleet data is not affected.",
                     font=("RuneScape Plain 11", 10), text_color=DIM, justify="center",
                     ).pack(padx=16, pady=(0, 14))
        ctk.CTkButton(uninstall_card, text="Uninstall", width=110, height=32,
                      fg_color="#5a2020", hover_color="#6a2828",
                      command=self._go_uninstall,
                      ).pack(pady=(0, 18))

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=24, pady=12)
        ctk.CTkButton(btn_row, text="Close", width=80, height=32,
                      fg_color=BG2, hover_color=BG,
                      command=self.destroy,
                      ).pack(side="right")
        ctk.CTkButton(btn_row, text="📂  Open Folder", width=120, height=32,
                      fg_color=BG2, hover_color=BG,
                      command=self._open_install_folder,
                      ).pack(side="right", padx=(0, 8))

    def _go_reinstall(self):
        self._mode = "reinstall"
        self._show("options")

    def _go_uninstall(self):
        self._mode = "uninstall"
        self._confirm_uninstall()

    def _confirm_uninstall(self):
        """Inline confirm dialog overlaid on the installed page."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Confirm Uninstall")
        dialog.geometry("400x220")
        dialog.resizable(False, False)
        dialog.configure(fg_color=BG2)
        dialog.grab_set()
        dialog.transient(self)

        if ICON_ICO.exists():
            try:
                dialog.iconbitmap(str(ICON_ICO))
            except Exception:
                pass

        loc = (self._install_info or {}).get("InstallLocation", "the install directory")
        ctk.CTkLabel(dialog, text="Confirm Uninstall",
                     font=("RuneScape Bold 12", 13, "bold"), text_color=RED,
                     ).pack(pady=(20, 8))
        ctk.CTkLabel(dialog,
                     text=f"This will permanently remove:\n\n"
                          f"  •  {loc}\n"
                          f"  •  Desktop & Start Menu shortcuts\n"
                          f"  •  Windows registry entry\n\n"
                          f"Your fleet data is NOT affected.",
                     font=("RuneScape Plain 11", 10), text_color=TEXT, justify="left",
                     ).pack(padx=24, anchor="w")

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=20, pady=14)

        def confirm():
            dialog.destroy()
            self._show("working")
            threading.Thread(target=self._run_uninstall, daemon=True).start()

        ctk.CTkButton(btn_row, text="Yes, Uninstall", width=130, height=32,
                      fg_color="#5a2020", hover_color="#6a2828",
                      command=confirm,
                      ).pack(side="right")
        ctk.CTkButton(btn_row, text="Cancel", width=80, height=32,
                      fg_color=BG3, hover_color=BG,
                      command=dialog.destroy,
                      ).pack(side="right", padx=(0, 8))

    def _open_install_folder(self):
        loc = (self._install_info or {}).get("InstallLocation", "")
        p = Path(loc)
        if p.exists():
            os.startfile(str(p))

    # ── Page: Options (install / reinstall) ───────────────────────────────────
    def _page_options(self, parent):
        ctk.CTkLabel(parent, text="Installation Options",
                     font=("RuneScape Bold 12", 14, "bold"), text_color=GOLD,
                     ).pack(pady=(18, 8))

        # Scrollable body — buttons stay pinned at bottom
        scroll = ctk.CTkScrollableFrame(parent, fg_color=BG, corner_radius=0,
                                        scrollbar_button_color=BG3,
                                        scrollbar_button_hover_color="#444")
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        dir_frame = ctk.CTkFrame(scroll, fg_color=BG2, corner_radius=6)
        dir_frame.pack(fill="x", padx=28, pady=4)
        dir_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(dir_frame, text="Install to:", font=("RuneScape Plain 11", 10),
                     text_color=DIM, width=80, anchor="w",
                     ).grid(row=0, column=0, padx=(12, 6), pady=10, sticky="w")
        ctk.CTkEntry(dir_frame, textvariable=self._install_dir,
                     font=("Consolas", 10), fg_color=BG,
                     border_color="#444", text_color=TEXT,
                     ).grid(row=0, column=1, padx=4, pady=10, sticky="ew")
        ctk.CTkButton(dir_frame, text="Browse", width=70, height=28,
                      fg_color=BG3, hover_color=BG,
                      command=self._browse,
                      ).grid(row=0, column=2, padx=(4, 10), pady=10)

        sc_frame = ctk.CTkFrame(scroll, fg_color=BG2, corner_radius=6)
        sc_frame.pack(fill="x", padx=28, pady=8)
        ctk.CTkLabel(sc_frame, text="Shortcuts:", font=("RuneScape Plain 11", 10),
                     text_color=DIM, anchor="w",
                     ).pack(padx=12, pady=(10, 4), anchor="w")
        ctk.CTkCheckBox(sc_frame, text="Create Desktop shortcut",
                        variable=self._desktop_sc,
                        font=("RuneScape Plain 12", 11), text_color=TEXT,
                        fg_color=ACCENT, hover_color=ACCENT_H,
                        ).pack(padx=20, pady=3, anchor="w")
        ctk.CTkCheckBox(sc_frame, text="Add to Start Menu",
                        variable=self._startmenu_sc,
                        font=("RuneScape Plain 12", 11), text_color=TEXT,
                        fg_color=ACCENT, hover_color=ACCENT_H,
                        ).pack(padx=20, pady=(3, 10), anchor="w")

        # ── Prerequisites ─────────────────────────────────────────────
        prereq_frame = ctk.CTkFrame(scroll, fg_color=BG2, corner_radius=6)
        prereq_frame.pack(fill="x", padx=28, pady=4)
        ctk.CTkLabel(prereq_frame, text="Prerequisites:", font=("RuneScape Plain 11", 10),
                     text_color=DIM, anchor="w",
                     ).pack(padx=12, pady=(10, 4), anchor="w")

        # Python status (not installable — just detection)
        py_color = GREEN if self._python_found else RED
        py_text = f"Python  —  {self._python_found}" if self._python_found else "Python  —  not found"
        py_icon = "  \u2713" if self._python_found else "  \u2717"
        py_row = ctk.CTkFrame(prereq_frame, fg_color="transparent")
        py_row.pack(padx=20, pady=2, anchor="w", fill="x")
        ctk.CTkLabel(py_row, text=py_icon, font=("RuneScape Plain 12", 11), text_color=py_color,
                     width=24, anchor="w").pack(side="left")
        ctk.CTkLabel(py_row, text=py_text, font=("RuneScape Plain 12", 11), text_color=TEXT,
                     anchor="w").pack(side="left")
        if not self._python_found:
            ctk.CTkButton(py_row, text="Download", width=70, height=22,
                          font=("RuneScape Plain 11", 9), fg_color=ACCENT, hover_color=ACCENT_H,
                          command=lambda: os.startfile("https://www.python.org/downloads/"),
                          ).pack(side="right", padx=(8, 4))
            ctk.CTkLabel(prereq_frame,
                         text="Python 3.11+ required. Install it, then re-run Setup.",
                         font=("RuneScape Plain 11", 9), text_color=ORANGE, justify="left",
                         ).pack(padx=34, pady=(0, 2), anchor="w")

        # Ollama status + install option
        ol_color = GREEN if self._ollama_found else ORANGE
        ol_text = "Ollama  —  installed" if self._ollama_found else "Ollama  —  not installed"
        ol_icon = "  \u2713" if self._ollama_found else "  \u2717"
        ol_row = ctk.CTkFrame(prereq_frame, fg_color="transparent")
        ol_row.pack(padx=20, pady=(2, 2), anchor="w", fill="x")
        ctk.CTkLabel(ol_row, text=ol_icon, font=("RuneScape Plain 12", 11), text_color=ol_color,
                     width=24, anchor="w").pack(side="left")
        ctk.CTkLabel(ol_row, text=ol_text, font=("RuneScape Plain 12", 11), text_color=TEXT,
                     anchor="w").pack(side="left")
        if not self._ollama_found:
            ctk.CTkCheckBox(prereq_frame, text="Download and install Ollama (local AI models)",
                            variable=self._install_ollama,
                            font=("RuneScape Plain 12", 11), text_color=TEXT,
                            fg_color=ACCENT, hover_color=ACCENT_H,
                            ).pack(padx=34, pady=(2, 2), anchor="w")
            ctk.CTkLabel(prereq_frame,
                         text="Required for local AI. Skip if using API-only mode (Claude/Gemini).",
                         font=("RuneScape Plain 11", 9), text_color=DIM, justify="left",
                         ).pack(padx=48, pady=(0, 2), anchor="w")
        ctk.CTkLabel(prereq_frame, text="", font=("RuneScape Plain 11", 1)).pack(pady=(0, 4))

        # ── Optional components ──────────────────────────────────────────
        opt_frame = ctk.CTkFrame(scroll, fg_color=BG2, corner_radius=6)
        opt_frame.pack(fill="x", padx=28, pady=4)
        ctk.CTkLabel(opt_frame, text="Optional components:", font=("RuneScape Plain 11", 10),
                     text_color=DIM, anchor="w",
                     ).pack(padx=12, pady=(10, 4), anchor="w")
        ctk.CTkCheckBox(opt_frame, text="Stable Diffusion (local image generation)",
                        variable=self._diffusion,
                        font=("RuneScape Plain 12", 11), text_color=TEXT,
                        fg_color=ACCENT, hover_color=ACCENT_H,
                        ).pack(padx=20, pady=3, anchor="w")
        ctk.CTkLabel(opt_frame,
                     text="Installs diffusers, transformers, accelerate, torch (~2.5 GB download).\n"
                          "Models download separately on first use (~5-7 GB each).",
                     font=("RuneScape Plain 11", 9), text_color=DIM, justify="left",
                     ).pack(padx=34, pady=(0, 10), anchor="w")

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=24, pady=16)
        back_target = "installed" if self._is_installed else "welcome"
        ctk.CTkButton(btn_row, text="← Back", width=80, height=34,
                      fg_color=BG2, hover_color=BG,
                      command=lambda: self._show(back_target),
                      ).pack(side="left")
        action = "Reinstall" if self._mode == "reinstall" else "Install"
        ctk.CTkButton(btn_row, text=action, width=110, height=34,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._start_install,
                      ).pack(side="right")
        ctk.CTkButton(btn_row, text="Cancel", width=80, height=34,
                      fg_color=BG2, hover_color=BG,
                      command=self.destroy,
                      ).pack(side="right", padx=(0, 8))

    def _browse(self):
        from tkinter import filedialog
        chosen = filedialog.askdirectory(initialdir=self._install_dir.get())
        if chosen:
            self._install_dir.set(chosen)

    # ── Page: Working (progress — install, reinstall, uninstall) ─────────────
    def _page_working(self, parent):
        self._work_title = ctk.CTkLabel(parent, text="Working...",
                                        font=("RuneScape Bold 12", 14, "bold"), text_color=GOLD)
        self._work_title.pack(pady=(28, 10))

        self._prog = ctk.CTkProgressBar(parent, height=14, corner_radius=4,
                                        fg_color=BG3, progress_color=ACCENT)
        self._prog.set(0)
        self._prog.pack(fill="x", padx=32, pady=(4, 12))

        self._prog_lbl = ctk.CTkLabel(parent, text="Preparing...",
                                      font=("RuneScape Plain 11", 10), text_color=DIM)
        self._prog_lbl.pack(anchor="w", padx=34)

        self._work_log = ctk.CTkTextbox(parent, font=("Consolas", 10),
                                        fg_color=BG2, text_color="#aaa",
                                        height=170, corner_radius=4)
        self._work_log.pack(fill="x", padx=28, pady=8)
        self._work_log.configure(state="disabled")

    def _log(self, msg: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self._work_log.configure(state="normal")
        self._work_log.insert("end", f"[{ts}] {msg}\n")
        self._work_log.see("end")
        self._work_log.configure(state="disabled")

    def _set_prog(self, pct: float, label: str):
        self._prog.set(pct)
        self._prog_lbl.configure(text=label)

    # ── Page: Complete ────────────────────────────────────────────────────────
    def _page_complete(self, parent):
        ctk.CTkLabel(parent, text="✓", font=("RuneScape Plain 12", 52), text_color=GREEN).pack(pady=(20, 4))
        self._complete_title = ctk.CTkLabel(
            parent, text="Done!", font=("RuneScape Bold 12", 15, "bold"), text_color=GOLD)
        self._complete_title.pack()
        self._complete_note = ctk.CTkLabel(
            parent, text="", font=("RuneScape Plain 11", 10), text_color=DIM)
        self._complete_note.pack(pady=6)

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=24, pady=20)

        self._launch_btn = ctk.CTkButton(
            btn_row, text="▶  Launch Big Edge Compute Command", width=240, height=36,
            fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._launch_fleet)
        self._launch_btn.pack(side="left")

        self._folder_btn = ctk.CTkButton(
            btn_row, text="📂  Open Folder", width=120, height=36,
            fg_color=BG2, hover_color=BG,
            command=self._open_install_folder)
        self._folder_btn.pack(side="left", padx=8)

        ctk.CTkButton(btn_row, text="Close", width=80, height=36,
                      fg_color=BG2, hover_color=BG,
                      command=self.destroy).pack(side="right")

    def _launch_fleet(self):
        exe = Path(self._install_dir.get()) / "BigEdCC.exe"
        if exe.exists():
            subprocess.Popen([str(exe)])
        self.destroy()

    # ── Install / Reinstall logic ─────────────────────────────────────────────
    def _start_install(self):
        self._show("working")
        self.after(0, lambda: self._work_title.configure(
            text="Reinstalling..." if self._mode == "reinstall" else "Installing..."))
        threading.Thread(target=self._run_install, daemon=True).start()

    def _run_install(self):
        install_dir = Path(self._install_dir.get())
        steps = []

        if self._mode == "reinstall":
            steps.append((0.05, "Building from source...", self._step_build))

        base = 0.30 if self._mode == "reinstall" else 0.0
        scale = 0.70 if self._mode == "reinstall" else 1.0

        def p(frac):
            return base + frac * scale

        steps += [
            (p(0.08), "Creating directory...",        lambda: self._step_mkdir(install_dir)),
            (p(0.25), "Copying BigEdCC.exe...",  lambda: self._step_copy(FLEET_EXE,   install_dir)),
            (p(0.40), "Copying Updater.exe...",       lambda: self._step_copy(UPDATER_EXE, install_dir)),
            (p(0.50), "Copying Setup.exe...",         lambda: self._step_copy_self(install_dir)),
            (p(0.58), "Copying icon...",              lambda: self._step_copy_optional(ICON_ICO, install_dir)),
            (p(0.65), "Registering with Windows...", lambda: self._step_register(install_dir)),
            (p(0.78), "Creating shortcuts...",        lambda: self._step_shortcuts(install_dir)),
            (p(0.85), "Installing Python packages...",lambda: self._step_pip()),
        ]
        if self._install_ollama.get():
            steps.append(
                (p(0.87), "Installing Ollama...", lambda: self._step_install_ollama()),
            )
            steps.append(
                (p(0.88), "Pulling health monitor model (qwen3:0.6b)...",
                 lambda: self._step_pull_model("qwen3:0.6b")),
            )
            steps.append(
                (p(0.90), "Pulling Dr. Ders low-tier model (qwen3:1.7b)...",
                 lambda: self._step_pull_model("qwen3:1.7b")),
            )
            steps.append(
                (p(0.92), "Pulling conductor model (qwen3:4b)...",
                 lambda: self._step_pull_model("qwen3:4b")),
            )
            steps.append(
                (p(0.94), "Pulling default model (qwen3:8b)...",
                 lambda: self._step_pull_model("qwen3:8b")),
            )
            steps.append(
                (p(0.96), "Verifying models...",
                 lambda: self._step_verify_models()),
            )
        if self._diffusion.get():
            steps.append(
                (p(0.92), "Installing Stable Diffusion...", lambda: self._step_pip_diffusion()),
            )
        steps += [
            (p(0.97), "Writing version file...",     lambda: self._step_write_version(install_dir)),
            (1.00,    "Done.",                        lambda: None),
        ]

        ok = self._run_steps(steps)
        self.after(0, lambda: self._on_install_complete(ok))

    def _run_steps(self, steps) -> bool:
        for pct, label, fn in steps:
            self.after(0, lambda p=pct, l=label: self._set_prog(p, l))
            self.after(0, lambda l=label: self._log(l))
            try:
                result = fn()
                if result:
                    self.after(0, lambda r=result: self._log(f"  {r}"))
            except Exception as e:
                self.after(0, lambda e=e: self._log(f"  ⚠ {e}"))
                return False
        return True

    def _on_install_complete(self, ok: bool):
        self._prog.configure(progress_color=GREEN if ok else RED)
        self._prog.set(1.0)
        if ok:
            mode = self._mode or "install"
            self._complete_title.configure(
                text="Reinstall Complete!" if mode == "reinstall" else "Installation Complete!")
            self._complete_note.configure(
                text=f"Installed to {self._install_dir.get()}")
            self._launch_btn.configure(state="normal")
            self._folder_btn.configure(state="normal")
            self._show("complete")
        else:
            self._prog_lbl.configure(text="✕ An error occurred — see log above", text_color=RED)

    # ── Uninstall logic ───────────────────────────────────────────────────────
    def _run_uninstall(self):
        install_dir = Path(
            (self._install_info or {}).get("InstallLocation", "")
        )
        self.after(0, lambda: self._work_title.configure(text="Uninstalling..."))

        steps = [
            (0.25, "Removing shortcuts...",      lambda: ", ".join(remove_shortcuts()) or "None found"),
            (0.60, "Removing registry entry...", remove_registry),
            (0.90, "Scheduling file removal...", lambda: self._step_schedule_removal(install_dir)),
            (1.00, "Done.",                      lambda: None),
        ]
        self._run_steps(steps)
        self.after(0, self._on_uninstall_complete)

    def _step_schedule_removal(self, target: Path) -> str:
        if not target.exists():
            return "Install directory not found — nothing to delete"
        bat = target / "_cleanup.bat"
        bat.write_text(
            "@echo off\n"
            ":wait\n"
            'tasklist /FI "IMAGENAME eq Setup.exe" 2>nul | find /I "Setup.exe" >nul\n'
            "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\n"
            f'rmdir /s /q "{target}"\n'
            'del "%~f0"\n',
            encoding="utf-8",
        )
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=subprocess.CREATE_NO_WINDOW)
        return f"Will remove {target} after close"

    def _on_uninstall_complete(self):
        self._prog.configure(progress_color=GREEN)
        self._prog.set(1.0)
        self._complete_title.configure(text="Uninstall Complete")
        self._complete_note.configure(text="Install directory will be removed after this window closes.")
        self._launch_btn.configure(state="disabled")
        self._folder_btn.configure(state="disabled")
        self._show("complete")

    # ── Step helpers ──────────────────────────────────────────────────────────
    def _step_build(self) -> str:
        src  = Path(__file__).parent if not getattr(sys, "frozen", False) else Path(sys.executable).parent.parent
        icon = src / "brick.ico"
        banner = src / "brick_banner.png"
        req  = src / "requirements.txt"
        cmds = [
            ["pip", "install", "--upgrade", "-r", str(req)],
            ["python", str(src / "generate_icon.py")],
            ["python", "-m", "PyInstaller", "--onefile", "--windowed",
             "--name", "BigEdCC", "--icon", str(icon),
             f"--add-data={banner};.", f"--add-data={icon};.",
             "--collect-all", "customtkinter",
             "--hidden-import", "psutil", "--hidden-import", "pynvml",
             str(src / "launcher.py")],
            ["python", "-m", "PyInstaller", "--onefile", "--windowed",
             "--name", "Updater", "--icon", str(icon),
             f"--add-data={icon};.", "--collect-all", "customtkinter",
             str(src / "updater.py")],
            ["python", "-m", "PyInstaller", "--onefile", "--windowed",
             "--name", "Setup", "--icon", str(icon),
             f"--add-data={icon};.", f"--add-data={banner};.",
             "--collect-all", "customtkinter",
             str(src / "installer.py")],
        ]
        for cmd in cmds:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=str(src), creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.after(0, lambda l=line: self._log(l))
            proc.wait()
            if proc.returncode != 0:
                full_cmd = ' '.join(cmd)
                short_cmd = full_cmd[:120] + ("..." if len(full_cmd) > 120 else "")
                raise RuntimeError(f"Build failed (exit {proc.returncode}): {short_cmd}")
        return "Build complete"

    def _step_mkdir(self, d: Path) -> str:
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def _step_copy(self, src: Path, dest_dir: Path) -> str:
        if not src.exists():
            raise FileNotFoundError(f"{src.name} not found in bundle")
        shutil.copy2(src, dest_dir / src.name)
        return f"{src.name} → {dest_dir}"

    def _step_copy_optional(self, src: Path, dest_dir: Path) -> str:
        """Copy a file if it exists — skip silently if missing (non-critical asset)."""
        if not src.exists():
            return f"{src.name} not found — skipped (non-critical)"
        shutil.copy2(src, dest_dir / src.name)
        return f"{src.name} → {dest_dir}"

    def _step_copy_self(self, dest_dir: Path) -> str:
        """Copy this exe (or script) to install dir as Setup.exe."""
        if getattr(sys, "frozen", False):
            dest = dest_dir / "Setup.exe"
            shutil.copy2(SELF_EXE, dest)
            return f"Setup.exe → {dest_dir}"
        # Running as .py — copy from dist if built
        built = Path(__file__).parent / "dist" / "Setup.exe"
        if built.exists():
            shutil.copy2(built, dest_dir / "Setup.exe")
            return f"Setup.exe → {dest_dir}"
        return "Setup.exe not built yet — run build.bat"

    def _step_register(self, install_dir: Path) -> str:
        setup_exe = install_dir / "Setup.exe"
        register_app(install_dir, setup_exe)
        return "Registered in Apps & Features"

    def _step_pip(self) -> str:
        python = shutil.which("python") or shutil.which("python3") or "python"
        # Check if we need --break-system-packages (PEP 668, Python 3.11+)
        pip_extra = []
        try:
            r = subprocess.run(
                [python, "-c", "import sys; print(sys.version_info >= (3,11))"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.stdout.strip() == "True":
                pip_extra = ["--break-system-packages"]
        except Exception:
            pass
        # Launcher deps
        pkgs = ["customtkinter", "pillow", "psutil", "nvidia-ml-py", "anthropic", "google-genai"]
        self.after(0, lambda: self._log("  pip install (launcher): " + " ".join(pkgs)))
        result = subprocess.run(
            [python, "-m", "pip", "install", "--quiet"] + pip_extra + pkgs,
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "pip failed")[-300:])
        # Fleet deps
        fleet_pkgs = ["httpx", "flask", "psutil", "anthropic", "google-genai"]
        self.after(0, lambda: self._log("  pip install (fleet): " + " ".join(fleet_pkgs)))
        result2 = subprocess.run(
            [python, "-m", "pip", "install", "--quiet"] + pip_extra + fleet_pkgs,
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result2.returncode != 0:
            self.after(0, lambda: self._log("  ⚠ Fleet deps had issues — dashboard may not work"))
        return "Python packages installed"

    def _step_pip_diffusion(self) -> str:
        python = shutil.which("python") or shutil.which("python3") or "python"
        pkgs = ["diffusers", "transformers", "accelerate", "torch"]
        self.after(0, lambda: self._log("  pip install " + " ".join(pkgs)))
        result = subprocess.run(
            [python, "-m", "pip", "install", "--quiet"] + pkgs,
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=600,  # torch is large, allow up to 10 min
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "pip failed")[-300:])
        return "Stable Diffusion dependencies installed"

    def _step_install_ollama(self) -> str:
        """Install Ollama using best available method: winget > curl+exe > urllib."""
        _NW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        installer = None

        try:
            # Strategy 1: winget (most reliable on Windows 11)
            winget = shutil.which("winget")
            if winget:
                self.after(0, lambda: self._log("  Installing via winget..."))
                result = subprocess.run(
                    [winget, "install", "Ollama.Ollama",
                     "--accept-package-agreements", "--accept-source-agreements", "--silent"],
                    capture_output=True, text=True, timeout=300,
                    creationflags=_NW,
                )
                if result.returncode == 0:
                    return "Ollama installed (winget)"
                self.after(0, lambda: self._log("  winget failed — trying direct download..."))

            # Strategy 2: curl (ships with Windows 11)
            curl = shutil.which("curl")
            dl_dir = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "C:/Windows/Temp")
            installer = dl_dir / "OllamaSetup.exe"
            url = "https://ollama.com/download/OllamaSetup.exe"

            if curl:
                self.after(0, lambda: self._log("  Downloading OllamaSetup.exe via curl..."))
                dl = subprocess.run(
                    [curl, "-L", "-o", str(installer), url],
                    capture_output=True, timeout=300, creationflags=_NW,
                )
                if dl.returncode != 0 or not installer.exists():
                    self.after(0, lambda: self._log("  curl download failed — trying urllib..."))
                    installer = None  # fall through

            # Strategy 3: urllib fallback
            if installer is None or not installer.exists():
                import urllib.request
                installer = dl_dir / "OllamaSetup.exe"
                self.after(0, lambda: self._log("  Downloading OllamaSetup.exe via urllib..."))
                try:
                    urllib.request.urlretrieve(url, str(installer))
                except Exception as e:
                    raise RuntimeError(f"All download methods failed: {e}")

            # Run the downloaded installer
            self.after(0, lambda: self._log("  Running OllamaSetup.exe..."))
            result = subprocess.run(
                [str(installer), "/VERYSILENT", "/NORESTART"],
                capture_output=True, timeout=180,
                creationflags=_NW,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Ollama installer exited with code {result.returncode}")
            return "Ollama installed"
        finally:
            if installer and installer.exists():
                try:
                    installer.unlink(missing_ok=True)
                except Exception:
                    pass

    def _step_pull_model(self, model: str = "qwen3:8b") -> str:
        """Pull an Ollama model. Skips if already installed. Starts the server if needed."""
        # Find the freshly-installed ollama
        ollama_exe = shutil.which("ollama")
        if not ollama_exe and sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA", "")
            if base:
                win_path = Path(base) / "Programs" / "Ollama" / "ollama.exe"
                if win_path.exists():
                    ollama_exe = str(win_path)
        if not ollama_exe:
            return f"Ollama not found — pull {model} manually later"

        # Ensure Ollama server is running (needed for pull and list)
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        except Exception:
            self.after(0, lambda: self._log("  Starting Ollama server..."))
            subprocess.Popen(
                [ollama_exe, "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            import time
            time.sleep(4)

        # Check if model is already installed
        try:
            list_result = subprocess.run(
                [ollama_exe, "list"],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if list_result.returncode == 0 and model in list_result.stdout:
                return f"{model} already installed — skipped"
        except Exception:
            pass  # If check fails, proceed with pull

        self.after(0, lambda m=model: self._log(f"  Pulling {m} — this may take a few minutes..."))
        result = subprocess.run(
            [ollama_exe, "pull", model],
            capture_output=True, text=True, timeout=600,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode != 0:
            self.after(0, lambda m=model: self._log(f"  ⚠ Pull failed — run 'ollama pull {m}' manually"))
            return f"{model} pull failed (non-fatal)"
        return f"{model} ready"

    def _step_verify_models(self) -> str:
        """Verify all expected models are installed. Warns but does not fail."""
        expected = ["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b", "qwen3:8b"]

        ollama_exe = shutil.which("ollama")
        if not ollama_exe and sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA", "")
            if base:
                win_path = Path(base) / "Programs" / "Ollama" / "ollama.exe"
                if win_path.exists():
                    ollama_exe = str(win_path)
        if not ollama_exe:
            return "Ollama not found — cannot verify models"

        try:
            list_result = subprocess.run(
                [ollama_exe, "list"],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if list_result.returncode != 0:
                return "Could not query Ollama — verify models manually"

            installed = [m for m in expected if m in list_result.stdout]
            missing = [m for m in expected if m not in list_result.stdout]
            count = len(installed)
            total = len(expected)

            if missing:
                msg = f"Models: {count}/{total} installed (missing: {', '.join(missing)})"
                self.after(0, lambda m=msg: self._log(f"  ⚠ {m}"))
                return msg
            return f"Models verified: {count}/{total} installed"
        except Exception as e:
            return f"Model verification failed: {e}"

    def _step_write_version(self, install_dir: Path) -> str:
        """Write version file so the release updater knows what's installed."""
        vf = install_dir / ".bigedcc_version"
        vf.write_text(f"v{APP_VERSION}", encoding="utf-8")
        return f"v{APP_VERSION}"

    def _step_shortcuts(self, install_dir: Path) -> str:
        target = install_dir / "BigEdCC.exe"
        icon   = install_dir / "brick.ico"
        created = []
        if self._desktop_sc.get():
            desktop = Path(os.environ.get("USERPROFILE", "~")) / "Desktop"
            create_shortcut(target, desktop / "BigEdCC.lnk", icon)
            created.append("Desktop shortcut")
        if self._startmenu_sc.get():
            programs = (
                Path(os.environ.get("APPDATA", "~"))
                / "Microsoft/Windows/Start Menu/Programs/Big Edge Compute Command"
            )
            programs.mkdir(parents=True, exist_ok=True)
            create_shortcut(target, programs / "BigEdCC.lnk", icon)
            create_shortcut(install_dir / "Updater.exe", programs / "Updater.lnk", icon)
            create_shortcut(install_dir / "Setup.exe",   programs / "Setup.lnk",   icon)
            created.append("Start Menu folder")
        return ", ".join(created) if created else "No shortcuts requested"


# ─── UAC elevation ─────────────────────────────────────────────────────────────
def _is_admin() -> bool:
    """Check if running with admin privileges."""
    import ctypes
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _elevate():
    """Re-launch this process with UAC elevation, then exit."""
    import ctypes
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = ""
    else:
        exe = sys.executable          # python.exe
        params = f'"{__file__}"'
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    sys.exit(0)


# ─── Entry ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if sys.platform == "win32" and not _is_admin():
        _elevate()
    Setup().mainloop()
