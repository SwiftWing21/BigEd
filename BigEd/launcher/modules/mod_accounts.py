"""
Accounts Module — Service account tracking for BigEd CC.

Tracks API services, usage vs free-tier limits, upgrade priorities, and costs.
"""
import json
import customtkinter as ctk

BG = BG2 = BG3 = ACCENT = ACCENT_H = GOLD = TEXT = DIM = GREEN = ORANGE = RED = ""
FONT_SM = ("Segoe UI", 10)


class Module:
    NAME = "accounts"
    LABEL = "Accounts"
    VERSION = "0.22"
    DEFAULT_ENABLED = False
    DEPENDS_ON = []

    DATA_SCHEMA = {
        "table": "accounts",
        "fields": {
            "service": {"type": "text", "required": True, "unique": True},
            "category": {"type": "text", "required": False},
            "tier": {"type": "text", "required": False, "default": "free",
                     "enum": ["free", "paid", "local"]},
            "monthly_cost": {"type": "real", "required": False, "default": 0.0},
            "free_limit": {"type": "text", "required": False},
            "usage_pct": {"type": "integer", "required": False, "default": 0},
            "reset_date": {"type": "text", "required": False},
            "account_email": {"type": "text", "required": False},
            "notes": {"type": "text", "required": False},
            "upgrade_priority": {"type": "integer", "required": False, "default": 0},
            "upgrade_reason": {"type": "text", "required": False},
            "signup_url": {"type": "text", "required": False},
        },
        "retention_days": None,
    }

    def __init__(self, app):
        self.app = app
        self._init_theme()
        self._rows = []

    def _init_theme(self):
        global BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM, GREEN, ORANGE, RED, FONT_SM
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

    def _db_conn(self):
        return self.app._db_conn()

    def build_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="Service accounts - usage vs free tier limits",
                     font=FONT_SM, text_color=DIM).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="+ Add", font=FONT_SM, height=26, width=70,
                      fg_color=BG3, hover_color=BG,
                      command=self._add_dialog
                      ).grid(row=0, column=2, sticky="e")
        ctk.CTkButton(hdr, text="Review Upgrades", font=FONT_SM, height=26, width=120,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._review_dispatch
                      ).grid(row=0, column=3, padx=(6, 0), sticky="e")

        self._scroll = ctk.CTkScrollableFrame(parent, fg_color=BG2, corner_radius=4)
        self._scroll.grid(row=1, column=0, sticky="nsew")
        self._scroll.grid_columnconfigure((0, 1, 2, 3, 4, 5, 6), weight=1)

        for col, txt in enumerate(["Service", "Category", "Tier", "Usage",
                                   "Free Limit", "Cost/mo", ""]):
            ctk.CTkLabel(self._scroll, text=txt, font=("Segoe UI", 9, "bold"),
                         text_color=DIM, anchor="w"
                         ).grid(row=0, column=col, padx=6, pady=(2, 4), sticky="ew")

        self._rows = []
        self.on_refresh()

    def on_refresh(self):
        for w in self._rows:
            w.destroy()
        self._rows.clear()

        con = self._db_conn()
        rows = con.execute(
            "SELECT * FROM accounts ORDER BY upgrade_priority DESC, category, service"
        ).fetchall()
        con.close()
        records = [dict(r) for r in rows]

        for i, rec in enumerate(records):
            row = i + 1
            bg = BG3 if i % 2 == 0 else BG2
            tier = rec.get("tier", "free")
            usage = rec.get("usage_pct", 0) or 0
            cost = rec.get("monthly_cost", 0.0) or 0.0

            if tier == "paid":
                tier_color = GREEN
            elif tier == "local":
                tier_color = ORANGE
            else:
                tier_color = DIM

            if usage >= 90:
                usage_color = RED
                usage_txt = f"!! {usage}%"
            elif usage >= 70:
                usage_color = ORANGE
                usage_txt = f"^ {usage}%"
            else:
                usage_color = DIM
                usage_txt = f"{usage}%"

            cost_txt = f"${cost:.2f}" if cost > 0 else "-"

            cols = [
                (rec.get("service", "-"), TEXT, "w"),
                (rec.get("category", "-"), DIM, "w"),
                (tier.upper(), tier_color, "center"),
                (usage_txt, usage_color, "center"),
                (rec.get("free_limit", "-"), DIM, "w"),
                (cost_txt, TEXT, "center"),
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
            btn.grid(row=row, column=6, padx=4, pady=2)
            widgets.append(btn)
            self._rows.extend(widgets)

    def on_close(self):
        pass

    def get_settings(self) -> dict:
        return {"enabled": True}

    def apply_settings(self, cfg: dict):
        pass

    def export_data(self) -> list[dict]:
        con = self._db_conn()
        rows = con.execute("SELECT * FROM accounts").fetchall()
        con.close()
        return [dict(r) for r in rows]

    def validate_record(self, data: dict) -> tuple[bool, str]:
        if not data.get("service"):
            return False, "Service name is required"
        tier = data.get("tier", "free")
        if tier not in ("free", "paid", "local"):
            return False, f"Invalid tier: {tier}"
        return True, "OK"

    def _add_dialog(self):
        self._edit_dialog({})

    def _edit_dialog(self, rec: dict):
        win = ctk.CTkToplevel(self.app)
        win.title("Service Account")
        win.geometry("460x520")
        win.configure(fg_color=BG)
        win.grab_set()

        fields = [
            ("Service", rec.get("service", "")),
            ("Category", rec.get("category", "")),
            ("Account Email", rec.get("account_email", "")),
            ("Free Limit", rec.get("free_limit", "")),
            ("Reset Date", rec.get("reset_date", "")),
            ("Notes", rec.get("notes", "")),
            ("Signup URL", rec.get("signup_url", "")),
            ("Upgrade Reason", rec.get("upgrade_reason", "")),
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

        row_idx = len(fields)

        ctk.CTkLabel(win, text="Tier", font=FONT_SM, text_color=DIM,
                     anchor="w").grid(row=row_idx, column=0, padx=(14, 6), pady=3, sticky="w")
        tier_var = ctk.StringVar(value=rec.get("tier", "free"))
        ctk.CTkOptionMenu(win, values=["free", "paid", "local"],
                          variable=tier_var, font=FONT_SM,
                          fg_color=BG3, button_color=BG3,
                          ).grid(row=row_idx, column=1, padx=(0, 14), pady=3, sticky="ew")
        row_idx += 1

        ctk.CTkLabel(win, text="Usage %", font=FONT_SM, text_color=DIM,
                     anchor="w").grid(row=row_idx, column=0, padx=(14, 6), pady=3, sticky="w")
        usage_var = ctk.IntVar(value=rec.get("usage_pct", 0) or 0)
        usage_lbl = ctk.CTkLabel(win, text=f"{usage_var.get()}%", font=FONT_SM, text_color=TEXT)
        usage_lbl.grid(row=row_idx, column=1, padx=(0, 14), pady=3, sticky="w")
        row_idx += 1
        ctk.CTkSlider(win, from_=0, to=100, variable=usage_var, number_of_steps=100,
                      command=lambda v: usage_lbl.configure(text=f"{int(v)}%")
                      ).grid(row=row_idx, column=0, columnspan=2, padx=14, pady=(0, 4), sticky="ew")
        row_idx += 1

        ctk.CTkLabel(win, text="Cost/mo $", font=FONT_SM, text_color=DIM,
                     anchor="w").grid(row=row_idx, column=0, padx=(14, 6), pady=3, sticky="w")
        cost_entry = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2,
                                  border_color="#444", text_color=TEXT)
        cost_entry.insert(0, str(rec.get("monthly_cost", 0.0) or 0.0))
        cost_entry.grid(row=row_idx, column=1, padx=(0, 14), pady=3, sticky="ew")
        row_idx += 1

        ctk.CTkLabel(win, text="Upgrade Priority", font=FONT_SM, text_color=DIM,
                     anchor="w").grid(row=row_idx, column=0, padx=(14, 6), pady=3, sticky="w")
        pri_var = ctk.IntVar(value=rec.get("upgrade_priority", 0) or 0)
        pri_lbl = ctk.CTkLabel(win, text=str(pri_var.get()), font=FONT_SM, text_color=TEXT)
        pri_lbl.grid(row=row_idx, column=1, padx=(0, 14), pady=3, sticky="w")
        row_idx += 1
        ctk.CTkSlider(win, from_=0, to=10, variable=pri_var, number_of_steps=10,
                      command=lambda v: pri_lbl.configure(text=str(int(v)))
                      ).grid(row=row_idx, column=0, columnspan=2, padx=14, pady=(0, 4), sticky="ew")
        row_idx += 1

        def _save():
            try:
                cost = float(cost_entry.get() or 0)
            except ValueError:
                cost = 0.0
            svc = entries["Service"].get().strip()
            if not svc:
                return
            con = self._db_conn()
            con.execute("DELETE FROM accounts WHERE service=?", (rec.get("service", ""),))
            con.execute(
                "INSERT INTO accounts"
                " (service, category, tier, monthly_cost, free_limit, usage_pct,"
                "  reset_date, account_email, notes, upgrade_priority, upgrade_reason, signup_url)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (svc, entries["Category"].get(), tier_var.get(), cost,
                 entries["Free Limit"].get(), int(usage_var.get()),
                 entries["Reset Date"].get(), entries["Account Email"].get(),
                 entries["Notes"].get(), int(pri_var.get()),
                 entries["Upgrade Reason"].get(), entries["Signup URL"].get()))
            con.commit()
            con.close()
            self.on_refresh()
            win.destroy()

        def _delete():
            svc = rec.get("service", "")
            if svc:
                con = self._db_conn()
                con.execute("DELETE FROM accounts WHERE service=?", (svc,))
                con.commit()
                con.close()
            self.on_refresh()
            win.destroy()

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.grid(row=row_idx, column=0, columnspan=2, padx=14, pady=(10, 14), sticky="ew")
        btn_row.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(btn_row, text="Save", font=FONT_SM, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_save
                      ).grid(row=0, column=0, sticky="ew")
        if rec.get("service"):
            ctk.CTkButton(btn_row, text="Delete", font=FONT_SM, height=30, width=70,
                          fg_color="#5c1010", hover_color="#3a0808", command=_delete
                          ).grid(row=0, column=1, padx=(8, 0))

    def _review_dispatch(self):
        payload = json.dumps({"focus": "upgrades", "threshold": 60})
        self.app._dispatch_raw("account_review", payload, assigned_to="account_manager",
                               msg="Account review queued -> account_manager agent")
