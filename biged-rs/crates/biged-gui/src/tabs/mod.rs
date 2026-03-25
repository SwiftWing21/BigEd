pub mod command_center;
pub mod fleet;
pub mod fleet_comm;
pub mod settings;

use crate::state::{AppState, Tab};

/// Dispatch to the active tab's show function.
pub fn show_active_tab(ui: &mut egui::Ui, state: &mut AppState) {
    match state.active_tab {
        Tab::CommandCenter => command_center::show(ui, state),
        Tab::Fleet => fleet::show(ui, state),
        Tab::FleetComm => fleet_comm::show(ui, state),
        Tab::Settings => settings::show(ui, state),
    }
}
