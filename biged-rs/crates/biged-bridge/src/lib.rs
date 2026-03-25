pub mod loader;
pub mod runner;
pub mod worker;

use std::path::PathBuf;

/// Configuration for the skill bridge.
pub struct BridgeConfig {
    /// Path to the fleet/ directory (contains skills/, db.py, etc.)
    pub fleet_dir: PathBuf,
    /// Default timeout for skill execution in seconds.
    pub default_timeout_secs: u64,
    /// Skill-specific timeout overrides.
    pub skill_timeouts: std::collections::HashMap<String, u64>,
}

impl BridgeConfig {
    pub fn new(fleet_dir: PathBuf) -> Self {
        let mut skill_timeouts = std::collections::HashMap::new();
        skill_timeouts.insert("code_write".into(), 900);
        skill_timeouts.insert("code_write_review".into(), 900);
        skill_timeouts.insert("fma_review".into(), 900);
        skill_timeouts.insert("pen_test".into(), 600);
        skill_timeouts.insert("security_audit".into(), 600);

        Self {
            fleet_dir,
            default_timeout_secs: 600,
            skill_timeouts,
        }
    }

    pub fn timeout_for(&self, skill: &str) -> std::time::Duration {
        let secs = self
            .skill_timeouts
            .get(skill)
            .copied()
            .unwrap_or(self.default_timeout_secs);
        std::time::Duration::from_secs(secs)
    }
}
