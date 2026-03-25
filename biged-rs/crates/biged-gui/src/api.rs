use anyhow::Result;
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

/// Fleet status snapshot from /api/status.
/// Matches server response: `{ "agents": [...], "tasks": {"PENDING": N, ...} }`
#[derive(Debug, Clone, Default, Deserialize)]
pub struct FleetStatus {
    #[serde(default)]
    pub agents: Vec<AgentInfo>,
    #[serde(default)]
    pub tasks: HashMap<String, i64>,
}

impl FleetStatus {
    /// Number of agents reported in this status snapshot.
    pub fn agent_count(&self) -> usize {
        self.agents.len()
    }

    /// Get a task count by status key (e.g. "PENDING", "DONE", "FAILED", "RUNNING").
    pub fn task_count(&self, key: &str) -> i64 {
        self.tasks.get(key).copied().unwrap_or(0)
    }
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
    #[serde(alias = "day")]
    pub name: String,
    #[serde(alias = "DONE", default)]
    pub done: u32,
    #[serde(alias = "FAILED", default)]
    pub failed: u32,
    #[serde(alias = "RUNNING", default)]
    pub running: u32,
}

/// Thermal info from /api/thermal.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct ThermalInfo {
    #[serde(alias = "gpu_temp_c")]
    pub gpu_temp: Option<f32>,
    #[serde(alias = "cpu_temp_c")]
    pub cpu_temp: Option<f32>,
    #[serde(alias = "thermal_state")]
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
    /// Handle to the tokio runtime for spawning fire-and-forget requests
    /// from non-async contexts (e.g. the GUI thread).
    rt_handle: Option<tokio::runtime::Handle>,
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
            thermal: Arc::new(Mutex::new(ThermalInfo::default())),
            connected: Arc::new(Mutex::new(false)),
            rt_handle: None,
        }
    }

    /// Attach a tokio runtime handle so fire-and-forget methods can spawn tasks.
    pub fn set_runtime(&mut self, handle: tokio::runtime::Handle) {
        self.rt_handle = Some(handle);
    }

    /// Fire-and-forget message post via POST /api/tasks.
    /// Errors are logged, not propagated.
    pub fn post_message_bg(&self, message: String) {
        let Some(handle) = &self.rt_handle else {
            tracing::warn!("Cannot post message: no tokio runtime handle");
            return;
        };
        let http = self.http.clone();
        let url = format!("{}/api/tasks", self.base_url);
        handle.spawn(async move {
            let body = serde_json::json!({
                "skill": "fleet_comm",
                "payload": serde_json::json!({ "message": message }).to_string(),
            });
            if let Err(e) = http.post(&url).json(&body).send().await {
                tracing::warn!("Failed to post message: {e}");
            }
        });
    }

    /// Fetch all endpoints and update cached state.
    pub async fn refresh(&self) {
        let connected = self.fetch_status().await.is_ok();
        *self.connected.lock().unwrap_or_else(|e| e.into_inner()) = connected;
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
        *self.status.lock().unwrap_or_else(|e| e.into_inner()) = resp;
        Ok(())
    }

    async fn fetch_agents(&self) -> Result<()> {
        let url = format!("{}/api/agents", self.base_url);
        let resp: Vec<AgentInfo> = self.http.get(&url).send().await?.json().await?;
        *self.agents.lock().unwrap_or_else(|e| e.into_inner()) = resp;
        Ok(())
    }

    async fn fetch_lanes(&self) -> Result<()> {
        let url = format!("{}/api/activity", self.base_url);
        let resp: Vec<ActivityLane> = self.http.get(&url).send().await?.json().await?;
        *self.lanes.lock().unwrap_or_else(|e| e.into_inner()) = resp;
        Ok(())
    }

    async fn fetch_thermal(&self) -> Result<()> {
        let url = format!("{}/api/thermal", self.base_url);
        let resp: ThermalInfo = self.http.get(&url).send().await?.json().await?;
        *self.thermal.lock().unwrap_or_else(|e| e.into_inner()) = resp;
        Ok(())
    }
}
