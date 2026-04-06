use crate::error::AppError;
use crate::AppState;
use axum::extract::{Path, Query, State};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::PathBuf;

/// Query parameters for paginated endpoints.
#[derive(Deserialize)]
pub struct PageParams {
    page: Option<i64>,
    per_page: Option<i64>,
}

/// GET /api/tasks/queue — paginated pending + running tasks
pub async fn queue(
    State(state): State<AppState>,
    Query(params): Query<PageParams>,
) -> Result<Json<Value>, AppError> {
    let page = params.page.unwrap_or(1).max(1);
    let per_page = params.per_page.unwrap_or(50).clamp(1, 100);
    let (tasks, total) = state.db.task_queue(page, per_page)?;
    let paused = queue_pause_file(&state.fleet_dir).exists();
    Ok(Json(json!({
        "tasks": tasks,
        "page": page,
        "per_page": per_page,
        "total": total,
        "paused": paused,
    })))
}

/// GET /api/tasks/recent — recent tasks, all statuses
pub async fn recent(
    State(state): State<AppState>,
    Query(params): Query<PageParams>,
) -> Result<Json<Value>, AppError> {
    let limit = params.per_page.unwrap_or(50).clamp(1, 200);
    let tasks = state.db.recent_tasks(limit)?;
    Ok(Json(json!({ "tasks": tasks })))
}

/// POST /api/tasks/dispatch — dispatch a task to the fleet queue
pub async fn dispatch(
    State(state): State<AppState>,
    Json(payload): Json<Value>,
) -> Result<Json<Value>, AppError> {
    let skill = payload
        .get("skill")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if skill.is_empty() || skill.len() > 64 {
        return Err(AppError(anyhow::anyhow!("skill is required (max 64 chars)")));
    }
    // Validate skill name format: alphanumeric + underscores
    if !skill.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return Err(AppError(anyhow::anyhow!("Invalid skill name format")));
    }

    let task_payload = payload
        .get("payload")
        .map(|v| v.to_string())
        .unwrap_or_else(|| "{}".into());
    let priority = payload
        .get("priority")
        .and_then(|v| v.as_i64())
        .unwrap_or(5)
        .clamp(1, 10) as i32;
    let assigned_to = payload
        .get("assigned_to")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty());

    let id = state
        .db
        .post_task(skill, &task_payload, priority, assigned_to)?;
    Ok(Json(json!({
        "status": "ok",
        "task_id": id,
        "skill": skill,
        "priority": priority,
        "assigned_to": assigned_to,
    })))
}

/// DELETE /api/tasks/{id} — cancel a pending task
pub async fn cancel(
    State(state): State<AppState>,
    Path(id): Path<i64>,
) -> Result<Json<Value>, AppError> {
    let cancelled = state.db.cancel_task(id)?;
    if cancelled {
        Ok(Json(json!({ "ok": true, "task_id": id, "status": "FAILED" })))
    } else {
        Err(AppError(anyhow::anyhow!(
            "Task {} not found or not PENDING",
            id
        )))
    }
}

/// PUT /api/tasks/{id}/priority — change priority of a pending task
pub async fn set_priority(
    State(state): State<AppState>,
    Path(id): Path<i64>,
    Json(payload): Json<Value>,
) -> Result<Json<Value>, AppError> {
    let priority = payload
        .get("priority")
        .and_then(|v| v.as_i64())
        .ok_or_else(|| AppError(anyhow::anyhow!("priority is required")))?;
    let priority = priority.clamp(1, 10) as i32;
    let updated = state.db.update_task_priority(id, priority)?;
    if updated {
        Ok(Json(
            json!({ "ok": true, "task_id": id, "priority": priority }),
        ))
    } else {
        Err(AppError(anyhow::anyhow!(
            "Task {} not found or not PENDING",
            id
        )))
    }
}

/// POST /api/tasks/{id}/requeue — requeue a failed task
pub async fn requeue(
    State(state): State<AppState>,
    Path(id): Path<i64>,
) -> Result<Json<Value>, AppError> {
    let requeued = state.db.requeue_task(id)?;
    if requeued {
        Ok(Json(
            json!({ "ok": true, "task_id": id, "status": "PENDING" }),
        ))
    } else {
        Err(AppError(anyhow::anyhow!(
            "Task {} not found or not FAILED",
            id
        )))
    }
}

/// GET /api/queue/status — queue pause state
pub async fn queue_status(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let paused = queue_pause_file(&state.fleet_dir).exists();
    Ok(Json(json!({ "paused": paused })))
}

/// POST /api/queue/pause — pause queue processing
pub async fn queue_pause(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let path = queue_pause_file(&state.fleet_dir);
    tokio::fs::write(&path, "paused")
        .await
        .map_err(|e| AppError(anyhow::anyhow!("Failed to create pause file: {}", e)))?;
    Ok(Json(json!({ "status": "ok", "paused": true })))
}

/// POST /api/queue/resume — resume queue processing
pub async fn queue_resume(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let path = queue_pause_file(&state.fleet_dir);
    let _ = tokio::fs::remove_file(&path).await; // ignore if missing
    Ok(Json(json!({ "status": "ok", "paused": false })))
}

/// GET /api/activity/live — running + recently completed tasks
pub async fn activity_live(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let tasks = state.db.live_activity(10)?;
    let items: Vec<Value> = tasks
        .iter()
        .map(|t| {
            json!({
                "id": t.id,
                "type": t.skill,
                "status": t.status,
                "agent": t.assigned_to.as_deref().unwrap_or("unassigned"),
                "created_at": t.created_at,
                "error": t.error.as_deref().unwrap_or(""),
                "priority": t.priority,
            })
        })
        .collect();
    Ok(Json(json!({ "items": items })))
}

/// GET /api/version — binary version + build info
pub async fn version() -> Json<Value> {
    Json(json!({
        "version": env!("CARGO_PKG_VERSION"),
        "track": "rust",
        "build": {
            "profile": if cfg!(debug_assertions) { "debug" } else { "release" },
        },
    }))
}

fn queue_pause_file(fleet_dir: &std::path::Path) -> PathBuf {
    fleet_dir.join(".queue_paused")
}
