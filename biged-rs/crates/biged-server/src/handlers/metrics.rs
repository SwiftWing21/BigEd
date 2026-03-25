use crate::error::AppError;
use crate::AppState;
use axum::extract::State;
use axum::Json;
use serde_json::{json, Value};
use std::time::SystemTime;

/// GET /api/metrics — basic fleet metrics
pub async fn metrics(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    // Uptime: seconds since UNIX epoch as a proxy (real boot time would need
    // state tracking; the caller can diff against the server's start time
    // sent in /api/health).
    let uptime_secs = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    // Task counts from the database
    let counts = state.db.task_counts_by_status()?;
    let done = *counts.get("DONE").unwrap_or(&0);
    let failed = *counts.get("FAILED").unwrap_or(&0);
    let pending = *counts.get("PENDING").unwrap_or(&0);
    let running = *counts.get("RUNNING").unwrap_or(&0);

    let total_tasks_processed = done + failed;
    let active_agents = state
        .db
        .all_agents()?
        .iter()
        .filter(|a| a.status == "busy")
        .count();
    let queue_depth = pending + running;

    Ok(Json(json!({
        "uptime_secs": uptime_secs,
        "total_tasks_processed": total_tasks_processed,
        "active_agents": active_agents,
        "queue_depth": queue_depth,
        "task_breakdown": {
            "done": done,
            "failed": failed,
            "pending": pending,
            "running": running,
        },
    })))
}
