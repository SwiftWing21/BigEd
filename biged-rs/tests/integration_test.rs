use biged_core::db::Db;
use biged_core::types::TaskStatus;

// ── Full task lifecycle ──────────────────────────────────────────────────────

#[test]
fn test_full_task_lifecycle() {
    let db = Db::in_memory().unwrap();

    // Register agent
    db.register_agent("test_worker", "coder").unwrap();

    // Post task
    let task_id = db
        .post_task("test_skill", r#"{"input":"hello"}"#, 5, None)
        .unwrap();
    assert!(task_id > 0);

    // Verify pending
    let counts = db.task_counts_by_status().unwrap();
    assert_eq!(counts.get("PENDING").copied().unwrap_or(0), 1);

    // Claim
    let task = db.claim_task("coder").unwrap();
    assert!(task.is_some());
    let task = task.unwrap();
    assert_eq!(task.id, task_id);
    assert_eq!(task.status, TaskStatus::Running);

    // Verify running
    let counts = db.task_counts_by_status().unwrap();
    assert_eq!(counts.get("RUNNING").copied().unwrap_or(0), 1);
    assert_eq!(counts.get("PENDING").copied().unwrap_or(0), 0);

    // Complete
    db.complete_task(task_id, r#"{"result": "ok"}"#).unwrap();

    // Verify done
    let counts = db.task_counts_by_status().unwrap();
    assert_eq!(counts.get("DONE").copied().unwrap_or(0), 1);
    assert_eq!(counts.get("PENDING").copied().unwrap_or(0), 0);
    assert_eq!(counts.get("RUNNING").copied().unwrap_or(0), 0);
}

// ── Multi-task lifecycle ─────────────────────────────────────────────────────

#[test]
fn test_multi_task_lifecycle() {
    let db = Db::in_memory().unwrap();

    db.register_agent("w1", "coder").unwrap();
    db.register_agent("w2", "coder").unwrap();

    // Post 5 tasks
    let mut ids = Vec::new();
    for i in 0..5 {
        let id = db
            .post_task(
                "batch_skill",
                &format!(r#"{{"i":{}}}"#, i),
                5,
                None,
            )
            .unwrap();
        ids.push(id);
    }

    // All 5 should be pending
    assert_eq!(db.queue_depth().unwrap(), 5);

    // Claim and complete all 5
    for _ in 0..5 {
        let task = db.claim_task("coder").unwrap();
        assert!(task.is_some(), "Should be able to claim a task");
        let task = task.unwrap();
        db.complete_task(task.id, r#"{"status":"ok"}"#).unwrap();
    }

    // Queue should be empty
    assert_eq!(db.queue_depth().unwrap(), 0);

    // Verify all done
    let counts = db.task_counts_by_status().unwrap();
    assert_eq!(counts.get("DONE").copied().unwrap_or(0), 5);
    assert_eq!(counts.get("PENDING").copied().unwrap_or(0), 0);
    assert_eq!(counts.get("RUNNING").copied().unwrap_or(0), 0);
}

// ── Task failure lifecycle ───────────────────────────────────────────────────

#[test]
fn test_task_failure_lifecycle() {
    let db = Db::in_memory().unwrap();

    db.register_agent("w1", "coder").unwrap();

    let task_id = db.post_task("flaky_skill", "{}", 5, None).unwrap();

    // Claim it
    let task = db.claim_task("coder").unwrap().unwrap();
    assert_eq!(task.id, task_id);

    // Fail it
    db.fail_task(task_id, "skill raised exception").unwrap();

    // Verify failed
    let task = db.get_task(task_id).unwrap().unwrap();
    assert_eq!(task.status, TaskStatus::Failed);
    assert_eq!(task.error.as_deref(), Some("skill raised exception"));

    // Verify counts
    let counts = db.task_counts_by_status().unwrap();
    assert_eq!(counts.get("FAILED").copied().unwrap_or(0), 1);
    assert_eq!(counts.get("RUNNING").copied().unwrap_or(0), 0);
}

// ── Mixed success/failure batch ──────────────────────────────────────────────

#[test]
fn test_mixed_success_failure_batch() {
    let db = Db::in_memory().unwrap();

    db.register_agent("w1", "coder").unwrap();

    // Post 4 tasks
    let id1 = db.post_task("skill_a", "{}", 5, None).unwrap();
    let id2 = db.post_task("skill_b", "{}", 5, None).unwrap();
    let id3 = db.post_task("skill_c", "{}", 5, None).unwrap();
    let id4 = db.post_task("skill_d", "{}", 5, None).unwrap();

    // Complete 2, fail 2
    db.claim_task("coder").unwrap();
    db.complete_task(id1, r#"{"ok":true}"#).unwrap();

    db.claim_task("coder").unwrap();
    db.fail_task(id2, "timeout").unwrap();

    db.claim_task("coder").unwrap();
    db.complete_task(id3, r#"{"ok":true}"#).unwrap();

    db.claim_task("coder").unwrap();
    db.fail_task(id4, "bad input").unwrap();

    let counts = db.task_counts_by_status().unwrap();
    assert_eq!(counts.get("DONE").copied().unwrap_or(0), 2);
    assert_eq!(counts.get("FAILED").copied().unwrap_or(0), 2);
    assert_eq!(counts.get("PENDING").copied().unwrap_or(0), 0);
    assert_eq!(counts.get("RUNNING").copied().unwrap_or(0), 0);
}

// ── Priority ordering ────────────────────────────────────────────────────────

#[test]
fn test_priority_ordering() {
    let db = Db::in_memory().unwrap();

    db.register_agent("w1", "coder").unwrap();

    // Post tasks with different priorities
    let low_id = db.post_task("low_pri", "{}", 1, None).unwrap();
    let high_id = db.post_task("high_pri", "{}", 10, None).unwrap();
    let _med_id = db.post_task("med_pri", "{}", 5, None).unwrap();

    // Highest priority should be claimed first
    let first = db.claim_task("coder").unwrap().unwrap();
    assert_eq!(first.id, high_id, "Highest priority task should be claimed first");

    let second = db.claim_task("coder").unwrap().unwrap();
    // Priority 5 is next
    assert_ne!(second.id, low_id, "Low priority task should be claimed last");
}

// ── Agent registration and lookup ────────────────────────────────────────────

#[test]
fn test_agent_lifecycle() {
    let db = Db::in_memory().unwrap();

    db.register_agent("agent_1", "coder").unwrap();
    db.register_agent("agent_2", "researcher").unwrap();

    let agent = db.get_agent("agent_1").unwrap().unwrap();
    assert_eq!(agent.name, "agent_1");
    assert_eq!(agent.role, "coder");
    assert_eq!(agent.status, "IDLE");

    // All agents
    let all = db.all_agents().unwrap();
    assert_eq!(all.len(), 2);

    // Heartbeat
    db.heartbeat("agent_1").unwrap();
    let agent = db.get_agent("agent_1").unwrap().unwrap();
    assert!(agent.last_heartbeat.is_some());
}

// ── Message lifecycle ────────────────────────────────────────────────────────

#[test]
fn test_message_lifecycle() {
    let db = Db::in_memory().unwrap();

    // Post messages to different channels
    db.post_message("coder_1", Some("researcher_1"), "fleet", "task update")
        .unwrap();
    db.post_message("supervisor", None, "fleet", "scaling up")
        .unwrap();
    db.post_message("coder_1", None, "debug", "log entry")
        .unwrap();

    // Fetch fleet channel
    let fleet_msgs = db.recent_messages("fleet", 10).unwrap();
    assert_eq!(fleet_msgs.len(), 2);

    // Fetch debug channel
    let debug_msgs = db.recent_messages("debug", 10).unwrap();
    assert_eq!(debug_msgs.len(), 1);
    assert_eq!(debug_msgs[0].body, "log entry");
}

// ── Skill counts ─────────────────────────────────────────────────────────────

#[test]
fn test_task_counts_by_skill() {
    let db = Db::in_memory().unwrap();

    db.register_agent("w1", "coder").unwrap();

    db.post_task("code_review", "{}", 5, None).unwrap();
    db.post_task("code_review", "{}", 5, None).unwrap();
    db.post_task("test_write", "{}", 5, None).unwrap();

    // Claim and complete one code_review
    let task = db.claim_task("coder").unwrap().unwrap();
    db.complete_task(task.id, "{}").unwrap();

    let counts = db.task_counts_by_skill().unwrap();
    assert!(counts.contains_key("code_review"));
    assert!(counts.contains_key("test_write"));

    // code_review should have 1 PENDING + 1 DONE
    let cr = counts.get("code_review").unwrap();
    assert_eq!(cr.get("PENDING").copied().unwrap_or(0), 1);
    assert_eq!(cr.get("DONE").copied().unwrap_or(0), 1);
}

// ── Recent tasks + activity ──────────────────────────────────────────────────

#[test]
fn test_recent_tasks_limit() {
    let db = Db::in_memory().unwrap();

    for i in 0..10 {
        db.post_task(&format!("skill_{}", i), "{}", 5, None)
            .unwrap();
    }

    let recent = db.recent_tasks(3).unwrap();
    assert_eq!(recent.len(), 3);

    // Most recent should have the highest ID
    assert!(recent[0].id > recent[1].id);
    assert!(recent[1].id > recent[2].id);
}

#[test]
fn test_activity_by_day() {
    let db = Db::in_memory().unwrap();

    db.register_agent("w1", "coder").unwrap();

    // Create some activity
    let id = db.post_task("daily_task", "{}", 5, None).unwrap();
    db.claim_task("coder").unwrap();
    db.complete_task(id, "{}").unwrap();

    let activity = db.activity_by_day(30).unwrap();
    assert!(!activity.is_empty(), "Should have at least one day of activity");

    // Today's entry should exist with DONE count
    let (_, day_counts) = &activity[activity.len() - 1];
    let total: i64 = day_counts.values().sum();
    assert!(total > 0, "Today should have non-zero task count");
}

// ── Empty queue edge case ────────────────────────────────────────────────────

#[test]
fn test_claim_on_empty_queue() {
    let db = Db::in_memory().unwrap();
    db.register_agent("w1", "coder").unwrap();

    let result = db.claim_task("coder").unwrap();
    assert!(result.is_none(), "Claiming from empty queue should return None");
}

#[test]
fn test_no_matching_role() {
    let db = Db::in_memory().unwrap();

    // Register a coder agent, but post a task that would need a researcher
    db.register_agent("w1", "coder").unwrap();
    db.post_task("research_task", "{}", 5, Some("nonexistent")).unwrap();

    // Coder should not be able to claim a task assigned to a specific agent
    // (unless that agent has the matching role)
    let result = db.claim_task("coder").unwrap();
    // The task is assigned to "nonexistent", not matching any coder agent
    assert!(
        result.is_none(),
        "Should not claim task assigned to a different agent"
    );
}
