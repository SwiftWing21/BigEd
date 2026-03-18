"""
Fleet Control — Updater
Only runs steps where tracked source files changed (or output is missing).
Force Full Rebuild bypasses checks. Dark mode.
"""
import hashlib
import json
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

# ─── Resolve paths whether frozen (.exe) or running as .py ───────────────────
if getattr(sys, "frozen", False):
    SRC_DIR  = Path(sys.executable).parent.parent
    DIST_DIR = Path(sys.executable).parent
else:
    SRC_DIR  = Path(__file__).parent
    DIST_DIR = SRC_DIR / "dist"

EXE_PATH      = DIST_DIR / "FleetControl.exe"
UPD_PATH      = DIST_DIR / "Updater.exe"
UPD_NEW_PATH  = DIST_DIR / "Updater_new.exe"   # staged build — swapped on close
REQ_FILE      = SRC_DIR  / "requirements.txt"
MANIFEST_FILE = DIST_DIR / ".update_manifest.json"
HERE          = SRC_DIR

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
MONO     = ("Consolas", 10)


# ─── Manifest helpers ─────────────────────────────────────────────────────────
def file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


def load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text())
        except Exception:
            pass
    return {}


def save_manifest(data: dict):
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(data, indent=2))


# ─── Steps ────────────────────────────────────────────────────────────────────
# (label, cmd, tracked_files, required_outputs)
# tracked_files   — SRC_DIR-relative filenames; step runs if any hash changed
# required_outputs — Paths that must exist; step runs if any are missing
# Empty tracked_files + empty outputs = always run
STEPS = [
    (
        "Upgrade pip",
        ["python", "-m", "pip", "install", "--upgrade", "pip"],
        [],
        [],
    ),
    (
        "Install packages",
        ["pip", "install", "--upgrade", "-r", str(REQ_FILE)],
        ["requirements.txt"],
        [],
    ),
    (
        "Generate icons",
        ["python", str(SRC_DIR / "generate_icon.py")],
        ["generate_icon.py"],
        [SRC_DIR / "brick.ico", SRC_DIR / "brick_banner.png"],
    ),
    (
        "Build FleetControl",
        [
            "python", "-m", "PyInstaller",
            "--onefile", "--windowed",
            "--name", "FleetControl",
            "--icon", str(SRC_DIR / "brick.ico"),
            f"--add-data={SRC_DIR / 'brick_banner.png'};.",
            f"--add-data={SRC_DIR / 'brick.ico'};.",
            "--collect-all", "customtkinter",
            "--hidden-import", "psutil",
            "--hidden-import", "pynvml",
            str(SRC_DIR / "launcher.py"),
        ],
        ["launcher.py", "requirements.txt"],
        [EXE_PATH],
    ),
    (
        "Build Updater",
        [
            "python", "-m", "PyInstaller",
            "--onefile", "--windowed",
            "--name", "Updater_new",        # staged — swapped on close so running exe isn't locked
            "--icon", str(SRC_DIR / "brick.ico"),
            f"--add-data={SRC_DIR / 'brick.ico'};.",
            "--collect-all", "customtkinter",
            str(SRC_DIR / "updater.py"),
        ],
        ["updater.py", "requirements.txt"],
        [],
    ),
]


# ─── App ──────────────────────────────────────────────────────────────────────
class Updater(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Fleet Control — Updater")
        self.geometry("640x500")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        self._set_icon()
        self._build_ui()
        self._running = False
        self._pending_self_update = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_icon(self):
        ico = HERE / "brick.ico"
        if ico.exists():
            try:
                self.iconbitmap(str(ico))
            except Exception:
                pass

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="🧱", font=("Segoe UI", 24)).grid(
            row=0, column=0, padx=(12, 8), pady=6)
        ctk.CTkLabel(hdr, text="FLEET CONTROL  —  UPDATER",
                     font=("Segoe UI", 14, "bold"),
                     text_color=GOLD).grid(row=0, column=1, sticky="w")

        # ── Progress ──────────────────────────────────────────────────────────
        prog_frame = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0)
        prog_frame.grid(row=1, column=0, sticky="ew")
        prog_frame.grid_columnconfigure(0, weight=1)

        self._step_label = ctk.CTkLabel(
            prog_frame, text="Ready — changed files only",
            font=("Segoe UI", 11), text_color=DIM, anchor="w")
        self._step_label.grid(row=0, column=0, padx=16, pady=(10, 4), sticky="w")

        self._progress = ctk.CTkProgressBar(
            prog_frame, height=12, corner_radius=4,
            fg_color=BG3, progress_color=ACCENT)
        self._progress.set(0)
        self._progress.grid(row=1, column=0, padx=16, pady=(0, 10), sticky="ew")

        # Step indicators
        steps_row = ctk.CTkFrame(prog_frame, fg_color="transparent")
        steps_row.grid(row=2, column=0, padx=12, pady=(0, 10), sticky="ew")
        steps_row.grid_columnconfigure(tuple(range(len(STEPS))), weight=1)

        self._step_dots = []
        for i, (name, *_) in enumerate(STEPS):
            f = ctk.CTkFrame(steps_row, fg_color="transparent")
            f.grid(row=0, column=i, padx=4)
            dot = ctk.CTkLabel(f, text="○", font=("Consolas", 14), text_color=DIM)
            dot.pack()
            lbl = ctk.CTkLabel(f, text=name, font=("Segoe UI", 9), text_color=DIM)
            lbl.pack()
            self._step_dots.append((dot, lbl))

        # ── Log ───────────────────────────────────────────────────────────────
        log_frame = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0)
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        self._log = ctk.CTkTextbox(
            log_frame, font=MONO, fg_color=BG2,
            text_color="#b0b0b0", wrap="word", corner_radius=0)
        self._log.grid(row=0, column=0, sticky="nsew")

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color=BG3, height=52, corner_radius=0)
        btn_frame.grid(row=3, column=0, sticky="ew")
        btn_frame.grid_propagate(False)
        btn_frame.grid_columnconfigure(3, weight=1)

        self._run_btn = ctk.CTkButton(
            btn_frame, text="▶  Run Update", font=("Segoe UI", 11, "bold"),
            width=140, height=34, fg_color="#2a6a2a", hover_color="#3a7a3a",
            command=lambda: self._start_update(force=False))
        self._run_btn.grid(row=0, column=0, padx=(12, 4), pady=9)

        self._force_btn = ctk.CTkButton(
            btn_frame, text="⟳  Force Full", font=("Segoe UI", 10),
            width=110, height=34, fg_color="#5a2020", hover_color="#6a2828",
            command=lambda: self._start_update(force=True))
        self._force_btn.grid(row=0, column=1, padx=4, pady=9)

        self._open_btn = ctk.CTkButton(
            btn_frame, text="▶  Run Fleet Control", font=("Segoe UI", 11),
            width=150, height=34, fg_color=BG2, hover_color=BG,
            command=self._run_fleet_control)
        self._open_btn.grid(row=0, column=2, padx=4, pady=9)

        self._status_lbl = ctk.CTkLabel(
            btn_frame, text="", font=("Segoe UI", 10), text_color=DIM)
        self._status_lbl.grid(row=0, column=4, padx=12, sticky="e")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _log_line(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.configure(state="normal")
        self._log.insert("end", f"[{ts}] {text}\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _set_dot(self, idx: int, state: str):
        dot, lbl = self._step_dots[idx]
        cfg = {
            "pending": ("○", DIM,    DIM),
            "skip":    ("–", DIM,    DIM),
            "running": ("◉", ORANGE, TEXT),
            "done":    ("●", GREEN,  GREEN),
            "error":   ("✕", RED,    RED),
        }.get(state, ("○", DIM, DIM))
        dot.configure(text=cfg[0], text_color=cfg[1])
        lbl.configure(text_color=cfg[2])

    def _run_fleet_control(self):
        if EXE_PATH.exists():
            subprocess.Popen([str(EXE_PATH)], cwd=str(DIST_DIR))
            self._on_close()
        else:
            self._log_line("FleetControl.exe not found — run update first.")

    # ── Manifest checks ───────────────────────────────────────────────────────
    def _should_run(self, tracked: list, outputs: list,
                    manifest: dict, force: bool) -> bool:
        if force:
            return True
        # Missing output always triggers rebuild
        for out in outputs:
            if not Path(out).exists():
                return True
        # No tracked files = always run (e.g. pip upgrade)
        if not tracked:
            return True
        # Run if any tracked file hash differs from stored
        for fname in tracked:
            if file_hash(SRC_DIR / fname) != manifest.get(fname, ""):
                return True
        return False

    def _record_hashes(self, tracked: list, manifest: dict):
        for fname in tracked:
            manifest[fname] = file_hash(SRC_DIR / fname)
        save_manifest(manifest)

    # ── Update logic ──────────────────────────────────────────────────────────
    def _start_update(self, force: bool = False):
        if self._running:
            return
        self._running = True
        for btn in (self._run_btn, self._force_btn):
            btn.configure(state="disabled")
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        for i in range(len(STEPS)):
            self._set_dot(i, "pending")
        self._progress.set(0)
        label = "Running full rebuild..." if force else "Checking for changes..."
        self._step_label.configure(text=label)
        threading.Thread(target=self._run_steps, args=(force,), daemon=True).start()

    def _run_steps(self, force: bool):
        manifest = load_manifest()
        total    = len(STEPS)
        skipped  = 0

        for i, (name, cmd, tracked, outputs) in enumerate(STEPS):
            run = self._should_run(tracked, outputs, manifest, force)

            if not run:
                skipped += 1
                self._log_line(f"── {name} — unchanged, skipping")
                self.after(0, lambda idx=i: (
                    self._set_dot(idx, "skip"),
                    self._progress.set((idx + 1) / total),
                ))
                continue

            self.after(0, lambda n=name, idx=i: (
                self._step_label.configure(text=f"Step {idx+1}/{total}: {n}..."),
                self._set_dot(idx, "running"),
            ))
            self._log_line(f"── {name} ──")

            ok = self._run_cmd(cmd)

            if ok:
                self._record_hashes(tracked, manifest)

            self.after(0, lambda idx=i, ok=ok: (
                self._set_dot(idx, "done" if ok else "error"),
                self._progress.set((idx + 1) / total),
            ))

            if not ok:
                self.after(0, lambda n=name: (
                    self._step_label.configure(text=f"✕ Failed: {n}"),
                    self._status_lbl.configure(text="Update failed", text_color=RED),
                ))
                self._running = False
                self.after(0, self._re_enable_btns)
                return

        self.after(0, lambda s=skipped: self._on_complete(s))

    def _run_cmd(self, cmd: list) -> bool:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(SRC_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self._log_line(line)
            proc.wait()
            return proc.returncode == 0
        except FileNotFoundError as e:
            self._log_line(f"Command not found: {e}")
            return False
        except Exception as e:
            self._log_line(f"Error: {e}")
            return False

    def _on_complete(self, skipped: int = 0):
        self._step_label.configure(text="✓ Update complete")
        skip_note = f"  ({skipped} step{'s' if skipped != 1 else ''} skipped — unchanged)" if skipped else ""
        self._status_lbl.configure(
            text=f"✓ Done{skip_note}", text_color=GREEN)
        self._progress.configure(progress_color=GREEN)
        self._progress.set(1.0)
        self._re_enable_btns()
        self._log_line("─" * 50)
        if EXE_PATH.exists():
            self._log_line(f"✓ {EXE_PATH}")
            self._open_btn.configure(fg_color="#2a6a2a", hover_color="#3a7a3a")
        else:
            self._log_line("⚠ FleetControl.exe not found — check log above.")
        if UPD_NEW_PATH.exists():
            self._pending_self_update = True
            self._log_line("✓ Updater_new.exe staged — Updater will self-update on close")
        self._running = False

    def _on_close(self):
        self._launch_swap_if_needed()
        self.destroy()

    def _launch_swap_if_needed(self):
        """Write a background bat that waits for this process to exit then swaps Updater_new → Updater."""
        if not self._pending_self_update or not UPD_NEW_PATH.exists():
            return
        bat = DIST_DIR / "_swap_updater.bat"
        bat.write_text(
            '@echo off\n'
            ':wait\n'
            'tasklist /FI "IMAGENAME eq Updater.exe" 2>nul | find /I "Updater.exe" >nul\n'
            'if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\n'
            f'move /y "{UPD_NEW_PATH}" "{UPD_PATH}"\n'
            'del "%~f0"\n',
            encoding='utf-8',
        )
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _re_enable_btns(self):
        self._run_btn.configure(state="normal", text="▶  Run Update")
        self._force_btn.configure(state="normal")


# ─── Auto mode (launched by FleetControl with --auto) ─────────────────────────
class AutoUpdater(Updater):
    """
    Runs the delta update immediately on open, then relaunches FleetControl.exe.
    Launched by FleetControl when it detects changed source files on startup.
    """
    def __init__(self):
        super().__init__()
        self.title("Fleet Control — Auto Updating...")
        # Disable manual buttons during auto-run
        self._run_btn.configure(state="disabled")
        self._force_btn.configure(state="disabled")
        self._step_label.configure(text="Auto-update started by FleetControl...")
        # Start after a brief moment so the window has time to render
        self.after(600, lambda: self._start_update(force=False))

    def _on_complete(self, skipped: int = 0):
        super()._on_complete(skipped)
        self._status_lbl.configure(
            text="Relaunching FleetControl...", text_color=GREEN)
        self.after(1500, self._relaunch)

    def _relaunch(self):
        self._launch_swap_if_needed()
        if EXE_PATH.exists():
            subprocess.Popen([str(EXE_PATH)])
        self.destroy()


# ─── Entry ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--auto" in sys.argv:
        app = AutoUpdater()
    else:
        app = Updater()
    app.mainloop()
