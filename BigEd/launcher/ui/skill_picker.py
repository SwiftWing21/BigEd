"""BigEd CC — Grouped Skill Picker with search and collapsible categories.

Reusable modal widget that replaces flat skill dropdowns with a categorized,
searchable picker. Supports 85+ skills grouped by domain.

Usage:
    from ui.skill_picker import pick_skill

    # Modal — blocks until user picks or cancels
    chosen = pick_skill(parent_widget, current="code_review")

    # Or use the widget class directly with a callback
    SkillPicker(parent, current_skill="code_review", on_select=my_callback)
"""
import json
import sys
from pathlib import Path

import customtkinter as ctk

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    FONT_SM, FONT_XS, FONT_BOLD, CARD_RADIUS,
)

# ── Custom Skill Groups persistence ──────────────────────────────────────────

_CUSTOM_GROUPS_FILE = Path(__file__).resolve().parent.parent / "data" / "custom_skill_groups.json"


def load_custom_groups() -> dict:
    """Load user custom skill group overrides from disk.

    Returns dict with keys: custom_groups (dict[str, list[str]]),
    overrides (dict[str, str] — skill_name -> target_group).
    """
    if _CUSTOM_GROUPS_FILE.exists():
        try:
            data = json.loads(_CUSTOM_GROUPS_FILE.read_text(encoding="utf-8"))
            return {
                "custom_groups": data.get("custom_groups", {}),
                "overrides": data.get("overrides", {}),
            }
        except Exception:
            pass
    return {"custom_groups": {}, "overrides": {}}


def save_custom_groups(data: dict):
    """Persist custom skill group overrides to disk."""
    _CUSTOM_GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "custom_groups": data.get("custom_groups", {}),
        "overrides": data.get("overrides", {}),
    }
    _CUSTOM_GROUPS_FILE.write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def reset_custom_groups():
    """Delete custom groups file, restoring defaults."""
    if _CUSTOM_GROUPS_FILE.exists():
        _CUSTOM_GROUPS_FILE.unlink()

# ── Skill Groups ─────────────────────────────────────────────────────────────

SKILL_GROUPS = {
    "Code & Dev": [
        "code_review", "code_write", "code_discuss", "code_quality", "code_index",
        "code_refactor", "code_write_review", "fma_review", "skill_draft", "skill_test",
        "skill_evolve", "skill_train", "skill_learn", "skill_promote", "skill_chain",
        "deploy_skill", "refactor_verify", "claude_code", "git_manager", "github_interact",
        "github_sync", "branch_manager", "benchmark",
    ],
    "Research": [
        "web_search", "web_crawl", "browser_crawl", "arxiv_fetch", "lead_research",
        "research_loop", "analyze_results", "synthesize", "summarize", "discuss", "marketing",
    ],
    "Security": [
        "security_audit", "security_review", "security_apply", "pen_test",
        "secret_rotate", "db_encrypt", "db_migrate", "key_manager",
    ],
    "Data & Knowledge": [
        "rag_index", "rag_query", "rag_compress", "ingest", "knowledge_prune",
        "flashcard", "marathon_log", "review_discards", "dataset_synthesize", "ml_bridge",
    ],
    "Operations": [
        "plan_workload", "evolution_coordinator", "swarm_intelligence", "swarm_consensus",
        "hardware_profiler", "model_manager", "model_recommend", "service_manager",
        "product_release", "curriculum_update", "oom_prevent", "memory_optimizer", "auto_profile",
    ],
    "Output & Media": [
        "generate_image", "generate_video", "generate_asset", "diffusion",
        "vision_analyze", "screenshot", "screenshot_diff",
    ],
    "Cost & Quality": [
        "billing_ocr", "token_optimizer", "packet_optimizer", "regression_detector",
        "stability_report",
    ],
    "IoT & System": [
        "home_assistant", "unifi_manage", "mqtt_inspect",
    ],
    "Specialized": [
        "clinical_review", "speech_to_text", "legal_draft", "account_review", "evaluate",
    ],
}

# Build reverse lookup: skill_name -> group_name
_SKILL_TO_GROUP = {}
for _grp, _skills in SKILL_GROUPS.items():
    for _sk in _skills:
        _SKILL_TO_GROUP[_sk] = _grp


def _discover_all_skills() -> dict[str, list[str]]:
    """Return SKILL_GROUPS merged with custom groups and auto-discovered skills.

    Merge order:
    1. Start with built-in SKILL_GROUPS
    2. Apply overrides (move skills between groups)
    3. Add custom groups
    4. Auto-discover uncategorized skills into 'Other'
    """
    groups = {k: list(v) for k, v in SKILL_GROUPS.items()}
    custom = load_custom_groups()
    overrides = custom.get("overrides", {})
    custom_groups = custom.get("custom_groups", {})

    # Apply overrides: remove skill from its built-in group
    for skill_name, target_group in overrides.items():
        # Remove from current group
        for grp_name, grp_skills in groups.items():
            if skill_name in grp_skills:
                grp_skills.remove(skill_name)
                break
        # Add to target group (create if needed)
        groups.setdefault(target_group, [])
        if skill_name not in groups[target_group]:
            groups[target_group].append(skill_name)

    # Merge custom groups (add new skills, create new groups)
    for grp_name, grp_skills in custom_groups.items():
        if grp_name not in groups:
            groups[grp_name] = []
        for skill in grp_skills:
            if skill not in groups[grp_name]:
                # Remove from any other group first
                for other_grp, other_skills in groups.items():
                    if other_grp != grp_name and skill in other_skills:
                        other_skills.remove(skill)
                        break
                groups[grp_name].append(skill)

    # Auto-discover uncategorized skills
    all_assigned = set()
    for grp_skills in groups.values():
        all_assigned.update(grp_skills)

    fleet_skills = Path(__file__).resolve().parent.parent.parent.parent / "fleet" / "skills"
    if fleet_skills.is_dir():
        uncategorized = []
        for py_file in sorted(fleet_skills.glob("*.py")):
            name = py_file.stem
            if name.startswith("_") or name == "__init__":
                continue
            if name not in all_assigned:
                uncategorized.append(name)
        if uncategorized:
            groups.setdefault("Other", []).extend(sorted(uncategorized))

    # Remove empty groups
    groups = {k: v for k, v in groups.items() if v}

    return groups


# ── Complexity badge colors ──────────────────────────────────────────────────

_COMPLEXITY_COLORS = {
    "simple":  "#4caf50",   # green
    "medium":  "#ff9800",   # orange
    "complex": "#f44336",   # red
}


def _load_complexity_map() -> dict[str, str]:
    """Build skill_name -> complexity tier from providers.SKILL_COMPLEXITY."""
    try:
        fleet_dir = Path(__file__).resolve().parent.parent.parent.parent / "fleet"
        if str(fleet_dir) not in sys.path:
            sys.path.insert(0, str(fleet_dir))
        from providers import SKILL_COMPLEXITY  # noqa: PLC0415
        out = {}
        for tier, skills in SKILL_COMPLEXITY.items():
            for s in skills:
                out[s] = tier
        return out
    except Exception:
        return {}


# ── SkillPicker Widget ───────────────────────────────────────────────────────

class SkillPicker(ctk.CTkToplevel):
    """Modal skill picker with grouped categories and search."""

    def __init__(self, parent, current_skill: str = "", on_select=None):
        super().__init__(parent)
        self.title("BigEd CC — Skill Picker")
        self.geometry("560x620")
        self.resizable(True, True)
        self.minsize(420, 400)
        self.configure(fg_color=BG)
        self.grab_set()
        self.focus_force()

        # Set window icon
        try:
            ico = Path(__file__).resolve().parent.parent / "brick.ico"
            if ico.exists():
                self.iconbitmap(str(ico))
        except Exception:
            pass

        self._current = current_skill
        self._selected = current_skill
        self._on_select = on_select
        self._result = None  # for synchronous pick_skill()

        self._groups = _discover_all_skills()
        self._complexity = _load_complexity_map()
        self._collapsed: dict[str, bool] = {}  # group_name -> collapsed?
        self._skill_buttons: dict[str, ctk.CTkButton] = {}  # skill_name -> button
        self._group_headers: dict[str, ctk.CTkButton] = {}
        self._group_frames: dict[str, ctk.CTkFrame] = {}

        self._build_ui()

        # Center on parent
        self.update_idletasks()
        if parent.winfo_exists():
            px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
            py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
            self.geometry(f"+{max(0, px)}+{max(0, py)}")

    def _build_ui(self):
        """Construct the full picker layout."""
        # ── Search bar ───────────────────────────────────────────────────
        search_frame = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0)
        search_frame.pack(fill="x", padx=0, pady=0)

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", self._on_search)

        search_entry = ctk.CTkEntry(
            search_frame, textvariable=self._search_var,
            placeholder_text="Search skills...",
            font=FONT_SM, fg_color=BG, border_color=BG3,
            height=32,
        )
        search_entry.pack(fill="x", padx=12, pady=10)
        search_entry.focus_set()

        # Bind Enter key to select first visible skill
        search_entry.bind("<Return>", self._select_first_visible)

        # ── Scrollable body ──────────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color=BG, corner_radius=0,
            scrollbar_button_color=BG3,
            scrollbar_button_hover_color=ACCENT,
        )
        self._scroll.pack(fill="both", expand=True, padx=0, pady=0)
        self._scroll.grid_columnconfigure(0, weight=1)

        self._build_groups()

        # ── Bottom bar ───────────────────────────────────────────────────
        bottom = ctk.CTkFrame(self, fg_color=BG2, corner_radius=0, height=50)
        bottom.pack(fill="x", side="bottom", padx=0, pady=0)
        bottom.pack_propagate(False)

        self._sel_label = ctk.CTkLabel(
            bottom, text=f"Selected: {self._selected or '(none)'}",
            font=FONT_SM, text_color=GOLD if self._selected else DIM,
            anchor="w",
        )
        self._sel_label.pack(side="left", padx=12, pady=10)

        ctk.CTkButton(
            bottom, text="Cancel", width=70, height=30,
            font=FONT_SM, fg_color=BG3, hover_color=BG,
            text_color=DIM, command=self._cancel,
        ).pack(side="right", padx=(4, 12), pady=10)

        ctk.CTkButton(
            bottom, text="Select", width=80, height=30,
            font=FONT_BOLD, fg_color=ACCENT, hover_color=ACCENT_H,
            text_color=TEXT, command=self._confirm,
        ).pack(side="right", padx=4, pady=10)

        # Bind Escape to cancel
        self.bind("<Escape>", lambda e: self._cancel())

    def _build_groups(self):
        """Build all group sections with headers and skill buttons."""
        for group_name, skills in self._groups.items():
            if not skills:
                continue

            # Group header — clickable to collapse/expand
            self._collapsed[group_name] = False
            arrow = "\u25be"  # ▾ (expanded)
            header_text = f" {arrow}  {group_name} ({len(skills)})"

            header = ctk.CTkButton(
                self._scroll, text=header_text,
                font=FONT_BOLD, text_color=TEXT,
                fg_color=BG2, hover_color=BG3,
                anchor="w", height=32, corner_radius=4,
                command=lambda g=group_name: self._toggle_group(g),
            )
            header.pack(fill="x", padx=6, pady=(6, 0))
            self._group_headers[group_name] = header

            # Skill buttons container
            skills_frame = ctk.CTkFrame(self._scroll, fg_color="transparent")
            skills_frame.pack(fill="x", padx=6, pady=(0, 2))
            self._group_frames[group_name] = skills_frame

            self._populate_group(group_name, skills, skills_frame)

    def _populate_group(self, group_name: str, skills: list[str], container: ctk.CTkFrame):
        """Fill a group container with skill buttons in a wrapping flow layout."""
        # Use a grid layout to wrap skill buttons
        cols = 4
        for idx, skill in enumerate(sorted(skills)):
            row, col = divmod(idx, cols)
            complexity = self._complexity.get(skill, "medium")
            badge_color = _COMPLEXITY_COLORS.get(complexity, DIM)

            is_selected = (skill == self._selected)
            fg = GOLD if is_selected else BG3
            text_col = BG if is_selected else TEXT

            btn = ctk.CTkButton(
                container, text=skill,
                font=FONT_XS, text_color=text_col,
                fg_color=fg, hover_color=BG2 if not is_selected else GOLD,
                border_width=1, border_color=badge_color,
                height=26, corner_radius=4,
                command=lambda s=skill: self._pick(s),
            )
            btn.grid(row=row, column=col, padx=2, pady=2, sticky="ew")
            container.grid_columnconfigure(col, weight=1)
            self._skill_buttons[skill] = btn

    def _toggle_group(self, group_name: str):
        """Collapse or expand a group section."""
        collapsed = not self._collapsed[group_name]
        self._collapsed[group_name] = collapsed

        frame = self._group_frames[group_name]
        header = self._group_headers[group_name]
        skills = self._groups[group_name]

        if collapsed:
            frame.pack_forget()
            arrow = "\u25b8"  # ▸ (collapsed)
        else:
            # Re-pack after its header
            frame.pack(fill="x", padx=6, pady=(0, 2), after=header)
            arrow = "\u25be"  # ▾ (expanded)

        header.configure(text=f" {arrow}  {group_name} ({len(skills)})")

    def _pick(self, skill_name: str):
        """Handle clicking a skill button."""
        # Deselect previous
        if self._selected and self._selected in self._skill_buttons:
            old_btn = self._skill_buttons[self._selected]
            old_btn.configure(fg_color=BG3, text_color=TEXT, hover_color=BG2)

        # Select new
        self._selected = skill_name
        if skill_name in self._skill_buttons:
            btn = self._skill_buttons[skill_name]
            btn.configure(fg_color=GOLD, text_color=BG, hover_color=GOLD)

        self._sel_label.configure(
            text=f"Selected: {skill_name}",
            text_color=GOLD,
        )

    def _on_search(self, *_args):
        """Filter skills by search query — show/hide groups and buttons."""
        query = self._search_var.get().strip().lower()

        for group_name, skills in self._groups.items():
            frame = self._group_frames[group_name]
            header = self._group_headers[group_name]

            if not query:
                # Reset: show all, restore collapse state
                if not self._collapsed[group_name]:
                    frame.pack(fill="x", padx=6, pady=(0, 2), after=header)
                else:
                    frame.pack_forget()
                header.pack(fill="x", padx=6, pady=(6, 0))
                # Show all buttons in group
                for skill in skills:
                    if skill in self._skill_buttons:
                        self._skill_buttons[skill].grid()
                visible_count = len(skills)
            else:
                # Filter: show only matching skills
                matching = [s for s in skills if query in s.lower()]
                if not matching:
                    header.pack_forget()
                    frame.pack_forget()
                    continue

                header.pack(fill="x", padx=6, pady=(6, 0))
                frame.pack(fill="x", padx=6, pady=(0, 2), after=header)

                # Show/hide individual buttons
                for skill in skills:
                    if skill in self._skill_buttons:
                        if skill in matching:
                            self._skill_buttons[skill].grid()
                        else:
                            self._skill_buttons[skill].grid_remove()

                visible_count = len(matching)

            # Update header count
            arrow = "\u25b8" if self._collapsed.get(group_name) and not query else "\u25be"
            if query:
                arrow = "\u25be"  # Always expanded during search
            header.configure(text=f" {arrow}  {group_name} ({visible_count})")

    def _select_first_visible(self, _event=None):
        """Select the first visible skill when Enter is pressed in search."""
        query = self._search_var.get().strip().lower()
        if not query:
            # If nothing typed, confirm current selection
            if self._selected:
                self._confirm()
            return

        for _group_name, skills in self._groups.items():
            for skill in sorted(skills):
                if query in skill.lower():
                    self._pick(skill)
                    self._confirm()
                    return

    def _confirm(self):
        """Confirm selection and close."""
        if self._selected:
            self._result = self._selected
            if self._on_select:
                self._on_select(self._selected)
        self.grab_release()
        self.destroy()

    def _cancel(self):
        """Cancel and close without selection."""
        self._result = None
        self.grab_release()
        self.destroy()

    @property
    def result(self) -> str | None:
        """The selected skill name, or None if cancelled."""
        return self._result


# ── Convenience function ─────────────────────────────────────────────────────

def pick_skill(parent, current: str = "") -> str | None:
    """Show skill picker modal, return selected skill name or None.

    Blocks until the user selects a skill or cancels.

    Args:
        parent: The parent CTk widget.
        current: The currently selected skill (highlighted in the picker).

    Returns:
        The selected skill name, or None if the dialog was cancelled.
    """
    dialog = SkillPicker(parent, current_skill=current)
    dialog.wait_window()
    return dialog.result
