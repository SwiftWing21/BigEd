use crate::state::AppState;
use crate::theme;
use crate::widgets::{agent_card, counter_card};

const GRID_COLS: usize = 3;

pub fn show(ui: &mut egui::Ui, state: &mut AppState) {
    let status = state.status();
    let agents = state.agents();

    // ── Derived counts from agent list
    let total = agents.len() as u32;
    let idle = agents
        .iter()
        .filter(|a| a.status.to_lowercase() == "idle")
        .count() as u32;
    let busy = agents
        .iter()
        .filter(|a| matches!(a.status.to_lowercase().as_str(), "busy" | "running"))
        .count() as u32;
    let pending = status.task_count("PENDING");
    let done = status.task_count("DONE");

    egui::ScrollArea::vertical().show(ui, |ui| {
        ui.add_space(12.0);

        // ── Counter card row
        ui.horizontal(|ui| {
            counter_card::show(ui, "TOTAL", &total.to_string(), theme::BLUE);
            ui.add_space(8.0);
            counter_card::show(ui, "IDLE", &idle.to_string(), theme::GREEN);
            ui.add_space(8.0);
            counter_card::show(ui, "BUSY", &busy.to_string(), theme::ORANGE);
            ui.add_space(8.0);
            counter_card::show(ui, "PENDING", &pending.to_string(), theme::YELLOW);
            ui.add_space(8.0);
            counter_card::show(ui, "DONE", &done.to_string(), theme::DIM);
        });

        ui.add_space(16.0);
        ui.separator();
        ui.add_space(12.0);

        if agents.is_empty() {
            ui.label(
                egui::RichText::new("No agents connected")
                    .color(theme::DIM)
                    .size(theme::FONT_SM),
            );
            return;
        }

        // ── Agent grid (3 columns)
        let col_width = (ui.available_width() - (GRID_COLS as f32 - 1.0) * 8.0) / GRID_COLS as f32;

        egui::Grid::new("agent_grid")
            .num_columns(GRID_COLS)
            .spacing([8.0, 8.0])
            .min_col_width(col_width)
            .max_col_width(col_width)
            .show(ui, |ui| {
                for (i, agent) in agents.iter().enumerate() {
                    agent_card::show(ui, agent);
                    if (i + 1) % GRID_COLS == 0 {
                        ui.end_row();
                    }
                }
                // End last partial row
                if !agents.is_empty() && !agents.len().is_multiple_of(GRID_COLS) {
                    ui.end_row();
                }
            });

        ui.add_space(12.0);
    });
}
