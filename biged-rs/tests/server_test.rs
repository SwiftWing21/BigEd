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

#[tokio::test]
async fn test_status_endpoint() {
    let state = test_state();
    state.db.register_agent("w1", "coder").unwrap();
    state.db.post_task("test", "{}", 5, None).unwrap();

    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/status").body(Body::empty()).unwrap())
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(json.get("agents").is_some());
    assert!(json.get("tasks").is_some());
}

#[tokio::test]
async fn test_health_endpoint() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/health").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(json.get("status").is_some());
}

#[tokio::test]
async fn test_thermal_endpoint() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/thermal").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn test_tasks_endpoint() {
    let state = test_state();
    state.db.post_task("test", "{}", 5, None).unwrap();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/tasks").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(!json.as_array().unwrap().is_empty());
}

#[tokio::test]
async fn test_agents_endpoint() {
    let state = test_state();
    state.db.register_agent("w1", "coder").unwrap();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/agents").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(!json.as_array().unwrap().is_empty());
}
