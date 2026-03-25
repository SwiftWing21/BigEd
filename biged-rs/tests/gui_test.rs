use biged_gui::api::{AgentInfo, ApiClient, FleetStatus};
use biged_gui::state::{AppState, SettingsSection, Tab};
use biged_gui::theme;
use std::collections::HashSet;

// ── Theme color tests ────────────────────────────────────────────────────────

#[test]
fn smoke_theme_colors_valid() {
    assert_ne!(theme::BG, theme::TEXT, "BG and TEXT must differ");
    assert_ne!(theme::GOLD, theme::BG, "GOLD must not be BG");
    assert_ne!(theme::GREEN, theme::RED, "GREEN and RED must differ");
}

#[test]
fn test_all_status_colors_distinct() {
    let status_colors = [
        ("GREEN", theme::GREEN),
        ("ORANGE", theme::ORANGE),
        ("RED", theme::RED),
        ("BLUE", theme::BLUE),
        ("YELLOW", theme::YELLOW),
    ];

    // Every pair of status colors must be different
    for i in 0..status_colors.len() {
        for j in (i + 1)..status_colors.len() {
            let (name_a, color_a) = status_colors[i];
            let (name_b, color_b) = status_colors[j];
            assert_ne!(
                color_a, color_b,
                "Status colors {} and {} must differ",
                name_a, name_b
            );
        }
    }
}

#[test]
fn test_background_layers_distinct() {
    let bgs = [
        ("BG", theme::BG),
        ("BG2", theme::BG2),
        ("BG3", theme::BG3),
    ];
    for i in 0..bgs.len() {
        for j in (i + 1)..bgs.len() {
            assert_ne!(
                bgs[i].1, bgs[j].1,
                "Background layers {} and {} must differ",
                bgs[i].0, bgs[j].0
            );
        }
    }
}

#[test]
fn test_provider_colors_distinct() {
    let providers = [
        ("LOCAL", theme::PROVIDER_LOCAL),
        ("CLAUDE", theme::PROVIDER_CLAUDE),
        ("GEMINI", theme::PROVIDER_GEMINI),
    ];
    let mut set = HashSet::new();
    for (name, color) in &providers {
        assert!(
            set.insert((color.r(), color.g(), color.b())),
            "Provider color {} must be unique",
            name
        );
    }
}

#[test]
fn test_accent_colors_not_background() {
    let accents = [theme::ACCENT, theme::ACCENT_HOVER, theme::GOLD, theme::BRAND];
    let backgrounds = [theme::BG, theme::BG2, theme::BG3];
    for accent in &accents {
        for bg in &backgrounds {
            assert_ne!(accent, bg, "Accent color must not match any background layer");
        }
    }
}

#[test]
fn test_text_readable_on_bg() {
    // TEXT should be significantly brighter than BG for readability
    let text_lum = theme::TEXT.r() as u16 + theme::TEXT.g() as u16 + theme::TEXT.b() as u16;
    let bg_lum = theme::BG.r() as u16 + theme::BG.g() as u16 + theme::BG.b() as u16;
    assert!(
        text_lum > bg_lum + 200,
        "TEXT ({}) must be significantly brighter than BG ({}) for readability",
        text_lum,
        bg_lum
    );
}

// ── ApiClient tests ──────────────────────────────────────────────────────────

#[test]
fn smoke_api_client_creates() {
    let client = ApiClient::new("http://localhost:9999");
    assert!(!*client.connected.lock().unwrap());
}

#[test]
fn test_api_client_initial_status_empty() {
    let client = ApiClient::new("http://localhost:9999");
    let status = client.status.lock().unwrap().clone();
    assert!(status.version.is_none(), "Initial version should be None");
    assert!(
        status.agent_count.is_none(),
        "Initial agent_count should be None"
    );
    assert!(
        status.task_pending.is_none(),
        "Initial task_pending should be None"
    );
}

#[test]
fn test_api_client_initial_agents_empty() {
    let client = ApiClient::new("http://localhost:9999");
    let agents = client.agents.lock().unwrap();
    assert!(agents.is_empty(), "Initial agents list should be empty");
}

#[test]
fn test_api_client_initial_thermal_default() {
    let client = ApiClient::new("http://localhost:9999");
    let thermal = client.thermal.lock().unwrap();
    assert!(
        thermal.gpu_temp.is_none(),
        "Initial GPU temp should be None"
    );
    assert!(
        thermal.cpu_temp.is_none(),
        "Initial CPU temp should be None"
    );
}

#[tokio::test]
async fn test_api_client_connection_failure() {
    // Connecting to a bogus port should leave connected = false
    let client = ApiClient::new("http://127.0.0.1:1");
    client.refresh().await;
    assert!(
        !*client.connected.lock().unwrap(),
        "Should remain disconnected after failed refresh"
    );
}

#[tokio::test]
async fn test_api_client_refresh_preserves_empty_on_failure() {
    let client = ApiClient::new("http://127.0.0.1:1");
    client.refresh().await;
    let agents = client.agents.lock().unwrap();
    assert!(
        agents.is_empty(),
        "Agents should stay empty after failed refresh"
    );
    let lanes = client.lanes.lock().unwrap();
    assert!(
        lanes.is_empty(),
        "Lanes should stay empty after failed refresh"
    );
}

// ── AppState tests ───────────────────────────────────────────────────────────

#[test]
fn smoke_state_defaults() {
    let client = ApiClient::new("http://localhost:9999");
    let state = AppState::new(client);
    assert_eq!(state.active_tab, Tab::CommandCenter);
    assert!(state.sidebar_open);
    assert!(!state.connected());
}

#[test]
fn test_state_tab_switching() {
    let client = ApiClient::new("http://localhost:9999");
    let mut state = AppState::new(client);

    // Verify initial tab
    assert_eq!(state.active_tab, Tab::CommandCenter);

    // Switch to Fleet
    state.active_tab = Tab::Fleet;
    assert_eq!(state.active_tab, Tab::Fleet);

    // Switch to FleetComm
    state.active_tab = Tab::FleetComm;
    assert_eq!(state.active_tab, Tab::FleetComm);

    // Switch to Settings
    state.active_tab = Tab::Settings;
    assert_eq!(state.active_tab, Tab::Settings);

    // Switch back to CommandCenter
    state.active_tab = Tab::CommandCenter;
    assert_eq!(state.active_tab, Tab::CommandCenter);
}

#[test]
fn test_state_settings_section_switching() {
    let client = ApiClient::new("http://localhost:9999");
    let mut state = AppState::new(client);

    // Default settings section
    assert_eq!(state.settings_section, SettingsSection::General);

    // Switch through all sections
    state.settings_section = SettingsSection::Hardware;
    assert_eq!(state.settings_section, SettingsSection::Hardware);

    state.settings_section = SettingsSection::Display;
    assert_eq!(state.settings_section, SettingsSection::Display);

    state.settings_section = SettingsSection::Keys;
    assert_eq!(state.settings_section, SettingsSection::Keys);

    state.settings_section = SettingsSection::General;
    assert_eq!(state.settings_section, SettingsSection::General);
}

#[test]
fn test_state_sidebar_toggle() {
    let client = ApiClient::new("http://localhost:9999");
    let mut state = AppState::new(client);

    assert!(state.sidebar_open);
    state.sidebar_open = false;
    assert!(!state.sidebar_open);
    state.sidebar_open = true;
    assert!(state.sidebar_open);
}

#[test]
fn test_state_chat_input() {
    let client = ApiClient::new("http://localhost:9999");
    let mut state = AppState::new(client);

    assert!(state.chat_input.is_empty());
    state.chat_input = "test command".to_string();
    assert_eq!(state.chat_input, "test command");
}

// ── Data flow tests ──────────────────────────────────────────────────────────

#[test]
fn test_fleet_status_data_flow() {
    let client = ApiClient::new("http://localhost:9999");

    // Simulate server data arriving
    {
        let mut status = client.status.lock().unwrap();
        *status = FleetStatus {
            version: Some("0.400.00b".to_string()),
            uptime_secs: Some(3600.0),
            agent_count: Some(5),
            task_pending: Some(3),
            task_running: Some(2),
            task_done: Some(100),
            task_failed: Some(1),
        };
    }

    let state = AppState::new(client);
    let status = state.status();
    assert_eq!(status.version.as_deref(), Some("0.400.00b"));
    assert_eq!(status.agent_count, Some(5));
    assert_eq!(status.task_pending, Some(3));
    assert_eq!(status.task_running, Some(2));
    assert_eq!(status.task_done, Some(100));
    assert_eq!(status.task_failed, Some(1));
}

#[test]
fn test_agent_card_data_flow() {
    let client = ApiClient::new("http://localhost:9999");

    // Simulate agent data arriving
    {
        let mut agents = client.agents.lock().unwrap();
        *agents = vec![
            AgentInfo {
                name: "coder_1".to_string(),
                role: "coder".to_string(),
                status: "idle".to_string(),
                last_heartbeat: Some("2026-03-24T12:00:00".to_string()),
            },
            AgentInfo {
                name: "researcher_1".to_string(),
                role: "researcher".to_string(),
                status: "busy".to_string(),
                last_heartbeat: Some("2026-03-24T12:00:00".to_string()),
            },
        ];
    }

    let state = AppState::new(client);
    let agents = state.agents();
    assert_eq!(agents.len(), 2);
    assert_eq!(agents[0].name, "coder_1");
    assert_eq!(agents[0].status, "idle");
    assert_eq!(agents[1].name, "researcher_1");
    assert_eq!(agents[1].status, "busy");
}

#[test]
fn test_thermal_data_flow() {
    let client = ApiClient::new("http://localhost:9999");

    {
        let mut thermal = client.thermal.lock().unwrap();
        thermal.gpu_temp = Some(72.5);
        thermal.cpu_temp = Some(65.0);
        thermal.action = Some("ready".to_string());
    }

    let state = AppState::new(client);
    let thermal = state.thermal();
    assert_eq!(thermal.gpu_temp, Some(72.5));
    assert_eq!(thermal.cpu_temp, Some(65.0));
    assert_eq!(thermal.action.as_deref(), Some("ready"));
}

// ── Font / dimension constants ───────────────────────────────────────────────

#[test]
fn test_font_sizes_positive() {
    assert!(theme::FONT_XS > 0.0);
    assert!(theme::FONT_SM > 0.0);
    assert!(theme::FONT_BODY > 0.0);
    assert!(theme::FONT_HEADING > 0.0);
    assert!(theme::FONT_TITLE > 0.0);
    assert!(theme::FONT_STAT > 0.0);
}

#[test]
fn test_font_sizes_ordered() {
    // XS < SM < BODY < HEADING < TITLE < STAT
    assert!(theme::FONT_XS < theme::FONT_SM);
    assert!(theme::FONT_SM < theme::FONT_BODY);
    assert!(theme::FONT_BODY < theme::FONT_HEADING);
    assert!(theme::FONT_HEADING < theme::FONT_TITLE);
    assert!(theme::FONT_TITLE < theme::FONT_STAT);
}

#[test]
fn test_dimensions_positive() {
    assert!(theme::HEADER_HEIGHT > 0.0);
    assert!(theme::SIDEBAR_WIDTH > 0.0);
    assert!(theme::CARD_ROUNDING > 0.0);
    assert!(theme::BTN_ROUNDING > 0.0);
}
