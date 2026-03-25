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
