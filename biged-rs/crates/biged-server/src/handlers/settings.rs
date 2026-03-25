use crate::error::AppError;
use crate::AppState;
use axum::extract::{Path, State};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

/// GET /api/settings/theme
pub async fn get_theme(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let config = state.config.read().await;
    Ok(Json(json!({ "theme": config.dashboard.theme })))
}

#[derive(Deserialize)]
pub struct ThemePayload {
    theme: String,
}

/// POST /api/settings/theme
/// NOTE: Updates in-memory config only. Persistence to fleet.toml is Phase 3 scope.
pub async fn set_theme(
    State(state): State<AppState>,
    Json(payload): Json<ThemePayload>,
) -> Result<Json<Value>, AppError> {
    let mut config = state.config.write().await;
    config.dashboard.theme = payload.theme.clone();
    Ok(Json(json!({ "ok": true, "theme": payload.theme })))
}

/// POST /api/fleet/worker/:name/disable
pub async fn disable_worker(
    State(state): State<AppState>,
    Path(name): Path<String>,
) -> Result<Json<Value>, AppError> {
    let mut config = state.config.write().await;
    if !config.fleet.disabled_agents.contains(&name) {
        config.fleet.disabled_agents.push(name.clone());
    }
    Ok(Json(json!({
        "status": "disabled",
        "agent": name,
        "disabled_agents": config.fleet.disabled_agents,
    })))
}

/// POST /api/fleet/worker/:name/enable
pub async fn enable_worker(
    State(state): State<AppState>,
    Path(name): Path<String>,
) -> Result<Json<Value>, AppError> {
    let mut config = state.config.write().await;
    config.fleet.disabled_agents.retain(|a| a != &name);
    Ok(Json(json!({
        "status": "enabled",
        "agent": name,
        "disabled_agents": config.fleet.disabled_agents,
    })))
}
