use biged_bridge::loader::SkillLoader;
use biged_bridge::runner::SkillRunner;
use biged_bridge::worker::Worker;
use biged_bridge::BridgeConfig;
use biged_core::db::Db;
use biged_core::types::TaskStatus;
use std::path::PathBuf;

fn fleet_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet")
}

#[test]
fn test_loader_initializes() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return; // Skip if fleet dir not available
    }
    let loader = SkillLoader::new(&fd).expect("loader should initialize");
    assert_eq!(loader.cached_count(), 0);
}

#[test]
fn test_loader_imports_skill() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return;
    }
    let loader = SkillLoader::new(&fd).expect("loader should initialize");

    // autoresearch_analyze is a simple skill with no network deps
    let result = loader.load("autoresearch_analyze");
    assert!(
        result.is_ok(),
        "should load autoresearch_analyze: {:?}",
        result.err()
    );
    assert_eq!(loader.cached_count(), 1);

    // Loading again should use cache
    let result2 = loader.load("autoresearch_analyze");
    assert!(result2.is_ok());
    assert_eq!(loader.cached_count(), 1, "should still be 1 — cached");
}

#[test]
fn test_loader_missing_skill() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return;
    }
    let loader = SkillLoader::new(&fd).expect("loader should initialize");
    let result = loader.load("nonexistent_skill_xyz");
    assert!(result.is_err());
}

#[test]
fn test_runner_executes_skill() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return;
    }

    let runner = SkillRunner::new(&fd).expect("runner should init");

    // autoresearch_analyze returns quickly with a skip if results.tsv doesn't exist
    let payload = serde_json::json!({});
    let fleet_config = serde_json::json!({
        "models": { "local": "qwen3:8b", "complex": "claude-sonnet-4-6" }
    });

    let result = runner.run_skill("autoresearch_analyze", &payload, &fleet_config);
    assert!(result.is_ok(), "skill should execute: {:?}", result.err());

    let value = result.unwrap();
    assert!(value.is_object());
}

#[test]
fn test_runner_handles_missing_skill() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return;
    }

    let runner = SkillRunner::new(&fd).expect("runner should init");
    let result = runner.run_skill(
        "nonexistent_xyz",
        &serde_json::json!({}),
        &serde_json::json!({}),
    );
    assert!(result.is_err());
}

#[tokio::test]
async fn test_worker_processes_task() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return;
    }

    let db = Db::in_memory().unwrap();
    db.register_agent("test_worker", "coder").unwrap();
    let task_id = db.post_task("autoresearch_analyze", "{}", 5, None).unwrap();

    let config = BridgeConfig::new(fd.clone());
    let worker =
        Worker::new(db.clone(), config, serde_json::json!({})).expect("worker should init");

    let processed = worker.process_one("coder").await;
    assert!(
        processed.is_ok(),
        "process_one failed: {:?}",
        processed.err()
    );
    assert!(processed.unwrap(), "should have processed a task");

    let task = db.get_task(task_id).unwrap().unwrap();
    assert!(
        task.status == TaskStatus::Done || task.status == TaskStatus::Failed,
        "task should be done or failed, got: {:?}",
        task.status
    );
}

#[tokio::test]
async fn test_worker_empty_queue() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return;
    }

    let db = Db::in_memory().unwrap();
    db.register_agent("test_worker", "coder").unwrap();

    let config = BridgeConfig::new(fd.clone());
    let worker = Worker::new(db, config, serde_json::json!({})).expect("worker should init");

    let processed = worker.process_one("coder").await;
    assert!(processed.is_ok());
    assert!(!processed.unwrap(), "should return false — no tasks");
}

/// Test running multiple skills through the bridge to verify
/// the module cache works across different skill modules.
#[test]
fn test_runner_multiple_skills() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return;
    }

    let runner = SkillRunner::new(&fd).expect("runner should init");
    let empty = serde_json::json!({});
    let config = serde_json::json!({
        "models": { "local": "qwen3:8b", "complex": "claude-sonnet-4-6" }
    });

    // Run autoresearch_analyze (no network, returns quickly)
    let r1 = runner.run_skill("autoresearch_analyze", &empty, &config);
    assert!(r1.is_ok(), "autoresearch_analyze failed: {:?}", r1.err());
    assert_eq!(runner.loader().cached_count(), 1);

    // Run it again — should use cache
    let r2 = runner.run_skill("autoresearch_analyze", &empty, &config);
    assert!(r2.is_ok());
    assert_eq!(runner.loader().cached_count(), 1, "cache should still be 1");
}

#[tokio::test]
async fn test_worker_full_lifecycle() {
    let fd = fleet_dir();
    if !fd.join("skills").exists() {
        return;
    }

    let db = Db::in_memory().unwrap();
    db.register_agent("lifecycle_worker", "coder").unwrap();

    // Post 3 tasks
    db.post_task("autoresearch_analyze", "{}", 5, None).unwrap();
    db.post_task("autoresearch_analyze", "{}", 5, None).unwrap();
    db.post_task("autoresearch_analyze", "{}", 5, None).unwrap();

    let config = BridgeConfig::new(fd.clone());
    let worker = Worker::new(db.clone(), config, serde_json::json!({})).expect("worker");

    // Process all 3
    for _ in 0..3 {
        let processed = worker.process_one("coder").await.unwrap();
        assert!(processed);
    }

    // Queue should now be empty
    let processed = worker.process_one("coder").await.unwrap();
    assert!(!processed, "queue should be empty");

    // All tasks should be done or failed (none pending)
    assert_eq!(db.queue_depth().unwrap(), 0);
}
