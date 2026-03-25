# BigEd Rust Rewrite — Phase 2: Axum REST Server

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `biged-server` crate with axum, serving 20 core REST endpoints that power the dashboard home + fleet tab, plus SSE streaming for live updates.

**Architecture:** New `biged-server` crate with axum router. Shared `AppState` holds DB pool, event bus, and config. Handlers are organized by domain (fleet, activity, skills, settings). SSE endpoint replaces polling. Unimplemented endpoints proxy to Flask on port 5556. The existing `dashboard.html` frontend works unchanged against the Rust server.

**Tech Stack:** axum 0.8, tower (middleware), tokio (async), axum-extra (SSE), serde_json (responses), biged-core (DB/types/config), biged-supervisor (events).

**Spec:** `docs/superpowers/specs/2026-03-24-rust-hybrid-architecture-design.md`

**Depends on:** Phase 0+1 complete (biged-core + biged-supervisor crates).

---

## File Structure

```
biged-rs/
├── Cargo.toml                                    # MODIFY: add biged-server to workspace deps
├── crates/
│   ├── biged-core/
│   │   └── src/
│   │       └── db.rs                             # MODIFY: add query methods for server
│   └── biged-server/                             # NEW CRATE
│       ├── Cargo.toml
│       └── src/
│           ├── lib.rs                            # Router + AppState
│           ├── handlers/
│           │   ├── mod.rs                        # Handler module re-exports
│           │   ├── fleet.rs                      # /api/status, /api/health, /api/thermal, /api/alerts
│           │   ├── activity.rs                   # /api/activity, /api/agent-cards, /api/agents/performance
│           │   ├── skills.rs                     # /api/skills, /api/knowledge, /api/timeline, /api/discussions
│           │   └── settings.rs                   # /api/settings/theme, /api/fleet/worker/:name/*
│           ├── sse.rs                            # /api/stream — SSE broadcaster
│           └── error.rs                          # Axum error → HTTP response mapping
├── src/
│   └── main.rs                                   # MODIFY: add `serve` subcommand
└── tests/
    └── server_test.rs                            # NEW: integration tests
```

---

## Task 1: Crate Scaffold + AppState

**Files:**
- Create: `biged-rs/crates/biged-server/Cargo.toml`
- Create: `biged-rs/crates/biged-server/src/lib.rs`
- Create: `biged-rs/crates/biged-server/src/error.rs`
- Create: `biged-rs/crates/biged-server/src/handlers/mod.rs`
- Modify: `biged-rs/Cargo.toml`

- [ ] **Step 1: Create biged-server Cargo.toml**

```toml
# biged-rs/crates/biged-server/Cargo.toml
[package]
name = "biged-server"
version = "0.1.0"
edition.workspace = true

[dependencies]
biged-core = { path = "../biged-core" }
biged-supervisor = { path = "../biged-supervisor" }
rusqlite.workspace = true
r2d2.workspace = true
axum.workspace = true
tokio.workspace = true
serde.workspace = true
serde_json.workspace = true
tracing.workspace = true
anyhow.workspace = true
```

- [ ] **Step 2: Add workspace dependencies to root Cargo.toml**

Add to `[workspace.dependencies]`:
```toml
axum = "0.8"
```

Add to root `[dependencies]`:
```toml
biged-server = { path = "crates/biged-server" }
```

- [ ] **Step 3: Create error.rs — map CoreError to HTTP responses**

```rust
// biged-rs/crates/biged-server/src/error.rs
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use biged_core::error::CoreError;

pub struct AppError(pub anyhow::Error);

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let status = match self.0.downcast_ref::<CoreError>() {
            Some(CoreError::TaskNotFound(_)) => StatusCode::NOT_FOUND,
            Some(CoreError::Db(_) | CoreError::Pool(_)) => StatusCode::INTERNAL_SERVER_ERROR,
            _ => StatusCode::INTERNAL_SERVER_ERROR,
        };
        let body = serde_json::json!({ "error": self.0.to_string() });
        (status, axum::Json(body)).into_response()
    }
}

impl From<CoreError> for AppError {
    fn from(err: CoreError) -> Self {
        Self(err.into())
    }
}

impl From<r2d2::Error> for AppError {
    fn from(err: r2d2::Error) -> Self {
        Self(err.into())
    }
}

impl From<rusqlite::Error> for AppError {
    fn from(err: rusqlite::Error) -> Self {
        Self(err.into())
    }
}

impl From<anyhow::Error> for AppError {
    fn from(err: anyhow::Error) -> Self {
        Self(err)
    }
}
```

- [ ] **Step 4: Create handlers/mod.rs — empty module**

```rust
// biged-rs/crates/biged-server/src/handlers/mod.rs
pub mod fleet;
pub mod activity;
pub mod skills;
pub mod settings;
```

- [ ] **Step 5: Create stub handler files (must be valid empty Rust modules)**

Each file must be a valid Rust module (just a comment is fine — Rust treats empty files as valid modules too).

Create these 4 files with NO content (empty files). Alternatively, a single comment line is fine:

`biged-rs/crates/biged-server/src/handlers/fleet.rs` — empty file
`biged-rs/crates/biged-server/src/handlers/activity.rs` — empty file
`biged-rs/crates/biged-server/src/handlers/skills.rs` — empty file
`biged-rs/crates/biged-server/src/handlers/settings.rs` — empty file

- [ ] **Step 6: Create lib.rs — AppState + router skeleton**

```rust
// biged-rs/crates/biged-server/src/lib.rs
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
```

- [ ] **Step 7: Create sse.rs stub**

```rust
// biged-rs/crates/biged-server/src/sse.rs
// SSE streaming — implemented in Task 7
```

- [ ] **Step 8: Verify it compiles**

Run: `cd biged-rs && cargo check`
Expected: compiles with no errors

- [ ] **Step 9: Commit**

```bash
git add biged-rs/crates/biged-server/ biged-rs/Cargo.toml biged-rs/Cargo.lock
git commit -m "feat(server): scaffold biged-server crate — AppState, router, error mapping"
```

---

## Task 2: DB Query Extensions

The server needs more query methods than biged-core currently provides. Add them to `db.rs`.

**Files:**
- Modify: `biged-rs/crates/biged-core/src/db.rs`
- Test: `biged-rs/tests/db_test.rs`

- [ ] **Step 1: Write failing tests for new queries**

Append to `biged-rs/tests/db_test.rs`:

```rust
#[test]
fn test_all_agents() {
    let db = Db::in_memory().unwrap();
    db.register_agent("a1", "coder").unwrap();
    db.register_agent("a2", "researcher").unwrap();
    let agents = db.all_agents().unwrap();
    assert_eq!(agents.len(), 2);
}

#[test]
fn test_task_counts_by_status() {
    let db = Db::in_memory().unwrap();
    db.post_task("a", "{}", 5, None).unwrap();
    db.post_task("b", "{}", 5, None).unwrap();
    db.register_agent("w1", "coder").unwrap();
    db.claim_task("coder").unwrap();

    let counts = db.task_counts_by_status().unwrap();
    assert_eq!(*counts.get("PENDING").unwrap_or(&0), 1);
    assert_eq!(*counts.get("RUNNING").unwrap_or(&0), 1);
}

#[test]
fn test_task_counts_by_skill() {
    let db = Db::in_memory().unwrap();
    db.post_task("code_review", "{}", 5, None).unwrap();
    db.post_task("code_review", "{}", 5, None).unwrap();
    db.post_task("test_skill", "{}", 5, None).unwrap();

    let counts = db.task_counts_by_skill().unwrap();
    assert_eq!(counts.len(), 2);
}

#[test]
fn test_recent_tasks() {
    let db = Db::in_memory().unwrap();
    db.post_task("a", "{}", 5, None).unwrap();
    db.post_task("b", "{}", 5, None).unwrap();
    db.post_task("c", "{}", 5, None).unwrap();

    let tasks = db.recent_tasks(2).unwrap();
    assert_eq!(tasks.len(), 2);
}

#[test]
fn test_activity_by_day() {
    let db = Db::in_memory().unwrap();
    db.post_task("a", "{}", 5, None).unwrap();
    db.register_agent("w1", "coder").unwrap();
    db.claim_task("coder").unwrap();

    let activity = db.activity_by_day(30).unwrap();
    assert!(!activity.is_empty());
}

#[test]
fn test_all_messages() {
    let db = Db::in_memory().unwrap();
    db.post_message("agent1", Some("agent2"), "fleet", "hello").unwrap();
    let msgs = db.recent_messages("fleet", 10).unwrap();
    assert_eq!(msgs.len(), 1);
    assert_eq!(msgs[0].body, "hello");
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd biged-rs && cargo test --test db_test`
Expected: FAIL — methods don't exist yet

- [ ] **Step 3: Implement new DB query methods**

Add to `biged-rs/crates/biged-core/src/db.rs` inside `impl Db`:

```rust
    // ── Server query methods ─────────────────────────────────────

    pub fn all_agents(&self) -> Result<Vec<Agent>> {
        let conn = self.pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT name, role, status, last_heartbeat, current_task_id
             FROM agents ORDER BY name",
        )?;
        let agents = stmt
            .query_map([], |row| {
                Ok(Agent {
                    name: row.get(0)?,
                    role: row.get(1)?,
                    status: row.get(2)?,
                    last_heartbeat: row.get(3)?,
                    current_task_id: row.get(4)?,
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(agents)
    }

    pub fn task_counts_by_status(&self) -> Result<std::collections::HashMap<String, i64>> {
        let conn = self.pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
        })?;
        let mut map = std::collections::HashMap::new();
        for row in rows {
            let (status, count) = row?;
            map.insert(status, count);
        }
        Ok(map)
    }

    /// Task counts grouped by skill (type column) and status.
    pub fn task_counts_by_skill(
        &self,
    ) -> Result<std::collections::HashMap<String, std::collections::HashMap<String, i64>>> {
        let conn = self.pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT type, status, COUNT(*) FROM tasks GROUP BY type, status",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
            ))
        })?;
        let mut map: std::collections::HashMap<String, std::collections::HashMap<String, i64>> =
            std::collections::HashMap::new();
        for row in rows {
            let (skill, status, count) = row?;
            map.entry(skill).or_default().insert(status, count);
        }
        Ok(map)
    }

    pub fn recent_tasks(&self, limit: i64) -> Result<Vec<Task>> {
        let conn = self.pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT id, created_at, assigned_to, status, priority, type,
                    payload_json, result_json, error, parent_id, depends_on,
                    intelligence_score
             FROM tasks ORDER BY id DESC LIMIT ?1",
        )?;
        let tasks = stmt
            .query_map(params![limit], Self::row_to_task)?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(tasks)
    }

    /// Activity grouped by day for the last N days.
    pub fn activity_by_day(
        &self,
        days: i64,
    ) -> Result<Vec<(String, std::collections::HashMap<String, i64>)>> {
        let conn = self.pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT date(created_at) as day, status, COUNT(*)
             FROM tasks
             WHERE created_at >= datetime('now', ?1 || ' days')
             GROUP BY day, status
             ORDER BY day",
        )?;
        let rows = stmt.query_map(params![format!("-{}", days)], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
            ))
        })?;
        let mut result: std::collections::BTreeMap<
            String,
            std::collections::HashMap<String, i64>,
        > = std::collections::BTreeMap::new();
        for row in rows {
            let (day, status, count) = row?;
            result.entry(day).or_default().insert(status, count);
        }
        Ok(result.into_iter().collect())
    }

    pub fn post_message(
        &self,
        from: &str,
        to: Option<&str>,
        channel: &str,
        body: &str,
    ) -> Result<i64> {
        let conn = self.pool.get()?;
        conn.execute(
            "INSERT INTO messages (from_agent, to_agent, channel, body)
             VALUES (?1, ?2, ?3, ?4)",
            params![from, to, channel, body],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn recent_messages(&self, channel: &str, limit: i64) -> Result<Vec<Message>> {
        let conn = self.pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT id, from_agent, to_agent, channel, body, created_at, read
             FROM messages
             WHERE channel = ?1
             ORDER BY id DESC LIMIT ?2",
        )?;
        let msgs = stmt
            .query_map(params![channel, limit], |row| {
                Ok(Message {
                    id: row.get(0)?,
                    from_agent: row.get(1)?,
                    to_agent: row.get(2)?,
                    channel: row.get(3)?,
                    body: row.get(4)?,
                    created_at: row.get(5)?,
                    read: row.get::<_, i32>(6)? != 0,
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(msgs)
    }
```

- [ ] **Step 4: Run tests**

Run: `cd biged-rs && cargo test --test db_test`
Expected: all tests PASS (6 old + 6 new = 12)

- [ ] **Step 5: Commit**

```bash
git add biged-rs/crates/biged-core/src/db.rs biged-rs/tests/db_test.rs
git commit -m "feat(core): DB query extensions — all_agents, task_counts, activity, messages"
```

---

## Task 3: Fleet Status Handlers

**Files:**
- Create: `biged-rs/crates/biged-server/src/handlers/fleet.rs`
- Modify: `biged-rs/crates/biged-server/src/lib.rs`

- [ ] **Step 1: Write failing integration test**

Create `biged-rs/tests/server_test.rs`:

```rust
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
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX).await.unwrap();
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
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX).await.unwrap();
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
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(json.as_array().unwrap().len() >= 1);
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
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(json.as_array().unwrap().len() >= 1);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd biged-rs && cargo test --test server_test`
Expected: FAIL — routes don't exist yet

- [ ] **Step 3: Implement fleet handlers**

```rust
// biged-rs/crates/biged-server/src/handlers/fleet.rs
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
        "version": "0.400.00b",
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
    // Read from alerts table if it exists, otherwise return empty
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
                Err(e) => { tracing::warn!("Alert row error: {}", e); None }
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
```

- [ ] **Step 4: Wire fleet routes into router**

Update `biged-rs/crates/biged-server/src/lib.rs`:

```rust
// Replace the router() function with:
pub fn router(state: AppState) -> Router {
    Router::new()
        // Fleet status
        .route("/api/status", axum::routing::get(handlers::fleet::status))
        .route("/api/health", axum::routing::get(handlers::fleet::health))
        .route("/api/thermal", axum::routing::get(handlers::fleet::thermal))
        .route("/api/dashboard/batch", axum::routing::get(handlers::fleet::dashboard_batch))
        .route("/api/alerts", axum::routing::get(handlers::fleet::alerts))
        .route("/api/tasks", axum::routing::get(handlers::fleet::tasks).post(handlers::fleet::post_task))
        .route("/api/agents", axum::routing::get(handlers::fleet::agents))
        .with_state(state)
}
```

- [ ] **Step 5: Run tests**

Run: `cd biged-rs && cargo test --test server_test`
Expected: 5 tests PASS (status, health, thermal, tasks, agents)

- [ ] **Step 6: Commit**

```bash
git add biged-rs/crates/biged-server/src/handlers/fleet.rs biged-rs/crates/biged-server/src/lib.rs biged-rs/tests/server_test.rs
git commit -m "feat(server): fleet handlers — status, health, thermal, batch, alerts"
```

---

## Task 4: Activity Handlers

**Files:**
- Create: `biged-rs/crates/biged-server/src/handlers/activity.rs`
- Modify: `biged-rs/crates/biged-server/src/lib.rs`
- Modify: `biged-rs/tests/server_test.rs`

- [ ] **Step 1: Write failing test**

Append to `biged-rs/tests/server_test.rs`:

```rust
#[tokio::test]
async fn test_activity_endpoint() {
    let state = test_state();
    state.db.post_task("test", "{}", 5, None).unwrap();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/activity").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn test_agent_cards_endpoint() {
    let state = test_state();
    state.db.register_agent("w1", "coder").unwrap();
    state.db.register_agent("w2", "researcher").unwrap();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/agent-cards").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(json.get("cards").is_some());
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd biged-rs && cargo test --test server_test test_activity`
Expected: FAIL

- [ ] **Step 3: Implement activity handlers**

```rust
// biged-rs/crates/biged-server/src/handlers/activity.rs
use crate::error::AppError;
use crate::AppState;
use axum::extract::State;
use axum::Json;
use serde_json::{json, Value};
use std::collections::HashMap;

/// GET /api/activity — 30-day activity histogram
pub async fn activity(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let data = state.db.activity_by_day(30)?;
    let result: Vec<Value> = data
        .into_iter()
        .map(|(day, counts)| {
            json!({
                "day": day,
                "DONE": counts.get("DONE").unwrap_or(&0),
                "FAILED": counts.get("FAILED").unwrap_or(&0),
                "PENDING": counts.get("PENDING").unwrap_or(&0),
                "RUNNING": counts.get("RUNNING").unwrap_or(&0),
            })
        })
        .collect();
    Ok(Json(json!(result)))
}

/// GET /api/agent-cards — agents grouped by role
pub async fn agent_cards(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let agents = state.db.all_agents()?;
    let mut by_role: HashMap<String, Vec<Value>> = HashMap::new();
    for agent in agents {
        by_role
            .entry(agent.role.clone())
            .or_default()
            .push(json!({
                "name": agent.name,
                "status": agent.status,
                "current_task_id": agent.current_task_id,
                "last_heartbeat": agent.last_heartbeat,
            }));
    }
    let cards: Vec<Value> = by_role
        .into_iter()
        .map(|(role, agents)| json!({ "role": role, "agents": agents }))
        .collect();
    Ok(Json(json!({ "cards": cards })))
}

/// GET /api/agents/performance — per-agent metrics
pub async fn agents_performance(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let agents = state.db.all_agents()?;
    let mut perf: Vec<Value> = Vec::new();
    let pool = state.db.pool_ref();
    let conn = pool.get()?;
    for agent in agents {
        let completed: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM tasks
                 WHERE assigned_to = ?1 AND status = 'DONE'
                   AND created_at >= datetime('now', '-1 hour')",
                rusqlite::params![agent.name],
                |row| row.get(0),
            )
            .unwrap_or(0);
        perf.push(json!({
            "name": agent.name,
            "tasks_completed_1h": completed,
            "status": agent.status,
        }));
    }
    Ok(Json(json!({ "agents": perf })))
}
```

- [ ] **Step 4: Wire activity routes into router**

Add to router in `lib.rs`:
```rust
        .route("/api/activity", axum::routing::get(handlers::activity::activity))
        .route("/api/agent-cards", axum::routing::get(handlers::activity::agent_cards))
        .route("/api/agents/performance", axum::routing::get(handlers::activity::agents_performance))
```

- [ ] **Step 5: Run tests**

Run: `cd biged-rs && cargo test --test server_test`
Expected: 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add biged-rs/crates/biged-server/src/handlers/activity.rs biged-rs/crates/biged-server/src/lib.rs biged-rs/tests/server_test.rs
git commit -m "feat(server): activity handlers — histogram, agent cards, performance"
```

---

## Task 5: Skills + Knowledge Handlers

**Files:**
- Create: `biged-rs/crates/biged-server/src/handlers/skills.rs`
- Modify: `biged-rs/crates/biged-server/src/lib.rs`
- Modify: `biged-rs/tests/server_test.rs`

- [ ] **Step 1: Write failing test**

Append to `biged-rs/tests/server_test.rs`:

```rust
#[tokio::test]
async fn test_skills_endpoint() {
    let state = test_state();
    state.db.post_task("code_review", "{}", 5, None).unwrap();
    state.db.post_task("test_skill", "{}", 5, None).unwrap();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/skills").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn test_timeline_endpoint() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/timeline").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd biged-rs && cargo test --test server_test test_skills`
Expected: FAIL

- [ ] **Step 3: Implement skills handlers**

```rust
// biged-rs/crates/biged-server/src/handlers/skills.rs
use crate::error::AppError;
use crate::AppState;
use axum::extract::State;
use axum::Json;
use serde_json::{json, Value};

/// GET /api/skills — task counts by skill and status
pub async fn skills(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let counts = state.db.task_counts_by_skill()?;
    let mut result: serde_json::Map<String, Value> = serde_json::Map::new();
    for (skill, statuses) in counts {
        let total: i64 = statuses.values().sum();
        let mut entry = serde_json::Map::new();
        for (status, count) in &statuses {
            entry.insert(status.clone(), json!(count));
        }
        entry.insert("total".into(), json!(total));
        result.insert(skill, Value::Object(entry));
    }
    Ok(Json(Value::Object(result)))
}

/// GET /api/knowledge — knowledge directory listing
pub async fn knowledge(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let knowledge_dir = state.fleet_dir.join("knowledge");
    let mut folders: serde_json::Map<String, Value> = serde_json::Map::new();

    if knowledge_dir.exists() {
        if let Ok(entries) = std::fs::read_dir(&knowledge_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    let name = path.file_name().unwrap_or_default().to_string_lossy().to_string();
                    let mut files = Vec::new();
                    if let Ok(sub_entries) = std::fs::read_dir(&path) {
                        for sub in sub_entries.flatten() {
                            let sp = sub.path();
                            if sp.is_file() {
                                let meta = sp.metadata().ok();
                                files.push(json!({
                                    "name": sp.file_name().unwrap_or_default().to_string_lossy(),
                                    "size": meta.as_ref().map(|m| m.len()).unwrap_or(0),
                                }));
                            }
                        }
                    }
                    folders.insert(name, json!({ "count": files.len(), "files": files }));
                }
            }
        }
    }
    Ok(Json(Value::Object(folders)))
}

/// GET /api/timeline — recent tasks + discussions
pub async fn timeline(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let tasks = state.db.recent_tasks(50)?;
    let events: Vec<Value> = tasks
        .into_iter()
        .map(|t| {
            json!({
                "time": t.created_at,
                "type": "task",
                "detail": t.skill,
                "agent": t.assigned_to,
                "status": t.status.as_str(),
            })
        })
        .collect();
    Ok(Json(json!(events)))
}

/// GET /api/discussions — agent discussion threads
pub async fn discussions(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let pool = state.db.pool_ref();
    let conn = pool.get()?;
    let mut stmt = conn.prepare(
        "SELECT channel, COUNT(*), MAX(created_at)
         FROM messages
         GROUP BY channel
         ORDER BY MAX(created_at) DESC",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok(json!({
            "topic": row.get::<_, String>(0)?,
            "rounds": row.get::<_, i64>(1)?,
            "last_activity": row.get::<_, String>(2)?,
        }))
    })?;
    let discussions: Vec<Value> = rows
        .filter_map(|r| match r {
            Ok(v) => Some(v),
            Err(e) => { tracing::warn!("Discussion row error: {}", e); None }
        })
        .collect();
    Ok(Json(json!(discussions)))
}
```

- [ ] **Step 4: Wire skills routes into router**

Add to router in `lib.rs`:
```rust
        .route("/api/skills", axum::routing::get(handlers::skills::skills))
        .route("/api/knowledge", axum::routing::get(handlers::skills::knowledge))
        .route("/api/timeline", axum::routing::get(handlers::skills::timeline))
        .route("/api/discussions", axum::routing::get(handlers::skills::discussions))
```

- [ ] **Step 5: Run tests**

Run: `cd biged-rs && cargo test --test server_test`
Expected: 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add biged-rs/crates/biged-server/src/handlers/skills.rs biged-rs/crates/biged-server/src/lib.rs biged-rs/tests/server_test.rs
git commit -m "feat(server): skills handlers — skill counts, knowledge dir, timeline, discussions"
```

---

## Task 6: Settings Handlers

**Files:**
- Create: `biged-rs/crates/biged-server/src/handlers/settings.rs`
- Modify: `biged-rs/crates/biged-server/src/lib.rs`
- Modify: `biged-rs/tests/server_test.rs`

- [ ] **Step 1: Write failing test**

Append to `biged-rs/tests/server_test.rs`:

```rust
#[tokio::test]
async fn test_get_theme() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/settings/theme").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(json.get("theme").is_some());
}

#[tokio::test]
async fn test_set_theme() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(
            Request::post("/api/settings/theme")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"theme":"figma"}"#))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
}
```

- [ ] **Step 2: Implement settings handlers**

```rust
// biged-rs/crates/biged-server/src/handlers/settings.rs
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
```

- [ ] **Step 3: Wire settings routes into router**

Add to router in `lib.rs`:
```rust
        .route("/api/settings/theme", axum::routing::get(handlers::settings::get_theme).post(handlers::settings::set_theme))
        .route("/api/fleet/worker/:name/disable", axum::routing::post(handlers::settings::disable_worker))
        .route("/api/fleet/worker/:name/enable", axum::routing::post(handlers::settings::enable_worker))
```

- [ ] **Step 4: Run tests**

Run: `cd biged-rs && cargo test --test server_test`
Expected: 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add biged-rs/crates/biged-server/src/handlers/settings.rs biged-rs/crates/biged-server/src/lib.rs biged-rs/tests/server_test.rs
git commit -m "feat(server): settings handlers — theme get/set, worker enable/disable"
```

---

## Task 7: SSE Streaming

**Files:**
- Create: `biged-rs/crates/biged-server/src/sse.rs`
- Modify: `biged-rs/crates/biged-server/src/lib.rs`
- Modify: `biged-rs/tests/server_test.rs`

- [ ] **Step 1: Write failing test**

Append to `biged-rs/tests/server_test.rs`:

```rust
#[tokio::test]
async fn test_sse_stream_connects() {
    let state = test_state();
    let app = biged_server::router(state);
    let resp = app
        .oneshot(Request::get("/api/stream").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    // SSE should return text/event-stream content type
    let ct = resp.headers().get("content-type").unwrap().to_str().unwrap();
    assert!(ct.contains("text/event-stream"));
}
```

- [ ] **Step 2: Implement SSE handler**

```rust
// biged-rs/crates/biged-server/src/sse.rs
use crate::AppState;
use axum::extract::State;
use axum::response::sse::{Event, KeepAlive, Sse};
use futures::stream::Stream;
use serde_json::json;
use std::convert::Infallible;
use std::time::Duration;
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt;

/// GET /api/stream — SSE endpoint for live fleet updates
pub async fn stream(
    State(state): State<AppState>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let rx = state.events.subscribe();
    let db = state.db.clone();

    let stream = async_stream::stream! {
        // Send initial connection event
        yield Ok(Event::default().data(json!({"type": "connected"}).to_string()));

        // Send initial status snapshot
        if let Ok(agents) = db.all_agents() {
            if let Ok(tasks) = db.task_counts_by_status() {
                yield Ok(Event::default().data(
                    json!({"type": "status", "data": {"agents": agents, "tasks": tasks}}).to_string()
                ));
            }
        }

        // Forward fleet events as SSE
        let mut event_stream = BroadcastStream::new(rx);
        while let Some(Ok(event)) = event_stream.next().await {
            let event_type = event.event_type();
            if let Ok(data) = serde_json::to_string(&event) {
                yield Ok(Event::default()
                    .event(event_type)
                    .data(data));
            }
        }
    };

    Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
}
```

- [ ] **Step 3: Add SSE dependencies**

Add to `[workspace.dependencies]` in root `biged-rs/Cargo.toml`:
```toml
futures = "0.3"
async-stream = "0.3"
tokio-stream = "0.1"
```

Add to `[dependencies]` in `biged-rs/crates/biged-server/Cargo.toml`:
```toml
futures.workspace = true
async-stream.workspace = true
tokio-stream.workspace = true
```

- [ ] **Step 4: Wire SSE route into router**

Add to router in `lib.rs`:
```rust
        .route("/api/stream", axum::routing::get(sse::stream))
```

- [ ] **Step 5: Run tests**

Run: `cd biged-rs && cargo test --test server_test`
Expected: 10 tests PASS

- [ ] **Step 6: Commit**

```bash
git add biged-rs/crates/biged-server/ biged-rs/Cargo.toml biged-rs/Cargo.lock biged-rs/tests/server_test.rs
git commit -m "feat(server): SSE streaming — live fleet events + status snapshots"
```

---

## Task 8: Wire Server into main.rs + Final Tests

**Files:**
- Modify: `biged-rs/src/main.rs`
- Modify: `biged-rs/tests/smoke_test.rs`

- [ ] **Step 1: Update main.rs to wire up serve subcommand**

Replace the `Serve` arm in `main.rs`:

```rust
        Some(Commands::Serve) => {
            tracing::info!("Starting BigEd HTTP server");
            let fleet_dir = std::env::current_dir()?.join("fleet");
            let config_path = fleet_dir.join("fleet.toml");
            let config = if config_path.exists() {
                biged_core::config::FleetConfig::from_file(&config_path)?
            } else {
                biged_core::config::FleetConfig::default()
            };
            let db_path = fleet_dir.join("fleet.db");
            let db = biged_core::db::Db::open(&db_path)?;
            let (tx, _rx) = biged_supervisor::events::create_event_bus(256);
            let state = biged_server::AppState {
                db,
                events: tx,
                config: std::sync::Arc::new(tokio::sync::RwLock::new(config)),
                fleet_dir,
            };
            biged_server::run(state).await?;
        }
```

- [ ] **Step 2: Add smoke test for server startup**

Append to `biged-rs/tests/smoke_test.rs`:

```rust
#[tokio::test]
async fn smoke_server_router_builds() {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = biged_supervisor::events::create_event_bus(10);
    let state = biged_server::AppState {
        db,
        events: tx,
        config: std::sync::Arc::new(tokio::sync::RwLock::new(FleetConfig::default())),
        fleet_dir: std::path::PathBuf::from("."),
    };
    let _router = biged_server::router(state);
    // If we get here, router construction succeeded
}
```

- [ ] **Step 3: Run full test suite**

Run: `cd biged-rs && cargo test`
Expected: all tests pass (config + db + queue + server + smoke + supervisor)

- [ ] **Step 4: Run clippy + fmt**

Run: `cd biged-rs && cargo clippy && cargo fmt --check`
Expected: zero warnings, formatting clean

- [ ] **Step 5: Commit**

```bash
git add biged-rs/src/main.rs biged-rs/tests/smoke_test.rs
git commit -m "feat(server): wire serve subcommand + smoke test for router"
```

---

## Gate Criteria

Phase 2 is complete when:
- [ ] `cargo test` passes all tests (config, db, queue, server, supervisor, smoke)
- [ ] `cargo run -- serve` starts, listens on configured port, responds to `/api/status`
- [ ] `cargo clippy` has zero warnings
- [ ] `cargo fmt --check` passes
- [ ] Dashboard HTML connects to Rust server and renders status/agents/thermal
- [ ] SSE stream delivers live events to connected clients
- [ ] Flask proxy fallback works for unimplemented endpoints
