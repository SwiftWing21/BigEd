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
    Migrate,
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
        Some(Commands::Migrate) => {
            tracing::info!("Running database migration");
        }
        Some(Commands::Gui { server_url }) => {
            biged_gui::run_gui(&server_url).expect("GUI failed");
        }
        _ => {
            tracing::warn!("Command not yet implemented");
        }
    }

    Ok(())
}
