use std::collections::BTreeMap;
use std::io::{self, Stdout};
use std::time::Duration;

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind},
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{Terminal, backend::CrosstermBackend};
use tokio::{sync::mpsc, task::JoinHandle};

use crate::{
    client::EngineClient,
    render,
    state::{AppState, MainTab},
    status::{
        RuntimeDisplayLag, RuntimeGates, RuntimeLive, RuntimeMonitor, RuntimeOutcomes,
        RuntimeProbabilities, RuntimeProbabilityRow, RuntimeStatus, RuntimeVolatility,
    },
};

type Tui = Terminal<CrosstermBackend<Stdout>>;
const PROBABILITY_POLL_INTERVAL: Duration = Duration::from_secs(3);
const OUTCOME_POLL_INTERVAL: Duration = Duration::from_secs(15);
const OUTCOME_HISTORY_LIMIT: usize = 5000;
const MAX_LOG_LINES: usize = 200;

pub async fn run(mut app: AppState, engine_api_url: String, poll_interval_ms: u64) -> Result<()> {
    let mut terminal = TerminalGuard::enter()?;
    let (runtime_tx, mut runtime_rx) = mpsc::unbounded_channel();
    let _poll_task = RuntimeLiveTask::spawn(engine_api_url, poll_interval_ms, runtime_tx);

    run_loop(terminal.terminal_mut(), &mut app, &mut runtime_rx)
}

struct TerminalGuard {
    terminal: Option<Tui>,
    raw_mode_enabled: bool,
    alternate_screen_enabled: bool,
}

impl TerminalGuard {
    fn enter() -> Result<Self> {
        let mut guard = Self {
            terminal: None,
            raw_mode_enabled: false,
            alternate_screen_enabled: false,
        };

        enable_raw_mode()?;
        guard.raw_mode_enabled = true;

        let mut stdout = io::stdout();
        execute!(stdout, EnterAlternateScreen)?;
        guard.alternate_screen_enabled = true;

        let backend = CrosstermBackend::new(stdout);
        guard.terminal = Some(Terminal::new(backend)?);

        Ok(guard)
    }

    fn terminal_mut(&mut self) -> &mut Tui {
        self.terminal
            .as_mut()
            .expect("terminal is initialized before use")
    }
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        if let Some(terminal) = self.terminal.as_mut() {
            let _ = execute!(terminal.backend_mut(), LeaveAlternateScreen);
            let _ = terminal.show_cursor();
        } else if self.alternate_screen_enabled {
            let mut stdout = io::stdout();
            let _ = execute!(stdout, LeaveAlternateScreen);
        }

        if self.raw_mode_enabled {
            let _ = disable_raw_mode();
        }
    }
}

fn run_loop(
    terminal: &mut Tui,
    app: &mut AppState,
    runtime_rx: &mut mpsc::UnboundedReceiver<RuntimeUpdate>,
) -> Result<()> {
    let mut redraw_needed = true;

    loop {
        if app.update_display_now(Utc::now()) {
            redraw_needed = true;
        }

        if drain_runtime_updates(app, runtime_rx) {
            redraw_needed = true;
        }

        if redraw_needed {
            terminal.draw(|frame| render::render(frame, app))?;
            redraw_needed = false;
        }

        if !event::poll(Duration::from_millis(250))? {
            continue;
        }

        let Event::Key(key) = event::read()? else {
            continue;
        };

        if key.kind != KeyEventKind::Press {
            continue;
        }

        if apply_key(app, key.code) {
            return Ok(());
        }
        redraw_needed = true;
    }
}

fn apply_key(app: &mut AppState, key_code: KeyCode) -> bool {
    match key_code {
        KeyCode::Char('q') | KeyCode::Esc => true,
        KeyCode::Left | KeyCode::BackTab => {
            app.previous_tab();
            false
        }
        KeyCode::Right | KeyCode::Tab => {
            app.next_tab();
            false
        }
        KeyCode::Up if app.active_tab == MainTab::Market => {
            app.select_previous_market();
            false
        }
        KeyCode::Down if app.active_tab == MainTab::Market => {
            app.select_next_market();
            false
        }
        KeyCode::Up if app.active_tab == MainTab::Outcomes => {
            app.select_previous_outcome();
            false
        }
        KeyCode::Down if app.active_tab == MainTab::Outcomes => {
            app.select_next_outcome();
            false
        }
        KeyCode::Enter | KeyCode::Char(' ') if app.active_tab == MainTab::Outcomes => {
            app.toggle_selected_outcome();
            false
        }
        _ => false,
    }
}

#[derive(Debug)]
struct RuntimeUpdate {
    status: Option<RuntimeStatus>,
    gates: Option<RuntimeGates>,
    monitor: Option<RuntimeMonitor>,
    volatility: Option<RuntimeVolatility>,
    probabilities: Option<RuntimeProbabilities>,
    outcomes: Option<RuntimeOutcomes>,
    display_lag: Option<RuntimeDisplayLag>,
    error: Option<String>,
}

struct RuntimeLiveTask {
    handle: JoinHandle<()>,
}

impl RuntimeLiveTask {
    fn spawn(
        engine_api_url: String,
        poll_interval_ms: u64,
        runtime_tx: mpsc::UnboundedSender<RuntimeUpdate>,
    ) -> Self {
        let handle = tokio::spawn(async move {
            let client = EngineClient::new(engine_api_url);
            let probability_client = client.clone();
            let probability_tx = runtime_tx.clone();
            let probability_handle = tokio::spawn(async move {
                let mut interval = tokio::time::interval(PROBABILITY_POLL_INTERVAL);
                loop {
                    interval.tick().await;
                    if probability_tx
                        .send(poll_probability_runtime(&probability_client).await)
                        .is_err()
                    {
                        break;
                    }
                }
            });
            let outcome_client = client.clone();
            let outcome_tx = runtime_tx.clone();
            let outcome_handle = tokio::spawn(async move {
                let mut interval = tokio::time::interval(OUTCOME_POLL_INTERVAL);
                loop {
                    interval.tick().await;
                    if outcome_tx
                        .send(poll_outcome_runtime(&outcome_client).await)
                        .is_err()
                    {
                        break;
                    }
                }
            });

            loop {
                if stream_runtime_updates(&client, poll_interval_ms, &runtime_tx)
                    .await
                    .is_err_and(|error| {
                        runtime_tx
                            .send(RuntimeUpdate {
                                status: None,
                                gates: None,
                                monitor: None,
                                volatility: None,
                                probabilities: None,
                                outcomes: None,
                                display_lag: None,
                                error: Some(format!("stream: {error}")),
                            })
                            .is_err()
                    })
                {
                    break;
                }
                let update = poll_runtime(&client).await;
                if runtime_tx.send(update).is_err() {
                    break;
                }
                tokio::time::sleep(poll_interval_duration(poll_interval_ms)).await;
            }
            probability_handle.abort();
            outcome_handle.abort();
        });

        Self { handle }
    }
}

fn poll_interval_duration(poll_interval_ms: u64) -> Duration {
    Duration::from_millis(poll_interval_ms.max(1))
}

impl Drop for RuntimeLiveTask {
    fn drop(&mut self) {
        self.handle.abort();
    }
}

fn drain_runtime_updates(
    app: &mut AppState,
    runtime_rx: &mut mpsc::UnboundedReceiver<RuntimeUpdate>,
) -> bool {
    let mut changed = false;
    while let Ok(update) = runtime_rx.try_recv() {
        changed |= apply_runtime_update(app, update);
    }
    changed
}

fn apply_runtime_update(app: &mut AppState, update: RuntimeUpdate) -> bool {
    let mut changed = false;

    if let Some(status) = update.status {
        changed |= replace_if_changed(&mut app.runtime_status, status);
    }

    if let Some(gates) = update.gates {
        changed |= replace_if_changed(&mut app.runtime_gates, gates);
    }

    if let Some(outcomes) = update.outcomes {
        if app.apply_runtime_outcomes(outcomes) {
            app.sync_outcome_selection();
            app.sync_market_selection();
            changed = true;
        }
    }

    if let Some(monitor) = update.monitor {
        if app.apply_runtime_monitor(monitor) {
            app.sync_market_selection();
            changed = true;
        }
    }

    if let Some(volatility) = update.volatility {
        changed |= replace_if_changed(&mut app.runtime_volatility, volatility);
    }

    if let Some(probabilities) = update.probabilities {
        let log_line = monte_carlo_log_line(&probabilities);
        if replace_if_changed(&mut app.runtime_probabilities, probabilities) {
            changed = true;
            changed |= push_log(app, log_line);
        }
    }

    if let Some(display_lag) = update.display_lag {
        changed |= replace_if_changed(&mut app.runtime_display_lag, display_lag);
    }

    let next_error = update.error;
    if app.runtime_error != next_error {
        if let Some(error) = next_error.as_ref() {
            push_log(app, format!("runtime_error {error}"));
        } else if app.runtime_error.is_some() {
            push_log(app, "runtime recovered".to_string());
        }
        app.runtime_error = next_error;
        changed = true;
    }

    changed
}

fn push_log(app: &mut AppState, line: String) -> bool {
    if app.logs.last() == Some(&line) {
        return false;
    }
    app.logs.push(line);
    let overflow = app.logs.len().saturating_sub(MAX_LOG_LINES);
    if overflow > 0 {
        app.logs.drain(0..overflow);
    }
    true
}

fn monte_carlo_log_line(probabilities: &RuntimeProbabilities) -> String {
    format!(
        "mc rows={} {} gates={} cache={} at={}",
        probabilities.rows.len(),
        probability_side_summary(&probabilities.rows),
        probability_gate_summary(&probabilities.rows),
        probability_cache_summary(&probabilities.rows),
        probabilities.generated_at
    )
}

fn probability_side_summary(rows: &[RuntimeProbabilityRow]) -> String {
    let mut sides_by_asset: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for row in rows {
        let asset = probability_asset(row);
        let side = probability_side(row).unwrap_or_else(|| "UNKNOWN".to_string());
        let sides = sides_by_asset.entry(asset).or_default();
        if !sides.contains(&side) {
            sides.push(side);
        }
    }
    if sides_by_asset.is_empty() {
        return "markets=-".to_string();
    }
    sides_by_asset
        .into_iter()
        .map(|(asset, mut sides)| {
            sides.sort_by(|left, right| {
                side_rank(left)
                    .cmp(&side_rank(right))
                    .then_with(|| left.cmp(right))
            });
            format!("{asset}={}", sides.join("/"))
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn probability_gate_summary(rows: &[RuntimeProbabilityRow]) -> String {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for row in rows {
        let gate = row
            .decision_hint
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("NO_HINT")
            .to_string();
        *counts.entry(gate).or_default() += 1;
    }
    if counts.is_empty() {
        return "-".to_string();
    }
    counts
        .into_iter()
        .map(|(gate, count)| format!("{gate}:{count}"))
        .collect::<Vec<_>>()
        .join(" ")
}

fn probability_cache_summary(rows: &[RuntimeProbabilityRow]) -> String {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for row in rows {
        let status = row
            .cache_status
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("NO_CACHE")
            .to_string();
        *counts.entry(status).or_default() += 1;
    }
    if counts.is_empty() {
        return "-".to_string();
    }
    let counts_label = counts
        .into_iter()
        .map(|(status, count)| format!("{status}:{count}"))
        .collect::<Vec<_>>()
        .join(" ");
    let Some(row) = rows.iter().find(|row| {
        row.cache_status
            .as_deref()
            .is_some_and(|status| !status.trim().is_empty())
    }) else {
        return counts_label;
    };
    format!(
        "{} asof={} gen={} valid={}->{}",
        counts_label,
        row.asof_ts.as_deref().unwrap_or("-"),
        row.generated_at.as_deref().unwrap_or("-"),
        row.valid_from.as_deref().unwrap_or("-"),
        row.valid_until.as_deref().unwrap_or("-")
    )
}

fn probability_asset(row: &RuntimeProbabilityRow) -> String {
    row.asset
        .as_deref()
        .map(str::trim)
        .filter(|asset| !asset.is_empty())
        .or_else(|| row.contract.split_whitespace().next())
        .map(str::to_ascii_uppercase)
        .unwrap_or_else(|| "OTHER".to_string())
}

fn probability_side(row: &RuntimeProbabilityRow) -> Option<String> {
    row.side
        .as_deref()
        .map(str::trim)
        .filter(|side| !side.is_empty())
        .map(str::to_ascii_uppercase)
        .or_else(|| {
            let contract = row.contract.to_ascii_uppercase();
            if contract.ends_with(" UP") {
                Some("UP".to_string())
            } else if contract.ends_with(" DOWN") {
                Some("DOWN".to_string())
            } else {
                None
            }
        })
}

fn side_rank(side: &str) -> u8 {
    match side {
        "UP" => 0,
        "DOWN" => 1,
        _ => 2,
    }
}

fn replace_if_changed<T>(slot: &mut Option<T>, next: T) -> bool
where
    T: PartialEq,
{
    if slot.as_ref() == Some(&next) {
        false
    } else {
        *slot = Some(next);
        true
    }
}

async fn poll_runtime(client: &EngineClient) -> RuntimeUpdate {
    let mut errors = Vec::new();
    let mut status = None;
    let mut gates = None;
    let mut monitor = None;
    let mut volatility = None;
    let mut probabilities = None;
    let mut outcomes = None;
    let mut display_lag = None;

    let (live_result, probabilities_result, outcomes_result) = tokio::join!(
        client.live(8),
        client.probabilities(8),
        client.outcomes(OUTCOME_HISTORY_LIMIT)
    );

    match live_result {
        Ok(next_live) => {
            status = Some(next_live.status);
            gates = Some(next_live.gates);
            monitor = Some(next_live.monitor);
            volatility = Some(next_live.volatility);
            display_lag = Some(display_lag_with_receive_ms(
                next_live.latency,
                next_live.server_sent_at,
            ));
        }
        Err(error) => {
            errors.push(format!("live: {error}"));
            let (status_result, gates_result, monitor_result) =
                tokio::join!(client.status(), client.gates(), client.monitor(8));
            match status_result {
                Ok(next_status) => status = Some(next_status),
                Err(error) => errors.push(format!("status: {error}")),
            }
            match gates_result {
                Ok(next_gates) => gates = Some(next_gates),
                Err(error) => errors.push(format!("gates: {error}")),
            }
            match monitor_result {
                Ok(next_monitor) => monitor = Some(next_monitor),
                Err(error) => errors.push(format!("monitor: {error}")),
            }
        }
    }

    match probabilities_result {
        Ok(next_probabilities) => probabilities = Some(next_probabilities),
        Err(error) => errors.push(format!("probabilities: {error}")),
    }

    match outcomes_result {
        Ok(next_outcomes) => outcomes = Some(next_outcomes),
        Err(error) => errors.push(format!("outcomes: {error}")),
    }

    RuntimeUpdate {
        status,
        gates,
        monitor,
        volatility,
        probabilities,
        outcomes,
        display_lag,
        error: if errors.is_empty() {
            None
        } else {
            Some(errors.join("; "))
        },
    }
}

async fn stream_runtime_updates(
    client: &EngineClient,
    poll_interval_ms: u64,
    runtime_tx: &mpsc::UnboundedSender<RuntimeUpdate>,
) -> Result<()> {
    let mut response = client
        .live_stream_response(8, poll_interval_ms)
        .await
        .context("open live stream")?;
    let mut buffer = String::new();

    while let Some(chunk) = response.chunk().await.context("read live stream")? {
        buffer.push_str(std::str::from_utf8(&chunk).context("decode live stream bytes")?);
        while let Some(event_end) = buffer.find("\n\n") {
            let event = buffer[..event_end].to_string();
            buffer.drain(..event_end + 2);
            if let Some(live) = parse_sse_live_event(&event)? {
                let update = runtime_update_from_live(live);
                if runtime_tx.send(update).is_err() {
                    return Ok(());
                }
            }
        }
    }

    Err(anyhow::anyhow!("live stream closed"))
}

fn parse_sse_live_event(event: &str) -> Result<Option<RuntimeLive>> {
    let data = event
        .lines()
        .filter_map(|line| line.strip_prefix("data:"))
        .map(str::trim_start)
        .collect::<Vec<_>>()
        .join("\n");
    if data.is_empty() {
        return Ok(None);
    }
    Ok(Some(serde_json::from_str(&data)?))
}

fn runtime_update_from_live(live: RuntimeLive) -> RuntimeUpdate {
    let display_lag = display_lag_with_receive_ms(live.latency, live.server_sent_at);
    RuntimeUpdate {
        status: Some(live.status),
        gates: Some(live.gates),
        monitor: Some(live.monitor),
        volatility: Some(live.volatility),
        probabilities: None,
        outcomes: None,
        display_lag: Some(display_lag),
        error: None,
    }
}

async fn poll_probability_runtime(client: &EngineClient) -> RuntimeUpdate {
    let probabilities_result = client.probabilities(8).await;
    let mut errors = Vec::new();
    let mut update = RuntimeUpdate {
        status: None,
        gates: None,
        monitor: None,
        volatility: None,
        probabilities: None,
        outcomes: None,
        display_lag: None,
        error: None,
    };

    match probabilities_result {
        Ok(probabilities) => update.probabilities = Some(probabilities),
        Err(error) => errors.push(format!("probabilities: {error}")),
    }

    if !errors.is_empty() {
        update.error = Some(errors.join("; "));
    }
    update
}

async fn poll_outcome_runtime(client: &EngineClient) -> RuntimeUpdate {
    let outcomes_result = client.outcomes(OUTCOME_HISTORY_LIMIT).await;
    let mut errors = Vec::new();
    let mut update = RuntimeUpdate {
        status: None,
        gates: None,
        monitor: None,
        volatility: None,
        probabilities: None,
        outcomes: None,
        display_lag: None,
        error: None,
    };

    match outcomes_result {
        Ok(outcomes) => update.outcomes = Some(outcomes),
        Err(error) => errors.push(format!("outcomes: {error}")),
    }

    if !errors.is_empty() {
        update.error = Some(errors.join("; "));
    }
    update
}

fn display_lag_with_receive_ms(
    mut display_lag: RuntimeDisplayLag,
    server_sent_at: Option<String>,
) -> RuntimeDisplayLag {
    let timestamp = display_lag
        .server_sent_at
        .as_deref()
        .or(server_sent_at.as_deref());
    display_lag.tui_receive_lag_ms = receive_lag_ms(timestamp);
    display_lag
}

fn receive_lag_ms(server_sent_at: Option<&str>) -> Option<u64> {
    let parsed = DateTime::parse_from_rfc3339(server_sent_at?).ok()?;
    let elapsed = Utc::now()
        .signed_duration_since(parsed.with_timezone(&Utc))
        .num_milliseconds();
    Some(elapsed.max(0) as u64)
}

#[cfg(test)]
mod tests {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        thread,
        time::{Duration, Instant},
    };

    use crossterm::event::KeyCode;
    use tokio::sync::mpsc;

    use super::{
        OUTCOME_HISTORY_LIMIT, OUTCOME_POLL_INTERVAL, PROBABILITY_POLL_INTERVAL, RuntimeUpdate,
        apply_key, apply_runtime_update, drain_runtime_updates, poll_interval_duration,
        poll_runtime,
    };
    use crate::client::EngineClient;
    use crate::{
        state::{AppState, MainTab},
        status::{
            RuntimeCounts, RuntimeDisplayLag, RuntimeGates, RuntimeMonitor, RuntimeOrderbookRow,
            RuntimeOutcomeRow, RuntimeOutcomes, RuntimePriceRow, RuntimeProbabilities,
            RuntimeProbabilityRow, RuntimeStatus,
        },
    };

    fn status(mode: &str) -> RuntimeStatus {
        RuntimeStatus {
            ok: true,
            schema_kind: "rust-live-probe-state-manager-v1".to_string(),
            mode: mode.to_string(),
            age_ms: Some(12),
            counts: RuntimeCounts {
                prices: 2,
                orderbooks: 4,
                current: 2,
                next: 2,
                next_next: 0,
                websocket_status: 2,
            },
            latency_marks: Vec::new(),
            health_flags: Vec::new(),
        }
    }

    fn monitor(price: &str) -> RuntimeMonitor {
        RuntimeMonitor {
            generated_at: "2026-06-03T20:43:20.744215+00:00".to_string(),
            price_rows: vec![RuntimePriceRow {
                source_key: Some("polymarket_rtds_chainlink".to_string()),
                symbol: "BTC/USD".to_string(),
                event_ts: Some("2026-06-03T20:43:16Z".to_string()),
                observed_ts: Some("2026-06-03T20:43:19.789163241Z".to_string()),
                price: Some(price.to_string()),
            }],
            orderbooks: Vec::new(),
        }
    }

    fn probabilities() -> RuntimeProbabilities {
        RuntimeProbabilities {
            generated_at: "2026-06-03T21:06:00Z".to_string(),
            cached: true,
            rows: vec![RuntimeProbabilityRow {
                contract: "BTC 5m UP".to_string(),
                contract_id: Some("btc-updown-5m-1780521900:UP".to_string()),
                market_slug: Some("btc-updown-5m-1780521900".to_string()),
                asset: Some("BTC".to_string()),
                side: Some("UP".to_string()),
                start_ts: Some("2026-06-03T21:05:00Z".to_string()),
                asof_ts: Some("2026-06-03T21:06:00Z".to_string()),
                expiry_ts: Some("2026-06-03T21:10:00Z".to_string()),
                p_finish: 0.57,
                p_no_touch: 0.31,
                z_path: 0.42,
                sigma_tau: 0.0123,
                u_gen: Some(0.046),
                age_ms: 850,
                flags: vec!["OK".to_string()],
                cache_key: Some("BTC|UP|h300|t0-30".to_string()),
                cache_status: Some("HIT".to_string()),
                generated_at: Some("2026-06-03T21:06:00Z".to_string()),
                valid_from: Some("2026-06-03T21:06:00Z".to_string()),
                valid_until: Some("2026-06-03T21:06:30Z".to_string()),
                time_bucket: Some("0-30".to_string()),
                z_path_bucket: Some("0.25-0.50".to_string()),
                sigma_bucket: Some("0.010-0.015".to_string()),
                volatility_regime: Some("normal".to_string()),
                generator_version: Some("offline-lognormal-chainlink-sigma-v1".to_string()),
                path_count: Some(10_000),
                mc_dispersion: None,
                uncertainty_buffer: None,
                path_diagnosis: Vec::new(),
                effective_weights: Default::default(),
                decision_hint: None,
                edge_after_costs: None,
                required_edge: None,
                gate_reasons: Vec::new(),
                generator_metadata: Default::default(),
            }],
        }
    }

    fn outcomes() -> RuntimeOutcomes {
        RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-03T22:00:00Z".to_string()),
            rows: vec![RuntimeOutcomeRow {
                market: "BTC 5m".to_string(),
                market_id: "btc-updown-5m-1780521900".to_string(),
                market_slug: Some("btc-updown-5m-1780521900".to_string()),
                asset: Some("BTC".to_string()),
                start_ts: None,
                expiry_ts: Some("2026-06-03T21:25:00Z".to_string()),
                threshold_price: None,
                threshold_event_ts: None,
                threshold_observed_ts: None,
                computed_winner: None,
                official_winner: Some("UP".to_string()),
                winning_token_id: Some("up-token".to_string()),
                official_resolution_status: "resolved".to_string(),
                mismatch: None,
            }],
        }
    }

    fn orderbook(asset: &str, side: &str) -> RuntimeOrderbookRow {
        RuntimeOrderbookRow {
            venue: Some("polymarket".to_string()),
            source_key: Some("polymarket_rust_sdk".to_string()),
            market_slug: Some(format!(
                "{}-updown-5m-1780519500",
                asset.to_ascii_lowercase()
            )),
            contract_id: format!("{asset}-{side}"),
            token_id: Some(format!("{asset}-{side}-token")),
            asset: Some(asset.to_string()),
            side: Some(side.to_string()),
            event_ts: None,
            observed_ts: Some("2026-06-03T21:05:58Z".to_string()),
            start_ts: None,
            expiry_ts: None,
            threshold_price: None,
            threshold_event_ts: None,
            threshold_observed_ts: None,
            settlement_price: None,
            settlement_event_ts: None,
            best_bid: None,
            best_ask: None,
            spread: None,
            bid_size_top: None,
            ask_size_top: None,
            bids: Vec::new(),
            asks: Vec::new(),
        }
    }

    fn orderbook_with_slug(
        asset: &str,
        side: &str,
        market_slug: &str,
        token_id: &str,
    ) -> RuntimeOrderbookRow {
        RuntimeOrderbookRow {
            venue: Some("polymarket".to_string()),
            source_key: Some("polymarket_rust_sdk".to_string()),
            market_slug: Some(market_slug.to_string()),
            contract_id: format!("{market_slug}:{side}"),
            token_id: Some(token_id.to_string()),
            asset: Some(asset.to_string()),
            side: Some(side.to_string()),
            event_ts: None,
            observed_ts: Some("2026-06-03T21:24:59Z".to_string()),
            start_ts: None,
            expiry_ts: None,
            threshold_price: None,
            threshold_event_ts: None,
            threshold_observed_ts: None,
            settlement_price: None,
            settlement_event_ts: None,
            best_bid: None,
            best_ask: None,
            spread: None,
            bid_size_top: None,
            ask_size_top: None,
            bids: Vec::new(),
            asks: Vec::new(),
        }
    }

    #[test]
    fn poll_interval_duration_uses_configured_milliseconds() {
        assert_eq!(poll_interval_duration(250), Duration::from_millis(250));
    }

    #[test]
    fn auxiliary_poll_interval_is_slower_than_live_market_polling() {
        assert_eq!(poll_interval_duration(100), Duration::from_millis(100));
        assert_eq!(PROBABILITY_POLL_INTERVAL, Duration::from_secs(3));
        assert_eq!(OUTCOME_POLL_INTERVAL, Duration::from_secs(15));
        assert_eq!(OUTCOME_HISTORY_LIMIT, 5000);
    }

    #[tokio::test]
    async fn poll_runtime_fetches_live_probabilities_and_outcomes_concurrently() {
        let engine_api_url = delayed_runtime_api_url(Duration::from_millis(200));
        let client = EngineClient::new(engine_api_url);
        let started = Instant::now();

        let update = poll_runtime(&client).await;

        assert!(started.elapsed() < Duration::from_millis(450));
        assert!(update.status.is_some());
        assert!(update.gates.is_some());
        assert!(update.monitor.is_some());
        assert!(update.probabilities.is_some());
        assert!(update.outcomes.is_some());
        assert_eq!(update.display_lag.unwrap().status_age_ms, Some(12));
        assert_eq!(update.error, None);
    }

    #[test]
    fn drain_runtime_updates_applies_pending_status_gates_monitor_and_errors() {
        let (tx, mut rx) = mpsc::unbounded_channel();
        tx.send(RuntimeUpdate {
            status: Some(status("first")),
            gates: None,
            monitor: Some(monitor("65000.00")),
            volatility: None,
            probabilities: None,
            outcomes: None,
            display_lag: Some(RuntimeDisplayLag {
                status_age_ms: Some(10),
                ..RuntimeDisplayLag::default()
            }),
            error: Some("status: timeout".to_string()),
        })
        .unwrap();
        tx.send(RuntimeUpdate {
            status: Some(status("second")),
            gates: Some(RuntimeGates {
                ok: false,
                failures: vec!["stale orderbook".to_string()],
            }),
            monitor: Some(monitor("65185.18")),
            volatility: None,
            probabilities: Some(probabilities()),
            outcomes: Some(outcomes()),
            display_lag: Some(RuntimeDisplayLag {
                status_age_ms: Some(12),
                ..RuntimeDisplayLag::default()
            }),
            error: None,
        })
        .unwrap();

        let mut app = AppState::default();

        assert!(drain_runtime_updates(&mut app, &mut rx));

        assert_eq!(app.runtime_status.as_ref().unwrap().mode, "second");
        assert_eq!(
            app.runtime_gates.as_ref().unwrap().failures,
            vec!["stale orderbook"]
        );
        assert_eq!(
            app.runtime_monitor.as_ref().unwrap().price_rows[0]
                .price
                .as_deref(),
            Some("65185.18")
        );
        assert_eq!(
            app.runtime_probabilities.as_ref().unwrap().rows[0].contract,
            "BTC 5m UP"
        );
        assert_eq!(
            app.runtime_outcomes.as_ref().unwrap().rows[0]
                .official_winner
                .as_deref(),
            Some("UP")
        );
        assert_eq!(
            app.runtime_display_lag.as_ref().unwrap().status_age_ms,
            Some(12)
        );
        assert_eq!(app.runtime_error, None);
    }

    #[test]
    fn apply_runtime_update_reports_changed_when_only_price_history_changes() {
        let mut app = AppState {
            runtime_monitor: Some(monitor("65000.00")),
            ..Default::default()
        };

        let changed = apply_runtime_update(
            &mut app,
            RuntimeUpdate {
                status: None,
                gates: None,
                monitor: Some(monitor("65000.00")),
                volatility: None,
                probabilities: None,
                outcomes: None,
                display_lag: None,
                error: None,
            },
        );

        assert!(changed);
        assert_eq!(app.price_history_for("BTC/USD").len(), 1);
        assert_eq!(app.price_history_for("BTC/USD")[0].price, 65000.00);
    }

    #[test]
    fn apply_runtime_update_logs_monte_carlo_health_summary() {
        let mut app = AppState::default();

        let changed = apply_runtime_update(
            &mut app,
            RuntimeUpdate {
                status: None,
                gates: None,
                monitor: None,
                volatility: None,
                probabilities: Some(probabilities()),
                outcomes: None,
                display_lag: None,
                error: None,
            },
        );

        assert!(changed);
        assert_eq!(
            app.logs,
            vec![
                "mc rows=1 BTC=UP gates=NO_HINT:1 cache=HIT:1 asof=2026-06-03T21:06:00Z gen=2026-06-03T21:06:00Z valid=2026-06-03T21:06:00Z->2026-06-03T21:06:30Z at=2026-06-03T21:06:00Z"
            ]
        );
    }

    #[test]
    fn apply_runtime_update_logs_runtime_error_and_recovery() {
        let mut app = AppState::default();

        assert!(apply_runtime_update(
            &mut app,
            RuntimeUpdate {
                status: None,
                gates: None,
                monitor: None,
                volatility: None,
                probabilities: None,
                outcomes: None,
                display_lag: None,
                error: Some("probabilities: timeout".to_string()),
            },
        ));
        assert!(apply_runtime_update(
            &mut app,
            RuntimeUpdate {
                status: None,
                gates: None,
                monitor: None,
                volatility: None,
                probabilities: None,
                outcomes: None,
                display_lag: None,
                error: None,
            },
        ));

        assert_eq!(
            app.logs,
            vec![
                "runtime_error probabilities: timeout".to_string(),
                "runtime recovered".to_string()
            ]
        );
    }

    #[test]
    fn runtime_monitor_retains_recently_expired_market_for_outcome_handoff() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:24:59Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    orderbook_with_slug(
                        "BTC",
                        "UP",
                        "btc-updown-5m-1780521900",
                        "expired-up-token",
                    ),
                    orderbook_with_slug(
                        "BTC",
                        "DOWN",
                        "btc-updown-5m-1780521900",
                        "expired-down-token",
                    ),
                ],
            }),
            ..Default::default()
        };

        let changed = apply_runtime_update(
            &mut app,
            RuntimeUpdate {
                status: None,
                gates: None,
                monitor: Some(RuntimeMonitor {
                    generated_at: "2026-06-03T21:25:30Z".to_string(),
                    price_rows: Vec::new(),
                    orderbooks: vec![orderbook_with_slug(
                        "BTC",
                        "UP",
                        "btc-updown-5m-1780522200",
                        "current-up-token",
                    )],
                }),
                volatility: None,
                probabilities: None,
                outcomes: None,
                display_lag: None,
                error: None,
            },
        );

        let token_ids = app
            .runtime_monitor
            .as_ref()
            .unwrap()
            .orderbooks
            .iter()
            .filter_map(|row| row.token_id.as_deref())
            .collect::<Vec<_>>();
        assert!(changed);
        assert_eq!(
            token_ids,
            vec!["current-up-token", "expired-up-token", "expired-down-token"]
        );
    }

    #[test]
    fn apply_runtime_update_records_outcome_before_monitor_retention() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:24:59Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    orderbook_with_slug(
                        "BTC",
                        "UP",
                        "btc-updown-5m-1780521900",
                        "expired-up-token",
                    ),
                    orderbook_with_slug(
                        "BTC",
                        "DOWN",
                        "btc-updown-5m-1780521900",
                        "expired-down-token",
                    ),
                ],
            }),
            ..Default::default()
        };

        let changed = apply_runtime_update(
            &mut app,
            RuntimeUpdate {
                status: None,
                gates: None,
                monitor: Some(RuntimeMonitor {
                    generated_at: "2026-06-03T21:26:49Z".to_string(),
                    price_rows: Vec::new(),
                    orderbooks: vec![orderbook_with_slug(
                        "BTC",
                        "UP",
                        "btc-updown-5m-1780522200",
                        "current-up-token",
                    )],
                }),
                volatility: None,
                probabilities: None,
                outcomes: Some(RuntimeOutcomes {
                    ok: true,
                    state: "OK".to_string(),
                    generated_at: Some("2026-06-03T21:26:20Z".to_string()),
                    rows: vec![RuntimeOutcomeRow {
                        market: "BTC 5m".to_string(),
                        market_id: "btc-updown-5m-1780521900".to_string(),
                        market_slug: Some("btc-updown-5m-1780521900".to_string()),
                        asset: Some("BTC".to_string()),
                        start_ts: Some("2026-06-03T21:20:00Z".to_string()),
                        expiry_ts: Some("2026-06-03T21:25:00Z".to_string()),
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        computed_winner: None,
                        official_winner: Some("UP".to_string()),
                        winning_token_id: Some("expired-up-token".to_string()),
                        official_resolution_status: "resolved".to_string(),
                        mismatch: None,
                    }],
                }),
                display_lag: None,
                error: None,
            },
        );

        let token_ids = app
            .runtime_monitor
            .as_ref()
            .unwrap()
            .orderbooks
            .iter()
            .filter_map(|row| row.token_id.as_deref())
            .collect::<Vec<_>>();
        assert!(changed);
        assert_eq!(
            token_ids,
            vec!["current-up-token", "expired-up-token", "expired-down-token"]
        );
    }

    #[test]
    fn drain_runtime_updates_ignores_unchanged_payloads() {
        let (tx, mut rx) = mpsc::unbounded_channel();
        tx.send(RuntimeUpdate {
            status: Some(status("state-manager")),
            gates: Some(RuntimeGates {
                ok: true,
                failures: Vec::new(),
            }),
            monitor: Some(monitor("65000.00")),
            volatility: None,
            probabilities: Some(probabilities()),
            outcomes: Some(outcomes()),
            display_lag: Some(RuntimeDisplayLag {
                status_age_ms: Some(12),
                ..RuntimeDisplayLag::default()
            }),
            error: None,
        })
        .unwrap();

        let mut app = AppState {
            runtime_status: Some(status("state-manager")),
            runtime_gates: Some(RuntimeGates {
                ok: true,
                failures: Vec::new(),
            }),
            runtime_probabilities: Some(probabilities()),
            runtime_display_lag: Some(RuntimeDisplayLag {
                status_age_ms: Some(12),
                ..RuntimeDisplayLag::default()
            }),
            runtime_error: None,
            ..Default::default()
        };
        app.apply_runtime_monitor(monitor("65000.00"));
        app.apply_runtime_outcomes(outcomes());

        assert!(!drain_runtime_updates(&mut app, &mut rx));
    }

    #[test]
    fn apply_key_moves_market_selection_with_up_down() {
        let mut app = AppState {
            active_tab: MainTab::Market,
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T20:44:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![orderbook("BTC", "UP"), orderbook("BTC", "DOWN")],
            }),
            ..Default::default()
        };
        app.sync_market_selection();

        assert!(!apply_key(&mut app, KeyCode::Down));
        assert_eq!(app.selected_market_index(), Some(0));

        assert!(!apply_key(&mut app, KeyCode::Up));
        assert_eq!(app.selected_market_index(), Some(0));
    }

    #[test]
    fn apply_key_moves_outcome_selection_with_up_down() {
        let mut app = AppState {
            active_tab: MainTab::Outcomes,
            runtime_outcomes: Some(RuntimeOutcomes {
                ok: true,
                state: "OK".to_string(),
                generated_at: Some("2026-06-03T22:00:00Z".to_string()),
                rows: vec![
                    RuntimeOutcomeRow {
                        market: "BTC 5m".to_string(),
                        market_id: "btc-updown-5m-1780521900".to_string(),
                        market_slug: Some("btc-updown-5m-1780521900".to_string()),
                        asset: Some("BTC".to_string()),
                        start_ts: None,
                        expiry_ts: Some("2026-06-03T21:25:00Z".to_string()),
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        computed_winner: None,
                        official_winner: Some("UP".to_string()),
                        winning_token_id: Some("up-token".to_string()),
                        official_resolution_status: "resolved".to_string(),
                        mismatch: None,
                    },
                    RuntimeOutcomeRow {
                        market: "ETH 5m".to_string(),
                        market_id: "eth-updown-5m-1780521900".to_string(),
                        market_slug: Some("eth-updown-5m-1780521900".to_string()),
                        asset: Some("ETH".to_string()),
                        start_ts: None,
                        expiry_ts: Some("2026-06-03T21:25:00Z".to_string()),
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        computed_winner: None,
                        official_winner: Some("DOWN".to_string()),
                        winning_token_id: Some("down-token".to_string()),
                        official_resolution_status: "resolved".to_string(),
                        mismatch: None,
                    },
                ],
            }),
            ..Default::default()
        };
        app.sync_outcome_expansion_defaults();
        app.sync_outcome_selection();

        assert!(!apply_key(&mut app, KeyCode::Down));
        assert_eq!(app.selected_outcome_index, Some(1));

        assert!(!apply_key(&mut app, KeyCode::Up));
        assert_eq!(app.selected_outcome_index, Some(0));
    }

    fn delayed_runtime_api_url(delay: Duration) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        thread::spawn(move || {
            for _ in 0..3 {
                let Ok((mut stream, _peer)) = listener.accept() else {
                    return;
                };
                thread::spawn(move || {
                    let mut buffer = [0; 1024];
                    let bytes_read = stream.read(&mut buffer).unwrap_or(0);
                    let request = String::from_utf8_lossy(&buffer[..bytes_read]).to_string();
                    let path = request
                        .lines()
                        .next()
                        .and_then(|line| line.split_whitespace().nth(1))
                        .unwrap_or("/");
                    thread::sleep(delay);
                    let body = runtime_response_body(path);
                    let response = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{}",
                        body.len(),
                        body
                    );
                    stream.write_all(response.as_bytes()).unwrap();
                });
            }
        });

        format!("http://{address}")
    }

    fn runtime_response_body(path: &str) -> &'static str {
        if path.starts_with("/api/runtime/live") {
            r#"{
                "ok": true,
                "server_sent_at": "2026-06-03T21:00:00+00:00",
                "status": {
                    "ok": true,
                    "schema_kind": "rust-live-probe-state-manager-v1",
                    "mode": "state-manager",
                    "age_ms": 12,
                    "counts": {"prices": 2, "orderbooks": 4, "current": 2, "next": 2, "next_next": 0, "websocket_status": 2},
                    "latency_marks": [],
                    "health_flags": []
                },
                "gates": {"ok": true, "failures": []},
                "monitor": {
                    "generated_at": "2026-06-03T20:43:20.744215+00:00",
                    "price_rows": [{
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "BTC/USD",
                        "observed_ts": "2026-06-03T20:43:19.789163241Z",
                        "price": "65000.00"
                    }],
                    "orderbooks": []
                },
                "latency": {
                    "status_age_ms": 12,
                    "api_build_ms": 1,
                    "server_sent_at": "2026-06-03T21:00:00+00:00"
                }
            }"#
        } else if path.starts_with("/api/runtime/status") {
            r#"{
                "ok": true,
                "schema_kind": "rust-live-probe-state-manager-v1",
                "mode": "state-manager",
                "age_ms": 10,
                "counts": {"prices": 2, "orderbooks": 4, "current": 2, "next": 2, "next_next": 0, "websocket_status": 2},
                "latency_marks": [],
                "health_flags": []
            }"#
        } else if path.starts_with("/api/runtime/gates") {
            r#"{"ok": true, "failures": []}"#
        } else if path.starts_with("/api/runtime/probabilities") {
            r#"{
                "generated_at": "2026-06-03T21:06:00Z",
                "cached": true,
                "rows": [{
                    "contract": "BTC 5m UP",
                    "p_finish": 0.57,
                    "p_no_touch": 0.31,
                    "z_path": 0.42,
                    "sigma_tau": 0.0123,
                    "age_ms": 850,
                    "flags": ["OK"]
                }]
            }"#
        } else if path.starts_with("/api/runtime/outcomes") {
            r#"{
                "ok": true,
                "state": "OK",
                "generated_at": "2026-06-03T22:00:00Z",
                "rows": [{
                    "market": "BTC 5m",
                    "market_id": "btc-updown-5m-1780521900",
                    "asset": "BTC",
                    "expiry_ts": "2026-06-03T21:25:00Z",
                    "computed_winner": null,
                    "official_winner": "UP",
                    "winning_token_id": "up-token",
                    "official_resolution_status": "resolved",
                    "mismatch": null
                }]
            }"#
        } else {
            r#"{
                "generated_at": "2026-06-03T20:43:20.744215+00:00",
                "price_rows": [{
                    "source_key": "polymarket_rtds_chainlink",
                    "symbol": "BTC/USD",
                    "observed_ts": "2026-06-03T20:43:19.789163241Z",
                    "price": "65000.00"
                }],
                "orderbooks": []
            }"#
        }
    }
}
