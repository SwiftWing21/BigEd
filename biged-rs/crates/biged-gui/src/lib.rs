pub mod api;
pub mod theme;

pub fn run_gui() -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("BigEd CC")
            .with_inner_size([1050.0, 960.0])
            .with_min_inner_size([800.0, 720.0]),
        ..Default::default()
    };

    eframe::run_native(
        "BigEd CC",
        options,
        Box::new(|cc| Ok(Box::new(BigEdApp::new(cc)))),
    )
}

struct BigEdApp;

impl BigEdApp {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        theme::apply_theme(&cc.egui_ctx);
        Self
    }
}

impl eframe::App for BigEdApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        egui::CentralPanel::default().show(ctx, |ui| {
            ui.heading("BigEd CC");
            ui.label("Phase 4 — egui GUI scaffold");
        });
    }
}
