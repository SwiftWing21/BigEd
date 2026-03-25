use crate::error::AppError;
use crate::AppState;
use axum::extract::{Path, State};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

/// GET /api/settings — return full FleetConfig as JSON
pub async fn get_settings(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let config = state.config.read().await;
    let val = serde_json::to_value(&*config)
        .map_err(|e| AppError(anyhow::anyhow!("serialize config: {}", e)))?;
    Ok(Json(val))
}

/// PUT /api/settings — partial config merge + persist to fleet.toml
pub async fn put_settings(
    State(state): State<AppState>,
    Json(patch): Json<Value>,
) -> Result<Json<Value>, AppError> {
    let mut config = state.config.write().await;

    // Merge patch into current config via JSON round-trip
    let mut current = serde_json::to_value(&*config)
        .map_err(|e| AppError(anyhow::anyhow!("serialize config: {}", e)))?;
    merge_json(&mut current, &patch);

    // Deserialize back into FleetConfig
    let updated: biged_core::config::FleetConfig = serde_json::from_value(current.clone())
        .map_err(|e| AppError(anyhow::anyhow!("invalid config after merge: {}", e)))?;

    // Persist to fleet.toml
    let toml_path = state.fleet_dir.join("fleet.toml");
    let toml_str = toml::to_string_pretty(&updated)
        .map_err(|e| AppError(anyhow::anyhow!("serialize toml: {}", e)))?;
    tokio::fs::write(&toml_path, &toml_str)
        .await
        .map_err(|e| AppError(anyhow::anyhow!("write fleet.toml: {}", e)))?;

    *config = updated;
    Ok(Json(json!({ "ok": true, "config": current })))
}

/// GET /api/settings/theme
pub async fn get_theme(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let config = state.config.read().await;
    Ok(Json(json!({ "theme": config.dashboard.theme })))
}

#[derive(Deserialize)]
pub struct ThemePayload {
    theme: String,
}

/// PUT /api/settings/theme — update theme + persist
pub async fn set_theme(
    State(state): State<AppState>,
    Json(payload): Json<ThemePayload>,
) -> Result<Json<Value>, AppError> {
    let mut config = state.config.write().await;
    config.dashboard.theme = payload.theme.clone();

    // Persist to fleet.toml
    let toml_path = state.fleet_dir.join("fleet.toml");
    let toml_str = toml::to_string_pretty(&*config)
        .map_err(|e| AppError(anyhow::anyhow!("serialize toml: {}", e)))?;
    tokio::fs::write(&toml_path, &toml_str)
        .await
        .map_err(|e| AppError(anyhow::anyhow!("write fleet.toml: {}", e)))?;

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

/// Recursively merge `patch` into `target` (shallow object merge at each level).
fn merge_json(target: &mut Value, patch: &Value) {
    match (target, patch) {
        (Value::Object(target_map), Value::Object(patch_map)) => {
            for (key, value) in patch_map {
                let entry = target_map.entry(key.clone()).or_insert(Value::Null);
                merge_json(entry, value);
            }
        }
        (target, patch) => {
            *target = patch.clone();
        }
    }
}
