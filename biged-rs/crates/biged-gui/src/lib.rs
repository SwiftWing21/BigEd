pub mod api;
pub mod header;
pub mod sidebar;
pub mod state;
pub mod tabs;
pub mod theme;
pub mod widgets;

use api::ApiClient;
use state::AppState;

/// Launch the native desktop GUI (desktop feature only).
#[cfg(feature = "desktop")]
pub fn run_gui(server_url: &str) -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("BigEd CC")
            .with_inner_size([1050.0, 960.0])
            .with_min_inner_size([800.0, 720.0]),
        ..Default::default()
    };

    let server_url = server_url.to_string();

    eframe::run_native(
        "BigEd CC",
        options,
        Box::new(move |cc| Ok(Box::new(BigEdApp::new(cc, &server_url)))),
    )
}

/// Create the app instance — usable from both native and WASM entry points.
pub fn create_app(cc: &eframe::CreationContext<'_>, server_url: &str) -> BigEdApp {
    BigEdApp::new(cc, server_url)
}

pub struct BigEdApp {
    state: AppState,
    /// Kept alive to drive the background polling task (desktop only).
    #[cfg(feature = "desktop")]
    #[allow(dead_code)]
    runtime: tokio::runtime::Runtime,
}

impl BigEdApp {
    fn new(cc: &eframe::CreationContext<'_>, server_url: &str) -> Self {
        theme::apply_theme(&cc.egui_ctx);

        let mut api = ApiClient::new(server_url);

        #[cfg(feature = "desktop")]
        {
            let runtime = tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .build()
                .expect("tokio runtime");

            // Give the API client a handle so it can spawn fire-and-forget requests.
            api.set_runtime(runtime.handle().clone());

            // Spawn background polling task — refreshes every 4s and requests repaint.
            let poll_api = api.clone();
            let poll_ctx = cc.egui_ctx.clone();
            runtime.spawn(async move {
                loop {
                    poll_api.refresh().await;
                    poll_ctx.request_repaint();
                    tokio::time::sleep(std::time::Duration::from_secs(4)).await;
                }
            });

            let launcher_available = tabs::fleet::detect_launcher();
            if launcher_available {
                tracing::info!("Python launcher detected — 'Launch Modules' button enabled");
            }

            let mut state = AppState::new(api);
            state.launcher_available = launcher_available;

            Self {
                state,
                runtime,
            }
        }

        #[cfg(not(feature = "desktop"))]
        {
            Self {
                state: AppState::new(api),
            }
        }
    }
}

impl eframe::App for BigEdApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // ── Header bar
        egui::TopBottomPanel::top("header")
            .exact_height(theme::HEADER_HEIGHT)
            .show(ctx, |ui| {
                header::show(ui, &self.state);
            });

        // ── Sidebar (only when open)
        if self.state.sidebar_open {
            egui::SidePanel::left("sidebar")
                .exact_width(theme::SIDEBAR_WIDTH)
                .resizable(false)
                .show(ctx, |ui| {
                    sidebar::show(ui, &mut self.state);
                });
        }

        // ── Main tab content area
        egui::CentralPanel::default().show(ctx, |ui| {
            tabs::show_active_tab(ui, &mut self.state);
        });
    }
}
