use crate::events::{EventSender, FleetEvent};
use biged_core::config::WorkersSection;
use biged_core::db::Db;
use tracing::{info, warn};

/// How often the scaler checks queue depth (seconds).
const SCALE_CHECK_INTERVAL_SECS: u64 = 30;

/// Queue-depth-to-worker ratio that triggers scale-up.
/// If queue_depth > current_workers * SCALE_UP_RATIO, we add workers.
const SCALE_UP_RATIO: u32 = 2;

/// How many consecutive empty-queue checks before scaling down (5 min at 30s intervals).
const EMPTY_CHECKS_BEFORE_SCALE_DOWN: u32 = 10;

/// Threshold-based auto-scaler that adjusts worker count based on queue depth.
///
/// - Scales up when pending tasks exceed `current_workers * SCALE_UP_RATIO`
/// - Scales down when the queue has been empty for `EMPTY_CHECKS_BEFORE_SCALE_DOWN`
///   consecutive checks
/// - Never exceeds `config.max_workers`
/// - Never drops below 1 worker
pub struct AutoScaler {
    config: WorkersSection,
    db: Db,
    sender: EventSender,
    current_workers: u32,
    consecutive_empty: u32,
}

impl AutoScaler {
    pub fn new(config: WorkersSection, db: Db, sender: EventSender) -> Self {
        let initial = config.coder_count.min(config.max_workers).max(1);
        Self {
            config,
            db,
            sender,
            current_workers: initial,
            consecutive_empty: 0,
        }
    }

    /// Run the scaling loop forever.
    pub async fn run_loop(&mut self) {
        let interval = std::time::Duration::from_secs(SCALE_CHECK_INTERVAL_SECS);
        let mut tick = tokio::time::interval(interval);
        info!(
            "AutoScaler started — max_workers={}, initial={}",
            self.config.max_workers, self.current_workers
        );

        loop {
            tick.tick().await;
            if let Err(e) = self.check_and_scale() {
                warn!("AutoScaler check failed: {e}");
            }
        }
    }

    /// Single scaling decision cycle.
    pub fn check_and_scale(&mut self) -> anyhow::Result<()> {
        let queue_depth = self.db.queue_depth()?;
        let desired = self.compute_desired_workers(queue_depth);

        if desired > self.current_workers {
            let added = desired - self.current_workers;
            info!(
                "Scaling up: {} -> {} workers (queue_depth={})",
                self.current_workers, desired, queue_depth
            );
            self.current_workers = desired;
            self.consecutive_empty = 0;
            let _ = self.sender.send(FleetEvent::ScaleUp {
                count: added,
                reason: format!(
                    "queue_depth {} exceeds threshold (workers * {})",
                    queue_depth, SCALE_UP_RATIO
                ),
            });
        } else if desired < self.current_workers {
            let removed = self.current_workers - desired;
            info!(
                "Scaling down: {} -> {} workers (queue empty for {} checks)",
                self.current_workers, desired, self.consecutive_empty
            );
            self.current_workers = desired;
            let _ = self.sender.send(FleetEvent::ScaleDown { count: removed });
        }

        Ok(())
    }

    /// Determine the desired worker count from queue depth and idle history.
    ///
    /// Returns a value in `[1, max_workers]`.
    pub fn compute_desired_workers(&mut self, queue_depth: i64) -> u32 {
        let max = self.config.max_workers.max(1);

        if queue_depth == 0 {
            self.consecutive_empty += 1;

            if self.consecutive_empty >= EMPTY_CHECKS_BEFORE_SCALE_DOWN && self.current_workers > 1
            {
                // Scale down by 1 each time we remain empty past threshold
                return (self.current_workers - 1).max(1);
            }
            return self.current_workers;
        }

        // Reset empty counter when there is work
        self.consecutive_empty = 0;

        // Scale up if queue_depth > current_workers * ratio
        if queue_depth > (self.current_workers * SCALE_UP_RATIO) as i64 {
            // Add enough workers to handle the queue, but cap at max
            let needed = ((queue_depth as u32) / SCALE_UP_RATIO).max(self.current_workers + 1);
            return needed.min(max);
        }

        self.current_workers
    }

    /// Current worker count (for testing / diagnostics).
    pub fn current_workers(&self) -> u32 {
        self.current_workers
    }
}
