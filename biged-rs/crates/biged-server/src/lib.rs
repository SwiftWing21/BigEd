pub mod error;
pub mod handlers;
pub mod sse;

use axum::Router;
use biged_core::config::FleetConfig;
use biged_core::db::Db;
use biged_supervisor::events::EventSender;
use std::sync::Arc;
use tokio::sync::RwLock;

#[derive(Clone)]
pub struct AppState {
    pub db: Db,
    pub events: EventSender,
    pub config: Arc<RwLock<FleetConfig>>,
    pub fleet_dir: std::path::PathBuf,
}

pub fn router(state: AppState) -> Router {
    Router::new().with_state(state)
}

/// Start the HTTP server on the configured port.
pub async fn run(state: AppState) -> anyhow::Result<()> {
    let port = {
        let cfg = state.config.read().await;
        cfg.dashboard.port
    };
    let app = router(state);
    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{}", port)).await?;
    tracing::info!("Server listening on port {}", port);
    axum::serve(listener, app).await?;
    Ok(())
}
