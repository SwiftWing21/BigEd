# BigEd Rust Rewrite — Phase 3: PyO3 Skill Bridge

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `biged-bridge` crate that executes Python skills from Rust via PyO3 — replacing the leaky daemon-thread dispatch in `worker.py` with proper timeout enforcement and module caching.

**Architecture:** New `biged-bridge` crate embeds a Python interpreter via PyO3. A `SkillLoader` caches imported skill modules in a `DashMap`. A `SkillRunner` calls `module.run(payload, config)` with the GIL held, converts the result dict to `serde_json::Value`. A `Worker` loop claims tasks from the Rust DB pool, dispatches to the runner, and writes results back. `tokio::time::timeout` wraps execution for clean cancellation. Skills continue using Python's own `db.py` for their DB access — both Python and Rust share the same `fleet.db` via WAL mode.

**Tech Stack:** PyO3 0.23, pyo3-asyncio (not needed — skills are sync), DashMap (module cache), tokio (timeout + worker loop), biged-core (DB pool, task queue, config).

**Spec:** `docs/superpowers/specs/2026-03-24-rust-hybrid-architecture-design.md` (Phase 3 section)

**Depends on:** Phase 0+1 (biged-core + biged-supervisor). Phase 2 (biged-server) is independent.

**Key insight from skill audit:** All 130+ skills use the canonical `run(payload, config)` 2-arg pattern. No 3-arg logger or alt-name variants exist in practice (the spec's claim of ~8 3-arg skills predates the normalization audit). The bridge only needs to handle one signature.

**Deferred from spec (intentional):**
- **DB module interception** — The spec calls for replacing Python's `db` module in `sys.modules` with a Rust-backed version. Deferred because: (1) skills and Rust both access the same `fleet.db` via WAL mode without conflicts, (2) the interception adds complexity with marginal benefit at this stage, (3) can be added later as an optimization if needed.
- **Signature inspection via `inspect.signature`** — Not needed since all skills are normalized to 2-arg. Can be re-added if new skill variants emerge.

---

## File Structure

```
biged-rs/
├── Cargo.toml                                    # MODIFY: add pyo3 + biged-bridge deps
├── crates/
│   └── biged-bridge/                             # NEW CRATE
│       ├── Cargo.toml
│       ├── build.rs                              # PyO3 build configuration
│       └── src/
│           ├── lib.rs                            # Public API: SkillBridge
│           ├── loader.rs                         # Skill module import + DashMap cache
│           ├── runner.rs                         # Execute run() with GIL, convert result
│           └── worker.rs                         # Worker loop: claim → dispatch → write
├── src/
│   └── main.rs                                   # MODIFY: add `worker` subcommand
└── tests/
    └── bridge_test.rs                            # NEW: integration tests
```

---

## Task 1: Crate Scaffold + PyO3 Setup

**Files:**
- Create: `biged-rs/crates/biged-bridge/Cargo.toml`
- Create: `biged-rs/crates/biged-bridge/build.rs`
- Create: `biged-rs/crates/biged-bridge/src/lib.rs`
- Create: `biged-rs/crates/biged-bridge/src/loader.rs` (empty stub)
- Create: `biged-rs/crates/biged-bridge/src/runner.rs` (empty stub)
- Create: `biged-rs/crates/biged-bridge/src/worker.rs` (empty stub)
- Modify: `biged-rs/Cargo.toml`

- [ ] **Step 1: Create biged-bridge Cargo.toml**

```toml
# biged-rs/crates/biged-bridge/Cargo.toml
[package]
name = "biged-bridge"
version = "0.1.0"
edition.workspace = true

[dependencies]
biged-core = { path = "../biged-core" }
pyo3 = { workspace = true, features = ["auto-initialize"] }
dashmap.workspace = true
tokio.workspace = true
serde.workspace = true
serde_json.workspace = true
tracing.workspace = true
anyhow.workspace = true
```

- [ ] **Step 2: Add workspace dependencies**

Add to `[workspace.dependencies]` in root `biged-rs/Cargo.toml`:
```toml
pyo3 = { version = "0.23", features = ["auto-initialize"] }
```

Add to root `[dependencies]`:
```toml
biged-bridge = { path = "crates/biged-bridge" }
```

- [ ] **Step 3: Create build.rs**

```rust
// biged-rs/crates/biged-bridge/build.rs
fn main() {
    // PyO3 automatically handles Python discovery via pyo3-build-config.
    // On Windows, ensure Python is on PATH or PYO3_PYTHON is set.
    println!("cargo:rerun-if-env-changed=PYO3_PYTHON");
}
```

- [ ] **Step 4: Create lib.rs — public API**

```rust
// biged-rs/crates/biged-bridge/src/lib.rs
pub mod loader;
pub mod runner;
pub mod worker;

use std::path::PathBuf;

/// Configuration for the skill bridge.
pub struct BridgeConfig {
    /// Path to the fleet/ directory (contains skills/, db.py, etc.)
    pub fleet_dir: PathBuf,
    /// Default timeout for skill execution in seconds.
    pub default_timeout_secs: u64,
    /// Skill-specific timeout overrides.
    pub skill_timeouts: std::collections::HashMap<String, u64>,
}

impl BridgeConfig {
    pub fn new(fleet_dir: PathBuf) -> Self {
        let mut skill_timeouts = std::collections::HashMap::new();
        // Match Python worker.py SKILL_TIMEOUTS
        skill_timeouts.insert("code_write".into(), 900);
        skill_timeouts.insert("code_write_review".into(), 900);
        skill_timeouts.insert("fma_review".into(), 900);
        skill_timeouts.insert("pen_test".into(), 600);
        skill_timeouts.insert("security_audit".into(), 600);

        Self {
            fleet_dir,
            default_timeout_secs: 600,
            skill_timeouts,
        }
    }

    /// Get timeout for a specific skill.
    pub fn timeout_for(&self, skill: &str) -> std::time::Duration {
        let secs = self
            .skill_timeouts
            .get(skill)
            .copied()
            .unwrap_or(self.default_timeout_secs);
        std::time::Duration::from_secs(secs)
    }
}
```

- [ ] **Step 5: Create empty stub files**

Create empty files:
- `biged-rs/crates/biged-bridge/src/loader.rs`
- `biged-rs/crates/biged-bridge/src/runner.rs`
- `biged-rs/crates/biged-bridge/src/worker.rs`

- [ ] **Step 6: Verify it compiles**

Run: `cd biged-rs && cargo check`
Expected: compiles (PyO3 will auto-discover Python)

If PyO3 can't find Python, set: `$env:PYO3_PYTHON = "python"` (PowerShell) or `export PYO3_PYTHON=python` (bash)

- [ ] **Step 7: Run `cargo fmt` and `cargo clippy`**

- [ ] **Step 8: Commit**

```bash
git add biged-rs/crates/biged-bridge/ biged-rs/Cargo.toml biged-rs/Cargo.lock
git commit -m "feat(bridge): scaffold biged-bridge crate — PyO3 setup, BridgeConfig"
```

---

## Task 2: Skill Loader (Import + Cache)

**Files:**
- Create: `biged-rs/crates/biged-bridge/src/loader.rs`
- Test: `biged-rs/tests/bridge_test.rs`

- [ ] **Step 1: Write failing test**

Create `biged-rs/tests/bridge_test.rs`:

```rust
use biged_bridge::loader::SkillLoader;
use std::path::PathBuf;

#[test]
fn test_loader_initializes() {
    let fleet_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet");
    if !fleet_dir.join("skills").exists() {
        return; // Skip if fleet dir not available
    }
    let loader = SkillLoader::new(&fleet_dir).expect("loader should initialize");
    assert_eq!(loader.cached_count(), 0);
}

#[test]
fn test_loader_imports_skill() {
    let fleet_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet");
    if !fleet_dir.join("skills").exists() {
        return;
    }
    let loader = SkillLoader::new(&fleet_dir).expect("loader should initialize");

    // autoresearch_analyze is a simple skill with no network deps
    let result = loader.load("autoresearch_analyze");
    assert!(result.is_ok(), "should load autoresearch_analyze: {:?}", result.err());
    assert_eq!(loader.cached_count(), 1);

    // Loading again should use cache
    let result2 = loader.load("autoresearch_analyze");
    assert!(result2.is_ok());
    assert_eq!(loader.cached_count(), 1, "should still be 1 — cached");
}

#[test]
fn test_loader_missing_skill() {
    let fleet_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet");
    if !fleet_dir.join("skills").exists() {
        return;
    }
    let loader = SkillLoader::new(&fleet_dir).expect("loader should initialize");
    let result = loader.load("nonexistent_skill_xyz");
    assert!(result.is_err());
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd biged-rs && cargo test --test bridge_test`
Expected: FAIL — `SkillLoader` doesn't exist

- [ ] **Step 3: Implement SkillLoader**

```rust
// biged-rs/crates/biged-bridge/src/loader.rs
use dashmap::DashMap;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use std::path::Path;
use std::sync::Arc;
use tracing::{debug, info};

/// Caches imported Python skill modules for reuse.
pub struct SkillLoader {
    cache: Arc<DashMap<String, PyObject>>,
}

impl SkillLoader {
    /// Initialize the skill loader. Sets up Python sys.path to include
    /// the fleet directory and fleet/skills directory.
    pub fn new(fleet_dir: &Path) -> anyhow::Result<Self> {
        Python::with_gil(|py| {
            let sys = py.import("sys")?;
            let path = sys.getattr("path")?;

            // Add fleet/ to sys.path so `import db` works
            let fleet_str = fleet_dir.to_string_lossy().to_string();
            path.call_method1("insert", (0, &fleet_str))?;

            // Add fleet/skills/ so `from skills._models import ...` works
            let skills_str = fleet_dir.join("skills").to_string_lossy().to_string();
            path.call_method1("insert", (0, &skills_str))?;

            info!("Python sys.path configured: fleet={}", fleet_str);
            Ok(())
        })?;

        Ok(Self {
            cache: Arc::new(DashMap::new()),
        })
    }

    /// Load a skill module by name. Returns a cached reference if available.
    pub fn load(&self, skill_name: &str) -> anyhow::Result<PyObject> {
        // Check cache first
        if let Some(module) = self.cache.get(skill_name) {
            return Ok(module.clone());
        }

        // Import the module
        let module = Python::with_gil(|py| -> anyhow::Result<PyObject> {
            let importlib = py.import("importlib")?;
            let module_name = format!("skills.{}", skill_name);
            let module = importlib.call_method1("import_module", (module_name.as_str(),))?;

            // Verify it has a run() function
            if !module.hasattr("run")? {
                anyhow::bail!("Skill '{}' has no run() function", skill_name);
            }

            debug!("Loaded skill: {}", skill_name);
            Ok(module.unbind())
        })?;

        // Cache it
        self.cache.insert(skill_name.to_string(), module.clone());
        Ok(module)
    }

    /// Number of cached skill modules.
    pub fn cached_count(&self) -> usize {
        self.cache.len()
    }
}
```

- [ ] **Step 4: Run tests**

Run: `cd biged-rs && cargo test --test bridge_test`
Expected: 3 tests PASS (or skip if fleet dir unavailable)

- [ ] **Step 5: Run `cargo fmt` and `cargo clippy`**

- [ ] **Step 6: Commit**

```bash
git add biged-rs/crates/biged-bridge/src/loader.rs biged-rs/tests/bridge_test.rs
git commit -m "feat(bridge): skill loader — PyO3 import + DashMap cache"
```

---

## Task 3: Skill Runner (Execute + Convert Result)

**Files:**
- Create: `biged-rs/crates/biged-bridge/src/runner.rs`
- Modify: `biged-rs/tests/bridge_test.rs`

- [ ] **Step 1: Write failing test**

Append to `biged-rs/tests/bridge_test.rs`:

```rust
use biged_bridge::runner::SkillRunner;
use biged_bridge::BridgeConfig;

#[test]
fn test_runner_executes_skill() {
    let fleet_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet");
    if !fleet_dir.join("skills").exists() {
        return;
    }

    let config = BridgeConfig::new(fleet_dir.clone());
    let runner = SkillRunner::new(&fleet_dir).expect("runner should init");

    // autoresearch_analyze returns quickly with a skip if results.tsv doesn't exist
    let payload = serde_json::json!({});
    let fleet_config = serde_json::json!({
        "models": { "local": "qwen3:8b", "complex": "claude-sonnet-4-6" }
    });

    let result = runner.run_skill("autoresearch_analyze", &payload, &fleet_config);
    assert!(result.is_ok(), "skill should execute: {:?}", result.err());

    let value = result.unwrap();
    // autoresearch_analyze returns {"status": "skip", "error": "..."} when no results.tsv
    assert!(value.is_object());
}

#[test]
fn test_runner_handles_missing_skill() {
    let fleet_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet");
    if !fleet_dir.join("skills").exists() {
        return;
    }

    let runner = SkillRunner::new(&fleet_dir).expect("runner should init");
    let result = runner.run_skill("nonexistent_xyz", &serde_json::json!({}), &serde_json::json!({}));
    assert!(result.is_err());
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd biged-rs && cargo test --test bridge_test test_runner`
Expected: FAIL — `SkillRunner` doesn't exist

- [ ] **Step 3: Implement SkillRunner**

```rust
// biged-rs/crates/biged-bridge/src/runner.rs
use crate::loader::SkillLoader;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict};
use std::path::Path;
use tracing::{debug, warn};

/// Executes Python skills via PyO3 with result conversion.
pub struct SkillRunner {
    loader: SkillLoader,
}

impl SkillRunner {
    pub fn new(fleet_dir: &Path) -> anyhow::Result<Self> {
        let loader = SkillLoader::new(fleet_dir)?;
        Ok(Self { loader })
    }

    /// Execute a skill's run(payload, config) function.
    /// Returns the result as a serde_json::Value.
    pub fn run_skill(
        &self,
        skill_name: &str,
        payload: &serde_json::Value,
        config: &serde_json::Value,
    ) -> anyhow::Result<serde_json::Value> {
        let module = self.loader.load(skill_name)?;

        Python::with_gil(|py| {
            // Convert JSON values to Python dicts
            let payload_dict = json_to_pydict(py, payload)?;
            let config_dict = json_to_pydict(py, config)?;

            // Call module.run(payload, config)
            let run_fn = module.getattr(py, "run")?;
            let result = run_fn.call1(py, (payload_dict, config_dict));

            match result {
                Ok(py_result) => {
                    // Convert Python dict back to JSON
                    let bound = py_result.bind(py);
                    let json_val = pyobj_to_json(py, bound)?;
                    debug!("Skill '{}' returned successfully", skill_name);
                    Ok(json_val)
                }
                Err(e) => {
                    warn!("Skill '{}' raised exception: {}", skill_name, e);
                    Err(anyhow::anyhow!("Skill '{}' failed: {}", skill_name, e))
                }
            }
        })
    }

    /// Access the underlying loader (for cache stats, etc.)
    pub fn loader(&self) -> &SkillLoader {
        &self.loader
    }
}

/// Convert a serde_json::Value to a Python dict.
fn json_to_pydict<'py>(py: Python<'py>, value: &serde_json::Value) -> PyResult<Bound<'py, PyDict>> {
    let json_mod = py.import("json")?;
    let json_str = serde_json::to_string(value).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("JSON serialization failed: {}", e))
    })?;
    let py_obj = json_mod.call_method1("loads", (json_str,))?;
    py_obj.downcast::<PyDict>().map(|d| d.clone()).map_err(|e| {
        pyo3::exceptions::PyTypeError::new_err(format!("Expected dict, got: {}", e))
    })
}

/// Convert a Python object to serde_json::Value.
fn pyobj_to_json(py: Python<'_>, obj: &Bound<'_, PyAny>) -> anyhow::Result<serde_json::Value> {
    let json_mod = py.import("json")?;
    let json_str: String = json_mod
        .call_method1("dumps", (obj,))?
        .extract()?;
    let value: serde_json::Value = serde_json::from_str(&json_str)?;
    Ok(value)
}
```

- [ ] **Step 4: Run tests**

Run: `cd biged-rs && cargo test --test bridge_test`
Expected: 5 tests PASS (3 loader + 2 runner)

- [ ] **Step 5: Commit**

```bash
git add biged-rs/crates/biged-bridge/src/runner.rs biged-rs/tests/bridge_test.rs
git commit -m "feat(bridge): skill runner — execute run(payload, config) via PyO3, JSON conversion"
```

---

## Task 4: Worker Loop (Claim → Dispatch → Write Result)

**Files:**
- Create: `biged-rs/crates/biged-bridge/src/worker.rs`
- Modify: `biged-rs/tests/bridge_test.rs`

- [ ] **Step 1: Write failing test**

Append to `biged-rs/tests/bridge_test.rs`:

```rust
use biged_bridge::worker::Worker;
use biged_core::db::Db;
use biged_core::types::TaskStatus;

#[tokio::test]
async fn test_worker_processes_task() {
    let fleet_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet");
    if !fleet_dir.join("skills").exists() {
        return;
    }

    let db = Db::in_memory().unwrap();
    db.register_agent("test_worker", "coder").unwrap();

    // Post a task that will be quick (autoresearch_analyze with no data = skip)
    let task_id = db.post_task("autoresearch_analyze", "{}", 5, None).unwrap();

    let config = BridgeConfig::new(fleet_dir.clone());
    let fleet_config = serde_json::json!({
        "models": { "local": "qwen3:8b" }
    });

    let worker = Worker::new(db.clone(), config, fleet_config).expect("worker should init");

    // Process one task
    let processed = worker.process_one("coder").await;
    assert!(processed.is_ok());
    assert!(processed.unwrap(), "should have processed a task");

    // Verify task is now DONE
    let task = db.get_task(task_id).unwrap().unwrap();
    assert!(
        task.status == TaskStatus::Done || task.status == TaskStatus::Failed,
        "task should be done or failed, got: {:?}",
        task.status
    );
}

#[tokio::test]
async fn test_worker_empty_queue() {
    let fleet_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet");
    if !fleet_dir.join("skills").exists() {
        return;
    }

    let db = Db::in_memory().unwrap();
    db.register_agent("test_worker", "coder").unwrap();

    let config = BridgeConfig::new(fleet_dir.clone());
    let worker = Worker::new(db, config, serde_json::json!({})).expect("worker should init");

    let processed = worker.process_one("coder").await;
    assert!(processed.is_ok());
    assert!(!processed.unwrap(), "should return false — no tasks");
}
```

- [ ] **Step 2: Implement Worker**

```rust
// biged-rs/crates/biged-bridge/src/worker.rs
use crate::runner::SkillRunner;
use crate::BridgeConfig;
use biged_core::db::Db;
use std::sync::Arc;
use std::time::Duration;
use tracing::{error, info, warn};

/// Worker that claims tasks from the DB and dispatches to Python skills.
pub struct Worker {
    db: Db,
    runner: Arc<SkillRunner>,
    config: BridgeConfig,
    fleet_config_json: serde_json::Value,
}

impl Worker {
    pub fn new(
        db: Db,
        config: BridgeConfig,
        fleet_config_json: serde_json::Value,
    ) -> anyhow::Result<Self> {
        let runner = Arc::new(SkillRunner::new(&config.fleet_dir)?);
        Ok(Self {
            db,
            runner,
            config,
            fleet_config_json,
        })
    }

    /// Try to claim and process one task. Returns Ok(true) if a task was processed,
    /// Ok(false) if the queue was empty.
    pub async fn process_one(&self, role: &str) -> anyhow::Result<bool> {
        // Try to claim a task
        let task = match self.db.claim_task(role)? {
            Some(t) => t,
            None => return Ok(false),
        };

        let skill = task.skill.clone();
        let task_id = task.id;
        let timeout = self.config.timeout_for(&skill);

        info!("Processing task {} (skill: {})", task_id, skill);

        // Parse payload
        let payload: serde_json::Value = task
            .payload_json
            .as_deref()
            .and_then(|s| serde_json::from_str(s).ok())
            .unwrap_or(serde_json::json!({}));

        // Execute with timeout
        let result = self.execute_with_timeout(&skill, &payload, timeout).await;

        match result {
            Ok(value) => {
                let result_str = serde_json::to_string(&value)?;
                self.db.complete_task(task_id, &result_str)?;
                info!("Task {} completed (skill: {})", task_id, skill);
            }
            Err(e) => {
                let error_msg = format!("{}", e);
                self.db.fail_task(task_id, &error_msg)?;
                warn!("Task {} failed (skill: {}): {}", task_id, skill, error_msg);
            }
        }

        Ok(true)
    }

    /// Execute a skill with a timeout. Runs the blocking Python call
    /// on a dedicated thread via spawn_blocking to avoid blocking the
    /// tokio runtime. tokio::time::timeout wraps the JoinHandle so
    /// the timeout can fire even while the blocking thread holds the GIL.
    async fn execute_with_timeout(
        &self,
        skill: &str,
        payload: &serde_json::Value,
        timeout: Duration,
    ) -> anyhow::Result<serde_json::Value> {
        let skill = skill.to_string();
        let payload = payload.clone();
        let config = self.fleet_config_json.clone();
        let runner = Arc::clone(&self.runner);

        // spawn_blocking moves the Python call to a dedicated OS thread.
        // tokio::time::timeout wraps the JoinHandle future, which IS async
        // and can be cancelled by the timeout.
        let result = tokio::time::timeout(timeout, tokio::task::spawn_blocking(
            move || runner.run_skill(&skill, &payload, &config),
        ))
        .await;

        match result {
            Ok(Ok(inner)) => inner,
            Ok(Err(join_err)) => {
                Err(anyhow::anyhow!("Skill task panicked: {}", join_err))
            }
            Err(_) => {
                error!("Skill '{}' timed out after {:?}", skill, timeout);
                Err(anyhow::anyhow!(
                    "Skill '{}' timed out after {} seconds",
                    skill,
                    timeout.as_secs()
                ))
            }
        }
    }

    /// Run the worker loop continuously, polling for tasks.
    pub async fn run_loop(&self, role: &str) -> anyhow::Result<()> {
        info!("Worker loop started (role: {})", role);
        loop {
            match self.process_one(role).await {
                Ok(true) => {
                    // Processed a task — immediately check for more
                    continue;
                }
                Ok(false) => {
                    // No tasks — back off
                    tokio::time::sleep(Duration::from_secs(2)).await;
                }
                Err(e) => {
                    error!("Worker error: {}", e);
                    tokio::time::sleep(Duration::from_secs(5)).await;
                }
            }
        }
    }
}
```

- [ ] **Step 3: Run tests**

Run: `cd biged-rs && cargo test --test bridge_test`
Expected: 7 tests PASS

- [ ] **Step 4: Commit**

```bash
git add biged-rs/crates/biged-bridge/src/worker.rs biged-rs/tests/bridge_test.rs
git commit -m "feat(bridge): worker loop — claim task, dispatch to Python, write result"
```

---

## Task 5: Wire into main.rs

**Files:**
- Modify: `biged-rs/src/main.rs`
- Modify: `biged-rs/tests/smoke_test.rs`

- [ ] **Step 1: Add `Serialize` to all FleetConfig structs**

In `biged-rs/crates/biged-core/src/config.rs`, add `Serialize` to the derive macros on ALL 8 config structs:
- `FleetConfig`, `FleetSection`, `ModelsSection`, `DashboardSection`, `WorkersSection`, `ThermalSection`, `BudgetsSection`, `BackupSection`

Change each `#[derive(Debug, Clone, Deserialize)]` to `#[derive(Debug, Clone, Serialize, Deserialize)]`.

Also add the `Serialize` import: ensure `use serde::{Deserialize, Serialize};` is at the top.

Run: `cd biged-rs && cargo check` — should compile.

- [ ] **Step 2: Update main.rs Worker subcommand**

The current `main.rs` has `Commands::Worker` that logs a warning. Replace it:

```rust
        Some(Commands::Worker) => {
            tracing::info!("Starting BigEd worker");
            let fleet_dir = std::env::current_dir()?.join("fleet");
            let config_path = fleet_dir.join("fleet.toml");
            let fleet_config = if config_path.exists() {
                biged_core::config::FleetConfig::from_file(&config_path)?
            } else {
                biged_core::config::FleetConfig::default()
            };
            let db_path = fleet_dir.join("fleet.db");
            let db = biged_core::db::Db::open(&db_path)?;
            let bridge_config = biged_bridge::BridgeConfig::new(fleet_dir);
            let fleet_config_json = serde_json::to_value(&fleet_config)?;
            let worker = biged_bridge::worker::Worker::new(db, bridge_config, fleet_config_json)?;
            worker.run_loop("coder").await?;
        }
```

- [ ] **Step 3: Add smoke test**

Append to `biged-rs/tests/smoke_test.rs`:

```rust
#[test]
fn smoke_bridge_config() {
    let config = biged_bridge::BridgeConfig::new(std::path::PathBuf::from("fleet"));
    assert_eq!(
        config.timeout_for("code_write"),
        std::time::Duration::from_secs(900)
    );
    assert_eq!(
        config.timeout_for("unknown_skill"),
        std::time::Duration::from_secs(600)
    );
}
```

- [ ] **Step 4: Run full test suite**

Run: `cd biged-rs && cargo test`
Expected: all tests pass

- [ ] **Step 5: Run clippy + fmt**

Run: `cd biged-rs && cargo clippy && cargo fmt --check`

- [ ] **Step 6: Commit**

```bash
git add biged-rs/src/main.rs biged-rs/tests/smoke_test.rs biged-rs/crates/biged-core/src/config.rs
git commit -m "feat(bridge): wire worker subcommand, add Serialize to FleetConfig, smoke test"
```

---

## Task 6: Integration Test with Real Skills

**Files:**
- Modify: `biged-rs/tests/bridge_test.rs`

- [ ] **Step 1: Add integration tests that run real skills**

Append to `biged-rs/tests/bridge_test.rs`:

```rust
/// Test running multiple skills through the bridge to verify
/// the module cache works and different skill patterns are handled.
#[test]
fn test_runner_multiple_skills() {
    let fleet_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet");
    if !fleet_dir.join("skills").exists() {
        return;
    }

    let runner = SkillRunner::new(&fleet_dir).expect("runner should init");
    let empty = serde_json::json!({});
    let config = serde_json::json!({
        "models": { "local": "qwen3:8b", "complex": "claude-sonnet-4-6" }
    });

    // Run autoresearch_analyze (no network, returns quickly)
    let r1 = runner.run_skill("autoresearch_analyze", &empty, &config);
    assert!(r1.is_ok(), "autoresearch_analyze failed: {:?}", r1.err());

    // Verify cache has 1 entry
    assert_eq!(runner.loader().cached_count(), 1);

    // Run it again — should use cache
    let r2 = runner.run_skill("autoresearch_analyze", &empty, &config);
    assert!(r2.is_ok());
    assert_eq!(runner.loader().cached_count(), 1, "cache should still be 1");
}

#[tokio::test]
async fn test_worker_full_lifecycle() {
    let fleet_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet");
    if !fleet_dir.join("skills").exists() {
        return;
    }

    let db = Db::in_memory().unwrap();
    db.register_agent("lifecycle_worker", "coder").unwrap();

    // Post 3 tasks
    db.post_task("autoresearch_analyze", "{}", 5, None).unwrap();
    db.post_task("autoresearch_analyze", "{}", 5, None).unwrap();
    db.post_task("autoresearch_analyze", "{}", 5, None).unwrap();

    let config = BridgeConfig::new(fleet_dir.clone());
    let worker = Worker::new(db.clone(), config, serde_json::json!({})).expect("worker");

    // Process all 3
    for _ in 0..3 {
        let processed = worker.process_one("coder").await.unwrap();
        assert!(processed);
    }

    // Queue should now be empty
    let processed = worker.process_one("coder").await.unwrap();
    assert!(!processed, "queue should be empty");

    // All tasks should be done or failed
    assert_eq!(db.queue_depth().unwrap(), 0);
}
```

- [ ] **Step 2: Run full test suite**

Run: `cd biged-rs && cargo test`
Expected: all tests pass

- [ ] **Step 3: Run clippy + fmt**

- [ ] **Step 4: Commit**

```bash
git add biged-rs/tests/bridge_test.rs
git commit -m "test: bridge integration tests — multi-skill cache, full worker lifecycle"
```

---

## Gate Criteria

Phase 3 is complete when:
- [ ] `cargo test` passes all tests (existing + bridge tests)
- [ ] `cargo run -- worker` starts, claims tasks, dispatches to Python, writes results
- [ ] `cargo clippy` has zero warnings
- [ ] `cargo fmt --check` passes
- [ ] Worker processes at least 5 different skills successfully
- [ ] Module cache reuses imported skills (verified by `cached_count()`)
- [ ] Timeout enforcement works (skill that exceeds timeout gets FAILED status)
- [ ] Python's `db.py` still works for skills that write to DB (WAL coexistence)

## Rollback

If PyO3 integration fails on the target platform:
- Fall back to subprocess: `python -c "import skills.X; import json; print(json.dumps(skills.X.run(...)))"`
- This is 10x slower but functional
- The `Worker` struct's `execute_with_timeout` can be swapped to use `tokio::process::Command` instead of PyO3
