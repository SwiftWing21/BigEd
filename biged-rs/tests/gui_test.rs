#[test]
fn smoke_theme_colors_valid() {
    use biged_gui::theme;
    assert_ne!(theme::BG, theme::TEXT, "BG and TEXT must differ");
    assert_ne!(theme::GOLD, theme::BG, "GOLD must not be BG");
    assert_ne!(theme::GREEN, theme::RED, "GREEN and RED must differ");
}

#[test]
fn smoke_api_client_creates() {
    use biged_gui::api::ApiClient;
    let client = ApiClient::new("http://localhost:9999");
    assert!(!*client.connected.lock().unwrap());
}

#[test]
fn smoke_state_defaults() {
    use biged_gui::api::ApiClient;
    use biged_gui::state::{AppState, Tab};
    let client = ApiClient::new("http://localhost:9999");
    let state = AppState::new(client);
    assert_eq!(state.active_tab, Tab::CommandCenter);
    assert!(state.sidebar_open);
    assert!(!state.connected());
}
