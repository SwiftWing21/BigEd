use crate::{
    state::{AppState, ConfigSection},
    theme,
};

pub fn show(ui: &mut egui::Ui, state: &mut AppState) {
    ui.add_space(8.0);

    ui.horizontal(|ui| {
        // ── Left nav
        ui.vertical(|ui| {
            ui.set_width(130.0);
            nav_button(ui, "General", ConfigSection::General, state);
            nav_button(ui, "Hardware", ConfigSection::Hardware, state);
            nav_button(ui, "Backup", ConfigSection::Backup, state);
        });

        ui.separator();

        // ── Right content
        egui::ScrollArea::vertical()
            .id_salt("config_content_scroll")
            .show(ui, |ui| {
                ui.add_space(4.0);
                match state.config_section {
                    ConfigSection::General => show_general(ui, state),
                    ConfigSection::Hardware => show_hardware(ui, state),
                    ConfigSection::Backup => show_backup(ui),
                }
            });
    });
}

fn nav_button(ui: &mut egui::Ui, label: &str, section: ConfigSection, state: &mut AppState) {
    let is_active = state.config_section == section;
    let bg = if is_active {
        theme::SB_ACTIVE
    } else {
        theme::BG2
    };
    let text_color = if is_active { theme::TEXT } else { theme::DIM };

    let button = egui::Button::new(
        egui::RichText::new(label)
            .color(text_color)
            .size(theme::FONT_SM),
    )
    .fill(bg)
    .corner_radius(theme::BTN_ROUNDING);

    if ui.add_sized([120.0, 30.0], button).clicked() {
        state.config_section = section;
    }
}

fn show_general(ui: &mut egui::Ui, state: &AppState) {
    ui.label(
        egui::RichText::new("General Configuration")
            .color(theme::GOLD)
            .size(theme::FONT_HEADING)
            .strong(),
    );
    ui.add_space(8.0);

    config_row(ui, "Fleet Status", if state.connected() { "Online" } else { "Offline" });
    config_row(ui, "Track", "Rust (production)");
    config_row(ui, "Version", env!("CARGO_PKG_VERSION"));

    ui.add_space(12.0);

    ui.label(
        egui::RichText::new("Ollama")
            .color(theme::TEXT)
            .size(theme::FONT_BODY)
            .strong(),
    );
    ui.add_space(4.0);
    config_row(ui, "Host", "localhost:11434");

    ui.add_space(12.0);
    ui.label(
        egui::RichText::new("Edit fleet.toml for full configuration")
            .color(theme::DIM)
            .size(theme::FONT_SM),
    );
}

fn show_hardware(ui: &mut egui::Ui, state: &AppState) {
    ui.label(
        egui::RichText::new("Hardware & Thermal")
            .color(theme::GOLD)
            .size(theme::FONT_HEADING)
            .strong(),
    );
    ui.add_space(8.0);

    let thermal = state.thermal();

    config_row(ui, "GPU Temp", &format!("{:.0} C", thermal.gpu_temp_c));
    config_row(
        ui,
        "GPU VRAM",
        &format!("{:.1} / {:.1} GB", thermal.gpu_vram_used_gb, thermal.gpu_vram_total_gb),
    );
    config_row(ui, "CPU Temp", &format!("{:.0} C", thermal.cpu_temp_c));
    config_row(
        ui,
        "RAM",
        &format!("{:.1} / {:.1} GB", thermal.ram_used_gb, thermal.ram_total_gb),
    );
    config_row(ui, "Thermal State", &thermal.thermal_state);
    config_row(
        ui,
        "Loaded Model",
        if thermal.current_model.is_empty() { "None" } else { &thermal.current_model },
    );
}

fn show_backup(ui: &mut egui::Ui) {
    ui.label(
        egui::RichText::new("Backup Configuration")
            .color(theme::GOLD)
            .size(theme::FONT_HEADING)
            .strong(),
    );
    ui.add_space(8.0);

    config_row(ui, "Location", "~/BigEd-backups/");
    config_row(ui, "Retention", "10 backups");
    config_row(ui, "Compression", "ZIP (deflate level 6)");
    config_row(ui, "Knowledge", "Compressed in knowledge.zip");

    ui.add_space(12.0);
    ui.label(
        egui::RichText::new("Backup includes: fleet.db, rag.db, fleet.toml, knowledge/")
            .color(theme::DIM)
            .size(theme::FONT_SM),
    );
}

fn config_row(ui: &mut egui::Ui, label: &str, value: &str) {
    ui.horizontal(|ui| {
        ui.label(
            egui::RichText::new(format!("{}:", label))
                .color(theme::DIM)
                .size(theme::FONT_SM),
        );
        ui.label(
            egui::RichText::new(value)
                .color(theme::TEXT)
                .size(theme::FONT_SM),
        );
    });
}
