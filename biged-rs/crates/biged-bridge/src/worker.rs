use crate::runner::SkillRunner;
use crate::BridgeConfig;
use biged_core::db::Db;
use std::sync::Arc;
use std::time::Duration;
use tracing::{error, info, warn};

/// Worker that claims tasks from the DB and dispatches to Python skills.
pub struct Worker {
    db: Db,
    runner: Arc<SkillRunner>,
    config: BridgeConfig,
    fleet_config_json: serde_json::Value,
}

impl Worker {
    pub fn new(
        db: Db,
        config: BridgeConfig,
        fleet_config_json: serde_json::Value,
    ) -> anyhow::Result<Self> {
        let runner = Arc::new(SkillRunner::new(&config.fleet_dir)?);
        Ok(Self {
            db,
            runner,
            config,
            fleet_config_json,
        })
    }

    /// Try to claim and process one task. Returns Ok(true) if a task was processed,
    /// Ok(false) if the queue was empty.
    pub async fn process_one(&self, role: &str) -> anyhow::Result<bool> {
        let task = match self.db.claim_task(role)? {
            Some(t) => t,
            None => return Ok(false),
        };

        let skill = task.skill.clone();
        let task_id = task.id;
        let timeout = self.config.timeout_for(&skill);

        info!("Processing task {} (skill: {})", task_id, skill);

        let payload: serde_json::Value = task
            .payload_json
            .as_deref()
            .and_then(|s| serde_json::from_str(s).ok())
            .unwrap_or(serde_json::json!({}));

        let result = self.execute_with_timeout(&skill, &payload, timeout).await;

        match result {
            Ok(value) => {
                let result_str = serde_json::to_string(&value)?;
                self.db.complete_task(task_id, &result_str)?;
                info!("Task {} completed (skill: {})", task_id, skill);
            }
            Err(e) => {
                let error_msg = format!("{}", e);
                self.db.fail_task(task_id, &error_msg)?;
                warn!("Task {} failed (skill: {}): {}", task_id, skill, error_msg);
            }
        }

        Ok(true)
    }

    /// Execute a skill with a timeout. Uses spawn_blocking to avoid
    /// blocking the tokio runtime while Python holds the GIL.
    async fn execute_with_timeout(
        &self,
        skill: &str,
        payload: &serde_json::Value,
        timeout: Duration,
    ) -> anyhow::Result<serde_json::Value> {
        let skill_name = skill.to_string();
        let payload = payload.clone();
        let config = self.fleet_config_json.clone();
        let runner = Arc::clone(&self.runner);

        let result = tokio::time::timeout(
            timeout,
            tokio::task::spawn_blocking(move || runner.run_skill(&skill_name, &payload, &config)),
        )
        .await;

        match result {
            Ok(Ok(inner)) => inner,
            Ok(Err(join_err)) => Err(anyhow::anyhow!("Skill task panicked: {}", join_err)),
            Err(_) => {
                error!("Skill '{}' timed out after {:?}", skill, timeout);
                Err(anyhow::anyhow!(
                    "Skill timed out after {} seconds",
                    timeout.as_secs()
                ))
            }
        }
    }

    /// Run the worker loop continuously, polling for tasks.
    pub async fn run_loop(&self, role: &str) -> anyhow::Result<()> {
        info!("Worker loop started (role: {})", role);
        loop {
            match self.process_one(role).await {
                Ok(true) => continue,
                Ok(false) => tokio::time::sleep(Duration::from_secs(2)).await,
                Err(e) => {
                    error!("Worker error: {}", e);
                    tokio::time::sleep(Duration::from_secs(5)).await;
                }
            }
        }
    }
}
