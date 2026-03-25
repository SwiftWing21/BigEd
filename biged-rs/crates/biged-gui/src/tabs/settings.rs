use crate::{
    state::{AppState, SettingsSection},
    theme,
};

pub fn show(ui: &mut egui::Ui, state: &mut AppState) {
    ui.add_space(8.0);

    // ── Two-column layout: nav on the left, content on the right
    ui.horizontal(|ui| {
        // ── Left nav
        ui.vertical(|ui| {
            ui.set_width(130.0);
            nav_button(ui, "General", SettingsSection::General, state);
            nav_button(ui, "Hardware", SettingsSection::Hardware, state);
            nav_button(ui, "Display", SettingsSection::Display, state);
            nav_button(ui, "API Keys", SettingsSection::Keys, state);
        });

        ui.separator();

        // ── Right content
        egui::ScrollArea::vertical()
            .id_salt("settings_content_scroll")
            .show(ui, |ui| {
                ui.add_space(4.0);
                match state.settings_section {
                    SettingsSection::General => show_general(ui),
                    SettingsSection::Hardware => show_hardware(ui),
                    SettingsSection::Display => show_display(ui),
                    SettingsSection::Keys => show_keys(ui),
                }
            });
    });
}

// ── Nav button helper
fn nav_button(ui: &mut egui::Ui, label: &str, section: SettingsSection, state: &mut AppState) {
    let is_active = state.settings_section == section;
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
        state.settings_section = section;
    }
}

// ── Section content

fn show_general(ui: &mut egui::Ui) {
    ui.label(
        egui::RichText::new("General")
            .color(theme::GOLD)
            .size(theme::FONT_HEADING)
            .strong(),
    );
    ui.add_space(6.0);
    ui.label(
        egui::RichText::new("Theme, fleet behavior, tab configuration")
            .color(theme::DIM)
            .size(theme::FONT_SM),
    );
}

fn show_hardware(ui: &mut egui::Ui) {
    ui.label(
        egui::RichText::new("Hardware")
            .color(theme::GOLD)
            .size(theme::FONT_HEADING)
            .strong(),
    );
    ui.add_space(6.0);
    ui.label(
        egui::RichText::new("CPU, RAM, GPU limits")
            .color(theme::DIM)
            .size(theme::FONT_SM),
    );
}

fn show_display(ui: &mut egui::Ui) {
    ui.label(
        egui::RichText::new("Display")
            .color(theme::GOLD)
            .size(theme::FONT_HEADING)
            .strong(),
    );
    ui.add_space(6.0);
    ui.label(
        egui::RichText::new("Font, UI scale, sidebar preferences")
            .color(theme::DIM)
            .size(theme::FONT_SM),
    );
}

fn show_keys(ui: &mut egui::Ui) {
    ui.label(
        egui::RichText::new("API Keys")
            .color(theme::GOLD)
            .size(theme::FONT_HEADING)
            .strong(),
    );
    ui.add_space(6.0);
    ui.label(
        egui::RichText::new("Manage API keys")
            .color(theme::DIM)
            .size(theme::FONT_SM),
    );
    ui.add_space(8.0);
    ui.label(
        egui::RichText::new("API key disabled (cost guard active)")
            .color(theme::ORANGE)
            .size(theme::FONT_SM),
    );
}
