use egui::{CornerRadius, Frame, Vec2};

use crate::api::AgentInfo;
use crate::theme;

const DOT_RADIUS: f32 = 5.0;
const CARD_INNER_PAD: i8 = 8;

/// Resolve the status dot colour from an agent's status string.
fn status_color(status: &str) -> egui::Color32 {
    match status.to_lowercase().as_str() {
        "idle" => theme::GREEN,
        "busy" | "running" => theme::ORANGE,
        "failed" | "error" => theme::RED,
        _ => theme::DIM,
    }
}

/// Agent card with status dot, name (TEXT), and role (DIM).
/// Uses egui::Frame::none().fill(BG2).rounding(CARD_ROUNDING).
pub fn show(ui: &mut egui::Ui, agent: &AgentInfo) {
    let dot_color = status_color(&agent.status);

    Frame::new()
        .fill(theme::BG2)
        .corner_radius(CornerRadius::same(theme::CARD_ROUNDING as u8))
        .inner_margin(egui::Margin::same(CARD_INNER_PAD))
        .show(ui, |ui| {
            ui.set_min_size(Vec2::new(0.0, 50.0));

            ui.horizontal(|ui| {
                // Status dot
                let (dot_rect, _) =
                    ui.allocate_exact_size(Vec2::splat(DOT_RADIUS * 2.0), egui::Sense::hover());
                ui.painter_at(dot_rect)
                    .circle_filled(dot_rect.center(), DOT_RADIUS, dot_color);

                ui.vertical(|ui| {
                    // Agent name
                    ui.label(
                        egui::RichText::new(&agent.name)
                            .color(theme::TEXT)
                            .size(theme::FONT_SM)
                            .strong(),
                    );
                    // Role
                    ui.label(
                        egui::RichText::new(&agent.role)
                            .color(theme::DIM)
                            .size(theme::FONT_XS),
                    );
                });
            });
        });
}
