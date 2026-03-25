use egui::{Color32, CornerRadius, Sense, Vec2};

use crate::theme;

const CARD_W: f32 = 100.0;
const CARD_H: f32 = 60.0;
const CARD_ROUND: CornerRadius = CornerRadius::same(theme::CARD_ROUNDING as u8);

/// Small stat card: label (FONT_XS, DIM) on top, big coloured value (FONT_STAT) below.
/// Renders as a 100×60 px filled rect drawn via allocate_exact_size + painter.
pub fn show(ui: &mut egui::Ui, label: &str, value: &str, value_color: Color32) {
    let (rect, _resp) = ui.allocate_exact_size(Vec2::new(CARD_W, CARD_H), Sense::hover());

    let painter = ui.painter_at(rect);

    // Card background
    painter.rect_filled(rect, CARD_ROUND, theme::BG2);

    // Label — centred horizontally, upper third
    let label_y = rect.min.y + CARD_H * 0.28;
    painter.text(
        egui::pos2(rect.center().x, label_y),
        egui::Align2::CENTER_CENTER,
        label,
        egui::FontId::new(theme::FONT_XS, egui::FontFamily::Proportional),
        theme::DIM,
    );

    // Value — centred horizontally, lower two-thirds
    let value_y = rect.min.y + CARD_H * 0.68;
    painter.text(
        egui::pos2(rect.center().x, value_y),
        egui::Align2::CENTER_CENTER,
        value,
        egui::FontId::new(theme::FONT_STAT, egui::FontFamily::Proportional),
        value_color,
    );
}
