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
    /// Run the supervisor (process lifecycle + thermal)
    Supervisor,
    /// Run the HTTP server
    Serve,
    /// Run as worker only (edge node)
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

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter("biged=info")
        .json()
        .init();

    let cli = Cli::parse();

    match cli.command {
        Some(Commands::Supervisor) | None => {
            tracing::info!("Starting BigEd supervisor");
            biged_supervisor::supervisor::run().await?;
        }
        Some(Commands::Serve) => {
            tracing::info!("Starting BigEd HTTP server");
            let fleet_dir = std::env::current_dir()?.join("fleet");
            let config_path = fleet_dir.join("fleet.toml");
            let config = if config_path.exists() {
                biged_core::config::FleetConfig::from_file(&config_path)?
            } else {
                biged_core::config::FleetConfig::default()
            };
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
        Some(Commands::Worker) => {
            tracing::info!("Starting BigEd worker");
            let fleet_dir = std::env::current_dir()?.join("fleet");
            let config_path = fleet_dir.join("fleet.toml");
            let fleet_config = if config_path.exists() {
                biged_core::config::FleetConfig::from_file(&config_path)?
            } else {
                biged_core::config::FleetConfig::default()
            };
            let db_path = fleet_dir.join("fleet.db");
            let db = biged_core::db::Db::open(&db_path)?;
            let bridge_config = biged_bridge::BridgeConfig::new(fleet_dir);
            let fleet_config_json = serde_json::to_value(&fleet_config)?;
            let worker = biged_bridge::worker::Worker::new(db, bridge_config, fleet_config_json)?;
            worker.run_loop("coder").await?;
        }
        Some(Commands::Migrate { from }) => {
            let source = from
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|| std::env::current_dir().unwrap().parent().unwrap_or(&std::env::current_dir().unwrap()).join("fleet"));
            let target = std::env::current_dir()?.join("fleet");

            tracing::info!("Migrating from {} to {}", source.display(), target.display());

            // Copy fleet.db
            let src_db = source.join("fleet.db");
            let dst_db = target.join("fleet.db");
            if src_db.exists() {
                std::fs::create_dir_all(&target)?;
                std::fs::copy(&src_db, &dst_db)?;
                tracing::info!("Copied fleet.db ({} bytes)", dst_db.metadata()?.len());
            } else {
                tracing::warn!("No fleet.db found at {}", src_db.display());
            }

            // Copy rag.db
            let src_rag = source.join("rag.db");
            if src_rag.exists() {
                std::fs::copy(&src_rag, target.join("rag.db"))?;
                tracing::info!("Copied rag.db");
            }

            // Copy fleet.toml
            let src_cfg = source.join("fleet.toml");
            if src_cfg.exists() {
                std::fs::copy(&src_cfg, target.join("fleet.toml"))?;
                tracing::info!("Copied fleet.toml");
            }

            // Copy skills directory
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

            // Verify the imported DB
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
            let fleet_dir = std::env::current_dir()?.join("fleet");
            let config_path = fleet_dir.join("fleet.toml");
            let config = if config_path.exists() {
                biged_core::config::FleetConfig::from_file(&config_path)?
            } else {
                biged_core::config::FleetConfig::default()
            };
            let (tx, _rx) = biged_supervisor::events::create_event_bus(256);
            let mut monitor = biged_supervisor::thermal::ThermalMonitor::new(
                std::sync::Arc::new(tokio::sync::RwLock::new(config)),
                tx,
                fleet_dir,
            );
            monitor.run().await;
        }
        _ => {
            tracing::warn!("Command not yet implemented");
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
