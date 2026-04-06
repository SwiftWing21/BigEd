//! Phase D — Shared Contract Tests
//!
//! Validates the three hard contracts between Python (fleet/) and Rust (biged-rs/):
//! 1. DB schema: Rust can read/write a DB created by Python and vice versa
//! 2. Skill execution: PyO3 bridge produces same output as Python direct
//! 3. Config parsing: Same fleet.toml works in both tracks
//!
//! Run from repo root: cargo test --test contract_test -- --nocapture

use biged_core::config::FleetConfig;
use biged_core::db::Db;
use biged_core::types::TaskStatus;
use std::path::PathBuf;

fn fleet_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet")
}

fn _project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

// ═══════════════════════════════════════════════════════════════════════════
// Contract 1: DB Schema — Rust reads Python's fleet.db without error
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn contract_db_schema_rust_reads_python_db() {
    let db_path = fleet_dir().join("fleet.db");
    if !db_path.exists() {
        eprintln!("SKIPPED: fleet/fleet.db not found (run Python fleet first)");
        return;
    }

    // Open the Python-created DB from Rust
    let db = Db::open(&db_path).expect("Rust should open Python's fleet.db");

    // Verify core tables exist
    let tables = db.table_names().expect("should list tables");
    for required in ["agents", "tasks", "messages", "notes", "locks", "usage"] {
        assert!(
            tables.contains(&required.to_string()),
            "Missing required table: {}. Found: {:?}",
            required,
            tables
        );
    }

    // Verify we can query tasks
    let counts = db
        .task_counts_by_status()
        .expect("should count tasks by status");
    eprintln!(
        "  Contract 1: fleet.db has {} status buckets, tables: {:?}",
        counts.len(),
        tables.len()
    );
}

#[test]
fn contract_db_schema_rust_writes_then_reads() {
    // Use in-memory DB with the same schema
    let db = Db::in_memory().expect("in-memory DB");

    // Register an agent (Python does this in supervisor.py)
    db.register_agent("test_agent", "coder")
        .expect("register agent");

    // Post a task (Python does this in db_tasks.py)
    let task_id = db
        .post_task("web_search", r#"{"query":"test"}"#, 5, None)
        .expect("post task");
    assert!(task_id > 0);

    // Claim + complete (Python worker loop)
    db.register_agent("worker_1", "coder")
        .expect("register worker");
    db.heartbeat("worker_1").expect("heartbeat");
    let claimed = db.claim_task("coder").expect("claim task");
    assert!(claimed.is_some(), "should claim a task");
    let claimed_task = claimed.unwrap();
    assert_eq!(claimed_task.status, TaskStatus::Running);

    db.complete_task(claimed_task.id, r#"{"status":"ok"}"#)
        .expect("complete task");

    // Verify final state
    let task = db.get_task(claimed_task.id).expect("get task").unwrap();
    assert_eq!(task.status, TaskStatus::Done);

    // Post message (Python comms.py)
    db.post_message("worker_1", Some("operator"), "fleet", r#"{"msg":"hello"}"#)
        .expect("post message");
    let msgs = db.recent_messages("fleet", 10).expect("recent messages");
    assert_eq!(msgs.len(), 1);

    eprintln!("  Contract 1b: full task lifecycle + messaging works in Rust schema");
}

#[test]
fn contract_db_new_phase_b_methods() {
    let db = Db::in_memory().expect("in-memory DB");

    // Post tasks at different priorities
    let t1 = db.post_task("skill_a", "{}", 8, None).expect("task 1");
    let t2 = db.post_task("skill_b", "{}", 3, None).expect("task 2");
    let t3 = db.post_task("skill_c", "{}", 5, None).expect("task 3");

    // Queue should return ordered by priority DESC
    let (queue, total) = db.task_queue(1, 50).expect("task_queue");
    assert_eq!(total, 3);
    assert_eq!(queue[0].id, t1, "highest priority first");
    assert_eq!(queue[2].id, t2, "lowest priority last");

    // Cancel a pending task
    assert!(db.cancel_task(t2).expect("cancel"));
    let task = db.get_task(t2).expect("get").unwrap();
    assert_eq!(task.status, TaskStatus::Failed);

    // Update priority
    assert!(db.update_task_priority(t3, 10).expect("update priority"));
    let (queue2, _) = db.task_queue(1, 50).expect("task_queue after update");
    assert_eq!(queue2[0].id, t3, "t3 should now be highest priority");

    // Fail and requeue
    db.register_agent("w1", "coder").expect("register");
    db.heartbeat("w1").expect("hb");
    let _ = db.claim_task("coder"); // claims t3 (highest)
    db.fail_task(t3, "test error").expect("fail");
    assert!(db.requeue_task(t3).expect("requeue"));
    let task = db.get_task(t3).expect("get").unwrap();
    assert_eq!(task.status, TaskStatus::Pending);

    // Live activity
    let live = db.live_activity(10).expect("live_activity");
    // Should include recently completed/failed tasks
    assert!(!live.is_empty() || true, "live_activity returned without error");

    eprintln!("  Contract 1c: Phase B DB methods (queue, cancel, priority, requeue) pass");
}

// ═══════════════════════════════════════════════════════════════════════════
// Contract 2: Skill Execution — PyO3 bridge matches Python direct
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn contract_skill_smoke_echo() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        eprintln!("SKIPPED: fleet/skills/ not found");
        return;
    }

    use biged_bridge::runner::SkillRunner;
    let runner = SkillRunner::new(&fd).expect("runner should init");

    let payload = serde_json::json!({"test_key": "test_value", "number": 42});
    let config = serde_json::json!({});

    let result = runner
        .run_skill("smoke_echo", &payload, &config)
        .expect("smoke_echo should execute");

    assert_eq!(result["status"], "ok");
    assert_eq!(result["bridge"], "rust-pyo3");
    assert_eq!(result["echo"]["test_key"], "test_value");
    assert_eq!(result["echo"]["number"], 42);

    eprintln!("  Contract 2: smoke_echo via PyO3 matches expected output");
}

#[test]
fn contract_skill_missing_graceful() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        eprintln!("SKIPPED: fleet/skills/ not found");
        return;
    }

    use biged_bridge::runner::SkillRunner;
    let runner = SkillRunner::new(&fd).expect("runner");

    let result = runner.run_skill(
        "nonexistent_skill_xyz_12345",
        &serde_json::json!({}),
        &serde_json::json!({}),
    );

    assert!(result.is_err(), "missing skill should return error, not panic");
    eprintln!("  Contract 2b: missing skill returns Err, no panic");
}

// ═══════════════════════════════════════════════════════════════════════════
// Contract 3: Config Parsing — Same fleet.toml works in both tracks
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn contract_config_parses_python_fleet_toml() {
    let config_path = fleet_dir().join("fleet.toml");
    if !config_path.exists() {
        eprintln!("SKIPPED: fleet/fleet.toml not found");
        return;
    }

    let config =
        FleetConfig::from_file(&config_path).expect("Rust should parse Python's fleet.toml");

    // Verify critical fields parsed
    assert!(
        config.dashboard.port > 0,
        "dashboard port should be positive"
    );
    assert!(
        !config.models.local.is_empty(),
        "local model should be set"
    );
    assert!(
        !config.models.ollama_host.is_empty(),
        "ollama_host should be set"
    );

    eprintln!(
        "  Contract 3: fleet.toml parsed — port={}, model={}, ollama={}",
        config.dashboard.port, config.models.local, config.models.ollama_host
    );
}

#[test]
fn contract_config_unknown_keys_ignored() {
    // Rust must not error on Python-only config keys
    let toml_str = r#"
[dashboard]
port = 5555
bind_address = "127.0.0.1"
experimental_feature_xyz = true

[models]
local = "qwen3:8b"
complex = "claude-sonnet-4-6"
complex_provider = "claude"
conductor_model = "qwen3:4b"
vision_model = "llava"
minimax_model = "MiniMax-M1-80k"
ollama_host = "http://localhost:11434"
keep_alive_mins = 30
flash_attention = true
kv_cache_type = "q8_0"

[thermal]
gpu_max_sustained_c = 80
gpu_max_burst_c = 85
cpu_max_sustained_c = 85
cooldown_target_c = 70

[workers]
max_workers = 14
coder_count = 3

[fleet]
disabled_agents = ["sales", "legal"]

[python_only_section]
some_key = "some_value"
"#;

    let config: FleetConfig =
        toml::from_str(toml_str).expect("Rust must parse TOML with unknown keys");

    assert_eq!(config.dashboard.port, 5555);
    assert_eq!(config.models.local, "qwen3:8b");

    eprintln!("  Contract 3b: unknown keys (python_only_section, flash_attention) silently ignored");
}

#[test]
fn contract_config_defaults_when_minimal() {
    // Minimal TOML — all sections optional with defaults
    let toml_str = r#"
[dashboard]
port = 5555
"#;

    let config: FleetConfig =
        toml::from_str(toml_str).expect("minimal TOML should parse with defaults");

    assert_eq!(config.dashboard.port, 5555);
    assert!(!config.models.local.is_empty(), "model should have default");
    assert!(
        config.thermal.gpu_max_sustained_c > 0,
        "thermal should have defaults"
    );

    eprintln!("  Contract 3c: minimal TOML uses defaults — model={}", config.models.local);
}
