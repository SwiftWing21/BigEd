use crate::events::{EventSender, FleetEvent};
use biged_core::config::BackupSection;
use std::path::{Path, PathBuf};
use tracing::{info, warn};

/// Files to include in each backup snapshot.
const BACKUP_FILES: &[&str] = &["fleet.db", "fleet.toml"];

/// Periodic backup manager that copies fleet data to a timestamped directory
/// and prunes old snapshots beyond the configured depth.
pub struct BackupManager {
    config: BackupSection,
    fleet_dir: PathBuf,
    sender: EventSender,
}

impl BackupManager {
    pub fn new(config: BackupSection, fleet_dir: PathBuf, sender: EventSender) -> Self {
        Self {
            config,
            fleet_dir,
            sender,
        }
    }

    /// Run the backup loop forever, ticking at the configured interval.
    pub async fn run_loop(&self) {
        if !self.config.enabled {
            info!("Backup manager disabled by config");
            return;
        }

        let interval = std::time::Duration::from_secs(self.config.interval_secs);
        let mut tick = tokio::time::interval(interval);
        info!(
            "Backup manager started — interval={}s, depth={}, location={}",
            self.config.interval_secs, self.config.depth, self.config.location
        );

        loop {
            tick.tick().await;
            if let Err(e) = self.do_backup().await {
                warn!("Backup failed: {e}");
            }
        }
    }

    /// Perform a single backup: create timestamped directory, copy files, prune.
    async fn do_backup(&self) -> anyhow::Result<()> {
        let backup_root = self.resolve_backup_location();
        tokio::fs::create_dir_all(&backup_root).await?;

        let timestamp = chrono::Utc::now().format("%Y%m%d_%H%M%S").to_string();
        let snapshot_dir = backup_root.join(format!("backup_{timestamp}"));
        tokio::fs::create_dir_all(&snapshot_dir).await?;

        let mut total_size: u64 = 0;

        for filename in BACKUP_FILES {
            let src = self.fleet_dir.join(filename);
            let dst = snapshot_dir.join(filename);

            if !src.exists() {
                warn!("Backup source not found, skipping: {}", src.display());
                continue;
            }

            tokio::fs::copy(&src, &dst).await?;
            let meta = tokio::fs::metadata(&dst).await?;
            total_size += meta.len();
        }

        info!(
            "Backup completed: {} ({} bytes)",
            snapshot_dir.display(),
            total_size
        );

        let _ = self.sender.send(FleetEvent::BackupCompleted {
            path: snapshot_dir.to_string_lossy().into_owned(),
            size_bytes: total_size,
        });

        // Prune old snapshots if enabled
        if self.config.prune_enabled {
            if let Err(e) = self.prune_old_backups(&backup_root).await {
                warn!("Backup prune failed: {e}");
            }
        }

        Ok(())
    }

    /// Remove the oldest backup directories beyond the configured depth.
    async fn prune_old_backups(&self, backup_root: &Path) -> anyhow::Result<()> {
        let mut snapshots = Self::list_snapshot_dirs(backup_root).await?;
        if snapshots.len() <= self.config.depth as usize {
            return Ok(());
        }

        // Sort ascending by name (timestamp), so oldest are first
        snapshots.sort();

        let remove_count = snapshots.len() - self.config.depth as usize;
        for entry in snapshots.iter().take(remove_count) {
            let path = backup_root.join(entry);
            info!("Pruning old backup: {}", path.display());
            tokio::fs::remove_dir_all(&path).await?;
        }

        info!("Pruned {} old backup(s)", remove_count);
        Ok(())
    }

    /// List subdirectories matching the `backup_YYYYMMDD_HHMMSS` pattern.
    async fn list_snapshot_dirs(backup_root: &Path) -> anyhow::Result<Vec<String>> {
        let mut entries = tokio::fs::read_dir(backup_root).await?;
        let mut dirs = Vec::new();

        while let Some(entry) = entries.next_entry().await? {
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.starts_with("backup_") && entry.file_type().await?.is_dir() {
                dirs.push(name);
            }
        }

        Ok(dirs)
    }

    /// Resolve `~` in the configured backup location to the user home directory.
    /// Strips `..` components to prevent path traversal attacks (the backup
    /// location is user-configurable via PUT /api/settings).
    fn resolve_backup_location(&self) -> PathBuf {
        let loc = &self.config.location;
        let raw = if loc.starts_with("~/") || loc.starts_with("~\\") {
            let home = std::env::var("HOME")
                .or_else(|_| std::env::var("USERPROFILE"))
                .unwrap_or_else(|_| ".".into());
            PathBuf::from(home).join(&loc[2..])
        } else {
            PathBuf::from(loc)
        };

        // Normalize path components to prevent traversal
        let mut normalized = PathBuf::new();
        for component in raw.components() {
            match component {
                std::path::Component::ParentDir => {
                    warn!("Backup path contains '..', removing component");
                    // Don't go up — just skip
                }
                other => normalized.push(other),
            }
        }

        normalized
    }
}
