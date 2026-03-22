"""
Customers Module — Deployment tracking for BigEd CC.

Tracks connected customer deployments, fleet versions, air-gap status, ping health.
Cross-module: Onboarding completion feeds into customer status.
"""
import customtkinter as ctk
from data_access import DataAccess

BG = BG2 = BG3 = ACCENT = ACCENT_H = GOLD = TEXT = DIM = GREEN = ORANGE = RED = ""
FONT_SM = ("RuneScape Plain 11", 10)


class Module:
    NAME = "customers"
    LABEL = "Customers"
    VERSION = "0.23"
    DEFAULT_ENABLED = False
    DEPENDS_ON = []

    DATA_SCHEMA = {
        "table": "customers",
        "fields": {
            "name": {"type": "text", "required": True, "unique": True},
            "fleet_version": {"type": "text", "required": False},
            "contact": {"type": "text", "required": False},
            "notes": {"type": "text", "required": False},
            "air_gapped": {"type": "integer", "required": False, "default": 0},
            "status": {"type": "text", "required": False, "default": "Unknown",
                       "enum": ["Online", "Degraded", "Offline", "Unknown"]},
            "last_ping": {"type": "text", "required": False, "default": "-"},
        },
        "retention_days": None,
    }

    STATUS_COLORS = {}

    def __init__(self, app):
        self.app = app
        self._init_theme()
        self._rows = []
        self._scroll = None
        self._dal_inst = None

    @property
    def _dal(self):
        if self._dal_inst is None:
            import launcher
            self._dal_inst = DataAccess(launcher.DB_PATH)
        return self._dal_inst

    def _init_theme(self):
        global BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM, GREEN, ORANGE, RED, FONT_SM
        import launcher
        BG = launcher.BG; BG2 = launcher.BG2; BG3 = launcher.BG3
        ACCENT = launcher.ACCENT; ACCENT_H = launcher.ACCENT_H
        GOLD = launcher.GOLD; TEXT = launcher.TEXT; DIM = launcher.DIM
        GREEN = launcher.GREEN; ORANGE = launcher.ORANGE; RED = launcher.RED
        FONT_SM = launcher.FONT_SM
        self.STATUS_COLORS = {"Online": GREEN, "Degraded": ORANGE,
                              "Offline": RED, "Unknown": DIM}

    def _db_query_bg(self, query_fn, callback):
        self.app._db_query_bg(query_fn, callback)

    def build_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="Connected deployments",
                     font=FONT_SM, text_color=DIM).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="+ Add", font=FONT_SM, height=26, width=70,
                      fg_color=BG3, hover_color=BG,
                      command=self._add_dialog
                      ).grid(row=0, column=2, sticky="e")
        ctk.CTkButton(hdr, text="Ping All", font=FONT_SM, height=26, width=80,
                      fg_color=BG3, hover_color=BG,
                      command=self._ping_all
                      ).grid(row=0, column=3, padx=(6, 0), sticky="e")

        self._scroll = ctk.CTkScrollableFrame(parent, fg_color=BG2, corner_radius=4)
        self._scroll.grid(row=1, column=0, sticky="nsew")
        self._scroll.grid_columnconfigure((0, 1, 2, 3, 4, 5), weight=1)

        for col, txt in enumerate(["Customer", "Status", "Last Ping",
                                   "Fleet Ver", "Isolation", ""]):
            ctk.CTkLabel(self._scroll, text=txt, font=("RuneScape Bold 12", 9, "bold"),
                         text_color=DIM, anchor="w"
                         ).grid(row=0, column=col, padx=6, pady=(2, 4), sticky="ew")

        self._rows = []
        self.on_refresh()

    def on_refresh(self):
        def _fetch(_con):
            return self._dal.query("customers")

        def _render(records):
            for w in self._rows:
                w.destroy()
            self._rows.clear()
            records = records or []

            for i, rec in enumerate(records):
                row = i + 1
                bg = BG3 if i % 2 == 0 else BG2
                st = rec.get("status", "Unknown")
                airgap = rec.get("air_gapped", False)

                cols = [
                    (rec.get("name", "-"), TEXT, "w"),
                    (f"* {st}", self.STATUS_COLORS.get(st, DIM), "w"),
                    (rec.get("last_ping", "-"), DIM, "center"),
                    (rec.get("fleet_version", "-"), DIM, "center"),
                    ("Air-Gapped" if airgap else "Connected",
                     ORANGE if airgap else GREEN, "center"),
                ]
                widgets = []
                for col, (txt, color, anchor) in enumerate(cols):
                    lbl = ctk.CTkLabel(self._scroll, text=txt, font=FONT_SM,
                                       text_color=color, anchor=anchor, fg_color=bg)
                    lbl.grid(row=row, column=col, padx=6, pady=2, sticky="ew")
                    widgets.append(lbl)

                btn = ctk.CTkButton(self._scroll, text="Edit", font=FONT_SM,
                                    width=28, height=22, fg_color=bg, hover_color=BG3,
                                    command=lambda r=rec: self._edit_dialog(r))
                btn.grid(row=row, column=5, padx=4, pady=2)
                widgets.append(btn)
                self._rows.extend(widgets)

        self._db_query_bg(_fetch, _render)

    def on_close(self):
        pass

    def get_settings(self) -> dict:
        return {"enabled": True}

    def apply_settings(self, cfg: dict):
        pass

    def export_data(self) -> list[dict]:
        return self._dal.query("customers")

    def _add_dialog(self):
        self._edit_dialog({})

    def _edit_dialog(self, rec: dict):
        win = ctk.CTkToplevel(self.app)
        try:
            import launcher as _L
            _ico = _L.HERE / "brick.ico"
            if _ico.exists():
                win.iconbitmap(str(_ico))
        except Exception:
            pass
        win.title("Customer Deployment")
        win.geometry("420x340")
        win.resizable(False, False)
        win.configure(fg_color=BG)
        win.grab_set()

        fields = [
            ("Name", rec.get("name", "")),
            ("Fleet Version", rec.get("fleet_version", "")),
            ("Contact", rec.get("contact", "")),
            ("Notes", rec.get("notes", "")),
        ]
        entries = {}
        for i, (lbl, val) in enumerate(fields):
            ctk.CTkLabel(win, text=lbl, font=FONT_SM, text_color=DIM,
                         anchor="w").grid(row=i, column=0, padx=(14, 6), pady=4, sticky="w")
            e = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2,
                             border_color="#444", text_color=TEXT)
            e.insert(0, val)
            e.grid(row=i, column=1, padx=(0, 14), pady=4, sticky="ew")
            entries[lbl] = e
        win.grid_columnconfigure(1, weight=1)

        row_idx = len(fields)
        airgap_var = ctk.BooleanVar(value=rec.get("air_gapped", False))
        ctk.CTkCheckBox(win, text="Air-Gapped (no remote access)",
                        variable=airgap_var, font=FONT_SM, text_color=TEXT,
                        fg_color=ORANGE, hover_color="#cc7700"
                        ).grid(row=row_idx, column=0, columnspan=2,
                               padx=14, pady=6, sticky="w")

        def _save():
            new = {k.lower().replace(" ", "_"): v.get() for k, v in entries.items()}
            old_name = rec.get("name", "")
            if old_name:
                self._dal.delete("customers", where={"name": old_name})
            if new.get("name"):
                self._dal.insert("customers", {
                    "name": new.get("name", ""),
                    "fleet_version": new.get("fleet_version", ""),
                    "contact": new.get("contact", ""),
                    "notes": new.get("notes", ""),
                    "air_gapped": int(airgap_var.get()),
                    "status": rec.get("status", "Unknown"),
                    "last_ping": rec.get("last_ping", "-"),
                })
            self.on_refresh()
            win.destroy()

        ctk.CTkButton(win, text="Save", font=FONT_SM, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_save
                      ).grid(row=row_idx + 1, column=0, columnspan=2,
                             padx=14, pady=(10, 14), sticky="ew")

    def _ping_all(self):
        self.app._log_output("Ping All: not yet implemented - will hit each customer fleet API.")
