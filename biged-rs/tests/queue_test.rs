use biged_core::db::Db;
use biged_core::queue::TaskQueue;

#[tokio::test]
async fn test_queue_send_receive() {
    let db = Db::in_memory().unwrap();
    db.register_agent("w1", "coder").unwrap();
    let queue = TaskQueue::new(db.clone(), 100);

    queue
        .submit("test_skill", r#"{"key":"value"}"#, 5)
        .await
        .unwrap();

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
