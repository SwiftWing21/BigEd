use biged_core::config::FleetConfig;

#[test]
fn test_parse_existing_fleet_toml() {
    // Read the real fleet.toml from the project root (one level up from biged-rs/)
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../fleet/fleet.toml");
    let cfg = FleetConfig::from_file(&path).expect("should parse existing fleet.toml");

    assert!(!cfg.fleet.offline_mode, "offline_mode should be false");
    assert!(cfg.dashboard.port > 0, "dashboard port should be > 0");
}

#[test]
fn test_parse_minimal_config() {
    let toml_str = r#"
[fleet]
offline_mode = true

[models]
conductor_model = "llama3:8b"

[dashboard]
port = 9999
enabled = false

[workers]
max_workers = 4

[thermal]
gpu_target_c = 70
"#;
    let cfg = FleetConfig::parse(toml_str).expect("should parse minimal TOML");

    // Explicit values
    assert!(cfg.fleet.offline_mode, "offline_mode should be true");
    assert_eq!(cfg.models.conductor_model, "llama3:8b");
    assert_eq!(cfg.dashboard.port, 9999);
    assert!(!cfg.dashboard.enabled);
    assert_eq!(cfg.workers.max_workers, 4);
    assert_eq!(cfg.thermal.gpu_target_c, 70);

    // Defaults for fields not provided in the minimal TOML
    assert!(
        !cfg.fleet.air_gap_mode,
        "air_gap_mode should default to false"
    );
    assert_eq!(
        cfg.fleet.disabled_agents,
        vec![
            "sales",
            "onboarding",
            "implementation",
            "legal",
            "account_manager",
            "ds_rag",
            "ds_fleet",
            "ds_research",
        ]
    );
    assert_eq!(cfg.models.complex, "qwen3:8b");
    assert_eq!(cfg.dashboard.bind_address, "127.0.0.1");
    assert_eq!(cfg.workers.memory_limit_mb, 384);
    assert_eq!(cfg.thermal.cpu_max_sustained_c, 85);
    assert!(cfg.backup.enabled);
    assert_eq!(cfg.backup.interval_secs, 1200);
    assert_eq!(cfg.backup.depth, 10);
    assert_eq!(cfg.budgets.period, "day");
    assert_eq!(cfg.budgets.enforcement, "warn");
}
