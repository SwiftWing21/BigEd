use crate::api::{ActivityLane, AgentInfo, ApiClient, FleetStatus, ThermalInfo};

/// Tab identifiers — 5-section operator GUI (Phase C).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tab {
    Overview,
    Fleet,
    Tasks,
    Config,
    Logs,
}

/// Config sub-sections.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConfigSection {
    General,
    Hardware,
    Backup,
}

/// Log source selector.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogSource {
    Supervisor,
    Worker,
    Fleet,
}

/// Log level filter.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogLevel {
    All,
    Warn,
    Error,
}

/// Application state — drives all UI rendering.
pub struct AppState {
    pub active_tab: Tab,
    pub sidebar_open: bool,
    pub config_section: ConfigSection,
    pub api: ApiClient,

    // Tasks tab state
    pub dispatch_skill: String,
    pub dispatch_priority: i32,
    pub queue_paused: bool,

    // Logs tab state
    pub log_source: LogSource,
    pub log_level: LogLevel,
    pub log_lines: Vec<String>,

    // Fleet tab state
    pub launcher_available: bool,
}

impl AppState {
    pub fn new(api: ApiClient) -> Self {
        Self {
            active_tab: Tab::Overview,
            sidebar_open: true,
            config_section: ConfigSection::General,
            api,
            dispatch_skill: String::new(),
            dispatch_priority: 5,
            queue_paused: false,
            log_source: LogSource::Supervisor,
            log_level: LogLevel::All,
            log_lines: Vec::new(),
            launcher_available: false,
        }
    }

    pub fn status(&self) -> FleetStatus {
        self.api.status.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn agents(&self) -> Vec<AgentInfo> {
        self.api.agents.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn lanes(&self) -> Vec<ActivityLane> {
        self.api.lanes.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn thermal(&self) -> ThermalInfo {
        self.api.thermal.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn connected(&self) -> bool {
        *self.api.connected.lock().unwrap_or_else(|e| e.into_inner())
    }
}
