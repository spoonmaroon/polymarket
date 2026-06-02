use anyhow::{Result, anyhow};
use clap::Parser;
use std::future::Future;
use std::path::PathBuf;
use std::time::Duration;
use tokio::time::timeout;
use tracing::info;

mod book_state;
mod clob_ws;
mod polymarket;
mod prices;
mod report;
mod windows;

#[derive(Debug, Parser)]
struct Args {
    #[arg(long, default_value = "BTC")]
    assets: String,
    #[arg(long, default_value = "5m")]
    interval: String,
    #[arg(long, default_value_t = 1)]
    windows: u8,
    #[arg(long, default_value_t = 20)]
    timeout_seconds: u64,
    #[arg(long, default_value_t = 1500)]
    max_chainlink_cache_age_ms: u64,
    #[arg(long)]
    chainlink_cache_path: Option<PathBuf>,
    #[arg(long, default_value = "reports/live_probe/latest.json")]
    out: PathBuf,
}

#[tokio::main]
async fn main() -> Result<()> {
    let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
    tracing_subscriber::fmt::init();
    let args = Args::parse();
    run_probe(args).await
}

async fn run_probe(args: Args) -> Result<()> {
    let assets = args
        .assets
        .split(',')
        .map(|asset| asset.trim().to_uppercase())
        .filter(|asset| !asset.is_empty())
        .collect::<Vec<_>>();
    let asset_refs = assets.iter().map(String::as_str).collect::<Vec<_>>();

    let mut timer = report::ProbeTimer::start();
    timer.mark("start");

    let chainlink_timeout_seconds = args.timeout_seconds;
    let chainlink_cache_path = args
        .chainlink_cache_path
        .clone()
        .unwrap_or_else(|| args.out.with_file_name("chainlink_btc_usd_latest.json"));
    let max_chainlink_cache_age = Duration::from_millis(args.max_chainlink_cache_age_ms);
    let chainlink_task = tokio::spawn(async move {
        with_timeout(
            "fetch Chainlink BTC/USD RTDS tick",
            chainlink_timeout_seconds,
            prices::fetch_chainlink_btc_usd_cached(
                chainlink_timeout_seconds,
                chainlink_cache_path,
                max_chainlink_cache_age,
            ),
        )
        .await
    });

    let kraken_timeout_seconds = args.timeout_seconds;
    let http_client = reqwest::Client::builder()
        .timeout(Duration::from_secs(kraken_timeout_seconds))
        .build()?;
    let kraken_task = tokio::spawn(async move {
        with_timeout(
            "fetch Kraken XBT/USD ticker",
            kraken_timeout_seconds,
            prices::fetch_kraken_btc_usd(&http_client),
        )
        .await
    });
    timer.mark("price_tasks_started");

    let tokens = with_timeout(
        "discover current Polymarket markets",
        args.timeout_seconds,
        polymarket::discover_current_markets(
            chrono::Utc::now(),
            &asset_refs,
            &args.interval,
            args.windows,
        ),
    )
    .await?;
    timer.mark("contracts_discovered");

    let orderbooks = with_timeout(
        "fetch Polymarket orderbooks concurrently",
        args.timeout_seconds,
        polymarket::fetch_orderbooks(&tokens),
    )
    .await?;
    timer.mark("orderbooks_normalized");

    let chainlink_result = chainlink_task
        .await
        .map_err(|error| anyhow!("Chainlink price task failed: {error}"))??;
    if chainlink_result.cache_hit {
        timer.mark("chainlink_cache_hit");
    } else {
        timer.mark("chainlink_live_fetch");
    }
    let chainlink_btc = chainlink_result.tick;
    timer.mark("chainlink_btc_received");

    let kraken_btc = kraken_task
        .await
        .map_err(|error| anyhow!("Kraken price task failed: {error}"))??;
    timer.mark("kraken_btc_received");

    let source_disagreement = prices::compare_btc_sources(&chainlink_btc, &kraken_btc);
    timer.mark("source_disagreement_calculated");

    let prices = vec![chainlink_btc, kraken_btc];
    let source_disagreements = vec![source_disagreement];
    let mut final_report = report::build_report(report::ReportInput {
        assets,
        interval: args.interval,
        windows: args.windows,
        elapsed_ms: timer.elapsed_ms(),
        latency_marks: timer.marks_snapshot(),
        orderbooks,
        prices,
        source_disagreements,
    });
    report::write_report(&args.out, &final_report)?;
    timer.mark("report_written");
    final_report.elapsed_ms = timer.elapsed_ms();
    final_report.latency_marks = timer.marks_snapshot();
    report::write_report(&args.out, &final_report)?;
    info!(path = %args.out.display(), elapsed_ms = final_report.elapsed_ms, "wrote rust live probe report");
    Ok(())
}

async fn with_timeout<T, F>(label: &str, timeout_seconds: u64, future: F) -> Result<T>
where
    F: Future<Output = Result<T>>,
{
    timeout(Duration::from_secs(timeout_seconds), future)
        .await
        .map_err(|_| anyhow!("{label} timed out after {timeout_seconds} seconds"))?
}
