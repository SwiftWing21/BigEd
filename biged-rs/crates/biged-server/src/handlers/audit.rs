use crate::error::AppError;
use crate::AppState;
use axum::extract::{Query, State};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Deserialize)]
pub struct HistoryParams {
    dimension: Option<String>,
    days: Option<i64>,
}

/// GET /api/audit/scores — latest score per dimension
pub async fn scores(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let pool = state.db.pool_ref();
    let conn = pool.get()?;

    // Check if audit_scores table exists
    let has_table = conn
        .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_scores'")
        .and_then(|mut s| s.query_row([], |_| Ok(true)))
        .unwrap_or(false);

    if !has_table {
        return Ok(Json(json!({ "scores": [], "note": "audit_scores table not found" })));
    }

    let mut stmt = conn.prepare(
        "SELECT dimension, auto_score, manual_grade, auto_detail, timestamp,
                divergence, acknowledged
         FROM audit_scores
         WHERE id IN (SELECT MAX(id) FROM audit_scores GROUP BY dimension)
         ORDER BY dimension",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok(json!({
            "dimension": row.get::<_, String>(0)?,
            "auto_score": row.get::<_, f64>(1)?,
            "manual_grade": row.get::<_, Option<String>>(2)?,
            "auto_detail": row.get::<_, Option<String>>(3)?,
            "timestamp": row.get::<_, String>(4)?,
            "divergence": row.get::<_, i32>(5)? != 0,
            "acknowledged": row.get::<_, i32>(6)? != 0,
        }))
    })?;

    let scores: Vec<Value> = rows.filter_map(|r| r.ok()).collect();
    Ok(Json(json!({ "scores": scores })))
}

/// GET /api/audit/history — historical scores with optional filters
pub async fn history(
    State(state): State<AppState>,
    Query(params): Query<HistoryParams>,
) -> Result<Json<Value>, AppError> {
    let pool = state.db.pool_ref();
    let conn = pool.get()?;
    let days = params.days.unwrap_or(30);

    let has_table = conn
        .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_scores'")
        .and_then(|mut s| s.query_row([], |_| Ok(true)))
        .unwrap_or(false);

    if !has_table {
        return Ok(Json(json!({ "history": [] })));
    }

    let (sql, param_values): (String, Vec<Box<dyn rusqlite::types::ToSql>>) =
        if let Some(ref dim) = params.dimension {
            (
                "SELECT dimension, auto_score, manual_grade, timestamp, divergence
                 FROM audit_scores
                 WHERE dimension = ?1 AND timestamp >= datetime('now', ?2 || ' days')
                 ORDER BY timestamp DESC LIMIT 200"
                    .into(),
                vec![
                    Box::new(dim.clone()) as Box<dyn rusqlite::types::ToSql>,
                    Box::new(format!("-{}", days)),
                ],
            )
        } else {
            (
                "SELECT dimension, auto_score, manual_grade, timestamp, divergence
                 FROM audit_scores
                 WHERE timestamp >= datetime('now', ?1 || ' days')
                 ORDER BY timestamp DESC LIMIT 500"
                    .into(),
                vec![Box::new(format!("-{}", days)) as Box<dyn rusqlite::types::ToSql>],
            )
        };

    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(
        rusqlite::params_from_iter(param_values.iter().map(|p| p.as_ref())),
        |row| {
            Ok(json!({
                "dimension": row.get::<_, String>(0)?,
                "auto_score": row.get::<_, f64>(1)?,
                "manual_grade": row.get::<_, Option<String>>(2)?,
                "timestamp": row.get::<_, String>(3)?,
                "divergence": row.get::<_, i32>(4)? != 0,
            }))
        },
    )?;

    let history: Vec<Value> = rows.filter_map(|r| r.ok()).collect();
    Ok(Json(json!({ "history": history })))
}

/// GET /api/audit/snapshot — export sanitized snapshot
pub async fn snapshot(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let pool = state.db.pool_ref();
    let conn = pool.get()?;

    let has_table = conn
        .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_scores'")
        .and_then(|mut s| s.query_row([], |_| Ok(true)))
        .unwrap_or(false);

    if !has_table {
        return Ok(Json(json!({ "snapshot": null, "note": "no audit data" })));
    }

    let mut stmt = conn.prepare(
        "SELECT dimension, auto_score, manual_grade, timestamp
         FROM audit_scores
         WHERE id IN (SELECT MAX(id) FROM audit_scores GROUP BY dimension)
         ORDER BY dimension",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok(json!({
            "dimension": row.get::<_, String>(0)?,
            "auto_score": row.get::<_, f64>(1)?,
            "manual_grade": row.get::<_, Option<String>>(2)?,
            "timestamp": row.get::<_, String>(3)?,
        }))
    })?;

    let scores: Vec<Value> = rows.filter_map(|r| r.ok()).collect();
    let overall: f64 = if scores.is_empty() {
        0.0
    } else {
        scores
            .iter()
            .filter_map(|s| s.get("auto_score").and_then(|v| v.as_f64()))
            .sum::<f64>()
            / scores.len() as f64
    };

    Ok(Json(json!({
        "snapshot": {
            "timestamp": chrono::Utc::now().to_rfc3339(),
            "scores": scores,
            "overall_score": (overall * 100.0).round() / 100.0,
            "track": "rust",
        }
    })))
}
