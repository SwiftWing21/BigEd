use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum TaskStatus {
    Pending,
    Running,
    Done,
    Failed,
    WaitingHuman,
    Review,
    Forwarded,
}

impl TaskStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Pending => "PENDING",
            Self::Running => "RUNNING",
            Self::Done => "DONE",
            Self::Failed => "FAILED",
            Self::WaitingHuman => "WAITING_HUMAN",
            Self::Review => "REVIEW",
            Self::Forwarded => "FORWARDED",
        }
    }
    pub fn from_db(s: &str) -> Self {
        match s {
            "PENDING" => Self::Pending,
            "RUNNING" => Self::Running,
            "DONE" => Self::Done,
            "FAILED" => Self::Failed,
            "WAITING_HUMAN" => Self::WaitingHuman,
            "REVIEW" => Self::Review,
            "FORWARDED" => Self::Forwarded,
            _ => Self::Pending,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    pub id: i64,
    pub created_at: String,
    pub assigned_to: Option<String>,
    pub status: TaskStatus,
    pub priority: i32,
    pub skill: String,
    pub payload_json: Option<String>,
    pub result_json: Option<String>,
    pub error: Option<String>,
    pub parent_id: Option<i64>,
    pub depends_on: Option<String>,
    pub intelligence_score: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Agent {
    pub name: String,
    pub role: String,
    pub status: String,
    pub last_heartbeat: Option<String>,
    pub current_task_id: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HwState {
    pub status: String,
    pub gpu_temp_c: f32,
    pub gpu_vram_used_gb: f64,
    pub gpu_vram_total_gb: f64,
    pub gpu_power_w: f32,
    pub cpu_temp_c: f32,
    pub ram_used_gb: f64,
    pub ram_total_gb: f64,
    pub loaded_models: Vec<String>,
    pub thermal_state: String,
    pub current_model: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub id: i64,
    pub from_agent: String,
    pub to_agent: Option<String>,
    pub channel: String,
    pub body: String,
    pub created_at: String,
    pub read: bool,
}
