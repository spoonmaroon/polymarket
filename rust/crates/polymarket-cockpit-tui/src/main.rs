pub mod client;
mod event_loop;
mod layout;
mod render;
pub mod status;
mod state;

use anyhow::Result;
use clap::Parser;
use state::AppState;

#[derive(Debug, Parser)]
#[command(author, version, about)]
struct Cli {
    /// Static preview mode for the first cockpit shell.
    #[arg(long, default_value_t = false)]
    once: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let cli = Cli::parse();
    let app = AppState::default();

    if cli.once {
        println!("polymarket-cockpit-tui: read-only shell ready");
        return Ok(());
    }

    event_loop::run(app)
}
