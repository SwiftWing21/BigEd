use axum::body::Body;
use axum::http::{Request, StatusCode};
use biged_core::config::FleetConfig;
use biged_core::db::Db;
use biged_server::AppState;
use std::sync::Arc;
use tokio::sync::RwLock;
use tower::ServiceExt;

fn test_state() -> AppState {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = biged_supervisor::events::create_event_bus(100);
    AppState {
        db,
        events: tx,
        config: Arc::new(RwLock::new(FleetConfig::default())),
        fleet_dir: std::path::PathBuf::from("."),
    }
}

// ── Settings endpoints ──────────────────────────────────────────────────────

#[tokio::test]
async fn test_get_settings_returns_valid_json() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(
            Request::get("/api/settings")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    // Should contain top-level config sections
    assert!(json.get("fleet").is_some(), "missing fleet section");
    assert!(json.get("models").is_some(), "missing models section");
    assert!(json.get("dashboard").is_some(), "missing dashboard section");
    assert!(json.get("thermal").is_some(), "missing thermal section");
}

#[tokio::test]
async fn test_get_settings_theme_returns_theme() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(
            Request::get("/api/settings/theme")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["theme"], "figma");
}

// ── Config endpoints (read-only) ────────────────────────────────────────────

#[tokio::test]
async fn test_get_config_returns_fleet_config() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(
            Request::get("/api/config")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(json.get("fleet").is_some());
    assert!(json.get("workers").is_some());
}

#[tokio::test]
async fn test_get_config_models_returns_models_section() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(
            Request::get("/api/config/models")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(json.get("local").is_some(), "missing local model");
    assert!(json.get("ollama_host").is_some(), "missing ollama_host");
}

#[tokio::test]
async fn test_get_config_thermal_returns_thermal_section() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(
            Request::get("/api/config/thermal")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(
        json.get("gpu_max_sustained_c").is_some(),
        "missing gpu_max_sustained_c"
    );
    assert!(
        json.get("cooldown_target_c").is_some(),
        "missing cooldown_target_c"
    );
}

// ── Metrics endpoint ────────────────────────────────────────────────────────

#[tokio::test]
async fn test_get_metrics_returns_expected_fields() {
    let state = test_state();
    // Seed some data so metrics are non-trivial
    state.db.register_agent("w1", "coder").unwrap();
    state.db.post_task("test", "{}", 5, None).unwrap();

    let app = biged_server::router(state);
    let resp = app
        .oneshot(
            Request::get("/api/metrics")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(json.get("uptime_secs").is_some(), "missing uptime_secs");
    assert!(
        json.get("total_tasks_processed").is_some(),
        "missing total_tasks_processed"
    );
    assert!(
        json.get("active_agents").is_some(),
        "missing active_agents"
    );
    assert!(json.get("queue_depth").is_some(), "missing queue_depth");
}
