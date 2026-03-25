use crate::{state::AppState, theme};

pub fn show(ui: &mut egui::Ui, state: &mut AppState) {
    // ── Heading
    ui.add_space(8.0);
    ui.label(
        egui::RichText::new("FLEET COMM")
            .color(theme::GOLD)
            .size(theme::FONT_HEADING)
            .strong(),
    );
    ui.add_space(8.0);

    let connected = state.connected();

    // ── Message area (fills available space above the input bar)
    let available = ui.available_height() - 56.0; // reserve space for separator + input row
    egui::ScrollArea::vertical()
        .id_salt("fleet_comm_scroll")
        .max_height(available)
        .stick_to_bottom(true)
        .show(ui, |ui| {
            ui.add_space(8.0);
            if connected {
                ui.label(
                    egui::RichText::new("Connected")
                        .color(theme::GREEN)
                        .size(theme::FONT_SM),
                );
            } else {
                ui.label(
                    egui::RichText::new("Fleet server not connected")
                        .color(theme::RED)
                        .size(theme::FONT_SM),
                );
            }
            ui.add_space(8.0);
        });

    ui.separator();

    // ── Input bar
    ui.horizontal(|ui| {
        let input_width = ui.available_width() - 64.0;
        let text_edit = egui::TextEdit::singleline(&mut state.chat_input)
            .hint_text("Type a message...")
            .desired_width(input_width);
        let response = ui.add(text_edit);

        let send_pressed = ui.button("Send").clicked();
        let enter_pressed = response.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));

        if (send_pressed || enter_pressed) && !state.chat_input.trim().is_empty() {
            // TODO: POST to API
            state.chat_input.clear();
        }
    });
}
