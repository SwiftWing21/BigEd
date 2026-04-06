use crate::error::AppError;
use crate::AppState;
use axum::extract::{Path, Query, State};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader};

#[derive(Deserialize)]
pub struct LogParams {
    lines: Option<usize>,
    level: Option<String>,
}

/// GET /api/logs/{source} — tail log file with optional level filter
///
/// Sources: "supervisor", "worker", "fleet" (maps to log files in fleet/logs/)
pub async fn tail(
    State(state): State<AppState>,
    Path(source): Path<String>,
    Query(params): Query<LogParams>,
) -> Result<Json<Value>, AppError> {
    let log_dir = state.fleet_dir.join("logs");
    let filename = match source.as_str() {
        "supervisor" => "supervisor.log",
        "worker" => "worker.log",
        "fleet" => "fleet.log",
        other => {
            return Err(AppError(anyhow::anyhow!(
                "Unknown log source: '{}'. Use: supervisor, worker, fleet",
                other
            )));
        }
    };

    let log_path = log_dir.join(filename);
    if !log_path.exists() {
        return Ok(Json(json!({
            "source": source,
            "lines": [],
            "total": 0,
            "note": format!("{} not found", filename),
        })));
    }

    let max_lines = params.lines.unwrap_or(100).min(500);
    let level_filter = params
        .level
        .as_deref()
        .unwrap_or("all")
        .to_uppercase();

    // Read file and take last N lines matching level filter
    let file = std::fs::File::open(&log_path)
        .map_err(|e| AppError(anyhow::anyhow!("Cannot read {}: {}", filename, e)))?;
    let reader = BufReader::new(file);

    let all_lines: Vec<String> = reader
        .lines()
        .filter_map(|l| l.ok())
        .collect();

    let filtered: Vec<&String> = if level_filter == "ALL" {
        all_lines.iter().rev().take(max_lines).collect()
    } else {
        all_lines
            .iter()
            .rev()
            .filter(|line| match level_filter.as_str() {
                "WARN" => {
                    line.contains("WARN") || line.contains("ERROR") || line.contains("CRITICAL")
                }
                "ERROR" => line.contains("ERROR") || line.contains("CRITICAL"),
                _ => true,
            })
            .take(max_lines)
            .collect()
    };

    // Reverse back to chronological order
    let result: Vec<&String> = filtered.into_iter().rev().collect();

    Ok(Json(json!({
        "source": source,
        "lines": result,
        "total": result.len(),
        "file": log_path.to_string_lossy(),
    })))
}
