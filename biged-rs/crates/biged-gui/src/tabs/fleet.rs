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

        // ── Header row: title + Launch Modules button (Phase G UX bridge)
        ui.horizontal(|ui| {
            ui.label(
                egui::RichText::new("FLEET")
                    .color(theme::GOLD)
                    .size(theme::FONT_HEADING)
                    .strong(),
            );

            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                // "Launch Modules" button — only visible when Python launcher is detected
                if state.launcher_available {
                    if ui
                        .add(
                            egui::Button::new(
                                egui::RichText::new("\u{1F9E9} Launch Modules")
                                    .color(egui::Color32::WHITE),
                            )
                            .fill(theme::PRIMARY),
                        )
                        .clicked()
                    {
                        launch_python_modules(state);
                    }
                }
            });
        });
        ui.add_space(8.0);

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

        // ── Agent grid (3 columns) with action buttons
        let col_width = (ui.available_width() - (GRID_COLS as f32 - 1.0) * 8.0) / GRID_COLS as f32;

        egui::Grid::new("agent_grid")
            .num_columns(GRID_COLS)
            .spacing([8.0, 8.0])
            .min_col_width(col_width)
            .max_col_width(col_width)
            .show(ui, |ui| {
                for (i, agent) in agents.iter().enumerate() {
                    ui.vertical(|ui| {
                        agent_card::show(ui, agent);
                        // Action buttons below each card
                        ui.horizontal(|ui| {
                            let is_active = matches!(
                                agent.status.to_lowercase().as_str(),
                                "idle" | "busy" | "running"
                            );
                            if is_active {
                                if ui
                                    .small_button(
                                        egui::RichText::new("Disable").color(theme::DIM),
                                    )
                                    .clicked()
                                {
                                    state.api.post_action(
                                        &format!("/api/fleet/worker/{}/disable", agent.name),
                                        "{}",
                                    );
                                }
                            } else {
                                if ui
                                    .small_button(
                                        egui::RichText::new("Enable").color(theme::SUCCESS),
                                    )
                                    .clicked()
                                {
                                    state.api.post_action(
                                        &format!("/api/fleet/worker/{}/enable", agent.name),
                                        "{}",
                                    );
                                }
                            }
                        });
                    });
                    if (i + 1) % GRID_COLS == 0 {
                        ui.end_row();
                    }
                }
                if !agents.is_empty() && !agents.len().is_multiple_of(GRID_COLS) {
                    ui.end_row();
                }
            });

        ui.add_space(12.0);
    });
}

/// Spawn the Python launcher as a subprocess (Phase G UX bridge).
fn launch_python_modules(_state: &AppState) {
    let launcher_path = find_python_launcher();
    let Some(launcher) = launcher_path else {
        tracing::warn!("Python launcher not found — cannot launch modules");
        return;
    };

    let server_url = format!("http://localhost:{}", 5555); // TODO: read from config

    #[cfg(windows)]
    let python = "pythonw.exe";
    #[cfg(not(windows))]
    let python = "python3";

    match std::process::Command::new(python)
        .arg(&launcher)
        .arg("--connect-to")
        .arg(&server_url)
        .spawn()
    {
        Ok(child) => {
            tracing::info!(
                "Launched Python modules (PID {}) via {}",
                child.id(),
                launcher.display()
            );
        }
        Err(e) => {
            tracing::error!("Failed to launch Python modules: {}", e);
        }
    }
}

/// Search for the Python launcher in standard locations.
fn find_python_launcher() -> Option<std::path::PathBuf> {
    let candidates = [
        // Sibling to biged-rs/: ../BigEd/launcher/launcher.py
        std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .map(|d| d.join("..").join("BigEd").join("launcher").join("launcher.py")),
        // Relative to CWD
        Some(
            std::env::current_dir()
                .unwrap_or_default()
                .join("BigEd")
                .join("launcher")
                .join("launcher.py"),
        ),
        // Up one level from CWD (if running from biged-rs/)
        std::env::current_dir()
            .ok()
            .and_then(|d| d.parent().map(|p| p.to_path_buf()))
            .map(|d| d.join("BigEd").join("launcher").join("launcher.py")),
    ];

    for candidate in candidates.into_iter().flatten() {
        let canonical = candidate.canonicalize().unwrap_or(candidate.clone());
        if canonical.exists() {
            return Some(canonical);
        }
    }
    None
}

/// Check at startup whether the Python launcher is available.
pub fn detect_launcher() -> bool {
    find_python_launcher().is_some()
}
