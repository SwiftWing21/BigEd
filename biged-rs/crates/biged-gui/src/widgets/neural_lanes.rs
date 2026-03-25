use egui::{Color32, CornerRadius, Rect, Sense, Vec2};

use crate::api::ActivityLane;
use crate::theme;

const RUNNING_BLUE: Color32 = Color32::from_rgb(0x3b, 0x82, 0xf6);
const LABEL_WIDTH: f32 = 90.0;
const LANE_HEIGHT: f32 = 18.0;
const LANE_GAP: f32 = 6.0;
const MAX_LANES: usize = 8;
const BAR_ROUNDING: CornerRadius = CornerRadius::same(3);

/// Swim-lane painter: up to 8 lanes with stacked horizontal bars.
/// Done = GREEN, Failed = RED, Running = BLUE (#3b82f6).
pub fn show(ui: &mut egui::Ui, lanes: &[ActivityLane]) {
    let display_lanes = if lanes.len() > MAX_LANES {
        &lanes[..MAX_LANES]
    } else {
        lanes
    };

    if display_lanes.is_empty() {
        ui.label(
            egui::RichText::new("No lane data")
                .color(theme::DIM)
                .size(theme::FONT_SM),
        );
        return;
    }

    for lane in display_lanes {
        let total = (lane.done + lane.failed + lane.running).max(1) as f32;

        // Allocate one row: label + bar
        let desired = Vec2::new(ui.available_width(), LANE_HEIGHT);
        let (rect, _resp) = ui.allocate_exact_size(desired, Sense::hover());

        let painter = ui.painter_at(rect);

        // Label (left-aligned, vertically centred)
        let label_pos = egui::pos2(rect.min.x, rect.center().y);
        painter.text(
            label_pos,
            egui::Align2::LEFT_CENTER,
            &lane.name,
            egui::FontId::new(theme::FONT_XS, egui::FontFamily::Proportional),
            theme::DIM,
        );

        // Bar area (everything to the right of the label)
        let bar_x = rect.min.x + LABEL_WIDTH;
        let bar_width = (rect.max.x - bar_x).max(0.0);
        let bar_rect = Rect::from_min_size(
            egui::pos2(bar_x, rect.min.y),
            Vec2::new(bar_width, LANE_HEIGHT),
        );

        // Background track
        painter.rect_filled(bar_rect, BAR_ROUNDING, theme::BG3);

        // Stacked segments: done | running | failed
        let mut cursor_x = bar_rect.min.x;

        let done_w = (lane.done as f32 / total) * bar_width;
        if done_w > 0.5 {
            let seg = Rect::from_min_size(
                egui::pos2(cursor_x, bar_rect.min.y),
                Vec2::new(done_w, LANE_HEIGHT),
            );
            painter.rect_filled(seg, BAR_ROUNDING, theme::GREEN);
            cursor_x += done_w;
        }

        let running_w = (lane.running as f32 / total) * bar_width;
        if running_w > 0.5 {
            let seg = Rect::from_min_size(
                egui::pos2(cursor_x, bar_rect.min.y),
                Vec2::new(running_w, LANE_HEIGHT),
            );
            painter.rect_filled(seg, BAR_ROUNDING, RUNNING_BLUE);
            cursor_x += running_w;
        }

        let failed_w = (lane.failed as f32 / total) * bar_width;
        if failed_w > 0.5 {
            let seg = Rect::from_min_size(
                egui::pos2(cursor_x, bar_rect.min.y),
                Vec2::new(failed_w, LANE_HEIGHT),
            );
            painter.rect_filled(seg, BAR_ROUNDING, theme::RED);
        }

        ui.add_space(LANE_GAP);
    }
}
