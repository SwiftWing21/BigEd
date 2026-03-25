use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum FleetEvent {
    TaskCompleted {
        id: i64,
        skill: String,
        agent: String,
    },
    TaskFailed {
        id: i64,
        skill: String,
        error: String,
    },
    AgentStateChange {
        agent: String,
        from: String,
        to: String,
    },
    ThermalAlert {
        gpu_temp: f32,
        action: ThermalAction,
    },
    ModelTransition {
        from: String,
        to: String,
        reason: String,
    },
    ConfigReloaded,
    ScaleUp {
        count: u32,
        reason: String,
    },
    ScaleDown {
        count: u32,
    },
    WorkerCrashed {
        agent: String,
        exit_code: Option<i32>,
    },
    BackupCompleted {
        path: String,
        size_bytes: u64,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ThermalAction {
    ThrottleDown,
    CooldownStart,
    CooldownEnd,
    EmergencyStop,
}

impl FleetEvent {
    pub fn event_type(&self) -> &'static str {
        match self {
            Self::TaskCompleted { .. } => "task_completed",
            Self::TaskFailed { .. } => "task_failed",
            Self::AgentStateChange { .. } => "agent_state",
            Self::ThermalAlert { .. } => "thermal",
            Self::ModelTransition { .. } => "model_transition",
            Self::ConfigReloaded => "config_reloaded",
            Self::ScaleUp { .. } => "scale_up",
            Self::ScaleDown { .. } => "scale_down",
            Self::WorkerCrashed { .. } => "worker_crashed",
            Self::BackupCompleted { .. } => "backup_completed",
        }
    }
}

/// Event bus using tokio broadcast channel.
pub type EventSender = tokio::sync::broadcast::Sender<FleetEvent>;
pub fn create_event_bus(capacity: usize) -> (EventSender, tokio::sync::broadcast::Receiver<FleetEvent>) {
    tokio::sync::broadcast::channel(capacity)
}
