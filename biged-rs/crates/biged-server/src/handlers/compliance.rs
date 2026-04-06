use crate::error::AppError;
use crate::AppState;
use axum::extract::{Path, State};
use axum::Json;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::Write;
use std::path::PathBuf;

/// POST /api/compliance/profiles — accept, hash, and store a compliance profile
pub async fn submit_profile(
    State(state): State<AppState>,
    Json(payload): Json<Value>,
) -> Result<Json<Value>, AppError> {
    let profiles_dir = compliance_dir(&state.fleet_dir);
    std::fs::create_dir_all(&profiles_dir)
        .map_err(|e| AppError(anyhow::anyhow!("Cannot create profiles dir: {}", e)))?;

    // Generate profile ID from timestamp
    let id = chrono::Utc::now().format("%Y%m%d_%H%M%S_%3f").to_string();
    let profile_json = serde_json::to_string_pretty(&payload)
        .map_err(|e| AppError(anyhow::anyhow!("Invalid profile JSON: {}", e)))?;

    // Compute SHA-256
    let mut hasher = Sha256::new();
    hasher.update(profile_json.as_bytes());
    let hash = format!("{:x}", hasher.finalize());

    // Write profile to disk
    let profile_path = profiles_dir.join(format!("{}.json", id));
    std::fs::write(&profile_path, &profile_json)
        .map_err(|e| AppError(anyhow::anyhow!("Failed to write profile: {}", e)))?;

    // Write hash sidecar
    let hash_path = profiles_dir.join(format!("{}.sha256", id));
    std::fs::write(&hash_path, &hash)
        .map_err(|e| AppError(anyhow::anyhow!("Failed to write hash: {}", e)))?;

    // Append to audit log (append-only)
    let log_path = profiles_dir.join("audit_log.jsonl");
    let log_entry = json!({
        "event": "profile_created",
        "profile_id": id,
        "sha256": hash,
        "timestamp": chrono::Utc::now().to_rfc3339(),
        "size_bytes": profile_json.len(),
        "track": "rust",
    });
    let mut log_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| AppError(anyhow::anyhow!("Failed to open audit log: {}", e)))?;
    writeln!(log_file, "{}", serde_json::to_string(&log_entry).unwrap_or_default())
        .map_err(|e| AppError(anyhow::anyhow!("Failed to write audit log: {}", e)))?;

    Ok(Json(json!({
        "status": "ok",
        "profile_id": id,
        "sha256": hash,
        "stored_at": profile_path.to_string_lossy(),
    })))
}

/// GET /api/compliance/profiles/{id} — retrieve a stored profile
pub async fn get_profile(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, AppError> {
    // Validate ID format (no path traversal)
    if id.contains("..") || id.contains('/') || id.contains('\\') {
        return Err(AppError(anyhow::anyhow!("Invalid profile ID")));
    }

    let profiles_dir = compliance_dir(&state.fleet_dir);
    let profile_path = profiles_dir.join(format!("{}.json", id));
    let hash_path = profiles_dir.join(format!("{}.sha256", id));

    if !profile_path.exists() {
        return Err(AppError(anyhow::anyhow!("Profile '{}' not found", id)));
    }

    let profile_data = std::fs::read_to_string(&profile_path)
        .map_err(|e| AppError(anyhow::anyhow!("Failed to read profile: {}", e)))?;
    let stored_hash = std::fs::read_to_string(&hash_path).unwrap_or_default();

    let profile: Value = serde_json::from_str(&profile_data)
        .map_err(|e| AppError(anyhow::anyhow!("Invalid profile JSON on disk: {}", e)))?;

    Ok(Json(json!({
        "profile_id": id,
        "profile": profile,
        "sha256": stored_hash.trim(),
    })))
}

/// GET /api/compliance/profiles/{id}/verify — verify profile integrity
pub async fn verify_profile(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, AppError> {
    if id.contains("..") || id.contains('/') || id.contains('\\') {
        return Err(AppError(anyhow::anyhow!("Invalid profile ID")));
    }

    let profiles_dir = compliance_dir(&state.fleet_dir);
    let profile_path = profiles_dir.join(format!("{}.json", id));
    let hash_path = profiles_dir.join(format!("{}.sha256", id));

    if !profile_path.exists() {
        return Err(AppError(anyhow::anyhow!("Profile '{}' not found", id)));
    }

    let profile_data = std::fs::read_to_string(&profile_path)
        .map_err(|e| AppError(anyhow::anyhow!("Failed to read profile: {}", e)))?;
    let stored_hash = std::fs::read_to_string(&hash_path)
        .map_err(|e| AppError(anyhow::anyhow!("No hash sidecar found: {}", e)))?
        .trim()
        .to_string();

    // Recompute hash
    let mut hasher = Sha256::new();
    hasher.update(profile_data.as_bytes());
    let computed_hash = format!("{:x}", hasher.finalize());

    let valid = computed_hash == stored_hash;

    // Log verification event
    let log_path = profiles_dir.join("audit_log.jsonl");
    let log_entry = json!({
        "event": "profile_verified",
        "profile_id": id,
        "valid": valid,
        "timestamp": chrono::Utc::now().to_rfc3339(),
    });
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&log_path) {
        let _ = writeln!(f, "{}", serde_json::to_string(&log_entry).unwrap_or_default());
    }

    Ok(Json(json!({
        "profile_id": id,
        "valid": valid,
        "stored_hash": stored_hash,
        "computed_hash": computed_hash,
    })))
}

/// GET /api/compliance/audit_log — read the append-only event log
pub async fn audit_log(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let log_path = compliance_dir(&state.fleet_dir).join("audit_log.jsonl");

    if !log_path.exists() {
        return Ok(Json(json!({ "events": [] })));
    }

    let data = std::fs::read_to_string(&log_path)
        .map_err(|e| AppError(anyhow::anyhow!("Failed to read audit log: {}", e)))?;

    let events: Vec<Value> = data
        .lines()
        .rev() // newest first
        .take(200)
        .filter_map(|line| serde_json::from_str(line).ok())
        .collect();

    Ok(Json(json!({ "events": events })))
}

fn compliance_dir(fleet_dir: &std::path::Path) -> PathBuf {
    fleet_dir.join("compliance_profiles")
}
