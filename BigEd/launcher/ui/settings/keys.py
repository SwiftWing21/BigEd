"""API Keys settings panel + Key Manager Dialog.

Modernized: tri-state status dots (green/orange/red), masked previews,
live key validation probes, per-provider health checks.
"""
import json
import os
import re
import subprocess
import threading
import urllib.request
from pathlib import Path

import customtkinter as ctk

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, FONT, FONT_SM, FONT_BOLD, FONT_XS,
    GLASS_BG, GLASS_PANEL, GLASS_BORDER,
)


def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


# ── Key validation probes ────────────────────────────────────────────────────
# Each probe returns True (valid), False (invalid), or None (could not reach).
_VALIDATION_PROBES = {
    "ANTHROPIC_API_KEY": {
        "url": "https://api.anthropic.com/v1/messages",
        "method": "POST",
        "headers_fn": lambda key: {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        # Minimal body — will return 400 (bad request) for valid key, 401 for invalid
        "body": b'{"model":"claude-haiku-4-5-20250515","max_tokens":1,"messages":[{"role":"user","content":"x"}]}',
        "valid_codes": {200, 400, 429},  # 400 = valid key, bad request; 429 = rate limited
    },
    "GEMINI_API_KEY": {
        "url_fn": lambda key: f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        "method": "GET",
        "valid_codes": {200},
    },
    "GITHUB_TOKEN": {
        "url": "https://api.github.com/user",
        "method": "GET",
        "headers_fn": lambda key: {
            "Authorization": f"Bearer {key}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "BigEdCC-KeyManager",
        },
        "valid_codes": {200},
    },
    "HF_TOKEN": {
        "url": "https://huggingface.co/api/whoami-v2",
        "method": "GET",
        "headers_fn": lambda key: {"Authorization": f"Bearer {key}"},
        "valid_codes": {200},
    },
    "BRAVE_API_KEY": {
        "url": "https://api.search.brave.com/res/v1/web/search?q=test&count=1",
        "method": "GET",
        "headers_fn": lambda key: {
            "Accept": "application/json",
            "X-Subscription-Token": key,
        },
        "valid_codes": {200, 429},
    },
}


def _probe_key(env_var: str, raw_value: str) -> bool | None:
    """Run a quick HTTP probe to validate a key. Returns True/False/None."""
    spec = _VALIDATION_PROBES.get(env_var)
    if not spec:
        return None  # no probe defined for this key
    try:
        url = spec.get("url") or spec["url_fn"](raw_value)
        method = spec.get("method", "GET")
        headers = spec["headers_fn"](raw_value) if "headers_fn" in spec else {}
        body = spec.get("body")
        req = urllib.request.Request(url, method=method, headers=headers, data=body)
        resp = urllib.request.urlopen(req, timeout=8)
        return resp.status in spec["valid_codes"]
    except urllib.error.HTTPError as e:
        return e.code in spec.get("valid_codes", set())
    except Exception:
        return None  # network error — cannot determine


def _read_raw_secret(env_var: str) -> str:
    """Read the raw (unmasked) value of a key from env or ~/.secrets."""
    val = os.environ.get(env_var, "").strip()
    if val:
        return val
    secrets_file = Path.home() / ".secrets"
    if not secrets_file.exists():
        return ""
    try:
        for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == env_var and v and v != "REPLACE_ME":
                return v
    except Exception:
        pass
    return ""


def _mask_key(raw: str) -> str:
    """Return masked preview: '...last4' or empty."""
    if not raw:
        return ""
    if len(raw) > 8:
        return f"...{raw[-4:]}"
    return "***set***"


class KeysPanelMixin:
    """Mixin providing the API Keys settings panel in Settings dialog."""

    def _build_keys_panel(self):
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["keys"] = panel

        self._section_header(panel, "API Key Manager")

        # Key status cards — now with tri-state dots + masked preview + Edit
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

        self._key_card_dots = {}  # env_var -> (dot_label, status_label)

        for i, (name, env_key, desc) in enumerate(key_defs):
            card = ctk.CTkFrame(keys_grid, fg_color=GLASS_BG, corner_radius=6, height=76)
            card.grid(row=i // 2, column=i % 2, padx=4, pady=4, sticky="nsew")
            card.grid_propagate(False)

            raw_val = _read_raw_secret(env_key)
            has_key = bool(raw_val)
            masked = _mask_key(raw_val)

            # Tri-state: red=missing, orange=set but not validated, green=valid
            if not has_key:
                dot_color = RED
                status_text = "not set"
            else:
                dot_color = ORANGE  # will upgrade to green after probe
                status_text = "checking..."

            # Status dot
            dot_lbl = ctk.CTkLabel(card, text="\u25cf", font=("Consolas", 14),
                                   text_color=dot_color)
            dot_lbl.place(x=10, y=8)
            # Provider name
            ctk.CTkLabel(card, text=name, font=FONT_BOLD,
                         text_color=TEXT).place(x=28, y=6)
            # Masked preview (last 4 chars)
            ctk.CTkLabel(card, text=masked if has_key else "\u2014",
                         font=("Consolas", 9), text_color=DIM).place(x=28, y=26)
            # Description
            ctk.CTkLabel(card, text=desc, font=FONT_XS,
                         text_color=DIM).place(x=10, y=54)
            # Status text (top-right)
            status_lbl = ctk.CTkLabel(card, text=status_text, font=("Consolas", 9),
                                      text_color=dot_color)
            status_lbl.place(relx=1.0, x=-10, y=8, anchor="ne")
            # Edit button (right side, middle)
            ctk.CTkButton(card, text="Edit", font=FONT_XS,
                          width=42, height=20, corner_radius=4,
                          fg_color=BG3, hover_color=BG2,
                          command=lambda ek=env_key, n=name: self._quick_edit_key(ek, n)
                          ).place(relx=1.0, x=-10, y=32, anchor="ne")

            self._key_card_dots[env_key] = (dot_lbl, status_lbl)

            # Launch background validation probe for keys that are set
            if has_key:
                self._launch_key_probe(env_key, raw_val)

        # Full key manager button
        self._section_header(panel, "Advanced")
        adv_frame = ctk.CTkFrame(panel, fg_color=GLASS_BG, corner_radius=6)
        adv_frame.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(adv_frame, text="Add, rotate, and manage API keys with the full key manager.",
                     font=FONT_XS, text_color=DIM
                     ).pack(padx=12, pady=(10, 4), anchor="w")
        ctk.CTkButton(adv_frame, text="Open Key Manager", font=FONT_SM,
                      width=150, height=28, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=lambda: KeyManagerDialog(self._parent)
                      ).pack(padx=12, pady=(0, 10), anchor="w")

    def _launch_key_probe(self, env_key: str, raw_val: str):
        """Run validation probe in background thread, update card dot on completion."""
        def _bg():
            result = _probe_key(env_key, raw_val)
            try:
                self._parent.after(0, lambda: self._update_key_dot(env_key, result))
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()

    def _update_key_dot(self, env_key: str, valid: bool | None):
        """Update card dot color based on probe result."""
        if env_key not in self._key_card_dots:
            return
        dot_lbl, status_lbl = self._key_card_dots[env_key]
        if valid is True:
            dot_lbl.configure(text_color=GREEN)
            status_lbl.configure(text="valid", text_color=GREEN)
        elif valid is False:
            dot_lbl.configure(text_color=RED)
            status_lbl.configure(text="invalid", text_color=RED)
        else:
            # None = network error, keep orange (set but unverified)
            dot_lbl.configure(text_color=ORANGE)
            status_lbl.configure(text="set (unverified)", text_color=ORANGE)

    def _quick_edit_key(self, env_key: str, label: str):
        """Inline edit from the settings card — opens input dialog, saves, re-probes."""
        dialog = ctk.CTkInputDialog(
            text=f"Enter value for {label}:\n({env_key})\n\nLeave blank to cancel.",
            title=f"Set {env_key}")
        value = dialog.get_input()
        if not value or not value.strip():
            return
        value = value.strip()

        def _save_bg():
            try:
                secrets_file = Path.home() / ".secrets"
                lines = []
                found = False
                if secrets_file.exists():
                    for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                        stripped = line.strip()
                        raw = stripped[7:].strip() if stripped.startswith("export ") else stripped
                        if "=" in raw and raw.split("=", 1)[0].strip() == env_key:
                            lines.append(f"export {env_key}={value}")
                            found = True
                        else:
                            lines.append(line)
                if not found:
                    lines.append(f"export {env_key}={value}")
                secrets_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.environ[env_key] = value
                # Update card immediately to orange, then probe
                self._parent.after(0, lambda: self._update_key_dot(env_key, None))
                result = _probe_key(env_key, value)
                self._parent.after(0, lambda: self._update_key_dot(env_key, result))
            except Exception:
                pass
        threading.Thread(target=_save_bg, daemon=True).start()


class KeyManagerDialog(ctk.CTkToplevel):

    def __init__(self, parent):
        L = _launcher()
        self.REGISTRY_FILE = L.FLEET_DIR / "keys_registry.toml"
        self.SECRETS_FILE  = Path.home() / ".wsl_secrets_cache"  # local cache from WSL read

        super().__init__(parent)
        self.title("BigEd CC \u2014 API Key Manager")
        self.geometry("780x540")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.grab_set()

        ico = L.HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._rows = {}   # key_name -> {dot, status_lbl}
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
        ctk.CTkLabel(hdr, text="Keys stored in ~/.secrets  |  masked values shown  |  probes run on load",
                     font=FONT_XS, text_color=DIM
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
        ctk.CTkButton(bar, text="\u2713 Validate All", font=FONT_SM, width=120, height=30,
                      fg_color=BG2, hover_color=BG, command=self._validate_all_keys
                      ).grid(row=0, column=3, padx=6, pady=8)

        self._scan_lbl = ctk.CTkLabel(bar, text="", font=FONT_SM, text_color=DIM)
        self._scan_lbl.grid(row=0, column=4, padx=12, sticky="e")

    def _load_keys(self):
        # Clear existing rows (keep header)
        for w in list(self._scroll.winfo_children())[1:]:
            w.destroy()
        self._rows = {}

        registry = self._read_registry()
        secrets  = self._read_secrets_via_wsl()
        raw_vals = self._read_raw_secrets()

        for i, info in enumerate(registry):
            name    = info.get("env_var", "")
            label   = info.get("label", name)
            purpose = info.get("purpose", "")
            tier    = info.get("tier", "")
            masked  = secrets.get(name, "")
            is_set  = bool(masked) and masked not in ("EMPTY", "not set")

            # Tri-state dot: red=missing, orange=set/unverified, green=valid
            if not is_set:
                dot_color = RED
                status_txt = "MISSING"
            else:
                dot_color = ORANGE  # will be updated after probe
                status_txt = "SET"
            status_col = dot_color
            tier_col   = {"free": DIM, "freemium": ORANGE, "paid": "#4488ff"}.get(tier, DIM)

            row = ctk.CTkFrame(self._scroll, fg_color=BG if i % 2 else "#1e1e1e",
                               corner_radius=3)
            row.grid(row=i + 1, column=0, sticky="ew", padx=8, pady=1)
            row.grid_columnconfigure(1, weight=1)

            dot_lbl = ctk.CTkLabel(row, text="\u25cf", font=("Consolas", 13),
                                   text_color=dot_color, width=18)
            dot_lbl.grid(row=0, column=0, padx=(8, 2), pady=6)

            name_frame = ctk.CTkFrame(row, fg_color="transparent")
            name_frame.grid(row=0, column=1, sticky="w", padx=4)
            ctk.CTkLabel(name_frame, text=name, font=("Consolas", 10, "bold"),
                         text_color=TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(name_frame, text=label, font=FONT_XS,
                         text_color=DIM, anchor="w").pack(anchor="w")

            ctk.CTkLabel(row, text=tier, font=FONT_XS,
                         text_color=tier_col, width=70).grid(row=0, column=2, padx=4)
            status_lbl = ctk.CTkLabel(row, text=status_txt, font=("RuneScape Bold 12", 10, "bold"),
                                      text_color=status_col, width=90)
            status_lbl.grid(row=0, column=3, padx=4)
            ctk.CTkLabel(row, text=masked or "\u2014", font=("Consolas", 9),
                         text_color=DIM, width=160, anchor="w").grid(row=0, column=4, padx=4)
            ctk.CTkButton(row, text="Edit", font=FONT_SM, width=60, height=24,
                          fg_color=ACCENT, hover_color=ACCENT_H,
                          command=lambda n=name, lbl=label: self._edit_key(n, lbl)
                          ).grid(row=0, column=5, padx=(4, 8), pady=4)

            # Purpose tooltip row
            ctk.CTkLabel(row, text=f"  {purpose[:90]}", font=FONT_XS,
                         text_color=DIM, anchor="w"
                         ).grid(row=1, column=1, columnspan=5, sticky="w", padx=4, pady=(0, 6))

            self._rows[name] = {"dot": dot_lbl, "status": status_lbl}

            # Auto-probe keys that are set
            if is_set and name in raw_vals:
                self._launch_probe(name, raw_vals[name])

    def _launch_probe(self, env_var: str, raw_val: str):
        """Background probe for a single key; updates row dot + status on completion."""
        def _bg():
            result = _probe_key(env_var, raw_val)
            try:
                self.after(0, lambda: self._apply_probe_result(env_var, result))
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()

    def _apply_probe_result(self, env_var: str, valid: bool | None):
        """Update the row dot and status label after probe completes."""
        if env_var not in self._rows:
            return
        info = self._rows[env_var]
        if valid is True:
            info["dot"].configure(text_color=GREEN)
            info["status"].configure(text="VALID", text_color=GREEN)
        elif valid is False:
            info["dot"].configure(text_color=RED)
            info["status"].configure(text="INVALID", text_color=RED)
        else:
            info["dot"].configure(text_color=ORANGE)
            info["status"].configure(text="SET", text_color=ORANGE)

    def _validate_all_keys(self):
        """Re-probe all keys that have values set."""
        self._scan_lbl.configure(text="Validating...", text_color=ORANGE)
        raw_vals = self._read_raw_secrets()
        count = 0
        for env_var, raw in raw_vals.items():
            if raw and env_var in self._rows:
                self._launch_probe(env_var, raw)
                count += 1
        self.after(3000, lambda: self._scan_lbl.configure(
            text=f"Probed {count} keys", text_color=GREEN))

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

    def _read_raw_secrets(self) -> dict[str, str]:
        """Read raw (unmasked) key values for validation probes."""
        secrets_file = Path.home() / ".secrets"
        raw = {}
        try:
            if not secrets_file.exists():
                return raw
            for line in secrets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line or line.startswith("#"):
                    continue
                k, _, v = line.partition("=")
                k = k.strip(); v = v.strip().strip('"').strip("'")
                if v and v != "REPLACE_ME":
                    raw[k] = v
        except Exception:
            pass
        # Also check env vars (may have been set at runtime)
        for env_var in _VALIDATION_PROBES:
            env_val = os.environ.get(env_var, "").strip()
            if env_val and env_var not in raw:
                raw[env_var] = env_val
        return raw

    def _edit_key(self, key_name: str, label: str):
        dialog = ctk.CTkInputDialog(
            text=f"Enter value for {label}:\n({key_name})\n\nLeave blank to cancel.",
            title=f"Set {key_name}")
        value = dialog.get_input()
        if not value or not value.strip():
            return
        value = value.strip()
        # Save key natively to ~/.secrets, then validate
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
                # Probe the newly saved key
                probe_result = _probe_key(key_name, value)
                if probe_result is True:
                    msg = f"\u2713 {key_name} saved \u2014 valid"
                    col = GREEN
                elif probe_result is False:
                    msg = f"\u2713 {key_name} saved \u2014 invalid key"
                    col = RED
                else:
                    msg = f"\u2713 {key_name} saved"
                    col = GREEN
                self.after(0, lambda: (
                    self._scan_lbl.configure(text=msg, text_color=col),
                    self._apply_probe_result(key_name, probe_result),
                    self.after(600, self._load_keys)))
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
