use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::error::{CoreError, Result};

// ── Top-level config ────────────────────────────────────────────────────────

/// Root configuration parsed from `fleet.toml`.
///
/// Every section uses `#[serde(default)]` so the parser tolerates missing keys,
/// and `#[serde(flatten)] extra` captures unknown sections without error.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct FleetConfig {
    pub fleet: FleetSection,
    pub models: ModelsSection,
    pub dashboard: DashboardSection,
    pub workers: WorkersSection,
    pub thermal: ThermalSection,
    pub budgets: BudgetsSection,
    pub backup: BackupSection,

    /// Catch-all for TOML sections we don't model (e.g. `[naming]`, `[ditl]`).
    #[serde(flatten)]
    pub extra: toml::Table,
}

impl Default for FleetConfig {
    fn default() -> Self {
        Self {
            fleet: FleetSection::default(),
            models: ModelsSection::default(),
            dashboard: DashboardSection::default(),
            workers: WorkersSection::default(),
            thermal: ThermalSection::default(),
            budgets: BudgetsSection::default(),
            backup: BackupSection::default(),
            extra: toml::Table::new(),
        }
    }
}

impl FleetConfig {
    /// Parse from a TOML file on disk.
    pub fn from_file(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Err(CoreError::ConfigNotFound(path.to_path_buf()));
        }
        let contents = std::fs::read_to_string(path)?;
        Self::parse(&contents)
    }

    /// Parse from a TOML string.
    pub fn parse(s: &str) -> Result<Self> {
        toml::from_str(s).map_err(|e| CoreError::Config(e.to_string()))
    }
}

// ── [fleet] ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct FleetSection {
    pub offline_mode: bool,
    pub air_gap_mode: bool,
    pub eco_mode: bool,
    pub idle_enabled: bool,
    pub idle_timeout_secs: u64,
    pub training_check_interval_secs: u64,
    pub max_workers: u32,
    pub worker_scale_interval_secs: u64,
    pub openclaw_enabled: bool,
    pub discord_bot_enabled: bool,
    pub api_keys_required: bool,
    pub disabled_agents: Vec<String>,
    pub hitl_evolution: bool,

    #[serde(flatten)]
    pub extra: toml::Table,
}

impl Default for FleetSection {
    fn default() -> Self {
        Self {
            offline_mode: false,
            air_gap_mode: false,
            eco_mode: false,
            idle_enabled: true,
            idle_timeout_secs: 10,
            training_check_interval_secs: 15,
            max_workers: 10,
            worker_scale_interval_secs: 900,
            openclaw_enabled: false,
            discord_bot_enabled: true,
            api_keys_required: false,
            disabled_agents: vec![
                "sales".into(),
                "onboarding".into(),
                "implementation".into(),
                "legal".into(),
                "account_manager".into(),
                "ds_rag".into(),
                "ds_fleet".into(),
                "ds_research".into(),
            ],
            hitl_evolution: false,
            extra: toml::Table::new(),
        }
    }
}

// ── [models] ────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ModelsSection {
    pub local: String,
    pub complex: String,
    pub complex_provider: String,
    pub conductor_model: String,
    pub vision_model: String,
    pub minimax_model: String,
    pub ollama_host: String,
    pub keep_alive_mins: u32,

    #[serde(flatten)]
    pub extra: toml::Table,
}

impl Default for ModelsSection {
    fn default() -> Self {
        Self {
            local: "qwen3:8b".into(),
            complex: "qwen3:8b".into(),
            complex_provider: "local".into(),
            conductor_model: "qwen3:4b".into(),
            vision_model: "llava".into(),
            minimax_model: "MiniMax-M1-80k".into(),
            ollama_host: "http://localhost:11434".into(),
            keep_alive_mins: 30,
            extra: toml::Table::new(),
        }
    }
}

// ── [dashboard] ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct DashboardSection {
    pub enabled: bool,
    pub port: u16,
    pub auto_open: bool,
    pub bind_address: String,
    pub cors_origins: Vec<String>,
    pub theme: String,

    #[serde(flatten)]
    pub extra: toml::Table,
}

impl Default for DashboardSection {
    fn default() -> Self {
        Self {
            enabled: true,
            port: 5555,
            auto_open: true,
            bind_address: "127.0.0.1".into(),
            cors_origins: Vec::new(),
            theme: "figma".into(),
            extra: toml::Table::new(),
        }
    }
}

// ── [workers] ────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct WorkersSection {
    pub max_workers: u32,
    pub nice_level: u32,
    pub cpu_limit_percent: u32,
    pub coder_count: u32,
    pub memory_limit_mb: u32,

    #[serde(flatten)]
    pub extra: toml::Table,
}

impl Default for WorkersSection {
    fn default() -> Self {
        Self {
            max_workers: 6,
            nice_level: 15,
            cpu_limit_percent: 10,
            coder_count: 3,
            memory_limit_mb: 384,
            extra: toml::Table::new(),
        }
    }
}

// ── [thermal] ────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ThermalSection {
    pub gpu_target_c: u32,
    pub gpu_max_sustained_c: u32,
    pub gpu_max_burst_c: u32,
    pub cpu_max_sustained_c: u32,
    pub cooldown_target_c: u32,
    pub cooldown_window_secs: u64,
    pub poll_interval_secs: u64,
    pub grace_period_secs: u64,
    pub cooldown_after_swap_secs: u64,
    pub ambient_estimation: bool,
    pub cpu_temp_enabled: bool,

    #[serde(flatten)]
    pub extra: toml::Table,
}

impl Default for ThermalSection {
    fn default() -> Self {
        Self {
            gpu_target_c: 75,
            gpu_max_sustained_c: 82,
            gpu_max_burst_c: 85,
            cpu_max_sustained_c: 85,
            cooldown_target_c: 75,
            cooldown_window_secs: 120,
            poll_interval_secs: 5,
            grace_period_secs: 20,
            cooldown_after_swap_secs: 60,
            ambient_estimation: true,
            cpu_temp_enabled: true,
            extra: toml::Table::new(),
        }
    }
}

// ── [budgets] ────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct BudgetsSection {
    pub period: String,
    pub enforcement: String,

    /// Per-skill budgets are captured here since the keys are dynamic.
    #[serde(flatten)]
    pub extra: toml::Table,
}

impl Default for BudgetsSection {
    fn default() -> Self {
        Self {
            period: "day".into(),
            enforcement: "warn".into(),
            extra: toml::Table::new(),
        }
    }
}

// ── [backup] ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct BackupSection {
    pub enabled: bool,
    pub interval_secs: u64,
    pub location: String,
    pub depth: u32,
    pub prune_enabled: bool,
    pub warn_disk_usage_pct: u32,

    #[serde(flatten)]
    pub extra: toml::Table,
}

impl Default for BackupSection {
    fn default() -> Self {
        Self {
            enabled: true,
            interval_secs: 1200,
            location: "~/BigEd-backups".into(),
            depth: 10,
            prune_enabled: true,
            warn_disk_usage_pct: 80,
            extra: toml::Table::new(),
        }
    }
}
