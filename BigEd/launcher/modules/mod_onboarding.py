"""
Onboarding Module — Customer onboarding checklists for BigEd CC.

Per-customer checklist with categories: Setup, Config, Training, Go-Live.
Cross-module: reads customers from CRM if available.
"""
import customtkinter as ctk
from data_access import DataAccess

BG = BG2 = BG3 = ACCENT = ACCENT_H = GOLD = TEXT = DIM = GREEN = ORANGE = RED = ""
FONT_SM = ("RuneScape Plain 11", 10)


class Module:
    NAME = "onboarding"
    LABEL = "Onboarding"
    VERSION = "0.23"
    DEFAULT_ENABLED = False
    DEPENDS_ON = []

    DATA_SCHEMA = {
        "table": "onboarding",
        "fields": {
            "customer": {"type": "text", "required": True},
            "category": {"type": "text", "required": True},
            "step": {"type": "text", "required": True},
            "done": {"type": "integer", "required": False, "default": 0},
        },
        "retention_days": None,
    }

    _DEFAULT_STEPS = [
        ("Setup", ["Create WSL environment", "Install Python + uv", "Clone fleet repo"]),
        ("Config", ["Set API keys in ~/.secrets", "Configure fleet.toml", "Test Ollama connectivity"]),
        ("Training", ["Run first autoresearch experiment", "Review results with analyst worker"]),
        ("Go-Live", ["Start supervisor", "Verify all agents IDLE", "Deliver handoff doc"]),
    ]

    def __init__(self, app):
        self.app = app
        self._init_theme()
        self._rows = []
        self._customer_var = None
        self._progress = None
        self._scroll = None
        self._menu = None
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

    def _db_query_bg(self, query_fn, callback):
        self.app._db_query_bg(query_fn, callback)

    def build_tab(self, parent):
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Customer:", font=FONT_SM,
                     text_color=DIM).grid(row=0, column=0, padx=(0, 8))

        rows = self._dal.raw_query(
            "SELECT DISTINCT customer FROM onboarding ORDER BY customer")
        custs = [r["customer"] for r in rows]
        customers = custs or ["(no customers)"]
        self._customer_var = ctk.StringVar(value=customers[0])
        self._menu = ctk.CTkOptionMenu(
            top, values=customers, variable=self._customer_var,
            font=FONT_SM, fg_color=BG3, button_color=ACCENT,
            button_hover_color=ACCENT_H, height=26, width=160,
            command=lambda _: self.on_refresh())
        self._menu.grid(row=0, column=1, sticky="w")

        ctk.CTkButton(top, text="+ Customer", font=FONT_SM, height=26, width=100,
                      fg_color=BG3, hover_color=BG,
                      command=self._add_customer
                      ).grid(row=0, column=2, padx=(8, 0))

        self._progress = ctk.CTkProgressBar(
            top, height=8, corner_radius=4, fg_color=BG3, progress_color=GREEN)
        self._progress.set(0)
        self._progress.grid(row=0, column=3, padx=(12, 0), sticky="ew")
        top.grid_columnconfigure(3, weight=1)

        self._scroll = ctk.CTkScrollableFrame(parent, fg_color=BG2, corner_radius=4)
        self._scroll.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self._scroll.grid_columnconfigure(1, weight=1)

        self._rows = []
        self.on_refresh()

    def on_refresh(self):
        customer = self._customer_var.get() if self._customer_var else ""

        def _fetch(_con):
            rows = self._dal.raw_query(
                "SELECT category, step, done FROM onboarding WHERE customer=? ORDER BY id",
                (customer,))
            steps = {}
            for r in rows:
                steps.setdefault(r["category"], {})[r["step"]] = bool(r["done"])
            return steps

        def _render(steps):
            for w in self._rows:
                w.destroy()
            self._rows.clear()

            if not steps:
                steps = {cat: {s: False for s in items}
                         for cat, items in self._DEFAULT_STEPS}

            self._render_steps(steps, customer)

        self._db_query_bg(_fetch, _render)

    def _render_steps(self, steps, customer):
        row = 0
        total = done_count = 0
        for cat, items in steps.items():
            lbl = ctk.CTkLabel(self._scroll, text=cat,
                               font=("RuneScape Bold 12", 10, "bold"), text_color=GOLD, anchor="w")
            lbl.grid(row=row, column=0, columnspan=2, padx=6, pady=(8, 2), sticky="w")
            self._rows.append(lbl)
            row += 1
            for step, checked in items.items():
                total += 1
                if checked:
                    done_count += 1
                var = ctk.BooleanVar(value=checked)

                def _on_toggle(v=var, c=customer, ca=cat, s=step):
                    self._dal.update("onboarding", {"done": int(v.get())},
                                     where={"customer": c, "category": ca, "step": s})
                    self.on_refresh()

                cb = ctk.CTkCheckBox(
                    self._scroll, text=step, variable=var,
                    font=FONT_SM, text_color=TEXT if not checked else DIM,
                    fg_color=ACCENT, hover_color=ACCENT_H,
                    command=_on_toggle)
                cb.grid(row=row, column=1, padx=(20, 6), pady=1, sticky="w")
                self._rows.append(cb)
                row += 1

        if total and self._progress:
            self._progress.set(done_count / total)

    def on_close(self):
        pass

    def get_settings(self) -> dict:
        return {"enabled": True}

    def apply_settings(self, cfg: dict):
        pass

    def export_data(self) -> list[dict]:
        return self._dal.query("onboarding")

    def _add_customer(self):
        win = ctk.CTkToplevel(self.app)
        win.title("Add Customer")
        win.geometry("300x120")
        win.resizable(False, False)
        win.configure(fg_color=BG)
        win.grab_set()
        ctk.CTkLabel(win, text="Customer name:", font=FONT_SM,
                     text_color=DIM).pack(padx=14, pady=(14, 4), anchor="w")
        entry = ctk.CTkEntry(win, font=FONT_SM, fg_color=BG2,
                             border_color="#444", text_color=TEXT)
        entry.pack(padx=14, fill="x")

        def _add():
            name = entry.get().strip()
            if not name:
                return
            existing = self._dal.query("onboarding", where={"customer": name}, limit=1)
            if not existing:
                for cat, items in self._DEFAULT_STEPS:
                    for step in items:
                        self._dal.insert("onboarding", {
                            "customer": name, "category": cat,
                            "step": step, "done": 0,
                        })
            rows = self._dal.raw_query(
                "SELECT DISTINCT customer FROM onboarding ORDER BY customer")
            custs = [r["customer"] for r in rows]
            self._menu.configure(values=custs)
            self._customer_var.set(name)
            self.on_refresh()
            win.destroy()

        ctk.CTkButton(win, text="Add", font=FONT_SM, height=28,
                      fg_color=ACCENT, hover_color=ACCENT_H, command=_add
                      ).pack(padx=14, pady=8, fill="x")
