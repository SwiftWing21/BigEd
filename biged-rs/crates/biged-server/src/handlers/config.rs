use crate::error::AppError;
use crate::AppState;
use axum::extract::State;
use axum::Json;
use serde_json::Value;

/// GET /api/config — read-only FleetConfig
pub async fn get_config(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let config = state.config.read().await;
    let val = serde_json::to_value(&*config)
        .map_err(|e| AppError(anyhow::anyhow!("serialize config: {}", e)))?;
    Ok(Json(val))
}

/// GET /api/config/models — models section only
pub async fn get_models(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let config = state.config.read().await;
    let val = serde_json::to_value(&config.models)
        .map_err(|e| AppError(anyhow::anyhow!("serialize models: {}", e)))?;
    Ok(Json(val))
}

/// GET /api/config/thermal — thermal section only
pub async fn get_thermal(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let config = state.config.read().await;
    let val = serde_json::to_value(&config.thermal)
        .map_err(|e| AppError(anyhow::anyhow!("serialize thermal: {}", e)))?;
    Ok(Json(val))
}
