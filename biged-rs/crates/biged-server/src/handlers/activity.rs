use crate::error::AppError;
use crate::AppState;
use axum::extract::State;
use axum::Json;
use serde_json::{json, Value};
use std::collections::HashMap;

/// GET /api/activity — 30-day activity histogram
pub async fn activity(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let data = state.db.activity_by_day(30)?;
    let result: Vec<Value> = data
        .into_iter()
        .map(|(day, counts)| {
            json!({
                "day": day,
                "DONE": counts.get("DONE").unwrap_or(&0),
                "FAILED": counts.get("FAILED").unwrap_or(&0),
                "PENDING": counts.get("PENDING").unwrap_or(&0),
                "RUNNING": counts.get("RUNNING").unwrap_or(&0),
            })
        })
        .collect();
    Ok(Json(json!(result)))
}

/// GET /api/agent-cards — agents grouped by role
pub async fn agent_cards(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let agents = state.db.all_agents()?;
    let mut by_role: HashMap<String, Vec<Value>> = HashMap::new();
    for agent in agents {
        by_role
            .entry(agent.role.clone())
            .or_default()
            .push(json!({
                "name": agent.name,
                "status": agent.status,
                "current_task_id": agent.current_task_id,
                "last_heartbeat": agent.last_heartbeat,
            }));
    }
    let cards: Vec<Value> = by_role
        .into_iter()
        .map(|(role, agents)| json!({ "role": role, "agents": agents }))
        .collect();
    Ok(Json(json!({ "cards": cards })))
}

/// GET /api/agents/performance — per-agent metrics
pub async fn agents_performance(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let agents = state.db.all_agents()?;
    let mut perf: Vec<Value> = Vec::new();
    let pool = state.db.pool_ref();
    let conn = pool.get()?;
    for agent in agents {
        let completed: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM tasks
                 WHERE assigned_to = ?1 AND status = 'DONE'
                   AND created_at >= datetime('now', '-1 hour')",
                rusqlite::params![agent.name],
                |row| row.get(0),
            )
            .unwrap_or(0);
        perf.push(json!({
            "name": agent.name,
            "tasks_completed_1h": completed,
            "status": agent.status,
        }));
    }
    Ok(Json(json!({ "agents": perf })))
}
