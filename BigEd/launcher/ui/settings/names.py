"""Agent Names Dialog — per-agent custom display name editor."""
import re

import customtkinter as ctk

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    FONT, FONT_SM, FONT_BOLD,
    GLASS_BG, GLASS_PANEL, GLASS_BORDER,
    MONO, FONT_H,
)


def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


class AgentNamesDialog(ctk.CTkToplevel):
    """Let the user assign custom names to individual agents."""

    ALL_ROLES = [
        "supervisor", "researcher", "coder", "coder_1", "coder_2", "coder_3",
        "archivist", "analyst", "sales", "onboarding", "implementation",
        "security", "planner",
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — Agent Names")
        self.geometry("500x560")
        self.configure(fg_color=BG)
        self.grab_set()
        self._parent = parent

        L = _launcher()
        ico = L.HERE / "brick.ico"
        if ico.exists():
            try: self.iconbitmap(str(ico))
            except Exception: pass

        self._entries = {}
        self._build_ui()

    def _build_ui(self):
        L = _launcher()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG3, height=48, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(hdr, text="  Custom Agent Names", font=FONT_H,
                     text_color=GOLD).pack(side="left", padx=12, pady=10)
        ctk.CTkLabel(hdr, text="Leave blank to use theme name", font=FONT_SM,
                     text_color=DIM).pack(side="right", padx=12)

        # Scrollable form
        form = ctk.CTkScrollableFrame(self, fg_color=BG)
        form.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        form.grid_columnconfigure(1, weight=1)

        for i, role in enumerate(self.ALL_ROLES):
            # Role label (themed fallback)
            theme_map = L.AGENT_THEMES.get(L._active_theme, L.AGENT_THEMES["default"])
            base = re.sub(r'_\d+$', '', role)
            suffix = role[len(base):]
            theme_default = theme_map.get(base, base.title())
            if suffix:
                theme_default += f" {suffix.lstrip('_')}"

            ctk.CTkLabel(form, text=f"{role}:", font=MONO,
                         text_color=DIM, anchor="e", width=120
                         ).grid(row=i, column=0, padx=(4, 8), pady=3, sticky="e")

            entry = ctk.CTkEntry(form, font=FONT, fg_color=BG2, border_color=BG3,
                                 text_color=TEXT, placeholder_text=theme_default,
                                 height=30)
            entry.grid(row=i, column=1, sticky="ew", padx=(0, 4), pady=3)

            # Pre-fill existing custom name
            current = L._custom_names.get(role, "")
            if current:
                entry.insert(0, current)

            self._entries[role] = entry

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color=BG)
        btn_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(5, 10))

        ctk.CTkButton(btn_frame, text="Save", font=FONT, width=100, height=32,
                       fg_color=ACCENT, hover_color=ACCENT_H,
                       command=self._save).pack(side="right", padx=4)
        ctk.CTkButton(btn_frame, text="Clear All", font=FONT, width=100, height=32,
                       fg_color=BG3, hover_color=BG2,
                       command=self._clear_all).pack(side="right", padx=4)
        ctk.CTkButton(btn_frame, text="Cancel", font=FONT, width=80, height=32,
                       fg_color=BG3, hover_color=BG2,
                       command=self.destroy).pack(side="right", padx=4)

    def _save(self):
        L = _launcher()
        import launcher as _mod
        names = {}
        for role, entry in self._entries.items():
            val = entry.get().strip()
            if val:
                names[role] = val
        _mod._custom_names = names
        L._save_custom_names(names)
        if hasattr(self._parent, "_refresh_status"):
            self._parent._refresh_status()
        if hasattr(self._parent, "_log_output"):
            count = len(names)
            self._parent._log_output(
                f"Custom agent names saved ({count} override{'s' if count != 1 else ''})")
        self.destroy()

    def _clear_all(self):
        for entry in self._entries.values():
            entry.delete(0, "end")
