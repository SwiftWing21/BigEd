pub mod config_tab;
pub mod fleet;
pub mod logs;
pub mod overview;
pub mod tasks;

use crate::state::{AppState, Tab};

/// Dispatch to the active tab's show function.
pub fn show_active_tab(ui: &mut egui::Ui, state: &mut AppState) {
    match state.active_tab {
        Tab::Overview => overview::show(ui, state),
        Tab::Fleet => fleet::show(ui, state),
        Tab::Tasks => tasks::show(ui, state),
        Tab::Config => config_tab::show(ui, state),
        Tab::Logs => logs::show(ui, state),
    }
}
