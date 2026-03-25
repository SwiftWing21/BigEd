use crate::state::AppState;
use crate::theme;
use egui::{Align2, FontId, Stroke};

/// Paint the top header bar: title left, connection status + stats right.
pub fn show(ui: &mut egui::Ui, state: &AppState) {
    let rect = ui.max_rect();
    let painter = ui.painter_at(rect);

    // Background fill.
    painter.rect_filled(rect, 0.0, theme::BG2);

    // Bottom border line.
    painter.line_segment(
        [rect.left_bottom(), rect.right_bottom()],
        Stroke::new(1.0, theme::GLASS_BORDER),
    );

    // "BigEd CC" title — left-aligned, vertically centred.
    let title_pos = egui::pos2(rect.left() + 20.0, rect.center().y);
    painter.text(
        title_pos,
        Align2::LEFT_CENTER,
        "BigEd CC",
        FontId::new(theme::FONT_TITLE, egui::FontFamily::Proportional),
        theme::GOLD,
    );

    // Connection dot + stats — right-aligned.
    let connected = state.connected();
    let status = state.status();

    let dot_color = if connected { theme::GREEN } else { theme::RED };
    let dot_label = if connected { "LIVE" } else { "OFFLINE" };
    let stats_text = format!(
        "agents: {}  pending: {}  done: {}",
        status.agent_count.unwrap_or(0),
        status.task_pending.unwrap_or(0),
        status.task_done.unwrap_or(0),
    );

    // Stats text (monospace, dim) — right edge.
    let stats_pos = egui::pos2(rect.right() - 16.0, rect.center().y + 8.0);
    painter.text(
        stats_pos,
        Align2::RIGHT_CENTER,
        &stats_text,
        FontId::new(theme::FONT_XS, egui::FontFamily::Monospace),
        theme::DIM,
    );

    // Connection status dot + label above stats.
    let label_pos = egui::pos2(rect.right() - 16.0, rect.center().y - 9.0);
    let label_galley = painter.layout_no_wrap(
        dot_label.to_string(),
        FontId::new(theme::FONT_XS, egui::FontFamily::Monospace),
        dot_color,
    );
    let label_width = label_galley.size().x;

    // Dot is 7px to the left of the label.
    let dot_center = egui::pos2(label_pos.x - label_width - 10.0, label_pos.y);
    painter.circle_filled(dot_center, 4.0, dot_color);

    // Paint the label.
    painter.galley(
        egui::pos2(
            label_pos.x - label_width,
            label_pos.y - label_galley.size().y / 2.0,
        ),
        label_galley,
        dot_color,
    );

    // Consume all the space so egui doesn't place widgets on top.
    ui.allocate_rect(rect, egui::Sense::hover());
}
