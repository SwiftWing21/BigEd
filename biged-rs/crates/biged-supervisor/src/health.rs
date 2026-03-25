use crate::events::{EventSender, FleetEvent};
use biged_core::db::Db;
use tracing::{info, warn};

pub struct HealthChecker {
    db: Db,
    events: EventSender,
}

impl HealthChecker {
    pub fn new(db: Db, events: EventSender) -> Self {
        Self { db, events }
    }

    /// Recover tasks stuck in RUNNING state for too long.
    pub async fn recover_stale_tasks(&self, timeout_secs: i64) -> anyhow::Result<u64> {
        let recovered = self.db.recover_stale_tasks(timeout_secs)?;
        if recovered > 0 {
            info!(
                "Recovered {} stale tasks (timeout={}s)",
                recovered, timeout_secs
            );
        }
        Ok(recovered)
    }

    /// Check agent heartbeats — mark agents with stale heartbeats as offline.
    pub async fn check_agent_health(&self, stale_secs: i64) -> anyhow::Result<()> {
        let pool = self.db.pool_ref();
        let conn = pool.get()?;
        let stale_agents: Vec<String> = {
            let mut stmt = conn.prepare(
                "SELECT name FROM agents
                 WHERE last_heartbeat < datetime('now', ?1 || ' seconds')
                   AND status != 'OFFLINE'",
            )?;
            let rows = stmt.query_map(rusqlite::params![format!("-{}", stale_secs)], |row| {
                row.get(0)
            })?;
            rows.collect::<std::result::Result<Vec<String>, _>>()?
        };

        for agent in &stale_agents {
            conn.execute(
                "UPDATE agents SET status = 'OFFLINE' WHERE name = ?1",
                rusqlite::params![agent],
            )?;
            warn!(
                "Agent {} marked offline (no heartbeat for {}s)",
                agent, stale_secs
            );
            if self
                .events
                .send(FleetEvent::AgentStateChange {
                    agent: agent.clone(),
                    from: "IDLE".into(),
                    to: "OFFLINE".into(),
                })
                .is_err()
            {
                tracing::debug!("Event bus: no subscribers for AgentStateChange");
            }
        }
        Ok(())
    }
}
