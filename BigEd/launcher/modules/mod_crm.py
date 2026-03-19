"""
CRM Module — Contact/lead management for BigEd CC.

Manages companies, contacts, lead stages, prospecting dispatch, and lead import.
"""
import base64
import json
import re
from pathlib import Path

import customtkinter as ctk
from data_access import DataAccess

# These are set by the module loader from the app's theme
BG = BG2 = BG3 = ACCENT = ACCENT_H = GOLD = TEXT = DIM = GREEN = ORANGE = RED = ""
FONT_SM = ("Segoe UI", 10)


def _shell_safe(s: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_.\-:]', '', s)


class Module:
    NAME = "crm"
    LABEL = "CRM"
    VERSION = "0.22"
    DEFAULT_ENABLED = False
    DEPENDS_ON = []

    # Data contract — schema for validation
    DATA_SCHEMA = {
        "table": "crm",
        "fields": {
            "company": {"type": "text", "required": True, "unique": True},
            "industry": {"type": "text", "required": False},
            "contact": {"type": "text", "required": False},
            "email": {"type": "text", "required": False},
            "phone": {"type": "text", "required": False},
            "stage": {"type": "text", "required": False, "default": "Lead",
                      "enum": ["Lead", "Prospect", "Active", "Churned", "Partner"]},
            "notes": {"type": "text", "required": False},
        },
        "retention_days": None,  # no auto-expiry
    }

    STAGE_COLORS = {}

    def __init__(self, app):
        self.app = app
        self._init_theme()
        self._rows = []
        self._search_var = None
        self._dal_inst = None

    @property
    def _dal(self):
        if self._dal_inst is None:
            import launcher
            self._dal_inst = DataAccess(launcher.DB_PATH)
        return self._dal_inst

    def _init_theme(self):
        global BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM, GREEN, ORANGE, RED, FONT_SM
        # Pull theme constants from app module
        import launcher
        BG = launcher.BG
        BG2 = launcher.BG2
        BG3 = launcher.BG3
        ACCENT = launcher.ACCENT
        ACCENT_H = launcher.ACCENT_H
        GOLD = launcher.GOLD
        TEXT = launcher.TEXT
        DIM = launcher.DIM
        GREEN = launcher.GREEN
        ORANGE = launcher.ORANGE
        RED = launcher.RED
        FONT_SM = launcher.FONT_SM
        self.STAGE_COLORS = {
            "Lead": DIM, "Prospect": ORANGE, "Active": GREEN,
            "Churned": RED, "Partner": GOLD,
        }

    def _db_query_bg(self, query_fn, callback):
        self.app._db_query_bg(query_fn, callback)

    def build_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(0, weight=1)

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self.on_refresh())
        ctk.CTkEntry(hdr, textvariable=self._search_var, font=FONT_SM,
                     fg_color=BG2, border_color="#444", text_color=TEXT,
                     placeholder_text="Search companies / contacts..."
                     ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(hdr, text="+ Add", font=FONT_SM, height=26, width=70,
                      fg_color=BG3, hover_color=BG,
                      command=self._add_dialog
                      ).grid(row=0, column=1, sticky="e", padx=(0, 4))
        ctk.CTkButton(hdr, text="Prospect", font=FONT_SM, height=26, width=90,
                      fg_color="#1a2a3a", hover_color="#253545",
                      command=self._prospect_dialog
                      ).grid(row=0, column=2, sticky="e", padx=(0, 4))
        ctk.CTkButton(hdr, text="Import Leads", font=FONT_SM, height=26, width=110,
                      fg_color="#1a3a1a", hover_color="#253a25",
                      command=self._import_leads_dialog
                      ).grid(row=0, column=3, sticky="e")

        self._scroll = ctk.CTkScrollableFrame(parent, fg_color=BG2, corner_radius=4)
        self._scroll.grid(row=1, column=0, sticky="nsew")
        self._scroll.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        for col, txt in enumerate(["Company", "Industry", "Contact", "Stage", ""]):
            ctk.CTkLabel(self._scroll, text=txt, font=("Segoe UI", 9, "bold"),
                         text_color=DIM, anchor="w"
                         ).grid(row=0, column=col, padx=6, pady=(2, 4), sticky="ew")

        self._rows = []
        self.on_refresh()

    def on_refresh(self):
        query = self._search_var.get().lower() if self._search_var else ""

        def _fetch(_con):
            return self._dal.query("crm")

        def _render(records):
            for w in self._rows:
                w.destroy()
            self._rows.clear()
            records = records or []

            displayed = 0
            for rec in records:
                if query and not any(query in str(v).lower() for v in rec.values()):
                    continue
                row = displayed + 1
                bg = BG3 if displayed % 2 == 0 else BG2
                displayed += 1
                for col, (key, anchor) in enumerate([
                    ("company", "w"), ("industry", "w"),
                    ("contact", "w"), ("stage", "center"),
                ]):
                    txt = rec.get(key, "-")
                    color = self.STAGE_COLORS.get(txt, DIM) if key == "stage" else TEXT
                    lbl = ctk.CTkLabel(self._scroll, text=txt, font=FONT_SM,
                                       text_color=color, anchor=anchor, fg_color=bg)
                    lbl.grid(row=row, column=col, padx=6, pady=2, sticky="ew")
                    self._rows.append(lbl)

                btn = ctk.CTkButton(self._scroll, text="Edit", font=FONT_SM,
                                    width=28, height=22, fg_color=bg, hover_color=BG3,
                                    command=lambda r=rec: self._edit_dialog(r))
                btn.grid(row=row, column=4, padx=4, pady=2)
                self._rows.append(btn)

        self._db_query_bg(_fetch, _render)

    def on_close(self):
        pass

    def get_settings(self) -> dict:
        return {"enabled": True}

    def apply_settings(self, cfg: dict):
        pass

    def export_data(self) -> list[dict]:
        """Export all CRM records for data portability."""
        return self._dal.query("crm")

    def validate_record(self, data: dict) -> tuple[bool, str]:
        """Validate a record against the data contract."""
        schema = self.DATA_SCHEMA["fields"]
        for field, spec in schema.items():
            if spec.get("required") and not data.get(field):
                return False, f"Missing required field: {field}"
            if "enum" in spec and data.get(field) and data[field] not in spec["enum"]:
                return False, f"Invalid value for {field}: {data[field]}"
        return True, "OK"

    # ── Dialogs ────────────────────────────────────────────────────────────────

    def _add_dialog(self):
        self._edit_dialog({})

    def _edit_dialog(self, rec: dict):
        win = ctk.CTkToplevel(self.app)
        win.title("CRM - Contact")
        win.geometry("400x340")
        win.configure(fg_color=BG)
        win.grab_set()

        fields = [
            ("Company", rec.get("company", "")),
            ("Industry", rec.get("industry", "")),
            ("Contact", rec.get("contact", "")),
            ("Email", rec.get("email", "")),
            ("Phone", rec.get("phone", "")),
            ("Stage", rec.get("stage", "Lead")),
            ("Notes", rec.get("notes", "")),
        ]
        entries = {}
        for i, (lbl, val) in enumerate(fields):
            ctk.CTkLabel(win, text=lbl, font=FONT_SM, text_color=DIM,
                         anchor="w").grid(row=i, column=0, padx=(14, 6), pady=3, sticky="w")
            e = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2,
                             border_color="#444", text_color=TEXT)
            e.insert(0, val)
            e.grid(row=i, column=1, padx=(0, 14), pady=3, sticky="ew")
            entries[lbl] = e
        win.grid_columnconfigure(1, weight=1)

        def _save():
            new = {k.lower(): v.get() for k, v in entries.items()}
            valid, msg = self.validate_record(new)
            if not valid:
                self.app._log_output(f"CRM validation: {msg}")
                return
            old_company = rec.get("company", "")
            if old_company:
                self._dal.delete("crm", where={"company": old_company})
            if new.get("company"):
                self._dal.insert("crm", {
                    "company": new.get("company", ""),
                    "industry": new.get("industry", ""),
                    "contact": new.get("contact", ""),
                    "email": new.get("email", ""),
                    "phone": new.get("phone", ""),
                    "stage": new.get("stage", "Lead"),
                    "notes": new.get("notes", ""),
                })
            self.on_refresh()
            win.destroy()

        ctk.CTkButton(win, text="Save", font=FONT_SM, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_save
                      ).grid(row=len(fields), column=0, columnspan=2,
                             padx=14, pady=(10, 14), sticky="ew")

    def _prospect_dialog(self):
        win = ctk.CTkToplevel(self.app)
        win.title("Prospect - Find Leads")
        win.geometry("360x260")
        win.configure(fg_color=BG)
        win.grab_set()

        fields = [
            ("Industry", "healthcare"),
            ("City", "Watsonville CA"),
            ("Zip Code", "95076"),
        ]
        entries = {}
        for i, (lbl, placeholder) in enumerate(fields):
            ctk.CTkLabel(win, text=lbl, font=FONT_SM, text_color=DIM,
                         anchor="w").grid(row=i, column=0, padx=(14, 6), pady=4, sticky="w")
            e = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2, border_color="#444",
                             text_color=TEXT, placeholder_text=placeholder)
            e.grid(row=i, column=1, padx=(0, 14), pady=4, sticky="ew")
            entries[lbl] = e
        win.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(win, text="Dispatches to sales agent via fleet task queue.",
                     font=("Segoe UI", 9), text_color=DIM, wraplength=300
                     ).grid(row=len(fields), column=0, columnspan=2, padx=14, pady=(6, 2))

        def _dispatch():
            industry = entries["Industry"].get().strip() or "healthcare"
            city = entries["City"].get().strip() or "Watsonville CA"
            zip_code = entries["Zip Code"].get().strip() or "95076"
            payload_json = json.dumps(
                {"industry": industry, "city": city, "zip_code": zip_code})
            self.app._dispatch_raw("lead_research", payload_json,
                                   assigned_to="sales",
                                   msg=f"Prospecting: {industry} / {city} {zip_code}")
            win.destroy()

        ctk.CTkButton(win, text="Dispatch", font=FONT_SM, height=30,
                      fg_color="#1a2a3a", hover_color="#253545", command=_dispatch
                      ).grid(row=len(fields) + 1, column=0, columnspan=2,
                             padx=14, pady=(6, 14), sticky="ew")

    def _import_leads_dialog(self):
        import launcher
        leads_dir = launcher.LEADS_DIR
        jsonl_files = sorted(leads_dir.glob("*.jsonl"), reverse=True)

        raw_leads = []
        for f in jsonl_files[:5]:
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        raw_leads.append(json.loads(line))
            except Exception:
                continue

        if not raw_leads:
            self.app._log_output("No lead files found in knowledge/leads/. Run Prospect first.")
            return

        seen_titles = set()
        leads = []
        for r in raw_leads:
            t = r.get("title", "").strip()
            if t and t not in seen_titles:
                seen_titles.add(t)
                leads.append(r)

        win = ctk.CTkToplevel(self.app)
        win.title(f"Import Leads ({len(leads)} found)")
        win.geometry("600x480")
        win.configure(fg_color=BG)
        win.grab_set()

        ctk.CTkLabel(win, text="Select leads to import as CRM contacts (stage: Lead):",
                     font=FONT_SM, text_color=DIM
                     ).pack(padx=14, pady=(10, 4), anchor="w")

        scroll = ctk.CTkScrollableFrame(win, fg_color=BG2, corner_radius=4)
        scroll.pack(fill="both", expand=True, padx=10, pady=4)
        scroll.grid_columnconfigure(1, weight=1)

        check_vars = []
        for i, lead in enumerate(leads):
            var = ctk.BooleanVar(value=False)
            check_vars.append((var, lead))
            bg = BG3 if i % 2 == 0 else BG2
            ctk.CTkCheckBox(scroll, text="", variable=var, fg_color=ACCENT,
                            hover_color=ACCENT_H, width=20
                            ).grid(row=i, column=0, padx=(6, 2), pady=2, sticky="w")
            label_text = f"{lead.get('title', '-')}  [{lead.get('sector', '?')} / {lead.get('zip', '')}]"
            ctk.CTkLabel(scroll, text=label_text, font=FONT_SM, text_color=TEXT,
                         anchor="w", fg_color=bg, wraplength=460
                         ).grid(row=i, column=1, padx=4, pady=2, sticky="ew")

        def _select_all():
            for v, _ in check_vars:
                v.set(True)

        def _do_import():
            selected = [(v, lead) for v, lead in check_vars if v.get()]
            if not selected:
                return
            imported = 0
            for _, lead in selected:
                title = lead.get("title", "").strip()
                sector = lead.get("sector", "")
                snippet = lead.get("snippet", "")
                url = lead.get("url", "")
                notes = f"{snippet}\n{url}".strip() if snippet or url else ""
                if not title:
                    continue
                try:
                    # INSERT OR IGNORE needs raw SQL — DAL insert doesn't support OR IGNORE
                    self._dal.execute(
                        "INSERT OR IGNORE INTO crm (company, industry, stage, notes)"
                        " VALUES (?,?,?,?)",
                        (title, sector, "Lead", notes))
                    imported += 1
                except Exception:
                    pass
            self.on_refresh()
            self.app._log_output(f"Imported {imported} leads into CRM.")
            win.destroy()

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(4, 10))
        btn_row.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(btn_row, text="Select All", font=FONT_SM, height=28, width=90,
                      fg_color=BG3, hover_color=BG, command=_select_all
                      ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(btn_row, text="Import Selected", font=FONT_SM, height=28,
                      fg_color="#1a3a1a", hover_color="#253a25", command=_do_import
                      ).grid(row=0, column=1, sticky="e")
