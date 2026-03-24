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

    let id = db
        .post_task("code_review", r#"{"file":"test.py"}"#, 5, None)
        .unwrap();
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

    let _id = db.post_task("test_skill", "{}", 5, None).unwrap();

    // Both try to claim — only one should succeed
    let t1 = db.claim_task("coder").unwrap();
    let t2 = db.claim_task("coder").unwrap();

    assert!(t1.is_some());
    assert!(
        t2.is_none(),
        "Second claim should return None — task already claimed"
    );
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
