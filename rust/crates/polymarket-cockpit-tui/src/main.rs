pub mod client;
mod event_loop;
mod layout;
pub mod market_view;
mod render;
mod state;
pub mod status;

use anyhow::Result;
use clap::Parser;
use state::AppState;

#[derive(Debug, Clone, Parser)]
#[command(author, version, about)]
struct Cli {
    /// Engine API base URL for read-only runtime status polling.
    #[arg(long, default_value = "http://127.0.0.1:8000")]
    engine_api_url: String,

    /// Runtime API polling interval in milliseconds.
    #[arg(long, default_value_t = 1000)]
    poll_interval_ms: u64,

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

    event_loop::run(app, cli.engine_api_url, cli.poll_interval_ms).await
}

#[cfg(test)]
mod cli_tests {
    use clap::Parser;

    use super::Cli;

    #[test]
    fn default_engine_api_url_is_localhost() {
        let cli = Cli::parse_from(["polymarket-cockpit-tui"]);

        assert_eq!(cli.engine_api_url, "http://127.0.0.1:8000");
    }

    #[test]
    fn default_poll_interval_is_one_second() {
        let cli = Cli::parse_from(["polymarket-cockpit-tui"]);

        assert_eq!(cli.poll_interval_ms, 1000);
    }

    #[test]
    fn custom_engine_api_url_is_accepted() {
        let cli = Cli::parse_from([
            "polymarket-cockpit-tui",
            "--engine-api-url",
            "http://100.72.104.49:8000",
        ]);

        assert_eq!(cli.engine_api_url, "http://100.72.104.49:8000");
    }

    #[test]
    fn custom_poll_interval_is_accepted() {
        let cli = Cli::parse_from(["polymarket-cockpit-tui", "--poll-interval-ms", "100"]);

        assert_eq!(cli.poll_interval_ms, 100);
    }
}
