"""BigEd CC — Review Settings dialog.

Extracted from launcher.py (TECH_DEBT 4.2).
"""
import customtkinter as ctk
import tomlkit

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, RED, FONT_SM,
)

# Late-bound: set by launcher.py after path resolution
HERE = None
FLEET_TOML = None


def _init_review_refs(here, fleet_toml):
    """Called once from launcher.py to inject refs without circular imports."""
    global HERE, FLEET_TOML
    HERE = here
    FLEET_TOML = fleet_toml


class ReviewDialog(ctk.CTkToplevel):
    """
    Configure the evaluator-optimizer review pass.
    - Enable/Disable: when disabled, skill outputs bypass review entirely.
    - Provider: 'api' uses Anthropic API key (billed); 'subscription' uses Gemini free tier.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — Review Settings")
        self.geometry("420x360")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.grab_set()

        if HERE:
            ico = HERE / "brick.ico"
            if ico.exists():
                try: self.iconbitmap(str(ico))
                except Exception: pass

        self._cfg = self._load()
        self._build_ui()

    def _load(self) -> dict:
        defaults = {"enabled": False, "provider": "api",
                    "claude_model": "claude-sonnet-4-6",
                    "gemini_model": "gemini-2.0-flash",
                    "local_model": "qwen3:8b",
                    "local_ctx": 16384,
                    "local_think": True}
        try:
            import tomllib
            with open(FLEET_TOML, "rb") as f:
                data = tomllib.load(f)
            return {**defaults, **data.get("review", {})}
        except Exception:
            return defaults

    def _save(self):
        try:
            doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
            review = doc.setdefault("review", {})
            review["enabled"] = self._enabled_var.get()
            review["provider"] = self._provider_var.get()
            review["claude_model"] = self._cfg["claude_model"]
            review["gemini_model"] = self._cfg["gemini_model"]
            review["local_model"] = self._local_model_var.get()
            review["local_ctx"] = self._cfg["local_ctx"]
            review["local_think"] = self._think_var.get()
            FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
            self._status.configure(text="Saved.", text_color=GREEN)
        except Exception as e:
            self._status.configure(text=f"Error: {e}", text_color=RED)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(self, fg_color=BG3, height=46, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="🧪  REVIEW SETTINGS",
                     font=("Segoe UI", 13, "bold"), text_color=GOLD
                     ).grid(row=0, column=0, padx=14, pady=10, sticky="w")

        body = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        body.grid_columnconfigure(1, weight=1)

        # Enable / Disable
        ctk.CTkLabel(body, text="Review pass", font=FONT_SM,
                     text_color=TEXT).grid(row=0, column=0, padx=16, pady=(18, 6), sticky="w")
        self._enabled_var = ctk.BooleanVar(value=self._cfg["enabled"])
        sw = ctk.CTkSwitch(body, text="", variable=self._enabled_var,
                           onvalue=True, offvalue=False,
                           progress_color=ACCENT, button_color=TEXT)
        sw.grid(row=0, column=1, padx=16, pady=(18, 6), sticky="w")
        ctk.CTkLabel(body, text="When OFF, skill outputs skip review entirely.",
                     font=("Segoe UI", 9), text_color=DIM
                     ).grid(row=1, column=0, columnspan=2, padx=16, pady=(0, 10), sticky="w")

        # Provider
        ctk.CTkLabel(body, text="Provider", font=FONT_SM,
                     text_color=TEXT).grid(row=2, column=0, padx=16, pady=6, sticky="w")
        self._provider_var = ctk.StringVar(value=self._cfg["provider"])
        seg = ctk.CTkSegmentedButton(
            body, values=["api", "subscription", "local"],
            variable=self._provider_var,
            font=FONT_SM, selected_color=ACCENT, selected_hover_color=ACCENT_H,
            command=self._on_provider_change,
        )
        seg.grid(row=2, column=1, padx=16, pady=6, sticky="w")

        desc_frame = ctk.CTkFrame(body, fg_color=BG3, corner_radius=4)
        desc_frame.grid(row=3, column=0, columnspan=2, padx=16, pady=(4, 8), sticky="ew")
        ctk.CTkLabel(desc_frame,
                     text="api — Claude Sonnet via Anthropic API key (billed)\n"
                          "subscription — Gemini 2.0 Flash free tier\n"
                          "local — Ollama model with extended thinking (offline)",
                     font=("Segoe UI", 9), text_color=DIM, justify="left"
                     ).pack(padx=10, pady=8, anchor="w")

        # Local model options (shown only when provider=local)
        self._local_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._local_frame.grid(row=4, column=0, columnspan=2, padx=16, pady=(0, 8), sticky="ew")
        self._local_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self._local_frame, text="Model", font=FONT_SM,
                     text_color=TEXT).grid(row=0, column=0, padx=(0, 10), pady=4, sticky="w")
        self._local_model_var = ctk.StringVar(value=self._cfg["local_model"])
        ctk.CTkEntry(self._local_frame, textvariable=self._local_model_var,
                     font=FONT_SM, fg_color=BG, height=28
                     ).grid(row=0, column=1, pady=4, sticky="ew")

        self._think_var = ctk.BooleanVar(value=self._cfg["local_think"])
        ctk.CTkCheckBox(self._local_frame, text="Extended thinking (/think prefix)",
                        variable=self._think_var, font=("Segoe UI", 9),
                        text_color=DIM, checkbox_width=16, checkbox_height=16,
                        ).grid(row=1, column=0, columnspan=2, pady=(2, 0), sticky="w")

        self._on_provider_change(self._provider_var.get())

        # Footer
        foot = ctk.CTkFrame(self, fg_color=BG3, height=46, corner_radius=0)
        foot.grid(row=2, column=0, sticky="ew")
        foot.grid_propagate(False)
        foot.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(foot, text="Save", width=90, height=30,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self._save).grid(row=0, column=0, padx=12, pady=8)
        self._status = ctk.CTkLabel(foot, text="", font=("Segoe UI", 9), text_color=DIM)
        self._status.grid(row=0, column=1, padx=8, sticky="w")

    def _on_provider_change(self, value: str):
        if value == "local":
            self._local_frame.grid()
        else:
            self._local_frame.grid_remove()
