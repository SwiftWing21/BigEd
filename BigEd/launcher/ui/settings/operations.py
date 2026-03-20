"""Operations settings panel — fleet recovery, security, marathon."""
import customtkinter as ctk

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    FONT_SM, FONT_BOLD,
    GLASS_BG, GLASS_PANEL,
)


class OperationsPanelMixin:
    """Mixin providing the Operations settings panel."""

    def _build_operations_panel(self):
        panel = ctk.CTkScrollableFrame(self._content, fg_color=GLASS_PANEL)
        self._panels["operations"] = panel

        def _ops_card(parent, icon, label, cmd, desc, color=BG3, hover=BG2):
            """Operation action card with icon, label, description."""
            card = ctk.CTkFrame(parent, fg_color=GLASS_BG, corner_radius=6)
            card.pack(fill="x", padx=0, pady=3)
            card.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(card, text=icon, font=("RuneScape Plain 12", 16)
                         ).grid(row=0, column=0, rowspan=2, padx=(12, 8), pady=8)
            ctk.CTkLabel(card, text=label, font=FONT_BOLD,
                         text_color=TEXT, anchor="w"
                         ).grid(row=0, column=1, padx=(0, 8), pady=(8, 0), sticky="w")
            ctk.CTkLabel(card, text=desc, font=("RuneScape Plain 11", 9),
                         text_color=DIM, anchor="w"
                         ).grid(row=1, column=1, padx=(0, 8), pady=(0, 8), sticky="w")
            ctk.CTkButton(card, text="Run", font=FONT_SM,
                          width=60, height=26, fg_color=color, hover_color=hover,
                          command=cmd
                          ).grid(row=0, column=2, rowspan=2, padx=(0, 12), pady=8)

        # Fleet Recovery
        self._section_header(panel, "Fleet Recovery")
        recover_frame = ctk.CTkFrame(panel, fg_color="transparent")
        recover_frame.pack(fill="x", padx=16, pady=(0, 12))
        _ops_card(recover_frame, "↺", "Recover All",
                  self._parent._recover_all,
                  "Kill and restart Ollama + supervisor + all workers")

        # Security
        self._section_header(panel, "Security")
        sec_frame = ctk.CTkFrame(panel, fg_color="transparent")
        sec_frame.pack(fill="x", padx=16, pady=(0, 12))
        _ops_card(sec_frame, "🔍", "Security Audit",
                  self._parent._run_audit,
                  "Audit all fleet skills and configs")
        _ops_card(sec_frame, "🌐", "Pen Test",
                  self._parent._run_pentest,
                  "Network service scan of local environment")
        _ops_card(sec_frame, "📂", "Advisories",
                  self._parent._open_advisories,
                  "View and apply pending security advisories")

        # Marathon
        self._section_header(panel, "Marathon")
        marathon_frame = ctk.CTkFrame(panel, fg_color="transparent")
        marathon_frame.pack(fill="x", padx=16, pady=(0, 12))
        _ops_card(marathon_frame, "🏃", "Start Marathon",
                  self._parent._start_marathon,
                  "8-hour discussion + lead research + synthesis run")
        _ops_card(marathon_frame, "📋", "Marathon Log",
                  self._parent._show_marathon_log,
                  "Tail marathon.log — current phase and output")
        _ops_card(marathon_frame, "⏹", "Stop Marathon",
                  self._parent._stop_marathon,
                  "Kill the running marathon process",
                  "#5a2020", "#6a2828")

        # Backup
        self._section_header(panel, "Auto-Save & Backup")
        backup_frame = ctk.CTkFrame(panel, fg_color="transparent")
        backup_frame.pack(fill="x", padx=16, pady=(0, 12))

        # Backup interval
        interval_row = ctk.CTkFrame(backup_frame, fg_color=GLASS_BG, corner_radius=6)
        interval_row.pack(fill="x", pady=3)
        interval_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(interval_row, text="Backup interval", font=FONT_SM,
                     text_color=TEXT, anchor="w"
                     ).grid(row=0, column=0, padx=12, pady=8, sticky="w")
        self._backup_interval_var = ctk.StringVar(value="20")
        interval_menu = ctk.CTkOptionMenu(
            interval_row, variable=self._backup_interval_var,
            values=["3", "5", "10", "15", "20", "30", "60"],
            font=FONT_SM, width=80, height=26,
            fg_color=BG3, button_color=BG2,
            command=self._on_backup_interval_change)
        interval_menu.grid(row=0, column=1, padx=8, pady=8, sticky="e")
        ctk.CTkLabel(interval_row, text="min", font=FONT_SM, text_color=DIM
                     ).grid(row=0, column=2, padx=(0, 12), pady=8)

        # Backup depth
        depth_row = ctk.CTkFrame(backup_frame, fg_color=GLASS_BG, corner_radius=6)
        depth_row.pack(fill="x", pady=3)
        depth_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(depth_row, text="Keep backups", font=FONT_SM,
                     text_color=TEXT, anchor="w"
                     ).grid(row=0, column=0, padx=12, pady=8, sticky="w")
        self._backup_depth_var = ctk.StringVar(value="10")
        depth_menu = ctk.CTkOptionMenu(
            depth_row, variable=self._backup_depth_var,
            values=[str(i) for i in range(1, 21)],
            font=FONT_SM, width=80, height=26,
            fg_color=BG3, button_color=BG2,
            command=self._on_backup_depth_change)
        depth_menu.grid(row=0, column=1, padx=8, pady=8, sticky="e")

        # Infinite backup toggle (do not clean)
        self._backup_infinite_var = ctk.BooleanVar(value=False)
        infinite_row = ctk.CTkFrame(backup_frame, fg_color=GLASS_BG, corner_radius=6)
        infinite_row.pack(fill="x", pady=3)
        ctk.CTkCheckBox(
            infinite_row, text="Keep all backups (do not clean)",
            variable=self._backup_infinite_var,
            font=FONT_SM, text_color=TEXT,
            fg_color=ACCENT, hover_color=ACCENT_H,
            command=self._on_backup_infinite_toggle,
        ).pack(padx=12, pady=8, anchor="w")
        self._backup_disk_warn = ctk.CTkLabel(
            infinite_row, text="", font=("RuneScape Plain 11", 9), text_color=DIM)
        self._backup_disk_warn.pack(padx=24, pady=(0, 6), anchor="w")

        # Location display
        loc_row = ctk.CTkFrame(backup_frame, fg_color=GLASS_BG, corner_radius=6)
        loc_row.pack(fill="x", pady=3)
        loc_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(loc_row, text="Location", font=FONT_SM,
                     text_color=TEXT, anchor="w"
                     ).grid(row=0, column=0, padx=12, pady=8, sticky="w")
        self._backup_loc_label = ctk.CTkLabel(
            loc_row, text="~/BigEd-backups", font=("Consolas", 9),
            text_color=DIM, anchor="w")
        self._backup_loc_label.grid(row=0, column=1, padx=4, pady=8, sticky="w")
        ctk.CTkButton(loc_row, text="Change", font=FONT_SM,
                      width=60, height=26, fg_color=BG3, hover_color=BG2,
                      command=self._on_backup_location_change
                      ).grid(row=0, column=2, padx=(4, 12), pady=8)

        # Manual backup button
        _ops_card(backup_frame, "💾", "Backup Now",
                  self._on_manual_backup,
                  "Create an immediate backup snapshot")

    def _on_backup_interval_change(self, value):
        """Update backup interval in fleet.toml."""
        try:
            mins = int(value)
            secs = max(180, mins * 60)
            self._parent._update_toml_value("backup", "interval_secs", secs)
        except Exception:
            pass

    def _on_backup_depth_change(self, value):
        """Update backup depth in fleet.toml."""
        try:
            depth = int(value)
            self._parent._update_toml_value("backup", "depth", depth)
        except Exception:
            pass

    def _on_backup_infinite_toggle(self):
        """Toggle infinite backup (do not clean)."""
        infinite = self._backup_infinite_var.get()
        if infinite:
            self._parent._update_toml_value("backup", "prune_enabled", False)
            self._parent._update_toml_value("backup", "depth", 0)
            self._backup_disk_warn.configure(
                text="Warning: backups will grow indefinitely. Monitor disk usage.",
                text_color="#ff9800")
        else:
            self._parent._update_toml_value("backup", "prune_enabled", True)
            depth = int(self._backup_depth_var.get() or 10)
            self._parent._update_toml_value("backup", "depth", depth)
            self._backup_disk_warn.configure(text="", text_color=DIM)

    def _on_backup_location_change(self):
        """Change backup location via folder picker."""
        from tkinter import filedialog
        chosen = filedialog.askdirectory(title="Select backup location")
        if chosen:
            self._parent._update_toml_value("backup", "location", chosen)
            self._backup_loc_label.configure(text=chosen)

    def _on_manual_backup(self):
        """Trigger immediate backup."""
        import threading
        def _run():
            try:
                import sys
                sys.path.insert(0, str(self._parent.FLEET_DIR))
                from backup_manager import BackupManager
                from config import load_config
                bm = BackupManager(load_config())
                result = bm.perform_backup(trigger="manual")
                size = result.get("total_size_bytes", 0) / 1024 / 1024
                self._parent._log_output(f"Backup complete: {result['id']} ({size:.1f} MB)")
            except Exception as e:
                self._parent._log_output(f"Backup failed: {e}")
        threading.Thread(target=_run, daemon=True).start()
