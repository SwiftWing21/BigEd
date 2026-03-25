use biged_core::config::BackupSection;
use biged_supervisor::backup::BackupManager;
use biged_supervisor::events::{create_event_bus, FleetEvent};
use std::path::PathBuf;

/// Helper: create a temporary fleet dir with dummy fleet.db and fleet.toml.
async fn setup_fleet_dir(tmp: &std::path::Path) {
    let fleet_dir = tmp.join("fleet");
    tokio::fs::create_dir_all(&fleet_dir).await.unwrap();
    tokio::fs::write(fleet_dir.join("fleet.db"), b"fake-db-content")
        .await
        .unwrap();
    tokio::fs::write(fleet_dir.join("fleet.toml"), b"[fleet]\noffline_mode = false\n")
        .await
        .unwrap();
}

#[tokio::test]
async fn test_backup_creates_files_in_temp_dir() {
    let tmp = tempfile::tempdir().unwrap();
    let fleet_dir = tmp.path().join("fleet");
    setup_fleet_dir(tmp.path()).await;

    let backup_location = tmp.path().join("backups");
    let config = BackupSection {
        enabled: true,
        interval_secs: 3600, // won't matter — we call do_backup directly via run_loop
        location: backup_location.to_string_lossy().into_owned(),
        depth: 5,
        prune_enabled: false,
        warn_disk_usage_pct: 80,
        extra: toml::Table::new(),
    };

    let (tx, mut rx) = create_event_bus(16);
    let manager = BackupManager::new(config, fleet_dir, tx);

    // Run the backup loop for just long enough to get one backup
    let handle = tokio::spawn(async move { manager.run_loop().await });

    // Wait for the BackupCompleted event
    let event = tokio::time::timeout(std::time::Duration::from_secs(5), rx.recv())
        .await
        .expect("Timed out waiting for backup event")
        .expect("Channel error");

    handle.abort();

    // Verify the event
    match event {
        FleetEvent::BackupCompleted { path, size_bytes } => {
            assert!(size_bytes > 0, "Backup should have non-zero size");
            let backup_path = PathBuf::from(&path);
            assert!(backup_path.exists(), "Backup directory should exist");
            assert!(
                backup_path.join("fleet.db").exists(),
                "fleet.db should be in backup"
            );
            assert!(
                backup_path.join("fleet.toml").exists(),
                "fleet.toml should be in backup"
            );
        }
        other => panic!("Expected BackupCompleted, got: {:?}", other),
    }
}

#[tokio::test]
async fn test_backup_prune_removes_old_beyond_depth() {
    let tmp = tempfile::tempdir().unwrap();
    let fleet_dir = tmp.path().join("fleet");
    setup_fleet_dir(tmp.path()).await;

    let backup_location = tmp.path().join("backups");

    // Pre-create 5 old backup directories
    tokio::fs::create_dir_all(&backup_location).await.unwrap();
    for i in 0..5 {
        let dir_name = format!("backup_20260101_00000{i}");
        let dir_path = backup_location.join(&dir_name);
        tokio::fs::create_dir_all(&dir_path).await.unwrap();
        tokio::fs::write(dir_path.join("fleet.db"), b"old")
            .await
            .unwrap();
    }

    // Config with depth=3 and prune_enabled
    let config = BackupSection {
        enabled: true,
        interval_secs: 3600,
        location: backup_location.to_string_lossy().into_owned(),
        depth: 3,
        prune_enabled: true,
        warn_disk_usage_pct: 80,
        extra: toml::Table::new(),
    };

    let (tx, mut rx) = create_event_bus(16);
    let manager = BackupManager::new(config, fleet_dir, tx);

    // Run the loop — it will create one new backup then prune
    let handle = tokio::spawn(async move { manager.run_loop().await });

    // Wait for backup event
    let _event = tokio::time::timeout(std::time::Duration::from_secs(5), rx.recv())
        .await
        .expect("Timed out")
        .expect("Channel error");

    handle.abort();

    // After prune: 5 old + 1 new = 6, depth=3, so 3 should remain
    let mut remaining = tokio::fs::read_dir(&backup_location).await.unwrap();
    let mut count = 0;
    while remaining.next_entry().await.unwrap().is_some() {
        count += 1;
    }
    assert_eq!(count, 3, "Should have pruned down to depth=3 backups");
}

#[tokio::test]
async fn test_backup_disabled_returns_immediately() {
    let tmp = tempfile::tempdir().unwrap();
    let fleet_dir = tmp.path().join("fleet");

    let config = BackupSection {
        enabled: false,
        interval_secs: 1,
        location: tmp.path().join("backups").to_string_lossy().into_owned(),
        depth: 5,
        prune_enabled: false,
        warn_disk_usage_pct: 80,
        extra: toml::Table::new(),
    };

    let (tx, _rx) = create_event_bus(16);
    let manager = BackupManager::new(config, fleet_dir, tx);

    // run_loop should return immediately when disabled
    let result = tokio::time::timeout(std::time::Duration::from_secs(2), manager.run_loop()).await;
    assert!(result.is_ok(), "Disabled backup loop should exit promptly");
}
