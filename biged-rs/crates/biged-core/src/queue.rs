use crate::db::Db;
use crate::error::Result;
use crate::types::Task;
use std::sync::Arc;
use tokio::sync::Notify;

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
