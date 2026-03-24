use biged_core::config::FleetConfig;
use biged_core::db::Db;
use std::sync::Arc;
use tokio::sync::RwLock;

#[tokio::test]
async fn test_supervisor_starts_and_stops() {
    let db = Db::in_memory().unwrap();
    let config = Arc::new(RwLock::new(FleetConfig::default()));
    let (tx, _rx) = biged_supervisor::events::create_event_bus(100);

    let supervisor = biged_supervisor::supervisor::Supervisor::new(
        config,
        db,
        tx,
        std::path::PathBuf::from("."),
    );

    // Start supervisor, let it run for 2 seconds, then stop
    let handle = tokio::spawn(async move { supervisor.run().await });

    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    handle.abort();
    // If we get here without panic, supervisor ran cleanly
}
