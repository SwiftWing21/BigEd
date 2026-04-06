use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "biged", version, about = "BigEd CC - Autonomous Agent Fleet")]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,

    /// Run headless (no GUI)
    #[arg(long)]
    headless: bool,
}

#[derive(Subcommand)]
enum Commands {
    /// Run the supervisor (process lifecycle + thermal + worker bridge)
    Supervisor,
    /// Run the HTTP server only (no PyO3, no Python required)
    Serve,
    /// Run as worker only (PyO3 bridge — requires Python)
    Worker,
    /// Run thermal monitor only
    Thermal,
    /// Migrate database from Python fleet
    Migrate {
        /// Path to existing Python fleet directory
        #[arg(long)]
        from: Option<String>,
    },
    /// Launch desktop GUI
    Gui {
        /// Server URL to connect to
        #[arg(long, default_value = "http://localhost:5555")]
        server_url: String,
    },
}

/// Locate the fleet directory: CWD/fleet/ or parent/fleet/.
fn find_fleet_dir() -> std::path::PathBuf {
    let cwd = std::env::current_dir().unwrap_or_default();
    let candidate = cwd.join("fleet");
    if candidate.exists() {
        return candidate;
    }
    // Try parent (running from biged-rs/ inside Education/)
    if let Some(parent) = cwd.parent() {
        let parent_fleet = parent.join("fleet");
        if parent_fleet.exists() {
            return parent_fleet;
        }
    }
    candidate // fallback to CWD/fleet/ even if it doesn't exist
}

fn load_config(fleet_dir: &std::path::Path) -> biged_core::config::FleetConfig {
    let config_path = fleet_dir.join("fleet.toml");
    if config_path.exists() {
        biged_core::config::FleetConfig::from_file(&config_path)
            .unwrap_or_else(|e| {
                tracing::warn!("Failed to parse fleet.toml: {}, using defaults", e);
                biged_core::config::FleetConfig::default()
            })
    } else {
        tracing::info!("No fleet.toml found at {}, using defaults", config_path.display());
        biged_core::config::FleetConfig::default()
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter("biged=info")
        .json()
        .init();

    let cli = Cli::parse();
    let fleet_dir = find_fleet_dir();

    match cli.command {
        // Default (no subcommand): start HTTP server — works without Python
        Some(Commands::Serve) | None => {
            tracing::info!("Starting BigEd HTTP server on fleet dir: {}", fleet_dir.display());
            let config = load_config(&fleet_dir);
            let db_path = fleet_dir.join("fleet.db");
            let db = biged_core::db::Db::open(&db_path)?;
            let (tx, _rx) = biged_supervisor::events::create_event_bus(256);
            let state = biged_server::AppState {
                db,
                events: tx,
                config: std::sync::Arc::new(tokio::sync::RwLock::new(config)),
                fleet_dir,
            };
            biged_server::run(state).await?;
        }
        Some(Commands::Supervisor) => {
            tracing::info!("Starting BigEd supervisor (requires Python for skill bridge)");
            biged_supervisor::supervisor::run().await?;
        }
        Some(Commands::Worker) => {
            tracing::info!("Starting BigEd worker (requires Python for PyO3)");
            let config = load_config(&fleet_dir);
            let db_path = fleet_dir.join("fleet.db");
            let db = biged_core::db::Db::open(&db_path)?;
            let bridge_config = biged_bridge::BridgeConfig::new(fleet_dir);
            let fleet_config_json = serde_json::to_value(&config)?;
            let worker = biged_bridge::worker::Worker::new(db, bridge_config, fleet_config_json)?;
            worker.run_loop("coder").await?;
        }
        Some(Commands::Migrate { from }) => {
            let source = from
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|| {
                    std::env::current_dir()
                        .unwrap_or_default()
                        .parent()
                        .unwrap_or(&std::env::current_dir().unwrap_or_default())
                        .join("fleet")
                });
            let target = fleet_dir;

            tracing::info!("Migrating from {} to {}", source.display(), target.display());

            let src_db = source.join("fleet.db");
            let dst_db = target.join("fleet.db");
            if src_db.exists() {
                std::fs::create_dir_all(&target)?;
                std::fs::copy(&src_db, &dst_db)?;
                tracing::info!("Copied fleet.db ({} bytes)", dst_db.metadata()?.len());
            } else {
                tracing::warn!("No fleet.db found at {}", src_db.display());
            }

            for name in ["rag.db", "fleet.toml"] {
                let src = source.join(name);
                if src.exists() {
                    std::fs::copy(&src, target.join(name))?;
                    tracing::info!("Copied {}", name);
                }
            }

            let src_skills = source.join("skills");
            let dst_skills = target.join("skills");
            if src_skills.exists() {
                if dst_skills.exists() {
                    std::fs::remove_dir_all(&dst_skills)?;
                }
                let mut count = 0u32;
                copy_dir_recursive(&src_skills, &dst_skills, &mut count)?;
                tracing::info!("Copied {} skill files", count);
            }

            let db = biged_core::db::Db::open(&dst_db)?;
            let tables = db.table_names()?;
            let counts = db.task_counts_by_status()?;
            tracing::info!(
                "Migration complete: {} tables, {} total tasks",
                tables.len(),
                counts.values().sum::<i64>()
            );
        }
        Some(Commands::Gui { server_url }) => {
            biged_gui::run_gui(&server_url).expect("GUI failed");
        }
        Some(Commands::Thermal) => {
            tracing::info!("Starting thermal monitor");
            let config = load_config(&fleet_dir);
            let (tx, _rx) = biged_supervisor::events::create_event_bus(256);
            let mut monitor = biged_supervisor::thermal::ThermalMonitor::new(
                std::sync::Arc::new(tokio::sync::RwLock::new(config)),
                tx,
                fleet_dir,
            );
            monitor.run().await;
        }
    }

    Ok(())
}

fn copy_dir_recursive(
    src: &std::path::Path,
    dst: &std::path::Path,
    count: &mut u32,
) -> anyhow::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let ty = entry.file_type()?;
        let dst_path = dst.join(entry.file_name());
        if ty.is_dir() {
            copy_dir_recursive(&entry.path(), &dst_path, count)?;
        } else {
            std::fs::copy(entry.path(), &dst_path)?;
            *count += 1;
        }
    }
    Ok(())
}
