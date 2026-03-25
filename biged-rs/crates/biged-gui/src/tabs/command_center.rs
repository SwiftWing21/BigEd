use crate::state::AppState;
use crate::theme;
use crate::widgets::neural_lanes;

pub fn show(ui: &mut egui::Ui, state: &mut AppState) {
    let status = state.status();
    let lanes = state.lanes();

    egui::ScrollArea::vertical().show(ui, |ui| {
        ui.add_space(12.0);

        // ── NEURAL ACTIVITY heading
        ui.label(
            egui::RichText::new("NEURAL ACTIVITY")
                .color(theme::GOLD)
                .size(theme::FONT_HEADING)
                .strong(),
        );
        ui.add_space(8.0);

        // ── NeuralLanes widget
        neural_lanes::show(ui, &lanes);

        ui.add_space(20.0);
        ui.separator();
        ui.add_space(12.0);

        // ── FLEET STATUS heading
        ui.label(
            egui::RichText::new("FLEET STATUS")
                .color(theme::GOLD)
                .size(theme::FONT_HEADING)
                .strong(),
        );
        ui.add_space(8.0);

        // ── Stats grid
        egui::Grid::new("fleet_status_grid")
            .num_columns(2)
            .spacing([40.0, 6.0])
            .show(ui, |ui| {
                stat_row(
                    ui,
                    "Agents",
                    status.agent_count.unwrap_or(0).to_string(),
                    theme::BLUE,
                );
                ui.end_row();

                stat_row(
                    ui,
                    "Pending",
                    status.task_pending.unwrap_or(0).to_string(),
                    theme::YELLOW,
                );
                ui.end_row();

                stat_row(
                    ui,
                    "Running",
                    status.task_running.unwrap_or(0).to_string(),
                    theme::ORANGE,
                );
                ui.end_row();

                stat_row(
                    ui,
                    "Done",
                    status.task_done.unwrap_or(0).to_string(),
                    theme::GREEN,
                );
                ui.end_row();

                stat_row(
                    ui,
                    "Failed",
                    status.task_failed.unwrap_or(0).to_string(),
                    theme::RED,
                );
                ui.end_row();

                if let Some(v) = &status.version {
                    stat_row(ui, "Version", v.clone(), theme::DIM);
                    ui.end_row();
                }
            });

        ui.add_space(12.0);
    });
}

fn stat_row(ui: &mut egui::Ui, label: &str, value: String, color: egui::Color32) {
    ui.label(
        egui::RichText::new(label)
            .color(theme::DIM)
            .size(theme::FONT_SM),
    );
    ui.label(
        egui::RichText::new(value)
            .color(color)
            .size(theme::FONT_BODY)
            .strong(),
    );
}
