use biged_core::config::FleetConfig;
use biged_core::db::Db;
use biged_supervisor::events::{create_event_bus, FleetEvent, ThermalAction};
use std::sync::Arc;
use tokio::sync::RwLock;

// ── Supervisor start/stop ────────────────────────────────────────────────────

#[tokio::test]
async fn test_supervisor_starts_and_stops() {
    let db = Db::in_memory().unwrap();
    let config = Arc::new(RwLock::new(FleetConfig::default()));
    let (tx, _rx) = create_event_bus(100);

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

// ── FleetEvent serialization ─────────────────────────────────────────────────

#[test]
fn test_fleet_event_task_completed_serde() {
    let event = FleetEvent::TaskCompleted {
        id: 42,
        skill: "code_review".to_string(),
        agent: "coder_1".to_string(),
    };
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    if let FleetEvent::TaskCompleted { id, skill, agent } = deser {
        assert_eq!(id, 42);
        assert_eq!(skill, "code_review");
        assert_eq!(agent, "coder_1");
    } else {
        panic!("Expected TaskCompleted variant");
    }
}

#[test]
fn test_fleet_event_task_failed_serde() {
    let event = FleetEvent::TaskFailed {
        id: 7,
        skill: "test_skill".to_string(),
        error: "timeout".to_string(),
    };
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    if let FleetEvent::TaskFailed { id, skill, error } = deser {
        assert_eq!(id, 7);
        assert_eq!(skill, "test_skill");
        assert_eq!(error, "timeout");
    } else {
        panic!("Expected TaskFailed variant");
    }
}

#[test]
fn test_fleet_event_agent_state_change_serde() {
    let event = FleetEvent::AgentStateChange {
        agent: "coder_1".to_string(),
        from: "IDLE".to_string(),
        to: "BUSY".to_string(),
    };
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    if let FleetEvent::AgentStateChange { agent, from, to } = deser {
        assert_eq!(agent, "coder_1");
        assert_eq!(from, "IDLE");
        assert_eq!(to, "BUSY");
    } else {
        panic!("Expected AgentStateChange variant");
    }
}

#[test]
fn test_fleet_event_thermal_alert_serde() {
    let event = FleetEvent::ThermalAlert {
        gpu_temp: 85.5,
        action: ThermalAction::ThrottleDown,
    };
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    if let FleetEvent::ThermalAlert { gpu_temp, action } = deser {
        assert!((gpu_temp - 85.5).abs() < 0.01);
        assert!(matches!(action, ThermalAction::ThrottleDown));
    } else {
        panic!("Expected ThermalAlert variant");
    }
}

#[test]
fn test_fleet_event_model_transition_serde() {
    let event = FleetEvent::ModelTransition {
        from: "qwen3:8b".to_string(),
        to: "qwen3:4b".to_string(),
        reason: "thermal throttle".to_string(),
    };
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    if let FleetEvent::ModelTransition { from, to, reason } = deser {
        assert_eq!(from, "qwen3:8b");
        assert_eq!(to, "qwen3:4b");
        assert_eq!(reason, "thermal throttle");
    } else {
        panic!("Expected ModelTransition variant");
    }
}

#[test]
fn test_fleet_event_config_reloaded_serde() {
    let event = FleetEvent::ConfigReloaded;
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    assert!(matches!(deser, FleetEvent::ConfigReloaded));
}

#[test]
fn test_fleet_event_scale_up_serde() {
    let event = FleetEvent::ScaleUp {
        count: 3,
        reason: "queue pressure".to_string(),
    };
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    if let FleetEvent::ScaleUp { count, reason } = deser {
        assert_eq!(count, 3);
        assert_eq!(reason, "queue pressure");
    } else {
        panic!("Expected ScaleUp variant");
    }
}

#[test]
fn test_fleet_event_scale_down_serde() {
    let event = FleetEvent::ScaleDown { count: 2 };
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    if let FleetEvent::ScaleDown { count } = deser {
        assert_eq!(count, 2);
    } else {
        panic!("Expected ScaleDown variant");
    }
}

#[test]
fn test_fleet_event_worker_crashed_serde() {
    let event = FleetEvent::WorkerCrashed {
        agent: "coder_2".to_string(),
        exit_code: Some(1),
    };
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    if let FleetEvent::WorkerCrashed { agent, exit_code } = deser {
        assert_eq!(agent, "coder_2");
        assert_eq!(exit_code, Some(1));
    } else {
        panic!("Expected WorkerCrashed variant");
    }
}

#[test]
fn test_fleet_event_worker_crashed_no_exit_code() {
    let event = FleetEvent::WorkerCrashed {
        agent: "researcher_1".to_string(),
        exit_code: None,
    };
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    if let FleetEvent::WorkerCrashed { exit_code, .. } = deser {
        assert_eq!(exit_code, None);
    } else {
        panic!("Expected WorkerCrashed variant");
    }
}

#[test]
fn test_fleet_event_backup_completed_serde() {
    let event = FleetEvent::BackupCompleted {
        path: "/backups/fleet-20260324.db".to_string(),
        size_bytes: 1_048_576,
    };
    let json = serde_json::to_string(&event).unwrap();
    let deser: FleetEvent = serde_json::from_str(&json).unwrap();
    if let FleetEvent::BackupCompleted { path, size_bytes } = deser {
        assert_eq!(path, "/backups/fleet-20260324.db");
        assert_eq!(size_bytes, 1_048_576);
    } else {
        panic!("Expected BackupCompleted variant");
    }
}

#[test]
fn test_all_thermal_action_variants_serde() {
    let actions = [
        ThermalAction::ThrottleDown,
        ThermalAction::CooldownStart,
        ThermalAction::CooldownEnd,
        ThermalAction::EmergencyStop,
    ];
    for action in &actions {
        let json = serde_json::to_string(action).unwrap();
        let deser: ThermalAction = serde_json::from_str(&json).unwrap();
        // Verify round-trip by re-serializing
        let json2 = serde_json::to_string(&deser).unwrap();
        assert_eq!(json, json2, "ThermalAction round-trip failed for {:?}", action);
    }
}

// ── Event type string ────────────────────────────────────────────────────────

#[test]
fn test_fleet_event_type_strings() {
    assert_eq!(
        FleetEvent::TaskCompleted {
            id: 0,
            skill: String::new(),
            agent: String::new()
        }
        .event_type(),
        "task_completed"
    );
    assert_eq!(
        FleetEvent::TaskFailed {
            id: 0,
            skill: String::new(),
            error: String::new()
        }
        .event_type(),
        "task_failed"
    );
    assert_eq!(
        FleetEvent::AgentStateChange {
            agent: String::new(),
            from: String::new(),
            to: String::new()
        }
        .event_type(),
        "agent_state"
    );
    assert_eq!(
        FleetEvent::ThermalAlert {
            gpu_temp: 0.0,
            action: ThermalAction::ThrottleDown
        }
        .event_type(),
        "thermal"
    );
    assert_eq!(
        FleetEvent::ModelTransition {
            from: String::new(),
            to: String::new(),
            reason: String::new()
        }
        .event_type(),
        "model_transition"
    );
    assert_eq!(FleetEvent::ConfigReloaded.event_type(), "config_reloaded");
    assert_eq!(
        FleetEvent::ScaleUp {
            count: 0,
            reason: String::new()
        }
        .event_type(),
        "scale_up"
    );
    assert_eq!(
        FleetEvent::ScaleDown { count: 0 }.event_type(),
        "scale_down"
    );
    assert_eq!(
        FleetEvent::WorkerCrashed {
            agent: String::new(),
            exit_code: None
        }
        .event_type(),
        "worker_crashed"
    );
    assert_eq!(
        FleetEvent::BackupCompleted {
            path: String::new(),
            size_bytes: 0
        }
        .event_type(),
        "backup_completed"
    );
}

// ── Event bus send/receive ───────────────────────────────────────────────────

#[tokio::test]
async fn test_event_bus_send_receive() {
    let (tx, mut rx) = create_event_bus(10);

    tx.send(FleetEvent::ConfigReloaded).unwrap();

    let event = rx.recv().await.unwrap();
    assert!(matches!(event, FleetEvent::ConfigReloaded));
}

#[tokio::test]
async fn test_event_bus_multiple_events() {
    let (tx, mut rx) = create_event_bus(10);

    tx.send(FleetEvent::ConfigReloaded).unwrap();
    tx.send(FleetEvent::ScaleUp {
        count: 2,
        reason: "test".to_string(),
    })
    .unwrap();
    tx.send(FleetEvent::ScaleDown { count: 1 }).unwrap();

    let e1 = rx.recv().await.unwrap();
    let e2 = rx.recv().await.unwrap();
    let e3 = rx.recv().await.unwrap();

    assert!(matches!(e1, FleetEvent::ConfigReloaded));
    assert!(matches!(e2, FleetEvent::ScaleUp { count: 2, .. }));
    assert!(matches!(e3, FleetEvent::ScaleDown { count: 1 }));
}

#[tokio::test]
async fn test_event_bus_broadcast_to_multiple_receivers() {
    let (tx, mut rx1) = create_event_bus(10);
    let mut rx2 = tx.subscribe();

    tx.send(FleetEvent::ConfigReloaded).unwrap();

    let e1 = rx1.recv().await.unwrap();
    let e2 = rx2.recv().await.unwrap();

    assert!(matches!(e1, FleetEvent::ConfigReloaded));
    assert!(matches!(e2, FleetEvent::ConfigReloaded));
}

// ── Thermal thresholds from config ───────────────────────────────────────────

#[test]
fn test_thermal_thresholds_defaults() {
    let config = FleetConfig::default();
    assert_eq!(config.thermal.gpu_target_c, 75);
    assert_eq!(config.thermal.gpu_max_sustained_c, 82);
    assert_eq!(config.thermal.gpu_max_burst_c, 85);
    assert_eq!(config.thermal.cpu_max_sustained_c, 85);
    assert_eq!(config.thermal.cooldown_target_c, 75);
}

#[test]
fn test_thermal_thresholds_ordering() {
    let config = FleetConfig::default();
    // target < max_sustained < max_burst
    assert!(config.thermal.gpu_target_c < config.thermal.gpu_max_sustained_c);
    assert!(config.thermal.gpu_max_sustained_c < config.thermal.gpu_max_burst_c);
    // cooldown target should be <= gpu target
    assert!(config.thermal.cooldown_target_c <= config.thermal.gpu_target_c);
}

#[test]
fn test_thermal_thresholds_custom() {
    let toml_str = r#"
[thermal]
gpu_target_c = 70
gpu_max_sustained_c = 80
gpu_max_burst_c = 90
cpu_max_sustained_c = 80
cooldown_target_c = 65
cooldown_window_secs = 180
poll_interval_secs = 10
"#;
    let config = FleetConfig::parse(toml_str).unwrap();
    assert_eq!(config.thermal.gpu_target_c, 70);
    assert_eq!(config.thermal.gpu_max_sustained_c, 80);
    assert_eq!(config.thermal.gpu_max_burst_c, 90);
    assert_eq!(config.thermal.cooldown_window_secs, 180);
    assert_eq!(config.thermal.poll_interval_secs, 10);
}

// ── Health checker — stale agent detection ───────────────────────────────────

#[tokio::test]
async fn test_health_checker_detects_stale_agents() {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = create_event_bus(100);

    // Register an agent, then backdate its heartbeat to 10 minutes ago
    db.register_agent("stale_worker", "coder").unwrap();
    {
        let pool = db.pool_ref();
        let conn = pool.get().unwrap();
        conn.execute(
            "UPDATE agents SET last_heartbeat = datetime('now', '-600 seconds') WHERE name = 'stale_worker'",
            [],
        ).unwrap();
    }

    let checker = biged_supervisor::health::HealthChecker::new(db.clone(), tx);

    // With a 60-second threshold, the 10-min-old heartbeat should be stale
    checker.check_agent_health(60).await.unwrap();

    let agent = db.get_agent("stale_worker").unwrap().unwrap();
    assert_eq!(
        agent.status, "OFFLINE",
        "Agent with stale heartbeat should be OFFLINE"
    );
}

#[tokio::test]
async fn test_health_checker_keeps_fresh_agents() {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = create_event_bus(100);

    // Register an agent — heartbeat is "now"
    db.register_agent("fresh_worker", "coder").unwrap();
    // Immediately heartbeat to refresh
    db.heartbeat("fresh_worker").unwrap();

    let checker = biged_supervisor::health::HealthChecker::new(db.clone(), tx);

    // With a generous threshold, agent should stay IDLE
    checker.check_agent_health(3600).await.unwrap();

    let agent = db.get_agent("fresh_worker").unwrap().unwrap();
    assert_eq!(
        agent.status, "IDLE",
        "Fresh agent should remain IDLE"
    );
}

#[tokio::test]
async fn test_health_checker_recover_stale_tasks() {
    let db = Db::in_memory().unwrap();
    let (tx, _rx) = create_event_bus(100);

    db.register_agent("w1", "coder").unwrap();
    let id = db.post_task("stuck_skill", "{}", 5, None).unwrap();
    db.claim_task("coder").unwrap();

    // Task is now RUNNING — backdate it so it looks stale
    {
        let pool = db.pool_ref();
        let conn = pool.get().unwrap();
        conn.execute(
            "UPDATE tasks SET created_at = datetime('now', '-600 seconds') WHERE id = ?1",
            [id],
        ).unwrap();
    }

    let task = db.get_task(id).unwrap().unwrap();
    assert_eq!(task.status, biged_core::types::TaskStatus::Running);

    let checker = biged_supervisor::health::HealthChecker::new(db.clone(), tx);

    // 60s threshold — the 10-min-old task should be recovered
    let recovered = checker.recover_stale_tasks(60).await.unwrap();
    assert!(recovered >= 1, "Should recover at least 1 stale task");

    // Task should be back to PENDING
    let task = db.get_task(id).unwrap().unwrap();
    assert_eq!(
        task.status,
        biged_core::types::TaskStatus::Pending,
        "Recovered task should be PENDING"
    );
}
