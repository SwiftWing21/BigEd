use biged_core::config::WorkersSection;
use biged_core::db::Db;
use biged_supervisor::events::{create_event_bus, FleetEvent};
use biged_supervisor::scaler::AutoScaler;

fn default_workers_config(max: u32) -> WorkersSection {
    WorkersSection {
        max_workers: max,
        nice_level: 15,
        cpu_limit_percent: 10,
        coder_count: 3,
        memory_limit_mb: 384,
        extra: toml::Table::new(),
    }
}

#[tokio::test]
async fn test_scale_up_when_queue_exceeds_threshold() {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = create_event_bus(16);

    let mut scaler = AutoScaler::new(default_workers_config(10), db.clone(), tx);
    assert_eq!(scaler.current_workers(), 3); // initial = coder_count

    // Post 10 pending tasks — exceeds 3 * 2 = 6 threshold
    for _ in 0..10 {
        db.post_task("test_skill", "{}", 1, None).unwrap();
    }

    // compute_desired should want more workers
    let desired = scaler.compute_desired_workers(10);
    assert!(
        desired > 3,
        "Should scale up from 3 when queue_depth=10, got {}",
        desired
    );
    assert!(
        desired <= 10,
        "Should not exceed max_workers=10, got {}",
        desired
    );
}

#[tokio::test]
async fn test_scale_down_when_queue_empty() {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = create_event_bus(16);

    let mut scaler = AutoScaler::new(default_workers_config(10), db, tx);

    // Simulate 10 consecutive empty checks (5 minutes at 30s intervals)
    for _ in 0..10 {
        let _ = scaler.compute_desired_workers(0);
    }

    // After 10 empty checks, next compute should scale down
    let desired = scaler.compute_desired_workers(0);
    assert!(
        desired < 3,
        "Should scale down after prolonged empty queue, got {}",
        desired
    );
    assert!(desired >= 1, "Should never go below 1 worker, got {}", desired);
}

#[tokio::test]
async fn test_respects_max_workers_ceiling() {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = create_event_bus(16);

    let mut scaler = AutoScaler::new(default_workers_config(4), db, tx);

    // Even with massive queue, should not exceed max_workers=4
    let desired = scaler.compute_desired_workers(1000);
    assert_eq!(desired, 4, "Should cap at max_workers=4");
}

#[tokio::test]
async fn test_no_change_when_queue_within_threshold() {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = create_event_bus(16);

    let mut scaler = AutoScaler::new(default_workers_config(10), db, tx);
    let initial = scaler.current_workers();

    // queue_depth=5 with 3 workers: 5 < 3*2=6 — no scale needed
    let desired = scaler.compute_desired_workers(5);
    assert_eq!(desired, initial, "Should not change when queue within threshold");
}

#[tokio::test]
async fn test_scaler_emits_scale_up_event() {
    let db = Db::in_memory().unwrap();
    let (tx, mut rx) = create_event_bus(16);

    // Post many tasks to trigger scale-up
    for _ in 0..20 {
        db.post_task("test_skill", "{}", 1, None).unwrap();
    }

    let mut scaler = AutoScaler::new(default_workers_config(10), db, tx);

    // Trigger a check
    scaler.check_and_scale().unwrap();

    // Should have emitted ScaleUp
    let event = rx.try_recv().expect("Should have received ScaleUp event");
    match event {
        FleetEvent::ScaleUp { count, reason } => {
            assert!(count > 0, "Scale-up count should be positive");
            assert!(
                reason.contains("queue_depth"),
                "Reason should mention queue_depth"
            );
        }
        other => panic!("Expected ScaleUp, got: {:?}", other),
    }
}

#[tokio::test]
async fn test_scale_resets_empty_counter_on_work() {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = create_event_bus(16);

    let mut scaler = AutoScaler::new(default_workers_config(10), db, tx);

    // Accumulate 8 empty checks
    for _ in 0..8 {
        let _ = scaler.compute_desired_workers(0);
    }

    // Now work arrives — should reset counter
    let _ = scaler.compute_desired_workers(1);

    // Another 8 empty checks should NOT trigger scale-down (need 10+ to trigger)
    for _ in 0..8 {
        let _ = scaler.compute_desired_workers(0);
    }

    let desired = scaler.compute_desired_workers(0);
    // We had 8 empty checks after reset + the one we just did = 9, still below 10
    // So no scale down yet
    assert_eq!(
        desired,
        scaler.current_workers(),
        "Should not scale down yet — only 9 consecutive empty checks after reset"
    );
}
