"""API Keys settings panel + Key Manager Dialog."""
import json
import os
import re
import subprocess
import threading
from pathlib import Path

import customtkinter as ctk

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, FONT, FONT_SM, FONT_BOLD,
    GLASS_BG, GLASS_PANEL, GLASS_BORDER,
)


def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


class KeysPanelMixin:
    """Mixin providing the API Keys settings panel."""

    def _build_keys_panel(self):
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["keys"] = panel

        self._section_header(panel, "API Key Manager")

        # Key status cards
        keys_grid = ctk.CTkFrame(panel, fg_color="transparent")
        keys_grid.pack(fill="x", padx=16, pady=(0, 12))
        keys_grid.grid_columnconfigure((0, 1), weight=1)

        key_defs = [
            ("Anthropic", "ANTHROPIC_API_KEY", "Claude API — code review, analysis, planning"),
            ("Google AI", "GEMINI_API_KEY", "Gemini — review pass, fallback reasoning"),
            ("HuggingFace", "HF_TOKEN", "Model downloads, dataset access"),
            ("GitHub", "GITHUB_TOKEN", "PR sync, code search, issue tracking"),
            ("Discord", "DISCORD_BOT_TOKEN", "Fleet chat bridge"),
            ("Brave", "BRAVE_API_KEY", "Web search API"),
        ]

        for i, (name, env_key, desc) in enumerate(key_defs):
            card = ctk.CTkFrame(keys_grid, fg_color=GLASS_BG, corner_radius=6, height=70)
            card.grid(row=i // 2, column=i % 2, padx=4, pady=4, sticky="nsew")
            card.grid_propagate(False)

            has_key = bool(os.environ.get(env_key))
            dot_color = GREEN if has_key else "#444"
            status_text = "configured" if has_key else "not set"

            ctk.CTkLabel(card, text="●", font=("Consolas", 12),
                         text_color=dot_color).place(x=10, y=10)
            ctk.CTkLabel(card, text=name, font=FONT_BOLD,
                         text_color=TEXT).place(x=28, y=8)
            ctk.CTkLabel(card, text=env_key, font=("Consolas", 8),
                         text_color=DIM).place(x=28, y=28)
            ctk.CTkLabel(card, text=desc, font=("RuneScape Plain 11", 8),
                         text_color=DIM).place(x=10, y=48)
            ctk.CTkLabel(card, text=status_text, font=("Consolas", 9),
                         text_color=dot_color).place(relx=1.0, x=-10, y=10, anchor="ne")

        # Full key manager button
        self._section_header(panel, "Advanced")
        adv_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        adv_frame.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(adv_frame, text="Add, rotate, and manage API keys with the full key manager.",
                     font=("RuneScape Plain 11", 9), text_color=DIM
                     ).pack(padx=12, pady=(10, 4), anchor="w")
        ctk.CTkButton(adv_frame, text="Open Key Manager", font=FONT_SM,
                      width=150, height=28, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=lambda: KeyManagerDialog(self._parent)
                      ).pack(padx=12, pady=(0, 10), anchor="w")


class KeyManagerDialog(ctk.CTkToplevel):

    def __init__(self, parent):
        L = _launcher()
        self.REGISTRY_FILE = L.FLEET_DIR / "keys_registry.toml"
        self.SECRETS_FILE  = Path.home() / ".wsl_secrets_cache"  # local cache from WSL read

        super().__init__(parent)
        self.title("BigEd CC — API Key Manager")
        self.geometry("780x540")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.grab_set()

        ico = L.HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._rows = {}   # key_name -> {label, value_label, dot}
        self._build_ui()
        self._load_keys()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=46, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="\U0001f511  API KEY MANAGER",
                     font=("RuneScape Bold 12", 13, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")
        ctk.CTkLabel(hdr, text="Keys stored in ~/.secrets  |  masked values shown",
                     font=("RuneScape Plain 11", 9), text_color=DIM
                     ).grid(row=0, column=1, padx=8, sticky="w")

        # Scrollable key table
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=BG2, corner_radius=0)
        self._scroll.grid(row=1, column=0, sticky="nsew")
        self._scroll.grid_columnconfigure(0, weight=1)

        # Column headers
        hrow = ctk.CTkFrame(self._scroll, fg_color=BG3, corner_radius=4)
        hrow.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        hrow.grid_columnconfigure(1, weight=1)
        for col, (txt, w) in enumerate([("", 18), ("Key / Label", 0),
                                         ("Tier", 70), ("Status", 110), ("Value", 160), ("", 80)]):
            ctk.CTkLabel(hrow, text=txt, font=("RuneScape Bold 12", 9, "bold"),
                         text_color=DIM, width=w, anchor="w"
                         ).grid(row=0, column=col, padx=6, pady=4, sticky="w")

        # Bottom toolbar
        bar = ctk.CTkFrame(self, fg_color=BG3, height=46, corner_radius=0)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)

        ctk.CTkButton(bar, text="\u21bb Refresh", font=FONT_SM, width=100, height=30,
                      fg_color=BG2, hover_color=BG, command=self._load_keys
                      ).grid(row=0, column=0, padx=(10, 6), pady=8)
        ctk.CTkButton(bar, text="\U0001f50d Scan Skills", font=FONT_SM, width=120, height=30,
                      fg_color=BG2, hover_color=BG, command=self._scan_skills
                      ).grid(row=0, column=1, padx=6, pady=8)
        ctk.CTkButton(bar, text="+ Add Custom Key", font=FONT_SM, width=140, height=30,
                      fg_color=BG2, hover_color=BG, command=self._add_custom_key
                      ).grid(row=0, column=2, padx=6, pady=8)

        self._scan_lbl = ctk.CTkLabel(bar, text="", font=FONT_SM, text_color=DIM)
        self._scan_lbl.grid(row=0, column=3, padx=12, sticky="e")

    def _load_keys(self):
        # Clear existing rows (keep header)
        for w in list(self._scroll.winfo_children())[1:]:
            w.destroy()
        self._rows = {}

        registry = self._read_registry()
        secrets  = self._read_secrets_via_wsl()

        for i, info in enumerate(registry):
            name    = info.get("env_var", "")
            label   = info.get("label", name)
            purpose = info.get("purpose", "")
            tier    = info.get("tier", "")
            masked  = secrets.get(name, "")
            is_set  = bool(masked) and masked not in ("EMPTY", "not set")

            dot_color  = GREEN  if is_set  else RED
            dot_text   = "\u25cf"
            status_txt = "SET"  if is_set  else "MISSING"
            status_col = GREEN  if is_set  else RED
            tier_col   = {"free": DIM, "freemium": ORANGE, "paid": "#4488ff"}.get(tier, DIM)

            row = ctk.CTkFrame(self._scroll, fg_color=BG if i % 2 else "#1e1e1e",
                               corner_radius=3)
            row.grid(row=i + 1, column=0, sticky="ew", padx=8, pady=1)
            row.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(row, text=dot_text, font=("Consolas", 13),
                         text_color=dot_color, width=18).grid(row=0, column=0, padx=(8,2), pady=6)

            name_frame = ctk.CTkFrame(row, fg_color="transparent")
            name_frame.grid(row=0, column=1, sticky="w", padx=4)
            ctk.CTkLabel(name_frame, text=name, font=("Consolas", 10, "bold"),
                         text_color=TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(name_frame, text=label, font=("RuneScape Plain 11", 9),
                         text_color=DIM, anchor="w").pack(anchor="w")

            ctk.CTkLabel(row, text=tier, font=("RuneScape Plain 11", 9),
                         text_color=tier_col, width=70).grid(row=0, column=2, padx=4)
            ctk.CTkLabel(row, text=status_txt, font=("RuneScape Bold 12", 10, "bold"),
                         text_color=status_col, width=90).grid(row=0, column=3, padx=4)
            ctk.CTkLabel(row, text=masked or "\u2014", font=("Consolas", 9),
                         text_color=DIM, width=160, anchor="w").grid(row=0, column=4, padx=4)
            ctk.CTkButton(row, text="Edit", font=FONT_SM, width=60, height=24,
                          fg_color=ACCENT, hover_color=ACCENT_H,
                          command=lambda n=name, lbl=label: self._edit_key(n, lbl)
                          ).grid(row=0, column=5, padx=(4, 8), pady=4)

            # Purpose tooltip row
            ctk.CTkLabel(row, text=f"  {purpose[:90]}", font=("RuneScape Plain 11", 9),
                         text_color=DIM, anchor="w"
                         ).grid(row=1, column=1, columnspan=5, sticky="w", padx=4, pady=(0, 6))

    def _read_registry(self):
        if not self.REGISTRY_FILE.exists():
            return []
        try:
            import tomllib
            with open(self.REGISTRY_FILE, "rb") as f:
                return tomllib.load(f).get("key", [])
        except Exception:
            return []

    def _read_secrets_via_wsl(self):
        """Read masked key values from ~/.secrets (native, no WSL)."""
        secrets_file = Path.home() / ".secrets"
        masked = {}
        try:
            if not secrets_file.exists():
                return masked
            for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line or line.startswith("#"):
                    continue
                k, _, v = line.partition("=")
                k = k.strip(); v = v.strip().strip('"').strip("'")
                if v and v != "REPLACE_ME":
                    masked[k] = v[:6] + "..." + v[-4:] if len(v) > 12 else "***set***"
                else:
                    masked[k] = "EMPTY"
        except Exception:
            pass
        return masked

    def _edit_key(self, key_name: str, label: str):
        L = _launcher()
        dialog = ctk.CTkInputDialog(
            text=f"Enter value for {label}:\n({key_name})\n\nLeave blank to cancel.",
            title=f"Set {key_name}")
        value = dialog.get_input()
        if not value or not value.strip():
            return
        value = value.strip()
        # Save key natively to ~/.secrets
        def _save_bg():
            try:
                secrets_file = Path.home() / ".secrets"
                lines = []
                found = False
                if secrets_file.exists():
                    for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                        stripped = line.strip()
                        raw = stripped[7:].strip() if stripped.startswith("export ") else stripped
                        if "=" in raw and raw.split("=", 1)[0].strip() == key_name:
                            lines.append(f"export {key_name}={value}")
                            found = True
                        else:
                            lines.append(line)
                if not found:
                    lines.append(f"export {key_name}={value}")
                secrets_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.environ[key_name] = value  # update current process too
                self.after(0, lambda: (
                    self._scan_lbl.configure(text="\u2713 {0} saved".format(key_name), text_color=GREEN),
                    self.after(400, self._load_keys)))
            except Exception as e:
                self.after(0, lambda: self._scan_lbl.configure(
                    text="\u2717 {0}".format(str(e)[:40]), text_color=RED))
        threading.Thread(target=_save_bg, daemon=True).start()

    def _add_custom_key(self):
        name_dialog = ctk.CTkInputDialog(
            text="Enter env var name (e.g. MY_API_KEY):", title="Add Key")
        name = name_dialog.get_input()
        if not name or not name.strip():
            return
        name = re.sub(r'[^A-Z0-9_]', '', name.strip().upper())
        self._edit_key(name, name)

    def _scan_skills(self):
        L = _launcher()
        self._scan_lbl.configure(text="Scanning...", text_color=ORANGE)
        def _bg():
            try:
                result = subprocess.run(
                    [L._get_fleet_python(), str(L.FLEET_DIR / "lead_client.py"),
                     "dispatch", "key_manager", json.dumps({"action": "scan"}), "--priority", "9"],
                    capture_output=True, text=True, timeout=30, cwd=str(L.FLEET_DIR),
                )
                msg = "Scan queued \u2192 knowledge/reports/key_scan.md" if result.returncode == 0 else "Error: {0}".format(result.stderr[:40])
                self.after(0, lambda: self._scan_lbl.configure(text=msg, text_color=GREEN if result.returncode == 0 else RED))
            except Exception as e:
                self.after(0, lambda: self._scan_lbl.configure(text="Error: {0}".format(str(e)[:40]), text_color=RED))
        threading.Thread(target=_bg, daemon=True).start()
