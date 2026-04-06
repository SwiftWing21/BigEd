use crate::error::AppError;
use crate::AppState;
use axum::extract::State;
use axum::Json;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// POST /api/backup/trigger — trigger a manual backup
pub async fn trigger(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let config = state.config.read().await;
    let backup_dir = backup_location(&config);
    let fleet_dir = state.fleet_dir.clone();

    // Run backup in a blocking task (file I/O heavy)
    let result = tokio::task::spawn_blocking(move || perform_backup(&fleet_dir, &backup_dir))
        .await
        .map_err(|e| AppError(anyhow::anyhow!("Backup task panicked: {}", e)))?
        .map_err(|e| AppError(anyhow::anyhow!("Backup failed: {}", e)))?;

    Ok(Json(result))
}

/// GET /api/backup/list — list available backups with manifests
pub async fn list(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    let config = state.config.read().await;
    let backup_dir = backup_location(&config);

    if !backup_dir.exists() {
        return Ok(Json(json!({ "backups": [] })));
    }

    let mut backups = Vec::new();
    let mut entries: Vec<_> = std::fs::read_dir(&backup_dir)
        .map_err(|e| AppError(anyhow::anyhow!("Cannot read backup dir: {}", e)))?
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir())
        .collect();
    entries.sort_by(|a, b| b.file_name().cmp(&a.file_name())); // newest first

    for entry in entries.iter().take(20) {
        let manifest_path = entry.path().join("manifest.json");
        if manifest_path.exists() {
            if let Ok(data) = std::fs::read_to_string(&manifest_path) {
                if let Ok(manifest) = serde_json::from_str::<Value>(&data) {
                    backups.push(manifest);
                }
            }
        } else {
            // Legacy backup without manifest — show basic info
            let name = entry.file_name().to_string_lossy().to_string();
            let size: u64 = walkdir_size(&entry.path());
            backups.push(json!({
                "id": name,
                "timestamp": name,
                "trigger": "unknown",
                "total_size_bytes": size,
                "legacy": true,
            }));
        }
    }

    Ok(Json(json!({ "backups": backups })))
}

/// POST /api/backup/restore — restore from a backup (fleet.db + config only)
pub async fn restore(
    State(state): State<AppState>,
    Json(payload): Json<Value>,
) -> Result<Json<Value>, AppError> {
    let backup_id = payload
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError(anyhow::anyhow!("backup id is required")))?;

    // Validate backup ID format (timestamp-like, no path traversal)
    if backup_id.contains("..") || backup_id.contains('/') || backup_id.contains('\\') {
        return Err(AppError(anyhow::anyhow!("Invalid backup ID")));
    }

    let config = state.config.read().await;
    let backup_dir = backup_location(&config).join(backup_id);
    let fleet_dir = state.fleet_dir.clone();

    if !backup_dir.exists() {
        return Err(AppError(anyhow::anyhow!(
            "Backup '{}' not found",
            backup_id
        )));
    }

    let result =
        tokio::task::spawn_blocking(move || perform_restore(&fleet_dir, &backup_dir))
            .await
            .map_err(|e| AppError(anyhow::anyhow!("Restore task panicked: {}", e)))?
            .map_err(|e| AppError(anyhow::anyhow!("Restore failed: {}", e)))?;

    Ok(Json(result))
}

// ── Backup implementation ────────────────────────────────────────────────

fn perform_backup(fleet_dir: &Path, backup_root: &Path) -> anyhow::Result<Value> {
    use std::io::Write;

    let ts = chrono::Local::now().format("%Y%m%d_%H%M%S").to_string();
    let dest = backup_root.join(&ts);
    std::fs::create_dir_all(&dest)?;

    let mut manifest = json!({
        "id": ts,
        "timestamp": chrono::Utc::now().to_rfc3339(),
        "trigger": "manual_rust",
        "location": dest.to_string_lossy(),
        "files": {},
        "total_size_bytes": 0u64,
    });

    let mut total: u64 = 0;

    // Checkpoint WAL before backup
    for db_name in ["fleet.db", "rag.db"] {
        let db_path = fleet_dir.join(db_name);
        if db_path.exists() {
            if let Ok(conn) = rusqlite::Connection::open(&db_path) {
                let _ = conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE)");
            }
        }
    }

    // Copy fleet.db
    let fleet_db = fleet_dir.join("fleet.db");
    if fleet_db.exists() {
        let dest_db = dest.join("fleet.db");
        std::fs::copy(&fleet_db, &dest_db)?;
        let size = dest_db.metadata()?.len();
        manifest["files"]["fleet.db"] = json!({ "size_bytes": size });
        total += size;
    }

    // Copy rag.db
    let rag_db = fleet_dir.join("rag.db");
    if rag_db.exists() {
        let dest_db = dest.join("rag.db");
        std::fs::copy(&rag_db, &dest_db)?;
        let size = dest_db.metadata()?.len();
        manifest["files"]["rag.db"] = json!({ "size_bytes": size });
        total += size;
    }

    // Copy fleet.toml
    let config_file = fleet_dir.join("fleet.toml");
    if config_file.exists() {
        let dest_cfg = dest.join("fleet.toml");
        std::fs::copy(&config_file, &dest_cfg)?;
        let size = dest_cfg.metadata()?.len();
        manifest["files"]["fleet.toml"] = json!({ "size_bytes": size });
        total += size;
    }

    // Zip knowledge directory (v3 backup compression)
    let knowledge_dir = fleet_dir.join("knowledge");
    if knowledge_dir.exists() {
        let zip_path = dest.join("knowledge.zip");
        let mut file_count = 0u64;
        let mut uncompressed = 0u64;

        let zip_file = std::fs::File::create(&zip_path)?;
        let mut zip = zip::ZipWriter::new(zip_file);
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated)
            .compression_level(Some(6));

        for entry in walkdir::WalkDir::new(&knowledge_dir)
            .into_iter()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_type().is_file())
        {
            let rel = entry
                .path()
                .strip_prefix(fleet_dir)
                .unwrap_or(entry.path());
            let rel_str = rel.to_string_lossy().replace('\\', "/");
            zip.start_file(&rel_str, options)?;
            let data = std::fs::read(entry.path())?;
            uncompressed += data.len() as u64;
            file_count += 1;
            zip.write_all(&data)?;
        }
        zip.finish()?;
        let zip_size = std::fs::metadata(&zip_path)?.len();

        manifest["files"]["knowledge.zip"] = json!({
            "size_bytes": zip_size,
            "uncompressed_bytes": uncompressed,
            "file_count": file_count,
        });
        total += zip_size;
    }

    manifest["total_size_bytes"] = json!(total);

    // Write manifest
    let manifest_path = dest.join("manifest.json");
    std::fs::write(&manifest_path, serde_json::to_string_pretty(&manifest)?)?;

    // Prune old backups (keep 10)
    prune_backups(backup_root, 10);

    Ok(manifest)
}

fn perform_restore(fleet_dir: &Path, backup_dir: &Path) -> anyhow::Result<Value> {
    let mut restored = Vec::new();

    // Restore fleet.db
    let backup_db = backup_dir.join("fleet.db");
    if backup_db.exists() {
        let dest = fleet_dir.join("fleet.db");
        std::fs::copy(&backup_db, &dest)?;
        restored.push("fleet.db");
    }

    // Restore rag.db
    let backup_rag = backup_dir.join("rag.db");
    if backup_rag.exists() {
        let dest = fleet_dir.join("rag.db");
        std::fs::copy(&backup_rag, &dest)?;
        restored.push("rag.db");
    }

    // Restore fleet.toml
    let backup_cfg = backup_dir.join("fleet.toml");
    if backup_cfg.exists() {
        let dest = fleet_dir.join("fleet.toml");
        std::fs::copy(&backup_cfg, &dest)?;
        restored.push("fleet.toml");
    }

    Ok(json!({
        "status": "ok",
        "restored": restored,
        "from": backup_dir.to_string_lossy(),
    }))
}

fn prune_backups(backup_root: &Path, keep: usize) {
    if !backup_root.exists() {
        return;
    }
    let mut dirs: Vec<_> = std::fs::read_dir(backup_root)
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir())
        .collect();
    dirs.sort_by(|a, b| b.file_name().cmp(&a.file_name()));
    for old in dirs.iter().skip(keep) {
        let _ = std::fs::remove_dir_all(old.path());
    }
}

fn walkdir_size(path: &Path) -> u64 {
    walkdir::WalkDir::new(path)
        .into_iter()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_type().is_file())
        .filter_map(|e| e.metadata().ok())
        .map(|m| m.len())
        .sum()
}

fn backup_location(
    config: &biged_core::config::FleetConfig,
) -> PathBuf {
    if let Some(loc) = config.extra.get("backup").and_then(|b| b.get("location")).and_then(|l| l.as_str()) {
        PathBuf::from(loc)
    } else {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("BigEd-backups")
    }
}
