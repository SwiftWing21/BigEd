use crate::error::Result;
use crate::types::{Agent, Message, Task, TaskStatus};
use r2d2::Pool;
use r2d2_sqlite::SqliteConnectionManager;
use rusqlite::{params, OptionalExtension};
use std::path::Path;
use tracing::debug;

/// Connection-pooled SQLite database for fleet operations.
#[derive(Clone)]
pub struct Db {
    pool: Pool<SqliteConnectionManager>,
}

impl Db {
    /// Open database at the given path with WAL mode and connection pool.
    pub fn open(path: &Path) -> Result<Self> {
        let manager = SqliteConnectionManager::file(path).with_init(|conn| {
            conn.execute_batch(
                "PRAGMA journal_mode=WAL;
                 PRAGMA busy_timeout=30000;
                 PRAGMA foreign_keys=ON;
                 PRAGMA synchronous=NORMAL;",
            )?;
            Ok(())
        });
        let pool = Pool::builder().max_size(8).build(manager)?;
        let db = Self { pool };
        db.init_schema()?;
        Ok(db)
    }

    /// Create an in-memory database for testing.
    pub fn in_memory() -> Result<Self> {
        let manager = SqliteConnectionManager::memory().with_init(|conn| {
            conn.execute_batch("PRAGMA foreign_keys=ON;")?;
            Ok(())
        });
        let pool = Pool::builder()
            .max_size(1) // in-memory DB can't be shared across connections
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
        let mut stmt =
            conn.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")?;
        let names = stmt
            .query_map([], |row| row.get(0))?
            .collect::<std::result::Result<Vec<String>, _>>()?;
        Ok(names)
    }

    // ── Agent operations ─────────────────────────────────────────

    pub fn register_agent(&self, name: &str, role: &str) -> Result<()> {
        let conn = self.pool.get()?;
        conn.execute(
            "INSERT OR IGNORE INTO agents (name, role, status, last_heartbeat)
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
             FROM agents WHERE name = ?1",
        )?;
        let agent = stmt
            .query_row(params![name], |row| {
                Ok(Agent {
                    name: row.get(0)?,
                    role: row.get(1)?,
                    status: row.get(2)?,
                    last_heartbeat: row.get(3)?,
                    current_task_id: row.get(4)?,
                })
            })
            .optional()?;
        Ok(agent)
    }

    // ── Task operations ──────────────────────────────────────────

    pub fn post_task(
        &self,
        skill: &str,
        payload: &str,
        priority: i32,
        assigned_to: Option<&str>,
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
        let task = conn
            .query_row(
                "SELECT id, created_at, assigned_to, status, priority, type,
                    payload_json, result_json, error, parent_id, depends_on,
                    intelligence_score
             FROM tasks WHERE id = ?1",
                params![id],
                Self::row_to_task,
            )
            .optional()?;
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

    /// Expose pool for advanced queries (e.g. health checker).
    pub fn pool_ref(&self) -> &Pool<SqliteConnectionManager> {
        &self.pool
    }

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
        let mut stmt = conn.prepare("SELECT status, COUNT(*) FROM tasks GROUP BY status")?;
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

    pub fn task_counts_by_skill(
        &self,
    ) -> Result<std::collections::HashMap<String, std::collections::HashMap<String, i64>>> {
        let conn = self.pool.get()?;
        let mut stmt =
            conn.prepare("SELECT type, status, COUNT(*) FROM tasks GROUP BY type, status")?;
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
        let mut result: std::collections::BTreeMap<String, std::collections::HashMap<String, i64>> =
            std::collections::BTreeMap::new();
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
        let to_agent = to.unwrap_or("broadcast");
        conn.execute(
            "INSERT INTO messages (from_agent, to_agent, channel, body_json)
             VALUES (?1, ?2, ?3, ?4)",
            params![from, to_agent, channel, body],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn recent_messages(&self, channel: &str, limit: i64) -> Result<Vec<Message>> {
        let conn = self.pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT id, from_agent, to_agent, channel, body_json, created_at, read_at
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
                    body: row.get::<_, Option<String>>(4)?.unwrap_or_default(),
                    created_at: row.get(5)?,
                    read: row.get::<_, Option<String>>(6)?.is_some(),
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(msgs)
    }

    // ── Task queue operations (Phase B Priority 1) ────────────────

    /// Paginated queue: PENDING + RUNNING tasks, ordered by priority DESC.
    pub fn task_queue(&self, page: i64, per_page: i64) -> Result<(Vec<Task>, i64)> {
        let conn = self.pool.get()?;
        let total: i64 = conn.query_row(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('PENDING', 'RUNNING')",
            [],
            |row| row.get(0),
        )?;
        let offset = (page - 1) * per_page;
        let mut stmt = conn.prepare(
            "SELECT id, created_at, assigned_to, status, priority, type,
                    payload_json, result_json, error, parent_id, depends_on,
                    intelligence_score
             FROM tasks WHERE status IN ('PENDING', 'RUNNING')
             ORDER BY priority DESC, created_at ASC
             LIMIT ?1 OFFSET ?2",
        )?;
        let tasks = stmt
            .query_map(params![per_page, offset], Self::row_to_task)?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok((tasks, total))
    }

    /// Cancel a PENDING task. Returns true if a row was updated.
    pub fn cancel_task(&self, id: i64) -> Result<bool> {
        let conn = self.pool.get()?;
        let changed = conn.execute(
            "UPDATE tasks SET status = 'FAILED', result_json = '{\"error\": \"Cancelled by operator\"}'
             WHERE id = ?1 AND status = 'PENDING'",
            params![id],
        )?;
        Ok(changed > 0)
    }

    /// Update priority of a PENDING task. Returns true if updated.
    pub fn update_task_priority(&self, id: i64, priority: i32) -> Result<bool> {
        let conn = self.pool.get()?;
        let changed = conn.execute(
            "UPDATE tasks SET priority = ?1 WHERE id = ?2 AND status = 'PENDING'",
            params![priority, id],
        )?;
        Ok(changed > 0)
    }

    /// Requeue a FAILED task back to PENDING. Returns true if updated.
    pub fn requeue_task(&self, id: i64) -> Result<bool> {
        let conn = self.pool.get()?;
        let changed = conn.execute(
            "UPDATE tasks SET status = 'PENDING', assigned_to = NULL,
                    result_json = NULL, error = NULL
             WHERE id = ?1 AND status = 'FAILED'",
            params![id],
        )?;
        Ok(changed > 0)
    }

    /// Running + recently completed tasks for live activity feed.
    pub fn live_activity(&self, recent_minutes: i64) -> Result<Vec<Task>> {
        let conn = self.pool.get()?;
        let mut stmt = conn.prepare(
            "SELECT id, created_at, assigned_to, status, priority, type,
                    payload_json, result_json, error, parent_id, depends_on,
                    intelligence_score
             FROM tasks
             WHERE status = 'RUNNING'
                OR (status IN ('DONE', 'FAILED')
                    AND created_at >= datetime('now', ?1 || ' minutes'))
             ORDER BY
                CASE status WHEN 'RUNNING' THEN 0 WHEN 'DONE' THEN 1 ELSE 2 END,
                id DESC
             LIMIT 30",
        )?;
        let tasks = stmt
            .query_map(params![format!("-{}", recent_minutes)], Self::row_to_task)?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(tasks)
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
