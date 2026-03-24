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
        Some(Commands::Migrate) => {
            tracing::info!("Running database migration");
        }
        _ => {
            tracing::warn!("Command not yet implemented");
        }
    }

    Ok(())
}
