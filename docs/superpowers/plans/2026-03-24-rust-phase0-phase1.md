# BigEd Rust Rewrite — Phase 0 + Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the Rust workspace, implement biged-core (config, DB pool, task queue, shared types), and biged-supervisor (unified supervisor + thermal monitor) that can run alongside the Python fleet.

**Architecture:** Cargo workspace with 7 crates. Phase 0 builds the foundation (biged-core). Phase 1 builds biged-supervisor as a standalone binary that replaces supervisor.py + hw_supervisor.py. Both phases produce a working `biged supervisor` command that manages the fleet with zero Python dependency for infrastructure.

**Tech Stack:** Rust 2021 edition (MSRV 1.76), tokio 1.x async runtime, rusqlite 0.38 (bundled with FTS5), r2d2 connection pool, toml 0.9 config parser, tracing for structured logging, clap 4 for CLI, thiserror 2 + anyhow 1 for errors, nvml-wrapper 0.11 for GPU.

**Spec:** `docs/superpowers/specs/2026-03-24-rust-hybrid-architecture-design.md`

**Working directory:** New directory `biged-rs/` at project root (sibling to `fleet/` and `BigEd/`).

---

## File Structure

```
biged-rs/
├── Cargo.toml                         # workspace manifest
├── rust-toolchain.toml                # pin Rust version
├── .cargo/config.toml                 # build settings
├── src/
│   └── main.rs                        # CLI entrypoint (clap subcommands)
├── crates/
│   ├── biged-core/
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs                 # re-exports
│   │       ├── config.rs              # FleetConfig from fleet.toml
│   │       ├── db.rs                  # Db pool + all queries
│   │       ├── types.rs               # TaskStatus, AgentStatus, Task, Agent, HwState
│   │       ├── queue.rs               # TaskQueue (mpsc + DB-backed)
│   │       └── error.rs              # CoreError enum
│   └── biged-supervisor/
│       ├── Cargo.toml
│       └── src/
│           ├── lib.rs                 # re-exports
│           ├── supervisor.rs          # Supervisor struct + tokio task tree
│           ├── scaler.rs              # AutoScaler (threshold-based, ONNX optional)
│           ├── thermal.rs             # ThermalMonitor (GPU/CPU, model management)
│           ├── health.rs              # worker health checks, stale recovery
│           ├── events.rs              # FleetEvent enum + broadcast bus
│           └── backup.rs              # periodic backup task
└── tests/
    ├── config_test.rs                 # config parsing integration tests
    ├── db_test.rs                     # DB operations integration tests
    ├── queue_test.rs                  # task queue semantics tests
    └── supervisor_test.rs             # supervisor lifecycle tests
```

---

## Task 1: Workspace Scaffold

**Files:**
- Create: `biged-rs/Cargo.toml`
- Create: `biged-rs/rust-toolchain.toml`
- Create: `biged-rs/.cargo/config.toml`
- Create: `biged-rs/src/main.rs`
- Create: `biged-rs/crates/biged-core/Cargo.toml`
- Create: `biged-rs/crates/biged-core/src/lib.rs`
- Create: `biged-rs/crates/biged-supervisor/Cargo.toml`
- Create: `biged-rs/crates/biged-supervisor/src/lib.rs`

- [ ] **Step 1: Create workspace root Cargo.toml**

```toml
# biged-rs/Cargo.toml
[workspace]
resolver = "2"
members = ["crates/*", "."]

[workspace.package]
edition = "2021"
rust-version = "1.76"
license = "Apache-2.0"

[workspace.dependencies]
tokio = { version = "1", features = ["full"] }
rusqlite = { version = "0.38", features = ["bundled"] }
r2d2 = "0.8"
r2d2_sqlite = "0.32"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
toml = "0.9"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["json", "env-filter"] }
clap = { version = "4", features = ["derive"] }
thiserror = "2"
anyhow = "1"
nvml-wrapper = "0.11"
reqwest = { version = "0.12", features = ["json"] }
dashmap = "6"

[package]
name = "biged"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true

[dependencies]
biged-core = { path = "crates/biged-core" }
biged-supervisor = { path = "crates/biged-supervisor" }
clap.workspace = true
tokio.workspace = true
tracing.workspace = true
tracing-subscriber.workspace = true
anyhow.workspace = true
```

- [ ] **Step 2: Create rust-toolchain.toml and cargo config**

```toml
# biged-rs/rust-toolchain.toml
[toolchain]
channel = "stable"
components = ["rustfmt", "clippy"]
```

```toml
# biged-rs/.cargo/config.toml
[build]
# Faster linking on Windows
rustflags = ["-C", "link-arg=-fuse-ld=lld"]

[target.x86_64-pc-windows-msvc]
linker = "lld-link"
```

- [ ] **Step 3: Create biged-core crate skeleton**

```toml
# biged-rs/crates/biged-core/Cargo.toml
[package]
name = "biged-core"
version = "0.1.0"
edition.workspace = true

[dependencies]
rusqlite.workspace = true
r2d2.workspace = true
r2d2_sqlite.workspace = true
serde.workspace = true
serde_json.workspace = true
toml.workspace = true
tracing.workspace = true
thiserror.workspace = true
tokio.workspace = true
```

```rust
// biged-rs/crates/biged-core/src/lib.rs
pub mod config;
pub mod db;
pub mod error;
pub mod queue;
pub mod types;
```

- [ ] **Step 4: Create biged-supervisor crate skeleton**

```toml
# biged-rs/crates/biged-supervisor/Cargo.toml
[package]
name = "biged-supervisor"
version = "0.1.0"
edition.workspace = true

[dependencies]
biged-core = { path = "../biged-core" }
tokio.workspace = true
tracing.workspace = true
anyhow.workspace = true
nvml-wrapper.workspace = true
reqwest.workspace = true
serde.workspace = true
serde_json.workspace = true
```

```rust
// biged-rs/crates/biged-supervisor/src/lib.rs
pub mod supervisor;
pub mod scaler;
pub mod thermal;
pub mod health;
pub mod events;
pub mod backup;
```

- [ ] **Step 5: Create CLI entrypoint**

```rust
// biged-rs/src/main.rs
use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "biged", version, about = "BigEd CC — Autonomous Agent Fleet")]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,

    /// Run headless (no GUI)
    #[arg(long)]
    headless: bool,
}

#[derive(Subcommand)]
enum Commands {
    /// Run the supervisor (process lifecycle + thermal)
    Supervisor,
    /// Run the HTTP server
    Serve,
    /// Run as worker only (edge node)
    Worker,
    /// Run thermal monitor only
    Thermal,
    /// Migrate database from Python fleet
    Migrate,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter("biged=info")
        .json()
        .init();

    let cli = Cli::parse();

    match cli.command {
        Some(Commands::Supervisor) | None => {
            tracing::info!("Starting BigEd supervisor");
            biged_supervisor::supervisor::run().await?;
        }
        Some(Commands::Migrate) => {
            tracing::info!("Running database migration");
            // TODO: Phase 0 migration
        }
        _ => {
            tracing::warn!("Command not yet implemented");
        }
    }

    Ok(())
}
```

- [ ] **Step 6: Verify workspace compiles**

Run: `cd biged-rs && cargo check`
Expected: compiles with no errors (empty modules)

- [ ] **Step 7: Commit**

```bash
git add biged-rs/
git commit -m "feat(rust): scaffold workspace — biged-core + biged-supervisor crates"
```

---

## Task 2: Shared Types (biged-core/types.rs)

**Files:**
- Create: `biged-rs/crates/biged-core/src/types.rs`
- Test: `biged-rs/tests/types_test.rs` (not needed — derive-only, tested via DB)

- [ ] **Step 1: Define core types**

```rust
// biged-rs/crates/biged-core/src/types.rs
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum TaskStatus {
    Pending,
    Running,
    Done,
    Failed,
    WaitingHuman,
    Review,
    Forwarded,
}

impl TaskStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Pending => "PENDING",
            Self::Running => "RUNNING",
            Self::Done => "DONE",
            Self::Failed => "FAILED",
            Self::WaitingHuman => "WAITING_HUMAN",
            Self::Review => "REVIEW",
            Self::Forwarded => "FORWARDED",
        }
    }

    pub fn from_db(s: &str) -> Self {
        match s {
            "PENDING" => Self::Pending,
            "RUNNING" => Self::Running,
            "DONE" => Self::Done,
            "FAILED" => Self::Failed,
            "WAITING_HUMAN" => Self::WaitingHuman,
            "REVIEW" => Self::Review,
            "FORWARDED" => Self::Forwarded,
            _ => Self::Pending,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AgentStatus {
    Idle,
    Busy,
    Quarantined,
    Offline,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    pub id: i64,
    pub created_at: String,
    pub assigned_to: Option<String>,
    pub status: TaskStatus,
    pub priority: i32,
    pub skill: String,
    pub payload_json: Option<String>,
    pub result_json: Option<String>,
    pub error: Option<String>,
    pub parent_id: Option<i64>,
    pub depends_on: Option<String>,
    pub intelligence_score: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Agent {
    pub name: String,
    pub role: String,
    pub status: String,
    pub last_heartbeat: Option<String>,
    pub current_task_id: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HwState {
    pub status: String,
    pub gpu_temp_c: f32,
    pub gpu_vram_used_gb: f64,
    pub gpu_vram_total_gb: f64,
    pub gpu_power_w: f32,
    pub cpu_temp_c: f32,
    pub ram_used_gb: f64,
    pub ram_total_gb: f64,
    pub loaded_models: Vec<String>,
    pub thermal_state: String,
    pub current_model: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub id: i64,
    pub from_agent: String,
    pub to_agent: Option<String>,
    pub channel: String,
    pub body: String,
    pub created_at: String,
    pub read: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageRecord {
    pub skill: String,
    pub model: String,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cost_usd: f64,
    pub task_id: Option<i64>,
}
```

- [ ] **Step 2: Verify compiles**

Run: `cd biged-rs && cargo check`
Expected: compiles

- [ ] **Step 3: Commit**

```bash
git add biged-rs/crates/biged-core/src/types.rs
git commit -m "feat(core): shared types — Task, Agent, HwState, Message, UsageRecord"
```

---

## Task 3: Error Types (biged-core/error.rs)

**Files:**
- Create: `biged-rs/crates/biged-core/src/error.rs`

- [ ] **Step 1: Define error hierarchy**

```rust
// biged-rs/crates/biged-core/src/error.rs
use thiserror::Error;

#[derive(Error, Debug)]
pub enum CoreError {
    #[error("database error: {0}")]
    Db(#[from] rusqlite::Error),

    #[error("connection pool error: {0}")]
    Pool(#[from] r2d2::Error),

    #[error("config parse error: {0}")]
    Config(String),

    #[error("config file not found: {0}")]
    ConfigNotFound(std::path::PathBuf),

    #[error("task queue full")]
    QueueFull,

    #[error("task not found: {0}")]
    TaskNotFound(i64),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
}

pub type Result<T> = std::result::Result<T, CoreError>;
```

- [ ] **Step 2: Verify compiles, commit**

Run: `cd biged-rs && cargo check`

```bash
git commit -m "feat(core): error types — CoreError with thiserror"
```

---

## Task 4: Config Parser (biged-core/config.rs)

**Files:**
- Create: `biged-rs/crates/biged-core/src/config.rs`
- Test: `biged-rs/tests/config_test.rs`

- [ ] **Step 1: Write failing test**

```rust
// biged-rs/tests/config_test.rs
use biged_core::config::FleetConfig;
use std::path::Path;

#[test]
fn test_parse_existing_fleet_toml() {
    // Read the actual fleet.toml from the Python project
    let fleet_toml = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent().unwrap()  // biged-rs/
        .parent().unwrap()  // project root
        .join("fleet/fleet.toml");

    if !fleet_toml.exists() {
        eprintln!("Skipping: fleet.toml not found at {:?}", fleet_toml);
        return;
    }

    let config = FleetConfig::from_file(&fleet_toml).expect("Failed to parse fleet.toml");
    assert!(!config.fleet.offline_mode, "offline_mode should be false");
    assert!(config.dashboard.port > 0, "dashboard port should be set");
    assert!(!config.models.conductor_model.is_empty(), "conductor model should be set");
}

#[test]
fn test_parse_minimal_config() {
    let toml_str = r#"
[fleet]
offline_mode = false
air_gap_mode = false

[models]
conductor_model = "qwen3:4b"

[dashboard]
port = 5555
enabled = true

[workers]
max_workers = 6

[thermal]
gpu_target_c = 75
"#;
    let config = FleetConfig::from_str(toml_str).expect("Failed to parse minimal config");
    assert_eq!(config.dashboard.port, 5555);
    assert_eq!(config.workers.max_workers, 6);
    assert_eq!(config.thermal.gpu_target_c, 75);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd biged-rs && cargo test --test config_test`
Expected: FAIL — `FleetConfig` doesn't exist yet

- [ ] **Step 3: Implement config parser**

```rust
// biged-rs/crates/biged-core/src/config.rs
use crate::error::{CoreError, Result};
use serde::Deserialize;
use std::path::Path;

/// Top-level fleet configuration parsed from fleet.toml.
/// Uses serde default for every field — tolerates missing/extra keys.
#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct FleetConfig {
    pub fleet: FleetSection,
    pub models: ModelsSection,
    pub dashboard: DashboardSection,
    pub workers: WorkersSection,
    pub thermal: ThermalSection,
    pub budgets: BudgetsSection,
    pub backup: BackupSection,
    // Catch-all for sections we don't model yet (federation, security, etc.)
    #[serde(flatten)]
    pub extra: toml::Table,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct FleetSection {
    pub offline_mode: bool,
    pub air_gap_mode: bool,
    pub disabled_agents: Vec<String>,
    pub hitl_evolution: bool,
    #[serde(flatten)]
    pub extra: toml::Table,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct ModelsSection {
    pub conductor_model: String,
    pub complex: String,
    pub complex_provider: String,
    #[serde(flatten)]
    pub extra: toml::Table,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct DashboardSection {
    pub port: u16,
    pub enabled: bool,
    pub bind_address: String,
    pub auto_open: bool,
    #[serde(flatten)]
    pub extra: toml::Table,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct WorkersSection {
    pub max_workers: u32,
    pub memory_limit_mb: u32,
    pub coder_count: u32,
    pub cpu_limit_percent: u32,
    pub nice_level: i32,
    #[serde(flatten)]
    pub extra: toml::Table,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct ThermalSection {
    pub gpu_target_c: u32,
    pub cpu_max_sustained_c: u32,
    pub cooldown_after_swap_secs: u32,
    pub cooldown_window_secs: u32,
    pub cooldown_target_c: u32,
    pub ambient_estimation: bool,
    #[serde(flatten)]
    pub extra: toml::Table,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct BudgetsSection {
    #[serde(flatten)]
    pub extra: toml::Table,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct BackupSection {
    pub enabled: bool,
    pub interval_secs: u64,
    pub depth: u32,
    pub location: String,
    #[serde(flatten)]
    pub extra: toml::Table,
}

// Defaults matching fleet.toml current values
impl Default for FleetConfig {
    fn default() -> Self {
        Self {
            fleet: FleetSection::default(),
            models: ModelsSection::default(),
            dashboard: DashboardSection::default(),
            workers: WorkersSection::default(),
            thermal: ThermalSection::default(),
            budgets: BudgetsSection::default(),
            backup: BackupSection::default(),
            extra: toml::Table::new(),
        }
    }
}

impl Default for FleetSection {
    fn default() -> Self {
        Self {
            offline_mode: false,
            air_gap_mode: false,
            disabled_agents: vec![
                "sales".into(), "onboarding".into(), "implementation".into(),
                "legal".into(), "account_manager".into(),
                "ds_rag".into(), "ds_fleet".into(), "ds_research".into(),
            ],
            hitl_evolution: false,
            extra: toml::Table::new(),
        }
    }
}

impl Default for ModelsSection {
    fn default() -> Self {
        Self {
            conductor_model: "qwen3:4b".into(),
            complex: "qwen3:8b".into(),
            complex_provider: "local".into(),
            extra: toml::Table::new(),
        }
    }
}

impl Default for DashboardSection {
    fn default() -> Self {
        Self { port: 5555, enabled: true, bind_address: "127.0.0.1".into(),
               auto_open: true, extra: toml::Table::new() }
    }
}

impl Default for WorkersSection {
    fn default() -> Self {
        Self { max_workers: 6, memory_limit_mb: 384, coder_count: 3,
               cpu_limit_percent: 10, nice_level: 15, extra: toml::Table::new() }
    }
}

impl Default for ThermalSection {
    fn default() -> Self {
        Self { gpu_target_c: 75, cpu_max_sustained_c: 85,
               cooldown_after_swap_secs: 60, cooldown_window_secs: 120,
               cooldown_target_c: 75, ambient_estimation: true,
               extra: toml::Table::new() }
    }
}

impl Default for BackupSection {
    fn default() -> Self {
        Self { enabled: true, interval_secs: 1200, depth: 10,
               location: "~/BigEd-backup".into(), extra: toml::Table::new() }
    }
}

impl FleetConfig {
    /// Parse from a fleet.toml file path.
    pub fn from_file(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Err(CoreError::ConfigNotFound(path.to_path_buf()));
        }
        let text = std::fs::read_to_string(path)?;
        Self::from_str(&text)
    }

    /// Parse from a TOML string.
    pub fn from_str(s: &str) -> Result<Self> {
        toml::from_str(s).map_err(|e| CoreError::Config(e.to_string()))
    }

    /// Get a raw TOML value by dotted path (e.g., "fleet.offline_mode").
    pub fn get_raw(&self, path: &str) -> Option<&toml::Value> {
        let parts: Vec<&str> = path.split('.').collect();
        if parts.len() < 2 {
            return self.extra.get(path);
        }
        // Check known sections first, fall back to extra
        self.extra.get(parts[0])
            .and_then(|v| v.get(parts[1]))
    }
}
```

- [ ] **Step 4: Run tests**

Run: `cd biged-rs && cargo test --test config_test`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add biged-rs/crates/biged-core/src/config.rs biged-rs/tests/config_test.rs
git commit -m "feat(core): config parser — reads existing fleet.toml with serde defaults"
```

---

## Task 5: Database Pool (biged-core/db.rs)

**Files:**
- Create: `biged-rs/crates/biged-core/src/db.rs`
- Test: `biged-rs/tests/db_test.rs`

- [ ] **Step 1: Write failing tests**

```rust
// biged-rs/tests/db_test.rs
use biged_core::db::Db;
use biged_core::types::TaskStatus;

#[test]
fn test_db_init_creates_tables() {
    let db = Db::in_memory().expect("Failed to create in-memory DB");
    let tables = db.table_names().expect("Failed to list tables");
    assert!(tables.contains(&"tasks".to_string()));
    assert!(tables.contains(&"agents".to_string()));
    assert!(tables.contains(&"messages".to_string()));
    assert!(tables.contains(&"usage".to_string()));
}

#[test]
fn test_post_and_claim_task() {
    let db = Db::in_memory().unwrap();
    db.register_agent("coder_1", "coder").unwrap();

    let id = db.post_task("code_review", r#"{"file":"test.py"}"#, 5, None).unwrap();
    assert!(id > 0);

    let task = db.claim_task("coder").unwrap();
    assert!(task.is_some());
    let task = task.unwrap();
    assert_eq!(task.id, id);
    assert_eq!(task.status, TaskStatus::Running);
    assert_eq!(task.assigned_to.as_deref(), Some("coder_1"));
}

#[test]
fn test_claim_is_atomic_no_double_claim() {
    let db = Db::in_memory().unwrap();
    db.register_agent("a1", "coder").unwrap();
    db.register_agent("a2", "coder").unwrap();

    let id = db.post_task("test_skill", "{}", 5, None).unwrap();

    // Both try to claim — only one should succeed
    let t1 = db.claim_task("coder").unwrap();
    let t2 = db.claim_task("coder").unwrap();

    assert!(t1.is_some());
    assert!(t2.is_none(), "Second claim should return None — task already claimed");
}

#[test]
fn test_complete_task() {
    let db = Db::in_memory().unwrap();
    db.register_agent("w1", "coder").unwrap();
    let id = db.post_task("test", "{}", 5, None).unwrap();
    db.claim_task("coder").unwrap();

    db.complete_task(id, r#"{"status":"ok"}"#).unwrap();

    let task = db.get_task(id).unwrap().unwrap();
    assert_eq!(task.status, TaskStatus::Done);
}

#[test]
fn test_heartbeat() {
    let db = Db::in_memory().unwrap();
    db.register_agent("w1", "coder").unwrap();
    db.heartbeat("w1").unwrap();

    let agent = db.get_agent("w1").unwrap().unwrap();
    assert!(agent.last_heartbeat.is_some());
}

#[test]
fn test_queue_depth() {
    let db = Db::in_memory().unwrap();
    db.post_task("a", "{}", 5, None).unwrap();
    db.post_task("b", "{}", 5, None).unwrap();
    db.post_task("c", "{}", 5, None).unwrap();

    assert_eq!(db.queue_depth().unwrap(), 3);
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd biged-rs && cargo test --test db_test`
Expected: FAIL — `Db` doesn't exist

- [ ] **Step 3: Implement Db**

```rust
// biged-rs/crates/biged-core/src/db.rs
use crate::error::{CoreError, Result};
use crate::types::{Agent, Message, Task, TaskStatus};
use r2d2::Pool;
use r2d2_sqlite::SqliteConnectionManager;
use rusqlite::params;
use std::path::Path;
use tracing::{debug, warn};

/// Connection-pooled SQLite database for fleet operations.
pub struct Db {
    pool: Pool<SqliteConnectionManager>,
}

impl Db {
    /// Open database at the given path with WAL mode and connection pool.
    pub fn open(path: &Path) -> Result<Self> {
        let manager = SqliteConnectionManager::file(path)
            .with_init(|conn| {
                conn.execute_batch(
                    "PRAGMA journal_mode=WAL;
                     PRAGMA busy_timeout=30000;
                     PRAGMA foreign_keys=ON;
                     PRAGMA synchronous=NORMAL;"
                )?;
                Ok(())
            });
        let pool = Pool::builder()
            .max_size(8)
            .build(manager)?;
        let db = Self { pool };
        db.init_schema()?;
        Ok(db)
    }

    /// Create an in-memory database for testing.
    pub fn in_memory() -> Result<Self> {
        let manager = SqliteConnectionManager::memory()
            .with_init(|conn| {
                conn.execute_batch("PRAGMA foreign_keys=ON;")?;
                Ok(())
            });
        let pool = Pool::builder()
            .max_size(1)  // in-memory DB can't be shared across connections
            .build(manager)?;
        let db = Self { pool };
        db.init_schema()?;
        Ok(db)
    }

    fn init_schema(&self) -> Result<()> {
        let conn = self.pool.get()?;
        conn.execute_batch(include_str!("schema.sql"))?;
        Ok(())
    }

    /// List all table names (for testing/diagnostics).
    pub fn table_names(&self) -> Result<Vec<String>> {
        let conn = self.pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )?;
        let names = stmt.query_map([], |row| row.get(0))?
            .collect::<std::result::Result<Vec<String>, _>>()?;
        Ok(names)
    }

    // ── Agent operations ─────────────────────────────────────────

    pub fn register_agent(&self, name: &str, role: &str) -> Result<()> {
        let conn = self.pool.get()?;
        conn.execute(
            "INSERT OR REPLACE INTO agents (name, role, status, last_heartbeat)
             VALUES (?1, ?2, 'IDLE', datetime('now'))",
            params![name, role],
        )?;
        Ok(())
    }

    pub fn heartbeat(&self, name: &str) -> Result<()> {
        let conn = self.pool.get()?;
        conn.execute(
            "UPDATE agents SET last_heartbeat = datetime('now') WHERE name = ?1",
            params![name],
        )?;
        Ok(())
    }

    pub fn get_agent(&self, name: &str) -> Result<Option<Agent>> {
        let conn = self.pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT name, role, status, last_heartbeat, current_task_id
             FROM agents WHERE name = ?1"
        )?;
        let agent = stmt.query_row(params![name], |row| {
            Ok(Agent {
                name: row.get(0)?,
                role: row.get(1)?,
                status: row.get(2)?,
                last_heartbeat: row.get(3)?,
                current_task_id: row.get(4)?,
            })
        }).optional()?;
        Ok(agent)
    }

    // ── Task operations ──────────────────────────────────────────

    pub fn post_task(
        &self, skill: &str, payload: &str, priority: i32, assigned_to: Option<&str>,
    ) -> Result<i64> {
        let conn = self.pool.get()?;
        conn.execute(
            "INSERT INTO tasks (type, payload_json, priority, assigned_to)
             VALUES (?1, ?2, ?3, ?4)",
            params![skill, payload, priority, assigned_to],
        )?;
        Ok(conn.last_insert_rowid())
    }

    /// Atomically claim the highest-priority pending task for the given role.
    /// Returns None if no tasks available.
    pub fn claim_task(&self, role: &str) -> Result<Option<Task>> {
        let conn = self.pool.get()?;
        // Atomic claim: UPDATE + SELECT in one statement
        // The subquery finds the best candidate, the UPDATE claims it
        let changed = conn.execute(
            "UPDATE tasks SET status = 'RUNNING',
                    assigned_to = (SELECT name FROM agents WHERE role = ?1
                                   AND status = 'IDLE' LIMIT 1)
             WHERE id = (
                SELECT id FROM tasks
                WHERE status = 'PENDING'
                  AND (assigned_to IS NULL
                       OR assigned_to IN (SELECT name FROM agents WHERE role = ?1))
                ORDER BY priority DESC, id ASC
                LIMIT 1
             ) AND status = 'PENDING'",
            params![role],
        )?;
        if changed == 0 {
            return Ok(None);
        }
        // Fetch the claimed task
        let task = conn.query_row(
            "SELECT id, created_at, assigned_to, status, priority, type,
                    payload_json, result_json, error, parent_id, depends_on,
                    intelligence_score
             FROM tasks
             WHERE status = 'RUNNING'
               AND assigned_to IN (SELECT name FROM agents WHERE role = ?1)
             ORDER BY id DESC LIMIT 1",
            params![role],
            Self::row_to_task,
        )?;
        // Mark agent as busy
        if let Some(ref agent) = task.assigned_to {
            conn.execute(
                "UPDATE agents SET status = 'BUSY', current_task_id = ?1 WHERE name = ?2",
                params![task.id, agent],
            )?;
        }
        Ok(Some(task))
    }

    pub fn complete_task(&self, id: i64, result_json: &str) -> Result<()> {
        let conn = self.pool.get()?;
        conn.execute(
            "UPDATE tasks SET status = 'DONE', result_json = ?1 WHERE id = ?2",
            params![result_json, id],
        )?;
        // Free the agent
        conn.execute(
            "UPDATE agents SET status = 'IDLE', current_task_id = NULL
             WHERE current_task_id = ?1",
            params![id],
        )?;
        Ok(())
    }

    pub fn fail_task(&self, id: i64, error: &str) -> Result<()> {
        let conn = self.pool.get()?;
        conn.execute(
            "UPDATE tasks SET status = 'FAILED', error = ?1 WHERE id = ?2",
            params![error, id],
        )?;
        conn.execute(
            "UPDATE agents SET status = 'IDLE', current_task_id = NULL
             WHERE current_task_id = ?1",
            params![id],
        )?;
        Ok(())
    }

    pub fn get_task(&self, id: i64) -> Result<Option<Task>> {
        let conn = self.pool.get()?;
        let task = conn.query_row(
            "SELECT id, created_at, assigned_to, status, priority, type,
                    payload_json, result_json, error, parent_id, depends_on,
                    intelligence_score
             FROM tasks WHERE id = ?1",
            params![id],
            Self::row_to_task,
        ).optional()?;
        Ok(task)
    }

    pub fn queue_depth(&self) -> Result<i64> {
        let conn = self.pool.get()?;
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM tasks WHERE status = 'PENDING'",
            [],
            |row| row.get(0),
        )?;
        Ok(count)
    }

    pub fn recover_stale_tasks(&self, timeout_secs: i64) -> Result<u64> {
        let conn = self.pool.get()?;
        let changed = conn.execute(
            "UPDATE tasks SET status = 'PENDING', assigned_to = NULL
             WHERE status = 'RUNNING'
               AND created_at < datetime('now', ?1 || ' seconds')",
            params![format!("-{}", timeout_secs)],
        )?;
        if changed > 0 {
            debug!("Recovered {} stale tasks", changed);
        }
        Ok(changed as u64)
    }

    // ── Helpers ──────────────────────────────────────────────────

    fn row_to_task(row: &rusqlite::Row) -> rusqlite::Result<Task> {
        Ok(Task {
            id: row.get(0)?,
            created_at: row.get(1)?,
            assigned_to: row.get(2)?,
            status: TaskStatus::from_db(&row.get::<_, String>(3)?),
            priority: row.get(4)?,
            skill: row.get::<_, Option<String>>(5)?.unwrap_or_default(),
            payload_json: row.get(6)?,
            result_json: row.get(7)?,
            error: row.get(8)?,
            parent_id: row.get(9)?,
            depends_on: row.get(10)?,
            intelligence_score: row.get(11)?,
        })
    }
}

// For rusqlite optional row
use rusqlite::OptionalExtension;
```

- [ ] **Step 4: Create schema.sql (matching Python db.py)**

```sql
-- biged-rs/crates/biged-core/src/schema.sql
CREATE TABLE IF NOT EXISTS agents (
    name            TEXT PRIMARY KEY,
    role            TEXT,
    status          TEXT DEFAULT 'IDLE',
    last_heartbeat  TEXT,
    current_task_id INTEGER
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT DEFAULT (datetime('now')),
    assigned_to  TEXT,
    status       TEXT DEFAULT 'PENDING',
    priority     INTEGER DEFAULT 5,
    type         TEXT NOT NULL,
    payload_json TEXT,
    result_json  TEXT,
    error        TEXT,
    parent_id    INTEGER,
    depends_on   TEXT,
    review_rounds INTEGER DEFAULT 0,
    conditions   TEXT,
    classification TEXT DEFAULT 'internal',
    intelligence_score REAL DEFAULT NULL,
    trace_id     TEXT DEFAULT NULL,
    FOREIGN KEY (parent_id) REFERENCES tasks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT,
    channel     TEXT DEFAULT 'fleet',
    body        TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now')),
    read        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS usage (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    skill               TEXT NOT NULL,
    model               TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_create_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL DEFAULT 0.0,
    task_id             INTEGER
);

CREATE TABLE IF NOT EXISTS locks (
    name       TEXT PRIMARY KEY,
    holder     TEXT,
    acquired   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS idle_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent      TEXT NOT NULL,
    skill      TEXT NOT NULL,
    cost_usd   REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS output_feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL,
    verdict    TEXT NOT NULL,
    result     TEXT,
    cost_usd   REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trusted_models (
    model       TEXT PRIMARY KEY,
    trusted_at  TEXT DEFAULT (datetime('now')),
    accept_count INTEGER DEFAULT 0,
    notes       TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(type);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_agent, read);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage(created_at);
```

- [ ] **Step 5: Run tests**

Run: `cd biged-rs && cargo test --test db_test`
Expected: all 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add biged-rs/crates/biged-core/src/db.rs biged-rs/crates/biged-core/src/schema.sql biged-rs/tests/db_test.rs
git commit -m "feat(core): database pool — r2d2 + rusqlite, atomic claim, WAL mode"
```

---

## Task 6: Task Queue (biged-core/queue.rs)

**Files:**
- Create: `biged-rs/crates/biged-core/src/queue.rs`
- Test: `biged-rs/tests/queue_test.rs`

- [ ] **Step 1: Write failing test**

```rust
// biged-rs/tests/queue_test.rs
use biged_core::db::Db;
use biged_core::queue::TaskQueue;

#[tokio::test]
async fn test_queue_send_receive() {
    let db = Db::in_memory().unwrap();
    db.register_agent("w1", "coder").unwrap();
    let queue = TaskQueue::new(db.clone(), 100);

    queue.submit("test_skill", r#"{"key":"value"}"#, 5).await.unwrap();

    let task = queue.next_task("coder").await.unwrap();
    assert!(task.is_some());
    assert_eq!(task.unwrap().skill, "test_skill");
}

#[tokio::test]
async fn test_queue_empty_returns_none() {
    let db = Db::in_memory().unwrap();
    db.register_agent("w1", "coder").unwrap();
    let queue = TaskQueue::new(db.clone(), 100);

    let task = queue.try_next_task("coder").unwrap();
    assert!(task.is_none());
}
```

- [ ] **Step 2: Implement TaskQueue**

```rust
// biged-rs/crates/biged-core/src/queue.rs
use crate::db::Db;
use crate::error::Result;
use crate::types::Task;
use tokio::sync::Notify;
use std::sync::Arc;

/// In-memory notification + DB-backed task queue.
/// Workers await notifications instead of polling.
pub struct TaskQueue {
    db: Db,
    notify: Arc<Notify>,
}

impl TaskQueue {
    pub fn new(db: Db, _capacity: usize) -> Self {
        Self {
            db,
            notify: Arc::new(Notify::new()),
        }
    }

    /// Submit a new task. Wakes any waiting workers.
    pub async fn submit(&self, skill: &str, payload: &str, priority: i32) -> Result<i64> {
        let id = self.db.post_task(skill, payload, priority, None)?;
        self.notify.notify_waiters();
        Ok(id)
    }

    /// Wait for and claim the next task for this role.
    /// Blocks until a task is available.
    pub async fn next_task(&self, role: &str) -> Result<Option<Task>> {
        loop {
            if let Some(task) = self.db.claim_task(role)? {
                return Ok(Some(task));
            }
            self.notify.notified().await;
        }
    }

    /// Try to claim a task without waiting. Returns None immediately if empty.
    pub fn try_next_task(&self, role: &str) -> Result<Option<Task>> {
        self.db.claim_task(role)
    }

    /// Get current queue depth.
    pub fn depth(&self) -> Result<i64> {
        self.db.queue_depth()
    }
}
```

- [ ] **Step 3: Make Db cloneable** (add `Clone` via Arc)

Add to `biged-rs/crates/biged-core/src/db.rs`:
```rust
impl Clone for Db {
    fn clone(&self) -> Self {
        Self { pool: self.pool.clone() }
    }
}
```

- [ ] **Step 4: Run tests**

Run: `cd biged-rs && cargo test --test queue_test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add biged-rs/crates/biged-core/src/queue.rs biged-rs/tests/queue_test.rs
git commit -m "feat(core): task queue — notify-based wakeup, DB-backed durability"
```

---

## Task 7: Event Bus (biged-supervisor/events.rs)

**Files:**
- Create: `biged-rs/crates/biged-supervisor/src/events.rs`

- [ ] **Step 1: Define FleetEvent enum**

```rust
// biged-rs/crates/biged-supervisor/src/events.rs
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum FleetEvent {
    TaskCompleted { id: i64, skill: String, agent: String },
    TaskFailed { id: i64, skill: String, error: String },
    AgentStateChange { agent: String, from: String, to: String },
    ThermalAlert { gpu_temp: f32, action: ThermalAction },
    ModelTransition { from: String, to: String, reason: String },
    ConfigReloaded,
    ScaleUp { count: u32, reason: String },
    ScaleDown { count: u32 },
    WorkerCrashed { agent: String, exit_code: Option<i32> },
    BackupCompleted { path: String, size_bytes: u64 },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ThermalAction {
    ThrottleDown,
    CooldownStart,
    CooldownEnd,
    EmergencyStop,
}

impl FleetEvent {
    pub fn event_type(&self) -> &'static str {
        match self {
            Self::TaskCompleted { .. } => "task_completed",
            Self::TaskFailed { .. } => "task_failed",
            Self::AgentStateChange { .. } => "agent_state",
            Self::ThermalAlert { .. } => "thermal",
            Self::ModelTransition { .. } => "model_transition",
            Self::ConfigReloaded => "config_reloaded",
            Self::ScaleUp { .. } => "scale_up",
            Self::ScaleDown { .. } => "scale_down",
            Self::WorkerCrashed { .. } => "worker_crashed",
            Self::BackupCompleted { .. } => "backup_completed",
        }
    }
}

/// Event bus using tokio broadcast channel.
pub type EventSender = tokio::sync::broadcast::Sender<FleetEvent>;
pub type EventReceiver = tokio::sync::broadcast::Receiver<FleetEvent>;

pub fn create_event_bus(capacity: usize) -> (EventSender, EventReceiver) {
    tokio::sync::broadcast::channel(capacity)
}
```

- [ ] **Step 2: Commit**

```bash
git add biged-rs/crates/biged-supervisor/src/events.rs
git commit -m "feat(supervisor): event bus — FleetEvent enum + broadcast channel"
```

---

## Task 8: Thermal Monitor (biged-supervisor/thermal.rs)

**Files:**
- Create: `biged-rs/crates/biged-supervisor/src/thermal.rs`

- [ ] **Step 1: Implement ThermalMonitor**

```rust
// biged-rs/crates/biged-supervisor/src/thermal.rs
use crate::events::{EventSender, FleetEvent, ThermalAction};
use biged_core::config::FleetConfig;
use biged_core::types::HwState;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{info, warn, debug};

pub struct ThermalMonitor {
    config: Arc<RwLock<FleetConfig>>,
    events: EventSender,
    fleet_dir: PathBuf,
    last_gpu_temp: f32,
    cooldown_until: Option<std::time::Instant>,
}

impl ThermalMonitor {
    pub fn new(
        config: Arc<RwLock<FleetConfig>>,
        events: EventSender,
        fleet_dir: PathBuf,
    ) -> Self {
        Self {
            config,
            events,
            fleet_dir,
            last_gpu_temp: 0.0,
            cooldown_until: None,
        }
    }

    /// Main thermal monitoring loop — runs as a tokio task.
    pub async fn run(&mut self) {
        info!("Thermal monitor started");
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(5));

        loop {
            interval.tick().await;

            match self.poll_once().await {
                Ok(state) => {
                    self.write_hw_state(&state).await;
                    self.check_thresholds(&state).await;
                }
                Err(e) => {
                    debug!("Thermal poll failed: {}", e);
                }
            }
        }
    }

    async fn poll_once(&mut self) -> anyhow::Result<HwState> {
        let (gpu_temp, gpu_vram_used, gpu_vram_total, gpu_power) = self.read_gpu()?;
        let cpu_temp = self.read_cpu_temp();
        let (ram_used, ram_total) = self.read_ram();
        let loaded_models = self.read_ollama_models().await;
        let config = self.config.read().await;

        self.last_gpu_temp = gpu_temp;

        Ok(HwState {
            status: "ready".into(),
            gpu_temp_c: gpu_temp,
            gpu_vram_used_gb: gpu_vram_used,
            gpu_vram_total_gb: gpu_vram_total,
            gpu_power_w: gpu_power,
            cpu_temp_c: cpu_temp,
            ram_used_gb: ram_used,
            ram_total_gb: ram_total,
            loaded_models,
            thermal_state: if gpu_temp > config.thermal.gpu_target_c as f32 {
                "throttled".into()
            } else {
                "ready".into()
            },
            current_model: config.models.conductor_model.clone(),
        })
    }

    fn read_gpu(&self) -> anyhow::Result<(f32, f64, f64, f32)> {
        #[cfg(feature = "nvidia")]
        {
            use nvml_wrapper::Nvml;
            let nvml = Nvml::init()?;
            let device = nvml.device_by_index(0)?;
            let temp = device.temperature(nvml_wrapper::enum_wrappers::device::TemperatureSensor::Gpu)? as f32;
            let mem = device.memory_info()?;
            let power = device.power_usage()? as f32 / 1000.0; // mW to W
            Ok((
                temp,
                mem.used as f64 / 1_073_741_824.0,
                mem.total as f64 / 1_073_741_824.0,
                power,
            ))
        }
        #[cfg(not(feature = "nvidia"))]
        {
            Ok((0.0, 0.0, 0.0, 0.0))
        }
    }

    fn read_cpu_temp(&self) -> f32 {
        // Platform-specific CPU temp reading
        // Windows: WMI query or fallback to 0
        // Linux: /sys/class/thermal/thermal_zone0/temp
        0.0 // placeholder — implemented per-platform
    }

    fn read_ram(&self) -> (f64, f64) {
        // sysinfo crate or platform-specific
        (0.0, 0.0) // placeholder
    }

    async fn read_ollama_models(&self) -> Vec<String> {
        match reqwest::Client::new()
            .get("http://localhost:11434/api/ps")
            .timeout(std::time::Duration::from_secs(3))
            .send()
            .await
        {
            Ok(resp) => {
                if let Ok(body) = resp.json::<serde_json::Value>().await {
                    body.get("models")
                        .and_then(|m| m.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|m| m.get("name").and_then(|n| n.as_str()))
                                .map(String::from)
                                .collect()
                        })
                        .unwrap_or_default()
                } else {
                    vec![]
                }
            }
            Err(_) => vec![],
        }
    }

    async fn write_hw_state(&self, state: &HwState) {
        let path = self.fleet_dir.join("hw_state.json");
        let tmp = self.fleet_dir.join(".hw_state.tmp");
        if let Ok(json) = serde_json::to_string_pretty(state) {
            if let Ok(()) = tokio::fs::write(&tmp, &json).await {
                let _ = tokio::fs::rename(&tmp, &path).await;
            }
        }
    }

    async fn check_thresholds(&mut self, state: &HwState) {
        let config = self.config.read().await;
        let target = config.thermal.gpu_target_c as f32;

        if state.gpu_temp_c > target + 10.0 {
            warn!("GPU temp {}C exceeds emergency threshold", state.gpu_temp_c);
            let _ = self.events.send(FleetEvent::ThermalAlert {
                gpu_temp: state.gpu_temp_c,
                action: ThermalAction::EmergencyStop,
            });
        } else if state.gpu_temp_c > target {
            debug!("GPU temp {}C above target {}C — throttling", state.gpu_temp_c, target);
            let _ = self.events.send(FleetEvent::ThermalAlert {
                gpu_temp: state.gpu_temp_c,
                action: ThermalAction::ThrottleDown,
            });
        }
    }
}
```

- [ ] **Step 2: Add nvidia feature to biged-supervisor Cargo.toml**

```toml
[features]
default = ["nvidia"]
nvidia = ["nvml-wrapper"]

[dependencies]
nvml-wrapper = { workspace = true, optional = true }
```

- [ ] **Step 3: Commit**

```bash
git add biged-rs/crates/biged-supervisor/src/thermal.rs
git commit -m "feat(supervisor): thermal monitor — GPU/CPU polling, hw_state.json, threshold alerts"
```

---

## Task 9: Health Checks + Stale Recovery (biged-supervisor/health.rs)

**Files:**
- Create: `biged-rs/crates/biged-supervisor/src/health.rs`

- [ ] **Step 1: Implement health module**

```rust
// biged-rs/crates/biged-supervisor/src/health.rs
use crate::events::{EventSender, FleetEvent};
use biged_core::db::Db;
use tracing::{info, warn};

pub struct HealthChecker {
    db: Db,
    events: EventSender,
}

impl HealthChecker {
    pub fn new(db: Db, events: EventSender) -> Self {
        Self { db, events }
    }

    /// Recover tasks stuck in RUNNING state for too long.
    pub async fn recover_stale_tasks(&self, timeout_secs: i64) -> anyhow::Result<u64> {
        let recovered = self.db.recover_stale_tasks(timeout_secs)?;
        if recovered > 0 {
            info!("Recovered {} stale tasks (timeout={}s)", recovered, timeout_secs);
        }
        Ok(recovered)
    }

    /// Check agent heartbeats — mark agents with stale heartbeats as offline.
    pub async fn check_agent_health(&self, stale_secs: i64) -> anyhow::Result<()> {
        // Agents that haven't heartbeated in stale_secs are marked offline
        let conn = self.db.pool_ref().get()?;
        let stale_agents: Vec<String> = {
            let mut stmt = conn.prepare(
                "SELECT name FROM agents
                 WHERE last_heartbeat < datetime('now', ?1 || ' seconds')
                   AND status != 'OFFLINE'"
            )?;
            stmt.query_map(
                rusqlite::params![format!("-{}", stale_secs)],
                |row| row.get(0),
            )?
            .collect::<Result<Vec<String>, _>>()?
        };

        for agent in &stale_agents {
            conn.execute(
                "UPDATE agents SET status = 'OFFLINE' WHERE name = ?1",
                rusqlite::params![agent],
            )?;
            warn!("Agent {} marked offline (no heartbeat for {}s)", agent, stale_secs);
            let _ = self.events.send(FleetEvent::AgentStateChange {
                agent: agent.clone(),
                from: "IDLE".into(),
                to: "OFFLINE".into(),
            });
        }
        Ok(())
    }
}
```

Note: This requires adding a `pool_ref()` method to Db:
```rust
// Add to biged-core/src/db.rs
pub fn pool_ref(&self) -> &Pool<SqliteConnectionManager> {
    &self.pool
}
```

- [ ] **Step 2: Commit**

```bash
git add biged-rs/crates/biged-supervisor/src/health.rs
git commit -m "feat(supervisor): health checks — stale task recovery, agent heartbeat monitoring"
```

---

## Task 10: Supervisor Main Loop (biged-supervisor/supervisor.rs)

**Files:**
- Create: `biged-rs/crates/biged-supervisor/src/supervisor.rs`
- Test: `biged-rs/tests/supervisor_test.rs`

- [ ] **Step 1: Write integration test**

```rust
// biged-rs/tests/supervisor_test.rs
use biged_core::db::Db;
use biged_core::config::FleetConfig;
use std::sync::Arc;
use tokio::sync::RwLock;

#[tokio::test]
async fn test_supervisor_starts_and_stops() {
    let db = Db::in_memory().unwrap();
    let config = Arc::new(RwLock::new(FleetConfig::default()));
    let (tx, _rx) = biged_supervisor::events::create_event_bus(100);

    let supervisor = biged_supervisor::supervisor::Supervisor::new(
        config, db, tx, std::path::PathBuf::from("."),
    );

    // Start supervisor, let it run for 2 seconds, then stop
    let handle = tokio::spawn(async move {
        supervisor.run().await
    });

    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    handle.abort();
    // If we get here without panic, supervisor ran cleanly
}
```

- [ ] **Step 2: Implement Supervisor**

```rust
// biged-rs/crates/biged-supervisor/src/supervisor.rs
use crate::events::{create_event_bus, EventSender, FleetEvent};
use crate::health::HealthChecker;
use crate::thermal::ThermalMonitor;
use biged_core::config::FleetConfig;
use biged_core::db::Db;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{info, warn, error};

pub struct Supervisor {
    config: Arc<RwLock<FleetConfig>>,
    db: Db,
    events: EventSender,
    fleet_dir: PathBuf,
}

impl Supervisor {
    pub fn new(
        config: Arc<RwLock<FleetConfig>>,
        db: Db,
        events: EventSender,
        fleet_dir: PathBuf,
    ) -> Self {
        Self { config, db, events, fleet_dir }
    }

    pub async fn run(self) -> anyhow::Result<()> {
        info!("Supervisor starting — fleet_dir={}", self.fleet_dir.display());

        let health = HealthChecker::new(self.db.clone(), self.events.clone());
        let mut thermal = ThermalMonitor::new(
            self.config.clone(),
            self.events.clone(),
            self.fleet_dir.clone(),
        );

        // Launch all subsystem tasks
        tokio::select! {
            r = self.stale_recovery_loop(&health) => {
                error!("Stale recovery loop exited: {:?}", r);
            }
            r = self.agent_health_loop(&health) => {
                error!("Agent health loop exited: {:?}", r);
            }
            r = self.heartbeat_loop() => {
                error!("Heartbeat loop exited: {:?}", r);
            }
            r = thermal.run() => {
                error!("Thermal monitor exited");
            }
            r = self.config_reload_loop() => {
                error!("Config reload loop exited: {:?}", r);
            }
        }

        Ok(())
    }

    async fn stale_recovery_loop(&self, health: &HealthChecker) -> anyhow::Result<()> {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(300));
        loop {
            interval.tick().await;
            if let Err(e) = health.recover_stale_tasks(600).await {
                warn!("Stale recovery failed: {}", e);
            }
        }
    }

    async fn agent_health_loop(&self, health: &HealthChecker) -> anyhow::Result<()> {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(30));
        loop {
            interval.tick().await;
            if let Err(e) = health.check_agent_health(300).await {
                warn!("Agent health check failed: {}", e);
            }
        }
    }

    async fn heartbeat_loop(&self) -> anyhow::Result<()> {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(30));
        let heartbeat_path = self.fleet_dir.join(".supervisor_heartbeat");
        loop {
            interval.tick().await;
            let content = serde_json::json!({
                "pid": std::process::id(),
                "ts": chrono::Utc::now().timestamp(),
                "status": "watching",
            });
            let _ = tokio::fs::write(&heartbeat_path, content.to_string()).await;
        }
    }

    async fn config_reload_loop(&self) -> anyhow::Result<()> {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(300));
        let config_path = self.fleet_dir.join("fleet.toml");
        loop {
            interval.tick().await;
            match FleetConfig::from_file(&config_path) {
                Ok(new_config) => {
                    let mut cfg = self.config.write().await;
                    *cfg = new_config;
                    let _ = self.events.send(FleetEvent::ConfigReloaded);
                    info!("Config reloaded from {}", config_path.display());
                }
                Err(e) => {
                    warn!("Config reload failed: {}", e);
                }
            }
        }
    }
}

/// Top-level entry point called from main.rs
pub async fn run() -> anyhow::Result<()> {
    let fleet_dir = std::env::current_dir()?.join("fleet");
    let config_path = fleet_dir.join("fleet.toml");

    let config = if config_path.exists() {
        FleetConfig::from_file(&config_path)?
    } else {
        warn!("fleet.toml not found, using defaults");
        FleetConfig::default()
    };

    let db_path = fleet_dir.join("fleet.db");
    let db = Db::open(&db_path)?;

    let (tx, _rx) = create_event_bus(256);
    let config = Arc::new(RwLock::new(config));

    let supervisor = Supervisor::new(config, db, tx, fleet_dir);
    supervisor.run().await
}
```

- [ ] **Step 3: Add chrono dependency**

Add to workspace Cargo.toml:
```toml
chrono = { version = "0.4", features = ["serde"] }
```

Add to biged-supervisor/Cargo.toml:
```toml
chrono.workspace = true
```

- [ ] **Step 4: Run full test suite**

Run: `cd biged-rs && cargo test`
Expected: all tests pass

- [ ] **Step 5: Run the binary against the real fleet**

Run: `cd biged-rs && cargo run -- supervisor`
Expected: starts, reads fleet.toml, opens fleet.db, writes hw_state.json, heartbeat file appears. Ctrl+C stops cleanly.

- [ ] **Step 6: Commit**

```bash
git add biged-rs/
git commit -m "feat(supervisor): unified supervisor — tokio task tree, thermal, health, config reload"
```

---

## Task 11: Smoke Test Parity

**Files:**
- Create: `biged-rs/tests/smoke_test.rs`

- [ ] **Step 1: Write parity smoke tests**

```rust
// biged-rs/tests/smoke_test.rs
use biged_core::config::FleetConfig;
use biged_core::db::Db;

#[test]
fn smoke_config_parses() {
    let fleet_toml = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent().unwrap().join("fleet/fleet.toml");
    if fleet_toml.exists() {
        FleetConfig::from_file(&fleet_toml).expect("fleet.toml should parse");
    }
}

#[test]
fn smoke_db_roundtrip() {
    let db = Db::in_memory().unwrap();
    db.register_agent("test", "coder").unwrap();
    let id = db.post_task("test_skill", "{}", 5, None).unwrap();
    assert!(id > 0);
    let task = db.claim_task("coder").unwrap();
    assert!(task.is_some());
    db.complete_task(id, r#"{"ok":true}"#).unwrap();
    let t = db.get_task(id).unwrap().unwrap();
    assert_eq!(t.status, biged_core::types::TaskStatus::Done);
}

#[test]
fn smoke_queue_depth() {
    let db = Db::in_memory().unwrap();
    assert_eq!(db.queue_depth().unwrap(), 0);
    db.post_task("a", "{}", 5, None).unwrap();
    assert_eq!(db.queue_depth().unwrap(), 1);
}

#[tokio::test]
async fn smoke_event_bus() {
    let (tx, mut rx) = biged_supervisor::events::create_event_bus(10);
    tx.send(biged_supervisor::events::FleetEvent::ConfigReloaded).unwrap();
    let event = rx.recv().await.unwrap();
    assert!(matches!(event, biged_supervisor::events::FleetEvent::ConfigReloaded));
}
```

- [ ] **Step 2: Run smoke tests**

Run: `cd biged-rs && cargo test --test smoke_test`
Expected: 4/4 PASS

- [ ] **Step 3: Final commit**

```bash
git add biged-rs/tests/smoke_test.rs
git commit -m "test: Phase 0+1 smoke tests — config, DB roundtrip, queue, event bus"
```

---

## Gate Criteria

Phase 0+1 is complete when:
- [ ] `cargo test` passes all tests (config, db, queue, supervisor, smoke)
- [ ] `cargo run -- supervisor` starts, reads real fleet.toml + fleet.db, writes hw_state.json
- [ ] `cargo clippy` has zero warnings
- [ ] `cargo fmt --check` passes
- [ ] Supervisor runs for 1 hour without crash
- [ ] Python supervisor.py can still run alongside (same DB, no conflicts)
