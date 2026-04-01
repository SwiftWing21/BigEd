# BigEd Rust Rewrite — Phase 4: egui Desktop GUI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `biged-gui` crate with egui/eframe, providing a native desktop GUI that replaces the customtkinter launcher with feature parity for the core tabs (Command Center, Fleet, Fleet Comm, Settings).

**Architecture:** New `biged-gui` crate using eframe for windowing and egui for immediate-mode UI. An `ApiClient` connects to `biged-server` via HTTP (reqwest) and SSE for real-time updates. App state is cached locally and refreshed reactively. The Figma dark theme maps to `egui::Visuals`. NeuralLanes renders agent activity at 60fps via `egui::Painter`.

**Tech Stack:** eframe 0.30, egui 0.30, reqwest 0.12 (HTTP+SSE), tokio (async runtime), serde_json (API responses), biged-core (types, config).

**Deferred from spec:** Files tab (egui_file_dialog), Graph View (native renderer), NeuralLanes animated pulses + Bezier edges, WebSocket binary MessagePack transport. These are post-Phase 4 enhancements — the core tabs establish the foundation first.

**Spec:** `docs/superpowers/specs/2026-03-24-rust-hybrid-architecture-design.md`

**Depends on:** Phase 2 (biged-server) for API endpoints.

**Rollback:** customtkinter launcher stays alongside. Both point at the same server.

---

## File Structure

```
biged-rs/
├── Cargo.toml                                    # MODIFY: add biged-gui + eframe/egui deps
├── crates/
│   └── biged-gui/                                # NEW CRATE
│       ├── Cargo.toml
│       └── src/
│           ├── lib.rs                            # BigEdApp + eframe::App impl
│           ├── theme.rs                          # Figma dark theme → egui::Visuals
│           ├── api.rs                            # ApiClient: HTTP + SSE to biged-server
│           ├── state.rs                          # AppState: cached fleet data model
│           ├── header.rs                         # Header bar: logo, stats, badges
│           ├── sidebar.rs                        # Sidebar: nav buttons, fleet controls
│           ├── tabs/
│           │   ├── mod.rs                        # Tab enum + dispatch
│           │   ├── command_center.rs             # Command Center: NeuralLanes + log + queue
│           │   ├── fleet.rs                      # Fleet: counter cards + agent grid
│           │   ├── fleet_comm.rs                 # Fleet Comm: chat + HITL queue
│           │   └── settings.rs                   # Settings: theme, keys, hardware
│           └── widgets/
│               ├── mod.rs                        # Widget re-exports
│               ├── neural_lanes.rs               # NeuralLanes: 60fps swim lane painter
│               ├── agent_card.rs                 # Agent card with status dot + metrics
│               └── counter_card.rs               # Colored stat card (TOTAL, IDLE, BUSY, etc.)
├── src/
│   └── main.rs                                   # MODIFY: add `gui` subcommand
└── tests/
    └── gui_test.rs                               # NEW: theme + state + API tests
```

---

## Task 1: Crate Scaffold + Empty Window

**Files:**
- Create: `biged-rs/crates/biged-gui/Cargo.toml`
- Create: `biged-rs/crates/biged-gui/src/lib.rs`
- Modify: `biged-rs/Cargo.toml`

- [ ] **Step 1: Add workspace dependencies to root Cargo.toml**

Add to `[workspace.dependencies]`:
```toml
eframe = "0.30"
egui = "0.30"
```

Add to `[workspace.members]`:
```
"crates/biged-gui"
```

Add to root `[dependencies]`:
```toml
biged-gui = { path = "crates/biged-gui" }
```

- [ ] **Step 2: Create biged-gui Cargo.toml**

```toml
[package]
name = "biged-gui"
version = "0.1.0"
edition.workspace = true

[dependencies]
biged-core = { path = "../biged-core" }
eframe = { workspace = true }
egui = { workspace = true }
reqwest.workspace = true
tokio.workspace = true
serde.workspace = true
serde_json.workspace = true
tracing.workspace = true
anyhow.workspace = true
```

- [ ] **Step 3: Create lib.rs with minimal eframe app**

```rust
// biged-rs/crates/biged-gui/src/lib.rs

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
    fn new(_cc: &eframe::CreationContext<'_>) -> Self {
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
```

- [ ] **Step 4: Verify it compiles**

Run: `cd biged-rs && cargo check -p biged-gui`
Expected: compiles clean

- [ ] **Step 5: cargo fmt + cargo clippy**

- [ ] **Step 6: Commit**

```bash
git add biged-rs/crates/biged-gui/ biged-rs/Cargo.toml biged-rs/Cargo.lock
git commit -m "feat(gui): scaffold biged-gui crate — eframe window renders"
```

---

## Task 2: Theme System

**Files:**
- Create: `biged-rs/crates/biged-gui/src/theme.rs`
- Modify: `biged-rs/crates/biged-gui/src/lib.rs`

- [ ] **Step 1: Create theme.rs with full Figma dark palette**

```rust
// biged-rs/crates/biged-gui/src/theme.rs
use egui::{Color32, FontFamily, FontId, Rounding, Stroke, Style, Visuals};

// ── Background layers ──────────────────────────────────────────────
pub const BG: Color32 = Color32::from_rgb(0x1a, 0x1a, 0x1a);
pub const BG2: Color32 = Color32::from_rgb(0x24, 0x24, 0x24);
pub const BG3: Color32 = Color32::from_rgb(0x2d, 0x2d, 0x2d);

// ── Accent ─────────────────────────────────────────────────────────
pub const ACCENT: Color32 = Color32::from_rgb(0xb2, 0x22, 0x22);     // firebrick
pub const ACCENT_HOVER: Color32 = Color32::from_rgb(0x8b, 0x00, 0x00);
pub const GOLD: Color32 = Color32::from_rgb(0xc8, 0xa8, 0x4b);
pub const BRAND: Color32 = Color32::from_rgb(0x00, 0xbc, 0xd4);      // teal

// ── Text ───────────────────────────────────────────────────────────
pub const TEXT: Color32 = Color32::from_rgb(0xe2, 0xe2, 0xe2);
pub const DIM: Color32 = Color32::from_rgb(0x88, 0x88, 0x88);

// ── Status ─────────────────────────────────────────────────────────
pub const GREEN: Color32 = Color32::from_rgb(0x10, 0xb9, 0x81);
pub const ORANGE: Color32 = Color32::from_rgb(0xf5, 0x9e, 0x0b);
pub const RED: Color32 = Color32::from_rgb(0xef, 0x44, 0x44);
pub const BLUE: Color32 = Color32::from_rgb(0x4f, 0xc3, 0xf7);
pub const YELLOW: Color32 = Color32::from_rgb(0xff, 0xd5, 0x4f);

// ── Provider ───────────────────────────────────────────────────────
pub const PROVIDER_LOCAL: Color32 = Color32::from_rgb(0xd4, 0xa8, 0x4b);
pub const PROVIDER_CLAUDE: Color32 = Color32::from_rgb(0x6b, 0x8a, 0xfd);
pub const PROVIDER_GEMINI: Color32 = Color32::from_rgb(0x4c, 0xaf, 0x50);

// ── Glass (settings panels) ───────────────────────────────────────
pub const GLASS_BG: Color32 = Color32::from_rgb(0x0f, 0x0f, 0x0f);
pub const GLASS_PANEL: Color32 = Color32::from_rgb(0x18, 0x18, 0x18);
pub const GLASS_BORDER: Color32 = Color32::from_rgb(0x2a, 0x2a, 0x2a);

// ── Sidebar ────────────────────────────────────────────────────────
pub const SB_HOVER: Color32 = Color32::from_rgb(0x1e, 0x29, 0x3b);
pub const SB_ACTIVE: Color32 = Color32::from_rgb(0x1e, 0x3a, 0x5f);

// ── Dimensions ─────────────────────────────────────────────────────
pub const HEADER_HEIGHT: f32 = 60.0;
pub const SIDEBAR_WIDTH: f32 = 155.0;
pub const CARD_ROUNDING: f32 = 8.0;
pub const BTN_ROUNDING: f32 = 4.0;

// ── Fonts ──────────────────────────────────────────────────────────
pub const FONT_BODY: f32 = 14.0;
pub const FONT_SM: f32 = 12.0;
pub const FONT_XS: f32 = 10.0;
pub const FONT_HEADING: f32 = 16.0;
pub const FONT_TITLE: f32 = 18.0;
pub const FONT_STAT: f32 = 22.0;

pub fn font_body() -> FontId {
    FontId::new(FONT_BODY, FontFamily::Proportional)
}

pub fn font_mono() -> FontId {
    FontId::new(FONT_SM, FontFamily::Monospace)
}

pub fn font_heading() -> FontId {
    FontId::new(FONT_HEADING, FontFamily::Proportional)
}

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
    visuals.window_rounding = Rounding::same(CARD_ROUNDING);
    visuals.widgets.noninteractive.bg_fill = BG2;
    visuals.widgets.inactive.bg_fill = BG2;
    visuals.widgets.hovered.bg_fill = BG3;
    visuals.widgets.active.bg_fill = ACCENT;

    style.visuals = visuals;
    style.spacing.item_spacing = egui::vec2(8.0, 6.0);

    ctx.set_style(style);
}
```

- [ ] **Step 2: Wire theme into lib.rs**

Update `BigEdApp::new` to call `apply_theme`:

```rust
mod theme;

impl BigEdApp {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        theme::apply_theme(&cc.egui_ctx);
        Self
    }
}
```

- [ ] **Step 3: Verify it compiles and cargo fmt + clippy**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(gui): Figma dark theme — full color palette + egui::Visuals mapping"
```

---

## Task 3: API Client (HTTP + SSE)

**Files:**
- Create: `biged-rs/crates/biged-gui/src/api.rs`

- [ ] **Step 1: Create api.rs — async HTTP client with SSE**

```rust
// biged-rs/crates/biged-gui/src/api.rs
use anyhow::Result;
use serde::Deserialize;
use std::sync::{Arc, Mutex};

/// Fleet status snapshot from /api/status.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct FleetStatus {
    pub version: Option<String>,
    pub uptime_secs: Option<f64>,
    pub agent_count: Option<u32>,
    pub task_pending: Option<u32>,
    pub task_running: Option<u32>,
    pub task_done: Option<u64>,
    pub task_failed: Option<u64>,
}

/// Agent info from /api/agents.
#[derive(Debug, Clone, Deserialize)]
pub struct AgentInfo {
    pub name: String,
    pub role: String,
    pub status: String,
    pub last_heartbeat: Option<String>,
}

/// Activity lane data from /api/activity.
#[derive(Debug, Clone, Deserialize)]
pub struct ActivityLane {
    pub name: String,
    pub done: u32,
    pub failed: u32,
    pub running: u32,
}

/// Thermal info from /api/thermal.
#[derive(Debug, Clone, Deserialize)]
pub struct ThermalInfo {
    pub gpu_temp: Option<f32>,
    pub cpu_temp: Option<f32>,
    pub action: Option<String>,
}

/// Non-blocking API client — spawns requests on tokio, stores results.
#[derive(Clone)]
pub struct ApiClient {
    base_url: String,
    http: reqwest::Client,
    pub status: Arc<Mutex<FleetStatus>>,
    pub agents: Arc<Mutex<Vec<AgentInfo>>>,
    pub lanes: Arc<Mutex<Vec<ActivityLane>>>,
    pub thermal: Arc<Mutex<ThermalInfo>>,
    pub connected: Arc<Mutex<bool>>,
}

impl ApiClient {
    pub fn new(base_url: &str) -> Self {
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(10))
                .build()
                .expect("HTTP client"),
            status: Arc::new(Mutex::new(FleetStatus::default())),
            agents: Arc::new(Mutex::new(Vec::new())),
            lanes: Arc::new(Mutex::new(Vec::new())),
            thermal: Arc::new(Mutex::new(ThermalInfo {
                gpu_temp: None,
                cpu_temp: None,
                action: None,
            })),
            connected: Arc::new(Mutex::new(false)),
        }
    }

    /// Fetch all endpoints and update cached state. Call from background task.
    pub async fn refresh(&self) {
        let connected = self.fetch_status().await.is_ok();
        *self.connected.lock().unwrap() = connected;
        if connected {
            if let Err(e) = self.fetch_agents().await {
                tracing::warn!("fetch_agents failed: {e}");
            }
            if let Err(e) = self.fetch_lanes().await {
                tracing::warn!("fetch_lanes failed: {e}");
            }
            if let Err(e) = self.fetch_thermal().await {
                tracing::warn!("fetch_thermal failed: {e}");
            }
        }
    }

    async fn fetch_status(&self) -> Result<()> {
        let url = format!("{}/api/status", self.base_url);
        let resp: FleetStatus = self.http.get(&url).send().await?.json().await?;
        *self.status.lock().unwrap() = resp;
        Ok(())
    }

    async fn fetch_agents(&self) -> Result<()> {
        let url = format!("{}/api/agents", self.base_url);
        let resp: Vec<AgentInfo> = self.http.get(&url).send().await?.json().await?;
        *self.agents.lock().unwrap() = resp;
        Ok(())
    }

    async fn fetch_lanes(&self) -> Result<()> {
        let url = format!("{}/api/activity", self.base_url);
        let resp: Vec<ActivityLane> = self.http.get(&url).send().await?.json().await?;
        *self.lanes.lock().unwrap() = resp;
        Ok(())
    }

    async fn fetch_thermal(&self) -> Result<()> {
        let url = format!("{}/api/thermal", self.base_url);
        let resp: ThermalInfo = self.http.get(&url).send().await?.json().await?;
        *self.thermal.lock().unwrap() = resp;
        Ok(())
    }
}
```

- [ ] **Step 2: Verify it compiles**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(gui): API client — HTTP fetch for status, agents, lanes, thermal"
```

---

## Task 4: App State + Reactive Data Model

**Files:**
- Create: `biged-rs/crates/biged-gui/src/state.rs`
- Modify: `biged-rs/crates/biged-gui/src/lib.rs`

- [ ] **Step 1: Create state.rs — cached state with snapshot reads**

```rust
// biged-rs/crates/biged-gui/src/state.rs
use crate::api::{ActivityLane, AgentInfo, ApiClient, FleetStatus, ThermalInfo};

/// Tab identifiers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tab {
    CommandCenter,
    Fleet,
    FleetComm,
    Settings,
}

/// Settings sub-sections.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SettingsSection {
    General,
    Hardware,
    Display,
    Keys,
}

/// Application state — drives all UI rendering.
pub struct AppState {
    pub active_tab: Tab,
    pub sidebar_open: bool,
    pub settings_section: SettingsSection,
    pub chat_input: String,
    pub api: ApiClient,
}

impl AppState {
    pub fn new(api: ApiClient) -> Self {
        Self {
            active_tab: Tab::CommandCenter,
            sidebar_open: true,
            settings_section: SettingsSection::General,
            chat_input: String::new(),
            api,
        }
    }

    /// Snapshot current fleet status (lock-free read, clone).
    pub fn status(&self) -> FleetStatus {
        self.api.status.lock().unwrap().clone()
    }

    pub fn agents(&self) -> Vec<AgentInfo> {
        self.api.agents.lock().unwrap().clone()
    }

    pub fn lanes(&self) -> Vec<ActivityLane> {
        self.api.lanes.lock().unwrap().clone()
    }

    pub fn thermal(&self) -> ThermalInfo {
        self.api.thermal.lock().unwrap().clone()
    }

    pub fn connected(&self) -> bool {
        *self.api.connected.lock().unwrap()
    }
}
```

- [ ] **Step 2: Update lib.rs to use AppState with background polling**

```rust
mod api;
mod state;
mod theme;

use api::ApiClient;
use state::{AppState, Tab};

pub fn run_gui(server_url: &str) -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("BigEd CC")
            .with_inner_size([1050.0, 960.0])
            .with_min_inner_size([800.0, 720.0]),
        ..Default::default()
    };

    let url = server_url.to_string();
    eframe::run_native(
        "BigEd CC",
        options,
        Box::new(move |cc| {
            theme::apply_theme(&cc.egui_ctx);
            Ok(Box::new(BigEdApp::new(&url, cc.egui_ctx.clone())))
        }),
    )
}

struct BigEdApp {
    state: AppState,
    runtime: tokio::runtime::Runtime,
}

impl BigEdApp {
    fn new(server_url: &str, ctx: egui::Context) -> Self {
        let api = ApiClient::new(server_url);
        let state = AppState::new(api.clone());

        // Background polling every 4 seconds
        let rt = tokio::runtime::Runtime::new().expect("tokio runtime");
        let poll_api = api.clone();
        rt.spawn(async move {
            loop {
                poll_api.refresh().await;
                ctx.request_repaint();
                tokio::time::sleep(std::time::Duration::from_secs(4)).await;
            }
        });

        Self {
            state,
            runtime: rt,
        }
    }
}

impl eframe::App for BigEdApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        egui::CentralPanel::default().show(ctx, |ui| {
            let status = self.state.status();
            let connected = self.state.connected();
            ui.heading("BigEd CC");
            ui.label(format!(
                "Connected: {} | Agents: {} | Pending: {}",
                connected,
                status.agent_count.unwrap_or(0),
                status.task_pending.unwrap_or(0),
            ));
            ui.label(format!("Active tab: {:?}", self.state.active_tab));
        });
    }
}
```

- [ ] **Step 3: Verify it compiles, fmt, clippy**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(gui): app state + background API polling every 4s"
```

---

## Task 5: Main Layout (Header + Sidebar + Tab Area)

**Files:**
- Create: `biged-rs/crates/biged-gui/src/header.rs`
- Create: `biged-rs/crates/biged-gui/src/sidebar.rs`
- Create: `biged-rs/crates/biged-gui/src/tabs/mod.rs`
- Modify: `biged-rs/crates/biged-gui/src/lib.rs`

- [ ] **Step 1: Create header.rs**

```rust
// biged-rs/crates/biged-gui/src/header.rs
use crate::state::AppState;
use crate::theme;

pub fn show(ui: &mut egui::Ui, state: &AppState) {
    let rect = ui.available_rect_before_wrap();
    let header_rect = egui::Rect::from_min_size(
        rect.min,
        egui::vec2(rect.width(), theme::HEADER_HEIGHT),
    );
    ui.allocate_rect(header_rect, egui::Sense::hover());

    let painter = ui.painter_at(header_rect);
    painter.rect_filled(header_rect, 0.0, theme::BG2);

    // Title
    painter.text(
        header_rect.left_center() + egui::vec2(16.0, 0.0),
        egui::Align2::LEFT_CENTER,
        "BigEd CC",
        theme::font_heading(),
        theme::GOLD,
    );

    // Connection status dot
    let status = state.status();
    let connected = state.connected();
    let dot_color = if connected { theme::GREEN } else { theme::RED };
    let dot_center = header_rect.right_center() - egui::vec2(200.0, 0.0);
    painter.circle_filled(dot_center, 5.0, dot_color);

    // Stats
    let stats_text = format!(
        "Agents: {}  Pending: {}  Done: {}",
        status.agent_count.unwrap_or(0),
        status.task_pending.unwrap_or(0),
        status.task_done.unwrap_or(0),
    );
    painter.text(
        dot_center + egui::vec2(14.0, 0.0),
        egui::Align2::LEFT_CENTER,
        stats_text,
        theme::font_mono(),
        theme::DIM,
    );
}
```

- [ ] **Step 2: Create sidebar.rs**

```rust
// biged-rs/crates/biged-gui/src/sidebar.rs
use crate::state::{AppState, Tab};
use crate::theme;

pub fn show(ui: &mut egui::Ui, state: &mut AppState) {
    ui.vertical(|ui| {
        ui.add_space(8.0);
        ui.colored_label(theme::GOLD, "NAVIGATION");
        ui.add_space(4.0);

        tab_button(ui, state, Tab::CommandCenter, "Command Center");
        tab_button(ui, state, Tab::Fleet, "Fleet");
        tab_button(ui, state, Tab::FleetComm, "Fleet Comm");
        tab_button(ui, state, Tab::Settings, "Settings");
    });
}

fn tab_button(ui: &mut egui::Ui, state: &mut AppState, tab: Tab, label: &str) {
    let active = state.active_tab == tab;

    let bg = if active { theme::SB_ACTIVE } else { theme::BG2 };
    let text_color = if active { theme::TEXT } else { theme::DIM };

    let button = egui::Button::new(
        egui::RichText::new(label).color(text_color).size(theme::FONT_SM),
    )
    .fill(bg)
    .rounding(theme::BTN_ROUNDING)
    .min_size(egui::vec2(theme::SIDEBAR_WIDTH - 16.0, 30.0));

    let response = ui.add(button);

    // Gold accent bar on active tab
    if active {
        let rect = response.rect;
        let painter = ui.painter();
        painter.rect_filled(
            egui::Rect::from_min_size(rect.left_top(), egui::vec2(3.0, rect.height())),
            0.0,
            theme::GOLD,
        );
    }

    if response.clicked() {
        state.active_tab = tab;
    }
}
```

- [ ] **Step 3: Create tabs/mod.rs**

```rust
// biged-rs/crates/biged-gui/src/tabs/mod.rs
pub mod command_center;
pub mod fleet;
pub mod fleet_comm;
pub mod settings;

use crate::state::{AppState, Tab};

pub fn show_active_tab(ui: &mut egui::Ui, state: &mut AppState) {
    match state.active_tab {
        Tab::CommandCenter => command_center::show(ui, state),
        Tab::Fleet => fleet::show(ui, state),
        Tab::FleetComm => fleet_comm::show(ui, state),
        Tab::Settings => settings::show(ui, state),
    }
}
```

Create stub files for each tab (valid Rust, placeholder UI):

```rust
// biged-rs/crates/biged-gui/src/tabs/command_center.rs
use crate::state::AppState;
pub fn show(ui: &mut egui::Ui, _state: &AppState) {
    ui.heading("Command Center");
    ui.label("Neural lanes and fleet activity will render here.");
}
```

```rust
// biged-rs/crates/biged-gui/src/tabs/fleet.rs
use crate::state::AppState;
pub fn show(ui: &mut egui::Ui, _state: &AppState) {
    ui.heading("Fleet");
    ui.label("Agent cards and counter cards will render here.");
}
```

```rust
// biged-rs/crates/biged-gui/src/tabs/fleet_comm.rs
use crate::state::AppState;
pub fn show(ui: &mut egui::Ui, _state: &mut AppState) {
    ui.heading("Fleet Comm");
    ui.label("Chat interface and HITL queue will render here.");
}
```

```rust
// biged-rs/crates/biged-gui/src/tabs/settings.rs
use crate::state::AppState;
pub fn show(ui: &mut egui::Ui, _state: &mut AppState) {
    ui.heading("Settings");
    ui.label("Theme, keys, and hardware settings will render here.");
}
```

- [ ] **Step 4: Wire layout into lib.rs update()**

Replace the `update` method body:

```rust
impl eframe::App for BigEdApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Header
        egui::TopBottomPanel::top("header")
            .exact_height(theme::HEADER_HEIGHT)
            .show(ctx, |ui| {
                header::show(ui, &self.state);
            });

        // Sidebar
        if self.state.sidebar_open {
            egui::SidePanel::left("sidebar")
                .exact_width(theme::SIDEBAR_WIDTH)
                .show(ctx, |ui| {
                    sidebar::show(ui, &mut self.state);
                });
        }

        // Main content area
        egui::CentralPanel::default().show(ctx, |ui| {
            tabs::show_active_tab(ui, &mut self.state);
        });
    }
}
```

- [ ] **Step 5: Verify compiles, fmt, clippy**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(gui): main layout — header bar, sidebar nav, tab switching"
```

---

## Task 6: Command Center + NeuralLanes Widget

**Files:**
- Create: `biged-rs/crates/biged-gui/src/widgets/mod.rs`
- Create: `biged-rs/crates/biged-gui/src/widgets/neural_lanes.rs`
- Modify: `biged-rs/crates/biged-gui/src/tabs/command_center.rs`

- [ ] **Step 1: Create widgets/mod.rs**

```rust
pub mod neural_lanes;
pub mod agent_card;
pub mod counter_card;
```

Create empty stubs for `agent_card.rs` and `counter_card.rs`.

- [ ] **Step 2: Create neural_lanes.rs — 60fps swim lane painter**

```rust
// biged-rs/crates/biged-gui/src/widgets/neural_lanes.rs
use egui::Color32;
use crate::api::ActivityLane;
use crate::theme;

const LANE_HEIGHT: f32 = 18.0;
const LANE_GAP: f32 = 3.0;
const LABEL_WIDTH: f32 = 90.0;
const MAX_LANES: usize = 8;

/// Paint neural activity swim lanes.
pub fn paint(ui: &mut egui::Ui, lanes: &[ActivityLane]) {
    let display_lanes = &lanes[..lanes.len().min(MAX_LANES)];
    let total_height = display_lanes.len() as f32 * (LANE_HEIGHT + LANE_GAP);
    let (rect, _) = ui.allocate_exact_size(
        egui::vec2(ui.available_width(), total_height.max(40.0)),
        egui::Sense::hover(),
    );

    let painter = ui.painter_at(rect);

    // Track background
    painter.rect_filled(rect, 4.0, Color32::from_rgb(0x1a, 0x1f, 0x2e));

    for (i, lane) in display_lanes.iter().enumerate() {
        let y = rect.min.y + i as f32 * (LANE_HEIGHT + LANE_GAP);
        let lane_rect = egui::Rect::from_min_size(
            egui::pos2(rect.min.x, y),
            egui::vec2(rect.width(), LANE_HEIGHT),
        );

        // Label
        painter.text(
            egui::pos2(lane_rect.min.x + 4.0, lane_rect.center().y),
            egui::Align2::LEFT_CENTER,
            &lane.name,
            theme::font_mono(),
            theme::DIM,
        );

        // Stacked bar
        let bar_left = lane_rect.min.x + LABEL_WIDTH;
        let bar_width = lane_rect.max.x - bar_left - 4.0;
        let total = (lane.done + lane.failed + lane.running).max(1) as f32;

        let done_w = (lane.done as f32 / total) * bar_width;
        let failed_w = (lane.failed as f32 / total) * bar_width;
        let running_w = (lane.running as f32 / total) * bar_width;

        let bar_y = lane_rect.min.y + 2.0;
        let bar_h = LANE_HEIGHT - 4.0;
        let mut x = bar_left;

        // Done (green)
        if done_w > 0.5 {
            painter.rect_filled(
                egui::Rect::from_min_size(egui::pos2(x, bar_y), egui::vec2(done_w, bar_h)),
                2.0,
                theme::GREEN,
            );
            x += done_w;
        }

        // Failed (red)
        if failed_w > 0.5 {
            painter.rect_filled(
                egui::Rect::from_min_size(egui::pos2(x, bar_y), egui::vec2(failed_w, bar_h)),
                2.0,
                theme::RED,
            );
            x += failed_w;
        }

        // Running (blue)
        if running_w > 0.5 {
            painter.rect_filled(
                egui::Rect::from_min_size(egui::pos2(x, bar_y), egui::vec2(running_w, bar_h)),
                2.0,
                egui::Color32::from_rgb(0x3b, 0x82, 0xf6),
            );
        }
    }
}
```

- [ ] **Step 3: Wire into command_center.rs**

```rust
// biged-rs/crates/biged-gui/src/tabs/command_center.rs
use crate::state::AppState;
use crate::theme;
use crate::widgets;

pub fn show(ui: &mut egui::Ui, state: &AppState) {
    ui.colored_label(theme::GOLD, "NEURAL ACTIVITY");
    ui.add_space(4.0);

    let lanes = state.lanes();
    if lanes.is_empty() {
        ui.label("Waiting for fleet data...");
    } else {
        widgets::neural_lanes::paint(ui, &lanes);
    }

    ui.add_space(12.0);
    ui.colored_label(theme::GOLD, "FLEET STATUS");
    ui.add_space(4.0);

    let status = state.status();
    egui::Grid::new("status_grid")
        .num_columns(2)
        .spacing([16.0, 4.0])
        .show(ui, |ui| {
            ui.label("Agents:");
            ui.label(format!("{}", status.agent_count.unwrap_or(0)));
            ui.end_row();
            ui.label("Pending:");
            ui.label(format!("{}", status.task_pending.unwrap_or(0)));
            ui.end_row();
            ui.label("Running:");
            ui.label(format!("{}", status.task_running.unwrap_or(0)));
            ui.end_row();
            ui.label("Done:");
            ui.label(format!("{}", status.task_done.unwrap_or(0)));
            ui.end_row();
        });
}
```

- [ ] **Step 4: Verify compiles, fmt, clippy**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(gui): NeuralLanes widget + Command Center tab"
```

---

## Task 7: Fleet Tab (Counter Cards + Agent Grid)

**Files:**
- Create: `biged-rs/crates/biged-gui/src/widgets/counter_card.rs`
- Create: `biged-rs/crates/biged-gui/src/widgets/agent_card.rs`
- Modify: `biged-rs/crates/biged-gui/src/tabs/fleet.rs`

- [ ] **Step 1: Create counter_card.rs**

```rust
// biged-rs/crates/biged-gui/src/widgets/counter_card.rs
use crate::theme;

pub fn show(ui: &mut egui::Ui, label: &str, value: u64, color: egui::Color32) {
    let (rect, _) = ui.allocate_exact_size(egui::vec2(100.0, 60.0), egui::Sense::hover());
    let painter = ui.painter_at(rect);

    painter.rect_filled(rect, theme::CARD_ROUNDING, theme::BG2);

    // Label
    painter.text(
        egui::pos2(rect.center().x, rect.min.y + 16.0),
        egui::Align2::CENTER_CENTER,
        label,
        egui::FontId::new(theme::FONT_XS, egui::FontFamily::Proportional),
        theme::DIM,
    );

    // Value
    painter.text(
        egui::pos2(rect.center().x, rect.min.y + 40.0),
        egui::Align2::CENTER_CENTER,
        format!("{}", value),
        egui::FontId::new(theme::FONT_STAT, egui::FontFamily::Proportional),
        color,
    );
}
```

- [ ] **Step 2: Create agent_card.rs**

```rust
// biged-rs/crates/biged-gui/src/widgets/agent_card.rs
use crate::api::AgentInfo;
use crate::theme;

pub fn show(ui: &mut egui::Ui, agent: &AgentInfo) {
    egui::Frame::none()
        .fill(theme::BG2)
        .rounding(theme::CARD_ROUNDING)
        .inner_margin(8.0)
        .show(ui, |ui| {
            ui.horizontal(|ui| {
                // Status dot
                let color = match agent.status.as_str() {
                    "IDLE" => theme::GREEN,
                    "BUSY" | "RUNNING" => theme::ORANGE,
                    "FAILED" => theme::RED,
                    _ => theme::DIM,
                };
                let (dot_rect, _) =
                    ui.allocate_exact_size(egui::vec2(10.0, 10.0), egui::Sense::hover());
                ui.painter()
                    .circle_filled(dot_rect.center(), 4.0, color);

                ui.vertical(|ui| {
                    ui.label(
                        egui::RichText::new(&agent.name)
                            .color(theme::TEXT)
                            .size(theme::FONT_SM),
                    );
                    ui.label(
                        egui::RichText::new(&agent.role)
                            .color(theme::DIM)
                            .size(theme::FONT_XS),
                    );
                });
            });
        });
}
```

- [ ] **Step 3: Update fleet.rs with counter cards + agent grid**

```rust
// biged-rs/crates/biged-gui/src/tabs/fleet.rs
use crate::state::AppState;
use crate::theme;
use crate::widgets::{agent_card, counter_card};

pub fn show(ui: &mut egui::Ui, state: &AppState) {
    let status = state.status();
    let agents = state.agents();

    // Counter cards row
    ui.colored_label(theme::GOLD, "FLEET OVERVIEW");
    ui.add_space(4.0);
    ui.horizontal(|ui| {
        counter_card::show(ui, "TOTAL", agents.len() as u64, theme::BLUE);
        let idle = agents.iter().filter(|a| a.status == "IDLE").count() as u64;
        let busy = agents.iter().filter(|a| a.status == "BUSY" || a.status == "RUNNING").count() as u64;
        counter_card::show(ui, "IDLE", idle, theme::GREEN);
        counter_card::show(ui, "BUSY", busy, theme::ORANGE);
        counter_card::show(ui, "PENDING", status.task_pending.unwrap_or(0) as u64, theme::YELLOW);
        counter_card::show(ui, "DONE", status.task_done.unwrap_or(0), theme::DIM);
    });

    ui.add_space(12.0);
    ui.colored_label(theme::GOLD, "AGENTS");
    ui.add_space(4.0);

    // Agent grid (scrollable)
    egui::ScrollArea::vertical().show(ui, |ui| {
        egui::Grid::new("agent_grid")
            .num_columns(3)
            .spacing([8.0, 8.0])
            .show(ui, |ui| {
                for (i, agent) in agents.iter().enumerate() {
                    agent_card::show(ui, agent);
                    if (i + 1) % 3 == 0 {
                        ui.end_row();
                    }
                }
            });
    });
}
```

- [ ] **Step 4: Verify compiles, fmt, clippy**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(gui): Fleet tab — counter cards + agent grid with status dots"
```

---

## Task 8: Fleet Comm Tab

**Files:**
- Modify: `biged-rs/crates/biged-gui/src/tabs/fleet_comm.rs`

- [ ] **Step 1: Implement Fleet Comm with chat input + message list**

```rust
// biged-rs/crates/biged-gui/src/tabs/fleet_comm.rs
use crate::state::AppState;
use crate::theme;

pub fn show(ui: &mut egui::Ui, state: &mut AppState) {
    ui.colored_label(theme::GOLD, "FLEET COMM");
    ui.add_space(4.0);

    // Message area (scrollable)
    let available = ui.available_height() - 40.0;
    egui::ScrollArea::vertical()
        .max_height(available)
        .stick_to_bottom(true)
        .show(ui, |ui| {
            ui.label(
                egui::RichText::new("Fleet communication channel")
                    .color(theme::DIM)
                    .size(theme::FONT_SM),
            );
            ui.add_space(8.0);

            if !state.connected() {
                ui.colored_label(theme::RED, "Fleet server not connected.");
            } else {
                ui.colored_label(theme::GREEN, "Connected to fleet server.");
            }
        });

    // Input bar at bottom
    ui.separator();
    ui.horizontal(|ui| {
        let response = ui.add(
            egui::TextEdit::singleline(&mut state.chat_input)
                .hint_text("Type a message...")
                .desired_width(ui.available_width() - 60.0),
        );
        let enter = response.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
        if (ui.button("Send").clicked() || enter) && !state.chat_input.is_empty() {
            // TODO: POST to /api/messages when wired
            state.chat_input.clear();
        }
    });
}
```

- [ ] **Step 2: Verify compiles, fmt, clippy**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(gui): Fleet Comm tab — chat layout with input bar"
```

---

## Task 9: Settings Tab

**Files:**
- Modify: `biged-rs/crates/biged-gui/src/tabs/settings.rs`

- [ ] **Step 1: Implement settings with key panels**

```rust
// biged-rs/crates/biged-gui/src/tabs/settings.rs
use crate::state::{AppState, SettingsSection};
use crate::theme;

pub fn show(ui: &mut egui::Ui, state: &mut AppState) {

    ui.horizontal(|ui| {
        // Section nav (left)
        ui.vertical(|ui| {
            ui.set_min_width(120.0);
            ui.colored_label(theme::GOLD, "SETTINGS");
            ui.add_space(8.0);

            for (section, label) in [
                (SettingsSection::General, "General"),
                (SettingsSection::Hardware, "Hardware"),
                (SettingsSection::Display, "Display"),
                (SettingsSection::Keys, "API Keys"),
            ] {
                let is_active = state.settings_section == section;
                let bg = if is_active { theme::SB_ACTIVE } else { theme::BG2 };
                let text_color = if is_active { theme::TEXT } else { theme::DIM };

                if ui
                    .add(
                        egui::Button::new(
                            egui::RichText::new(label).color(text_color).size(theme::FONT_SM),
                        )
                        .fill(bg)
                        .min_size(egui::vec2(110.0, 28.0)),
                    )
                    .clicked()
                {
                    state.settings_section = section;
                }
            }
        });

        ui.separator();

        // Section content (right)
        egui::ScrollArea::vertical().show(ui, |ui| {
            match state.settings_section {
                SettingsSection::General => {
                    ui.heading("General");
                    ui.label("Theme, fleet behavior, and tab configuration.");
                    ui.add_space(8.0);
                    ui.label(
                        egui::RichText::new("Settings panels will read/write fleet.toml via API.")
                            .color(theme::DIM),
                    );
                }
                SettingsSection::Hardware => {
                    ui.heading("Hardware");
                    ui.label("CPU, RAM, and GPU limits.");
                }
                SettingsSection::Display => {
                    ui.heading("Display");
                    ui.label("Font, UI scale, sidebar preferences.");
                }
                SettingsSection::Keys => {
                    ui.heading("API Keys");
                    ui.label("Manage Anthropic, Google, and other API keys.");
                    ui.add_space(8.0);
                    ui.colored_label(theme::ORANGE, "API key disabled (cost guard active).");
                }
            }
        });
    });
}
```

- [ ] **Step 2: Verify compiles, fmt, clippy**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(gui): Settings tab — section nav with General/Hardware/Display/Keys"
```

---

## Task 10: Wire `gui` Subcommand + Smoke Tests

**Files:**
- Modify: `biged-rs/src/main.rs`
- Create: `biged-rs/tests/gui_test.rs`

- [ ] **Step 1: Add `gui` subcommand to main.rs**

Add to the existing `Commands` enum:

```rust
/// Launch desktop GUI
Gui {
    /// Server URL to connect to
    #[arg(long, default_value = "http://localhost:5555")]
    server_url: String,
},
```

Add to the match arm:

```rust
Commands::Gui { server_url } => {
    biged_gui::run_gui(&server_url).expect("GUI failed");
}
```

- [ ] **Step 2: Create gui_test.rs — theme and API client tests**

```rust
// biged-rs/tests/gui_test.rs

#[test]
fn smoke_theme_colors_valid() {
    // Verify theme constants are non-zero (catch accidental all-black)
    use biged_gui::theme;
    assert_ne!(theme::BG, theme::TEXT, "BG and TEXT must differ");
    assert_ne!(theme::GOLD, theme::BG, "GOLD must not be BG");
    assert_ne!(theme::GREEN, theme::RED, "GREEN and RED must differ");
}

#[test]
fn smoke_api_client_creates() {
    use biged_gui::api::ApiClient;
    let client = ApiClient::new("http://localhost:9999");
    assert!(!*client.connected.lock().unwrap());
}

#[test]
fn smoke_state_defaults() {
    use biged_gui::api::ApiClient;
    use biged_gui::state::{AppState, Tab};
    let client = ApiClient::new("http://localhost:9999");
    let state = AppState::new(client);
    assert_eq!(state.active_tab, Tab::CommandCenter);
    assert!(state.sidebar_open);
    assert!(!state.connected());
}
```

- [ ] **Step 3: Make theme, api, state modules public in lib.rs**

Ensure `lib.rs` exposes:
```rust
pub mod theme;
pub mod api;
pub mod state;
```

- [ ] **Step 4: Run tests**

Run: `cd biged-rs && cargo test --test gui_test`
Expected: 3/3 pass

- [ ] **Step 5: Run full suite**

Run: `cd biged-rs && cargo test`
Expected: all tests pass (including prior suites)

- [ ] **Step 6: cargo fmt + cargo clippy**

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(gui): wire gui subcommand, smoke tests for theme + API + state"
```

---

## Notes

- **PYO3_PYTHON:** Tests linking biged-bridge need `PYO3_PYTHON=C:/Users/max/AppData/Local/Python/bin/python.exe` and `C:\Users\max\AppData\Local\Python\pythoncore-3.14-64` on PATH. Use `cmd /c` wrapper for full test suite.
- **egui version:** Use 0.30 (matches spec). If build issues, try 0.33 (latest on crates.io).
- **Font:** egui bundles a proportional + monospace font. Custom RuneScape font can be added later via `cc.egui_ctx.set_fonts()` — not in scope for this phase.
- **Gate:** Feature parity with customtkinter launcher for core tabs. Module tabs (Accounts, CRM, etc.), Files tab, Graph View, and NeuralLanes animations deferred to post-Phase 4.
- **Headless builds:** Consider feature-gating `biged-gui` behind a `gui` cargo feature on the root crate if CI/edge targets lack GPU/windowing.
