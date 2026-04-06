use crate::state::{AppState, Tab};
use crate::theme;
use egui::{FontId, Sense, Stroke, Vec2};

struct NavItem {
    tab: Tab,
    label: &'static str,
}

const NAV_ITEMS: &[NavItem] = &[
    NavItem {
        tab: Tab::Overview,
        label: "Overview",
    },
    NavItem {
        tab: Tab::Fleet,
        label: "Fleet",
    },
    NavItem {
        tab: Tab::Tasks,
        label: "Tasks",
    },
    NavItem {
        tab: Tab::Config,
        label: "Config",
    },
    NavItem {
        tab: Tab::Logs,
        label: "Logs",
    },
];

/// Paint the sidebar navigation panel.
pub fn show(ui: &mut egui::Ui, state: &mut AppState) {
    let full_rect = ui.max_rect();
    let painter = ui.painter_at(full_rect);

    // Sidebar background.
    painter.rect_filled(full_rect, 0.0, theme::BG2);

    // Right border.
    painter.line_segment(
        [full_rect.right_top(), full_rect.right_bottom()],
        Stroke::new(1.0, theme::GLASS_BORDER),
    );

    let btn_height = 56.0;
    let btn_width = theme::SIDEBAR_WIDTH;
    let accent_bar_w = 3.0;

    for item in NAV_ITEMS {
        let (rect, response) =
            ui.allocate_exact_size(Vec2::new(btn_width, btn_height), Sense::click());

        let is_active = state.active_tab == item.tab;

        // Background.
        let bg = if is_active {
            theme::SB_ACTIVE
        } else if response.hovered() {
            theme::SB_HOVER
        } else {
            theme::BG2
        };
        painter.rect_filled(rect, 0.0, bg);

        // Gold accent bar on left edge when active.
        if is_active {
            painter.rect_filled(
                egui::Rect::from_min_size(rect.left_top(), Vec2::new(accent_bar_w, btn_height)),
                0.0,
                theme::GOLD,
            );
        }

        // Label text, centred in button.
        let text_color = if is_active { theme::TEXT } else { theme::DIM };
        let font = FontId::new(theme::FONT_SM, egui::FontFamily::Proportional);

        let galley = painter.layout(
            item.label.to_string(),
            font,
            text_color,
            btn_width - accent_bar_w - 16.0,
        );
        let text_pos = egui::pos2(
            rect.left() + accent_bar_w + 12.0,
            rect.center().y - galley.size().y / 2.0,
        );
        painter.galley(text_pos, galley, text_color);

        if response.clicked() {
            state.active_tab = item.tab;
        }
    }

    // Consume remaining space.
    let used = ui.min_rect();
    let remaining =
        egui::Rect::from_min_max(egui::pos2(full_rect.left(), used.bottom()), full_rect.max);
    if remaining.height() > 0.0 {
        painter.rect_filled(remaining, 0.0, theme::BG2);
    }
}
