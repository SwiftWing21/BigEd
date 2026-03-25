use egui::{Color32, CornerRadius, Stroke, Visuals};

// ── Background layers
pub const BG: Color32 = Color32::from_rgb(0x1a, 0x1a, 0x1a);
pub const BG2: Color32 = Color32::from_rgb(0x24, 0x24, 0x24);
pub const BG3: Color32 = Color32::from_rgb(0x2d, 0x2d, 0x2d);

// ── Accent
pub const ACCENT: Color32 = Color32::from_rgb(0xb2, 0x22, 0x22); // firebrick
pub const ACCENT_HOVER: Color32 = Color32::from_rgb(0x8b, 0x00, 0x00);
pub const GOLD: Color32 = Color32::from_rgb(0xc8, 0xa8, 0x4b);
pub const BRAND: Color32 = Color32::from_rgb(0x00, 0xbc, 0xd4); // teal

// ── Text
pub const TEXT: Color32 = Color32::from_rgb(0xe2, 0xe2, 0xe2);
pub const DIM: Color32 = Color32::from_rgb(0x88, 0x88, 0x88);

// ── Status
pub const GREEN: Color32 = Color32::from_rgb(0x10, 0xb9, 0x81);
pub const ORANGE: Color32 = Color32::from_rgb(0xf5, 0x9e, 0x0b);
pub const RED: Color32 = Color32::from_rgb(0xef, 0x44, 0x44);
pub const BLUE: Color32 = Color32::from_rgb(0x4f, 0xc3, 0xf7);
pub const YELLOW: Color32 = Color32::from_rgb(0xff, 0xd5, 0x4f);

// ── Provider
pub const PROVIDER_LOCAL: Color32 = Color32::from_rgb(0xd4, 0xa8, 0x4b);
pub const PROVIDER_CLAUDE: Color32 = Color32::from_rgb(0x6b, 0x8a, 0xfd);
pub const PROVIDER_GEMINI: Color32 = Color32::from_rgb(0x4c, 0xaf, 0x50);

// ── Glass (settings panels)
pub const GLASS_BG: Color32 = Color32::from_rgb(0x0f, 0x0f, 0x0f);
pub const GLASS_PANEL: Color32 = Color32::from_rgb(0x18, 0x18, 0x18);
pub const GLASS_BORDER: Color32 = Color32::from_rgb(0x2a, 0x2a, 0x2a);

// ── Sidebar
pub const SB_HOVER: Color32 = Color32::from_rgb(0x1e, 0x29, 0x3b);
pub const SB_ACTIVE: Color32 = Color32::from_rgb(0x1e, 0x3a, 0x5f);

// ── Dimensions
pub const HEADER_HEIGHT: f32 = 60.0;
pub const SIDEBAR_WIDTH: f32 = 155.0;
pub const CARD_ROUNDING: f32 = 8.0;
pub const BTN_ROUNDING: f32 = 4.0;

// ── Font sizes
pub const FONT_BODY: f32 = 14.0;
pub const FONT_SM: f32 = 12.0;
pub const FONT_XS: f32 = 10.0;
pub const FONT_HEADING: f32 = 16.0;
pub const FONT_TITLE: f32 = 18.0;
pub const FONT_STAT: f32 = 22.0;

/// Apply BigEd dark theme to egui context.
pub fn apply_theme(ctx: &egui::Context) {
    let mut style = (*ctx.style()).clone();

    let mut visuals = Visuals::dark();
    visuals.panel_fill = BG;
    visuals.window_fill = BG;
    visuals.extreme_bg_color = BG3;
    visuals.faint_bg_color = BG2;
    visuals.selection.bg_fill = ACCENT;
    visuals.selection.stroke = Stroke::new(1.0, TEXT);
    visuals.override_text_color = Some(TEXT);
    visuals.window_corner_radius = CornerRadius::same(CARD_ROUNDING as u8);
    visuals.widgets.noninteractive.bg_fill = BG2;
    visuals.widgets.inactive.bg_fill = BG2;
    visuals.widgets.hovered.bg_fill = BG3;
    visuals.widgets.active.bg_fill = ACCENT;

    style.visuals = visuals;
    style.spacing.item_spacing = egui::vec2(8.0, 6.0);

    ctx.set_style(style);
}
