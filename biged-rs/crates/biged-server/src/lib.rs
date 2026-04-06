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
    Router::new()
        // Fleet status
        .route("/api/status", axum::routing::get(handlers::fleet::status))
        .route("/api/health", axum::routing::get(handlers::fleet::health))
        .route("/api/thermal", axum::routing::get(handlers::fleet::thermal))
        .route(
            "/api/dashboard/batch",
            axum::routing::get(handlers::fleet::dashboard_batch),
        )
        .route("/api/alerts", axum::routing::get(handlers::fleet::alerts))
        .route(
            "/api/tasks",
            axum::routing::get(handlers::fleet::tasks).post(handlers::fleet::post_task),
        )
        .route("/api/agents", axum::routing::get(handlers::fleet::agents))
        // Activity
        .route(
            "/api/activity",
            axum::routing::get(handlers::activity::activity),
        )
        .route(
            "/api/agent-cards",
            axum::routing::get(handlers::activity::agent_cards),
        )
        .route(
            "/api/agents/performance",
            axum::routing::get(handlers::activity::agents_performance),
        )
        // Skills + knowledge
        .route("/api/skills", axum::routing::get(handlers::skills::skills))
        .route(
            "/api/knowledge",
            axum::routing::get(handlers::skills::knowledge),
        )
        .route(
            "/api/timeline",
            axum::routing::get(handlers::skills::timeline),
        )
        .route(
            "/api/discussions",
            axum::routing::get(handlers::skills::discussions),
        )
        // SSE stream
        .route("/api/stream", axum::routing::get(sse::stream))
        // Settings
        .route(
            "/api/settings",
            axum::routing::get(handlers::settings::get_settings)
                .put(handlers::settings::put_settings),
        )
        .route(
            "/api/settings/theme",
            axum::routing::get(handlers::settings::get_theme)
                .put(handlers::settings::set_theme)
                .post(handlers::settings::set_theme),
        )
        .route(
            "/api/fleet/worker/{name}/disable",
            axum::routing::post(handlers::settings::disable_worker),
        )
        .route(
            "/api/fleet/worker/{name}/enable",
            axum::routing::post(handlers::settings::enable_worker),
        )
        // Config (read-only)
        .route(
            "/api/config",
            axum::routing::get(handlers::config::get_config),
        )
        .route(
            "/api/config/models",
            axum::routing::get(handlers::config::get_models),
        )
        .route(
            "/api/config/thermal",
            axum::routing::get(handlers::config::get_thermal),
        )
        // Metrics
        .route(
            "/api/metrics",
            axum::routing::get(handlers::metrics::metrics),
        )
        // Task queue management (Phase B P1)
        .route(
            "/api/tasks/queue",
            axum::routing::get(handlers::tasks::queue),
        )
        .route(
            "/api/tasks/recent",
            axum::routing::get(handlers::tasks::recent),
        )
        .route(
            "/api/tasks/dispatch",
            axum::routing::post(handlers::tasks::dispatch),
        )
        .route(
            "/api/tasks/{id}",
            axum::routing::delete(handlers::tasks::cancel),
        )
        .route(
            "/api/tasks/{id}/priority",
            axum::routing::put(handlers::tasks::set_priority),
        )
        .route(
            "/api/tasks/{id}/requeue",
            axum::routing::post(handlers::tasks::requeue),
        )
        // Queue control
        .route(
            "/api/queue/status",
            axum::routing::get(handlers::tasks::queue_status),
        )
        .route(
            "/api/queue/pause",
            axum::routing::post(handlers::tasks::queue_pause),
        )
        .route(
            "/api/queue/resume",
            axum::routing::post(handlers::tasks::queue_resume),
        )
        // Live activity feed
        .route(
            "/api/activity/live",
            axum::routing::get(handlers::tasks::activity_live),
        )
        // Version
        .route("/api/version", axum::routing::get(handlers::tasks::version))
        // Ollama management (Phase B P6)
        .route(
            "/api/ollama/status",
            axum::routing::get(handlers::ollama::status),
        )
        .route(
            "/api/ollama/ps",
            axum::routing::get(handlers::ollama::ps),
        )
        .route(
            "/api/ollama/start",
            axum::routing::post(handlers::ollama::start),
        )
        .route(
            "/api/ollama/stop",
            axum::routing::post(handlers::ollama::stop),
        )
        // Backup & recovery (Phase B P2)
        .route(
            "/api/backup/trigger",
            axum::routing::post(handlers::backup::trigger),
        )
        .route(
            "/api/backup/list",
            axum::routing::get(handlers::backup::list),
        )
        .route(
            "/api/backup/restore",
            axum::routing::post(handlers::backup::restore),
        )
        // Audit (Phase B P3)
        .route(
            "/api/audit/scores",
            axum::routing::get(handlers::audit::scores),
        )
        .route(
            "/api/audit/history",
            axum::routing::get(handlers::audit::history),
        )
        .route(
            "/api/audit/snapshot",
            axum::routing::get(handlers::audit::snapshot),
        )
        // Compliance artifacts (Phase B P4 — the CosmicTasha pattern)
        .route(
            "/api/compliance/profiles",
            axum::routing::post(handlers::compliance::submit_profile),
        )
        .route(
            "/api/compliance/profiles/{id}",
            axum::routing::get(handlers::compliance::get_profile),
        )
        .route(
            "/api/compliance/profiles/{id}/verify",
            axum::routing::get(handlers::compliance::verify_profile),
        )
        .route(
            "/api/compliance/audit_log",
            axum::routing::get(handlers::compliance::audit_log),
        )
        // Logs (Phase C — Logs tab data source)
        .route(
            "/api/logs/{source}",
            axum::routing::get(handlers::logs::tail),
        )
        .with_state(state)
}

/// Start the HTTP server on the configured address and port.
pub async fn run(state: AppState) -> anyhow::Result<()> {
    let addr = {
        let cfg = state.config.read().await;
        format!("{}:{}", cfg.dashboard.bind_address, cfg.dashboard.port)
    };
    let app = router(state);
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    tracing::info!("Server listening on {}", addr);
    axum::serve(listener, app).await?;
    Ok(())
}
