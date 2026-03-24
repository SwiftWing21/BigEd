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
                    let name = path
                        .file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .to_string();
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
            Err(e) => {
                tracing::warn!("Discussion row error: {}", e);
                None
            }
        })
        .collect();
    Ok(Json(json!(discussions)))
}
