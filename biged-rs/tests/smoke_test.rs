use biged_core::config::FleetConfig;
use biged_core::db::Db;
use std::sync::Arc;
use tokio::sync::RwLock;

#[test]
fn smoke_config_parses() {
    let fleet_toml = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fleet/fleet.toml");
    if !fleet_toml.exists() {
        eprintln!("SKIPPED: fleet.toml not found at {}", fleet_toml.display());
        return;
    }
    FleetConfig::from_file(&fleet_toml).expect("fleet.toml should parse");
}

#[test]
fn smoke_db_roundtrip() {
    let db = Db::in_memory().unwrap();
    db.register_agent("test", "coder").unwrap();
    let id = db.post_task("test_skill", "{}", 5, None).unwrap();
    assert!(id > 0);
    let task = db.claim_task("coder").unwrap();
    assert!(task.is_some());
    db.complete_task(id, r#"{"ok":true}"#).unwrap();
    let t = db.get_task(id).unwrap().unwrap();
    assert_eq!(t.status, biged_core::types::TaskStatus::Done);
}

#[test]
fn smoke_queue_depth() {
    let db = Db::in_memory().unwrap();
    assert_eq!(db.queue_depth().unwrap(), 0);
    db.post_task("a", "{}", 5, None).unwrap();
    assert_eq!(db.queue_depth().unwrap(), 1);
}

#[tokio::test]
async fn smoke_event_bus() {
    let (tx, mut rx) = biged_supervisor::events::create_event_bus(10);
    tx.send(biged_supervisor::events::FleetEvent::ConfigReloaded)
        .unwrap();
    let event = rx.recv().await.unwrap();
    assert!(matches!(
        event,
        biged_supervisor::events::FleetEvent::ConfigReloaded
    ));
}

#[tokio::test]
async fn smoke_server_router_builds() {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = biged_supervisor::events::create_event_bus(10);
    let state = biged_server::AppState {
        db,
        events: tx,
        config: Arc::new(RwLock::new(FleetConfig::default())),
        fleet_dir: std::path::PathBuf::from("."),
    };
    let _router = biged_server::router(state);
    // If we get here, router construction succeeded
}

#[test]
fn smoke_bridge_config() {
    let config = biged_bridge::BridgeConfig::new(std::path::PathBuf::from("fleet"));
    assert_eq!(
        config.timeout_for("code_write"),
        std::time::Duration::from_secs(900)
    );
    assert_eq!(
        config.timeout_for("unknown_skill"),
        std::time::Duration::from_secs(600)
    );
}
