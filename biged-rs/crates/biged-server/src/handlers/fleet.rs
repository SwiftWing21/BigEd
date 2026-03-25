use crate::error::AppError;
use crate::AppState;
use axum::extract::State;
use axum::Json;
use serde_json::{json, Value};

/// GET /api/status — core fleet status
pub async fn status(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let agents = state.db.all_agents()?;
    let tasks = state.db.task_counts_by_status()?;
    Ok(Json(json!({
        "agents": agents,
        "tasks": tasks,
    })))
}

/// GET /api/health — unified health check
pub async fn health(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let config = state.config.read().await;
    let db_ok = state.db.table_names().is_ok();
    Ok(Json(json!({
        "status": if db_ok { "healthy" } else { "unhealthy" },
        "subsystems": {
            "fleet_db": db_ok,
            "supervisor": true,
            "dashboard": true,
        },
        "version": env!("CARGO_PKG_VERSION"),
        "dashboard_port": config.dashboard.port,
    })))
}

/// GET /api/thermal — live GPU/CPU/thermal metrics
pub async fn thermal(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let hw_path = state.fleet_dir.join("hw_state.json");
    let hw: Value = match tokio::fs::read_to_string(&hw_path).await {
        Ok(data) => serde_json::from_str(&data).unwrap_or(json!({})),
        Err(_) => json!({}),
    };
    let config = state.config.read().await;
    Ok(Json(json!({
        "gpu_temp_c": hw.get("gpu_temp_c").and_then(|v| v.as_f64()).unwrap_or(0.0),
        "gpu_vram_used_gb": hw.get("gpu_vram_used_gb").and_then(|v| v.as_f64()).unwrap_or(0.0),
        "gpu_vram_total_gb": hw.get("gpu_vram_total_gb").and_then(|v| v.as_f64()).unwrap_or(0.0),
        "gpu_power_w": hw.get("gpu_power_w").and_then(|v| v.as_f64()).unwrap_or(0.0),
        "cpu_temp_c": hw.get("cpu_temp_c").and_then(|v| v.as_f64()).unwrap_or(0.0),
        "ram_used_gb": hw.get("ram_used_gb").and_then(|v| v.as_f64()).unwrap_or(0.0),
        "ram_total_gb": hw.get("ram_total_gb").and_then(|v| v.as_f64()).unwrap_or(0.0),
        "thermal_state": hw.get("thermal_state").and_then(|v| v.as_str()).unwrap_or("unknown"),
        "current_model": hw.get("current_model").and_then(|v| v.as_str()).unwrap_or(""),
        "loaded_models": hw.get("loaded_models").unwrap_or(&json!([])),
        "thresholds": {
            "gpu_sustained": config.thermal.gpu_max_sustained_c,
            "gpu_burst": config.thermal.gpu_max_burst_c,
            "cpu_sustained": config.thermal.cpu_max_sustained_c,
            "cooldown_target": config.thermal.cooldown_target_c,
        },
    })))
}

/// GET /api/dashboard/batch — combined status + thermal + training
pub async fn dashboard_batch(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let agents = state.db.all_agents()?;
    let tasks = state.db.task_counts_by_status()?;
    let hw_path = state.fleet_dir.join("hw_state.json");
    let hw: Value = match tokio::fs::read_to_string(&hw_path).await {
        Ok(data) => serde_json::from_str(&data).unwrap_or(json!({})),
        Err(_) => json!({}),
    };
    Ok(Json(json!({
        "status": { "agents": agents, "tasks": tasks },
        "thermal": hw,
        "training": { "locked": false },
    })))
}

/// GET /api/alerts — recent alerts
pub async fn alerts(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let pool = state.db.pool_ref();
    let conn = pool.get()?;
    let has_alerts = conn
        .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'")
        .and_then(|mut s| s.query_row([], |_| Ok(true)))
        .unwrap_or(false);

    if has_alerts {
        let mut stmt = conn.prepare(
            "SELECT id, severity, source, message, created_at
             FROM alerts ORDER BY id DESC LIMIT 50",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(json!({
                "id": row.get::<_, i64>(0)?,
                "severity": row.get::<_, String>(1)?,
                "source": row.get::<_, String>(2)?,
                "message": row.get::<_, String>(3)?,
                "created_at": row.get::<_, String>(4)?,
            }))
        })?;
        let alerts: Vec<Value> = rows
            .filter_map(|r| match r {
                Ok(v) => Some(v),
                Err(e) => {
                    tracing::warn!("Alert row error: {}", e);
                    None
                }
            })
            .collect();
        Ok(Json(json!({ "persistent": alerts, "memory": [] })))
    } else {
        Ok(Json(json!({ "persistent": [], "memory": [] })))
    }
}

/// GET /api/tasks — recent task listing
pub async fn tasks(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let tasks = state.db.recent_tasks(100)?;
    Ok(Json(json!(tasks)))
}

/// POST /api/tasks — dispatch a new task
pub async fn post_task(
    State(state): State<AppState>,
    Json(payload): Json<Value>,
) -> Result<Json<Value>, AppError> {
    let skill = payload
        .get("skill")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
    let body = payload
        .get("payload")
        .map(|v| v.to_string())
        .unwrap_or_else(|| "{}".into());
    let priority = payload
        .get("priority")
        .and_then(|v| v.as_i64())
        .unwrap_or(5) as i32;
    let id = state.db.post_task(skill, &body, priority, None)?;
    Ok(Json(json!({ "id": id, "status": "PENDING" })))
}

/// GET /api/agents — raw agent listing
pub async fn agents(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let agents = state.db.all_agents()?;
    Ok(Json(json!(agents)))
}
