use crate::state::{AppState, LogLevel, LogSource};
use crate::theme;

/// Logs tab — tail supervisor + worker logs with level filter.
pub fn show(ui: &mut egui::Ui, state: &mut AppState) {
    ui.add_space(12.0);

    // ── Header with source selector + level filter
    ui.horizontal(|ui| {
        ui.label(
            egui::RichText::new("LOGS")
                .color(theme::GOLD)
                .size(theme::FONT_HEADING)
                .strong(),
        );
        ui.add_space(16.0);

        // Source selector
        ui.label(egui::RichText::new("Source:").color(theme::DIM));
        for (source, label) in [
            (LogSource::Supervisor, "Supervisor"),
            (LogSource::Worker, "Worker"),
            (LogSource::Fleet, "Fleet"),
        ] {
            let active = state.log_source == source;
            let text = if active {
                egui::RichText::new(label).color(egui::Color32::WHITE).strong()
            } else {
                egui::RichText::new(label).color(theme::DIM)
            };
            let btn = egui::Button::new(text).fill(if active { theme::PRIMARY } else { theme::BG3 });
            if ui.add(btn).clicked() {
                state.log_source = source;
                state.log_lines.clear();
            }
        }

        ui.add_space(16.0);

        // Level filter
        ui.label(egui::RichText::new("Level:").color(theme::DIM));
        for (level, label) in [
            (LogLevel::All, "All"),
            (LogLevel::Warn, "Warn+"),
            (LogLevel::Error, "Error"),
        ] {
            let active = state.log_level == level;
            let text = if active {
                egui::RichText::new(label).color(egui::Color32::WHITE).strong()
            } else {
                egui::RichText::new(label).color(theme::DIM)
            };
            let btn = egui::Button::new(text).fill(if active { theme::PRIMARY } else { theme::BG3 });
            if ui.add(btn).clicked() {
                state.log_level = level;
            }
        }

        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            if ui.button("Clear").clicked() {
                state.log_lines.clear();
            }
        });
    });
    ui.add_space(8.0);

    // ── Log output panel
    let panel_rect = ui.available_rect_before_wrap();
    egui::Frame::default()
        .fill(theme::BG)
        .inner_margin(egui::Margin::same(8))
        .corner_radius(egui::CornerRadius::same(4))
        .stroke(egui::Stroke::new(1.0, theme::GLASS_BORDER))
        .show(ui, |ui| {
            egui::ScrollArea::vertical()
                .auto_shrink([false, false])
                .max_height(panel_rect.height() - 20.0)
                .stick_to_bottom(true)
                .show(ui, |ui| {
                    ui.set_min_width(ui.available_width());

                    let lines = &state.log_lines;
                    if lines.is_empty() {
                        ui.label(
                            egui::RichText::new("No log data. Logs will appear when the fleet is running.")
                                .color(theme::DIM)
                                .size(theme::FONT_SM),
                        );
                    } else {
                        let level_filter = state.log_level;
                        for line in lines {
                            let show = match level_filter {
                                LogLevel::All => true,
                                LogLevel::Warn => {
                                    line.contains("WARN") || line.contains("ERROR") || line.contains("CRITICAL")
                                }
                                LogLevel::Error => {
                                    line.contains("ERROR") || line.contains("CRITICAL")
                                }
                            };
                            if show {
                                let color = if line.contains("ERROR") || line.contains("CRITICAL") {
                                    theme::DANGER
                                } else if line.contains("WARN") {
                                    theme::WARN
                                } else {
                                    theme::DIM
                                };
                                ui.label(
                                    egui::RichText::new(line)
                                        .color(color)
                                        .size(11.0)
                                        .font(egui::FontId::monospace(11.0)),
                                );
                            }
                        }
                    }
                });
        });
}
