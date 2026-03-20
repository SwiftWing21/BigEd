"""
v0.47: Restricted Owner Core — secure internal CRM, fleet diagnostics, key management.
This module requires BIGED_OWNER_KEY in ~/.secrets to activate.
Excluded from public dist/ builds via build.py.
"""
import json
import os
import customtkinter as ctk
from pathlib import Path

# Theme
BG = "#1a1a2e"
BG2 = "#16213e"
BG3 = "#0f3460"
TEXT = "#e0e0e0"
DIM = "#888888"
ACCENT = "#4fc3f7"
RED = "#ef5350"
GREEN = "#66bb6a"
GOLD = "#ffd54f"
FONT = ("RuneScape Plain 12", 11)
FONT_SM = ("RuneScape Plain 11", 10)


def _verify_owner_key() -> bool:
    """Verify BIGED_OWNER_KEY is set and valid."""
    key = os.environ.get("BIGED_OWNER_KEY", "")
    if not key:
        # Try loading from ~/.secrets
        secrets = Path.home() / ".secrets"
        if secrets.exists():
            for line in secrets.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("export BIGED_OWNER_KEY="):
                    key = line.split("=", 1)[1].strip().strip("'\"")
                    break
    # Validate: must be non-empty and at least 32 chars (basic check)
    return len(key) >= 32


class Module:
    NAME = "owner_core"
    LABEL = "Owner"
    VERSION = "0.47"
    DEFAULT_ENABLED = False
    DEPENDS_ON = []

    DATA_SCHEMA = {
        "table": "owner_customers",
        "fields": {
            "name": "TEXT NOT NULL",
            "fleet_id": "TEXT",
            "status": "TEXT DEFAULT 'active'",
            "deployed_at": "TEXT",
            "license_tier": "TEXT DEFAULT 'standard'",
            "notes": "TEXT",
        },
        "retention_days": None,
    }

    def __init__(self, app):
        self.app = app
        self._verified = _verify_owner_key()
        if not self._verified:
            return
        # Initialize DAL
        try:
            from data_access import DataAccess
            import launcher
            self._dal = DataAccess(launcher.DB_PATH)
            self._dal.ensure_table("owner_customers", self.DATA_SCHEMA["fields"])
        except Exception:
            pass

    def build_tab(self, parent):
        if not self._verified:
            # Show access denied
            frame = ctk.CTkFrame(parent, fg_color=BG)
            frame.pack(fill="both", expand=True)
            ctk.CTkLabel(frame, text="OWNER ACCESS REQUIRED",
                        font=("RuneScape Bold 12", 16, "bold"), text_color=RED
                        ).pack(pady=(80, 10))
            ctk.CTkLabel(frame, text="Set BIGED_OWNER_KEY in ~/.secrets to access this module.",
                        font=FONT, text_color=DIM
                        ).pack()
            return

        self._frame = ctk.CTkFrame(parent, fg_color=BG)
        self._frame.pack(fill="both", expand=True)

        # Header
        hdr = ctk.CTkFrame(self._frame, fg_color=BG3, height=44, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="OWNER DASHBOARD", font=("RuneScape Bold 12", 13, "bold"),
                    text_color=GOLD).pack(side="left", padx=14, pady=10)

        # Three sections: Customer Fleet, Global Keys, Remote Diagnostics
        sections = ctk.CTkFrame(self._frame, fg_color=BG)
        sections.pack(fill="both", expand=True, padx=10, pady=10)
        sections.grid_columnconfigure((0, 1, 2), weight=1)
        sections.grid_rowconfigure(0, weight=1)

        # Section 1: Customer Fleet Management
        self._build_customer_section(sections, 0)
        # Section 2: Global Key Manager
        self._build_keys_section(sections, 1)
        # Section 3: Remote Diagnostics
        self._build_diagnostics_section(sections, 2)

    def _build_customer_section(self, parent, col):
        frame = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=8)
        frame.grid(row=0, column=col, sticky="nsew", padx=4)
        ctk.CTkLabel(frame, text="Customer Fleets", font=("RuneScape Bold 12", 12, "bold"),
                    text_color=ACCENT).pack(padx=10, pady=(10, 4))

        self._customer_list = ctk.CTkTextbox(frame, font=("Consolas", 10),
                                              fg_color=BG, text_color=TEXT, height=200)
        self._customer_list.pack(fill="both", expand=True, padx=8, pady=4)

        btn_frame = ctk.CTkFrame(frame, fg_color=BG2)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkButton(btn_frame, text="+ Add", width=70, height=26, font=FONT_SM,
                      fg_color=BG3, command=self._add_customer).pack(side="left", padx=2)
        ctk.CTkButton(btn_frame, text="Refresh", width=70, height=26, font=FONT_SM,
                      fg_color=BG3, command=self.on_refresh).pack(side="left", padx=2)

    def _build_keys_section(self, parent, col):
        frame = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=8)
        frame.grid(row=0, column=col, sticky="nsew", padx=4)
        ctk.CTkLabel(frame, text="Global Key Audit", font=("RuneScape Bold 12", 12, "bold"),
                    text_color=ACCENT).pack(padx=10, pady=(10, 4))

        self._keys_text = ctk.CTkTextbox(frame, font=("Consolas", 10),
                                          fg_color=BG, text_color=TEXT, height=200)
        self._keys_text.pack(fill="both", expand=True, padx=8, pady=4)

    def _build_diagnostics_section(self, parent, col):
        frame = ctk.CTkFrame(parent, fg_color=BG2, corner_radius=8)
        frame.grid(row=0, column=col, sticky="nsew", padx=4)
        ctk.CTkLabel(frame, text="Remote Diagnostics", font=("RuneScape Bold 12", 12, "bold"),
                    text_color=ACCENT).pack(padx=10, pady=(10, 4))

        self._diag_text = ctk.CTkTextbox(frame, font=("Consolas", 10),
                                          fg_color=BG, text_color=TEXT, height=200)
        self._diag_text.pack(fill="both", expand=True, padx=8, pady=4)

        ctk.CTkButton(frame, text="Generate Report", width=120, height=26, font=FONT_SM,
                      fg_color=BG3, command=self._generate_diag_report).pack(padx=8, pady=(0, 8))

    def _add_customer(self):
        """Add a new customer fleet entry."""
        if not hasattr(self, '_dal'):
            return
        # Simple dialog
        dialog = ctk.CTkInputDialog(text="Customer name:", title="Add Customer")
        name = dialog.get_input()
        if name:
            self._dal.insert("owner_customers", {"name": name, "status": "active"})
            self.on_refresh()

    def _generate_diag_report(self):
        """Generate fleet diagnostics report."""
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:5555/api/fleet/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                health = json.loads(resp.read())
            self._diag_text.configure(state="normal")
            self._diag_text.delete("1.0", "end")
            self._diag_text.insert("end", json.dumps(health, indent=2))
            self._diag_text.configure(state="disabled")
        except Exception as e:
            self._diag_text.configure(state="normal")
            self._diag_text.delete("1.0", "end")
            self._diag_text.insert("end", f"Error: {e}\n\nIs the dashboard running?")
            self._diag_text.configure(state="disabled")

    def on_refresh(self):
        if not self._verified or not hasattr(self, '_dal'):
            return
        try:
            customers = self._dal.query("owner_customers", order_by="name ASC")
            self._customer_list.configure(state="normal")
            self._customer_list.delete("1.0", "end")
            for c in customers:
                status_icon = "\u25cf" if c.get("status") == "active" else "\u25cb"
                self._customer_list.insert("end",
                    f"{status_icon} {c['name']:<20} {c.get('license_tier', 'standard'):<12} "
                    f"{c.get('deployed_at', 'not deployed')}\n")
            self._customer_list.configure(state="disabled")

            # Refresh keys audit
            secrets = Path.home() / ".secrets"
            if secrets.exists() and hasattr(self, '_keys_text'):
                lines = secrets.read_text(encoding="utf-8").splitlines()
                self._keys_text.configure(state="normal")
                self._keys_text.delete("1.0", "end")
                for line in lines:
                    if line.startswith("export ") and "=" in line:
                        key = line.split("=", 1)[0].replace("export ", "")
                        val = line.split("=", 1)[1].strip("'\" ")
                        masked = val[:4] + "..." + val[-4:] if len(val) > 10 else "***"
                        self._keys_text.insert("end", f"{key:<30} {masked}\n")
                self._keys_text.configure(state="disabled")
        except Exception:
            pass

    def on_close(self):
        if hasattr(self, '_dal'):
            self._dal.close()

    def get_settings(self) -> dict:
        return {"verified": self._verified}

    def apply_settings(self, cfg):
        pass

    def export_data(self) -> list:
        if not hasattr(self, '_dal'):
            return []
        return self._dal.query("owner_customers")

    def validate_record(self, data) -> tuple:
        if not data.get("name"):
            return False, "Name is required"
        return True, ""
