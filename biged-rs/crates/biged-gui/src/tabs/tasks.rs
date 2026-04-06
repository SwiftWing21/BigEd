use crate::state::AppState;
use crate::theme;

/// Tasks tab — queue view, dispatch, cancel, requeue.
pub fn show(ui: &mut egui::Ui, state: &mut AppState) {
    egui::ScrollArea::vertical().show(ui, |ui| {
        ui.add_space(12.0);

        // ── Header with queue controls
        ui.horizontal(|ui| {
            ui.label(
                egui::RichText::new("TASK QUEUE")
                    .color(theme::GOLD)
                    .size(theme::FONT_HEADING)
                    .strong(),
            );
            ui.add_space(16.0);

            // Pause/Resume toggle
            let paused = state.queue_paused;
            let btn_text = if paused { "Resume" } else { "Pause" };
            let btn_color = if paused { theme::SUCCESS } else { theme::WARN };
            if ui
                .add(egui::Button::new(
                    egui::RichText::new(btn_text).color(egui::Color32::WHITE),
                ).fill(btn_color))
                .clicked()
            {
                let endpoint = if paused { "/api/queue/resume" } else { "/api/queue/pause" };
                state.api.post_action(endpoint, "{}");
                state.queue_paused = !paused;
            }
        });
        ui.add_space(8.0);

        // ── Quick dispatch form
        ui.group(|ui| {
            ui.label(
                egui::RichText::new("Dispatch Task")
                    .color(theme::TEXT)
                    .size(theme::FONT_BODY)
                    .strong(),
            );
            ui.add_space(4.0);

            ui.horizontal(|ui| {
                ui.label(egui::RichText::new("Skill:").color(theme::DIM));
                ui.add(
                    egui::TextEdit::singleline(&mut state.dispatch_skill)
                        .desired_width(200.0)
                        .hint_text("e.g. web_search"),
                );

                ui.label(egui::RichText::new("Priority:").color(theme::DIM));
                ui.add(egui::Slider::new(&mut state.dispatch_priority, 1..=10));

                if ui
                    .add(egui::Button::new(
                        egui::RichText::new("Send to Fleet").color(egui::Color32::WHITE),
                    ).fill(theme::PRIMARY))
                    .clicked()
                {
                    if !state.dispatch_skill.is_empty() {
                        let body = format!(
                            r#"{{"skill":"{}","priority":{}}}"#,
                            state.dispatch_skill, state.dispatch_priority
                        );
                        state.api.post_action("/api/tasks/dispatch", &body);
                        state.dispatch_skill.clear();
                    }
                }
            });
        });
        ui.add_space(12.0);

        // ── Queue table
        let status = state.status();
        let pending = status.task_count("PENDING");
        let running = status.task_count("RUNNING");

        ui.label(
            egui::RichText::new(format!("{} queued  ·  {} running", pending, running))
                .color(theme::DIM)
                .size(theme::FONT_SM),
        );
        ui.add_space(6.0);

        // Table header
        egui::Grid::new("task_queue_grid")
            .num_columns(7)
            .spacing([12.0, 4.0])
            .striped(true)
            .show(ui, |ui| {
                // Header row
                for header in ["ID", "Skill", "Status", "Priority", "Agent", "Created", "Actions"] {
                    ui.label(
                        egui::RichText::new(header)
                            .color(theme::DIM)
                            .size(theme::FONT_SM)
                            .strong(),
                    );
                }
                ui.end_row();

                // Task rows from recent tasks in status
                let tasks = state.api.task_queue();
                for task in &tasks {
                    ui.label(
                        egui::RichText::new(format!("#{}", task.id))
                            .color(theme::TEXT)
                            .size(theme::FONT_SM),
                    );
                    ui.label(
                        egui::RichText::new(&task.skill)
                            .color(theme::TEXT)
                            .size(theme::FONT_SM),
                    );

                    let status_color = match task.status.as_str() {
                        "RUNNING" => theme::PRIMARY,
                        "PENDING" => theme::DIM,
                        "DONE" => theme::SUCCESS,
                        "FAILED" => theme::DANGER,
                        _ => theme::DIM,
                    };
                    ui.label(
                        egui::RichText::new(&task.status)
                            .color(status_color)
                            .size(theme::FONT_SM)
                            .strong(),
                    );

                    ui.label(
                        egui::RichText::new(format!("{}", task.priority))
                            .color(theme::TEXT)
                            .size(theme::FONT_SM),
                    );
                    ui.label(
                        egui::RichText::new(task.agent.as_deref().unwrap_or("-"))
                            .color(theme::DIM)
                            .size(theme::FONT_SM),
                    );
                    ui.label(
                        egui::RichText::new(
                            task.created_at.get(11..19).unwrap_or(&task.created_at),
                        )
                        .color(theme::DIM)
                        .size(theme::FONT_SM),
                    );

                    // Action buttons
                    ui.horizontal(|ui| {
                        if task.status == "PENDING" {
                            if ui
                                .small_button(egui::RichText::new("Cancel").color(theme::DANGER))
                                .clicked()
                            {
                                state
                                    .api
                                    .post_action(&format!("/api/tasks/{}", task.id), "DELETE");
                            }
                        }
                        if task.status == "FAILED" {
                            if ui
                                .small_button(egui::RichText::new("Requeue").color(theme::WARN))
                                .clicked()
                            {
                                state.api.post_action(
                                    &format!("/api/tasks/{}/requeue", task.id),
                                    "{}",
                                );
                            }
                        }
                    });
                    ui.end_row();
                }
            });

        if state.api.task_queue().is_empty() {
            ui.add_space(20.0);
            ui.label(
                egui::RichText::new("No active tasks in queue")
                    .color(theme::DIM)
                    .size(theme::FONT_BODY),
            );
        }
    });
}
