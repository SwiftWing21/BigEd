use crate::backup::BackupManager;
use crate::events::{create_event_bus, EventSender, FleetEvent};
use crate::health::HealthChecker;
use crate::scaler::AutoScaler;
use crate::thermal::ThermalMonitor;
use biged_core::config::FleetConfig;
use biged_core::db::Db;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{error, info, warn};

pub struct Supervisor {
    config: Arc<RwLock<FleetConfig>>,
    db: Db,
    events: EventSender,
    fleet_dir: PathBuf,
}

impl Supervisor {
    pub fn new(
        config: Arc<RwLock<FleetConfig>>,
        db: Db,
        events: EventSender,
        fleet_dir: PathBuf,
    ) -> Self {
        Self {
            config,
            db,
            events,
            fleet_dir,
        }
    }

    pub async fn run(self) -> anyhow::Result<()> {
        info!(
            "Supervisor starting — fleet_dir={}",
            self.fleet_dir.display()
        );

        let health = HealthChecker::new(self.db.clone(), self.events.clone());
        let mut thermal = ThermalMonitor::new(
            self.config.clone(),
            self.events.clone(),
            self.fleet_dir.clone(),
        );

        // Snapshot config for backup + scaler initialization
        let cfg_snapshot = self.config.read().await.clone();

        let backup = BackupManager::new(
            cfg_snapshot.backup.clone(),
            self.fleet_dir.clone(),
            self.events.clone(),
        );

        let mut scaler = AutoScaler::new(
            cfg_snapshot.workers.clone(),
            self.db.clone(),
            self.events.clone(),
        );

        // Launch all subsystem tasks
        tokio::select! {
            r = self.stale_recovery_loop(&health) => {
                error!("Stale recovery loop exited: {:?}", r);
            }
            r = self.agent_health_loop(&health) => {
                error!("Agent health loop exited: {:?}", r);
            }
            r = self.heartbeat_loop() => {
                error!("Heartbeat loop exited: {:?}", r);
            }
            _ = thermal.run() => {
                error!("Thermal monitor exited");
            }
            r = self.config_reload_loop() => {
                error!("Config reload loop exited: {:?}", r);
            }
            _ = backup.run_loop() => {
                error!("Backup manager exited");
            }
            _ = scaler.run_loop() => {
                error!("AutoScaler exited");
            }
        }

        Ok(())
    }

    async fn stale_recovery_loop(&self, health: &HealthChecker) -> anyhow::Result<()> {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(300));
        loop {
            interval.tick().await;
            if let Err(e) = health.recover_stale_tasks(600).await {
                warn!("Stale recovery failed: {}", e);
            }
        }
    }

    async fn agent_health_loop(&self, health: &HealthChecker) -> anyhow::Result<()> {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(30));
        loop {
            interval.tick().await;
            if let Err(e) = health.check_agent_health(300).await {
                warn!("Agent health check failed: {}", e);
            }
        }
    }

    async fn heartbeat_loop(&self) -> anyhow::Result<()> {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(30));
        let heartbeat_path = self.fleet_dir.join(".supervisor_heartbeat");
        loop {
            interval.tick().await;
            let content = serde_json::json!({
                "pid": std::process::id(),
                "ts": chrono::Utc::now().timestamp(),
                "status": "watching",
            });
            let _ = tokio::fs::write(&heartbeat_path, content.to_string()).await;
        }
    }

    async fn config_reload_loop(&self) -> anyhow::Result<()> {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(300));
        let config_path = self.fleet_dir.join("fleet.toml");
        loop {
            interval.tick().await;
            match FleetConfig::from_file(&config_path) {
                Ok(new_config) => {
                    let mut cfg = self.config.write().await;
                    *cfg = new_config;
                    let _ = self.events.send(FleetEvent::ConfigReloaded);
                    info!("Config reloaded from {}", config_path.display());
                }
                Err(e) => {
                    warn!("Config reload failed: {}", e);
                }
            }
        }
    }
}

/// Top-level entry point called from main.rs
pub async fn run() -> anyhow::Result<()> {
    let fleet_dir = std::env::current_dir()?.join("fleet");
    let config_path = fleet_dir.join("fleet.toml");

    let config = if config_path.exists() {
        FleetConfig::from_file(&config_path)?
    } else {
        warn!("fleet.toml not found, using defaults");
        FleetConfig::default()
    };

    let db_path = fleet_dir.join("fleet.db");
    let db = Db::open(&db_path)?;

    let (tx, _rx) = create_event_bus(256);
    let config = Arc::new(RwLock::new(config));

    let supervisor = Supervisor::new(config, db, tx, fleet_dir);
    supervisor.run().await
}
