use std::io::{self, Stdout};
use std::time::Duration;

use anyhow::Result;
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
    status::{RuntimeGates, RuntimeMonitor, RuntimeStatus},
};

type Tui = Terminal<CrosstermBackend<Stdout>>;

pub async fn run(mut app: AppState, engine_api_url: String, poll_interval_ms: u64) -> Result<()> {
    let mut terminal = TerminalGuard::enter()?;
    let (runtime_tx, mut runtime_rx) = mpsc::unbounded_channel();
    let _poll_task = RuntimePollTask::spawn(engine_api_url, poll_interval_ms, runtime_tx);

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
    loop {
        drain_runtime_updates(app, runtime_rx);

        terminal.draw(|frame| render::render(frame, app))?;

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
        _ => false,
    }
}

#[derive(Debug)]
struct RuntimeUpdate {
    status: Option<RuntimeStatus>,
    gates: Option<RuntimeGates>,
    monitor: Option<RuntimeMonitor>,
    error: Option<String>,
}

struct RuntimePollTask {
    handle: JoinHandle<()>,
}

impl RuntimePollTask {
    fn spawn(
        engine_api_url: String,
        poll_interval_ms: u64,
        runtime_tx: mpsc::UnboundedSender<RuntimeUpdate>,
    ) -> Self {
        let handle = tokio::spawn(async move {
            let client = EngineClient::new(engine_api_url);
            let mut interval = tokio::time::interval(poll_interval_duration(poll_interval_ms));

            loop {
                interval.tick().await;
                let update = poll_runtime(&client).await;
                if runtime_tx.send(update).is_err() {
                    break;
                }
            }
        });

        Self { handle }
    }
}

fn poll_interval_duration(poll_interval_ms: u64) -> Duration {
    Duration::from_millis(poll_interval_ms.max(1))
}

impl Drop for RuntimePollTask {
    fn drop(&mut self) {
        self.handle.abort();
    }
}

fn drain_runtime_updates(
    app: &mut AppState,
    runtime_rx: &mut mpsc::UnboundedReceiver<RuntimeUpdate>,
) {
    while let Ok(update) = runtime_rx.try_recv() {
        apply_runtime_update(app, update);
    }
}

fn apply_runtime_update(app: &mut AppState, update: RuntimeUpdate) {
    if let Some(status) = update.status {
        app.runtime_status = Some(status);
    }

    if let Some(gates) = update.gates {
        app.runtime_gates = Some(gates);
    }

    if let Some(monitor) = update.monitor {
        app.runtime_monitor = Some(monitor);
        app.sync_market_selection();
    }

    app.runtime_error = update.error;
}

async fn poll_runtime(client: &EngineClient) -> RuntimeUpdate {
    let mut errors = Vec::new();
    let mut status = None;
    let mut gates = None;
    let mut monitor = None;

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

    RuntimeUpdate {
        status,
        gates,
        monitor,
        error: if errors.is_empty() {
            None
        } else {
            Some(errors.join("; "))
        },
    }
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
        RuntimeUpdate, apply_key, drain_runtime_updates, poll_interval_duration, poll_runtime,
    };
    use crate::client::EngineClient;
    use crate::{
        state::{AppState, MainTab},
        status::{
            RuntimeCounts, RuntimeGates, RuntimeMonitor, RuntimeOrderbookRow, RuntimePriceRow,
            RuntimeStatus,
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

    #[tokio::test]
    async fn poll_runtime_fetches_endpoints_concurrently() {
        let engine_api_url = delayed_runtime_api_url(Duration::from_millis(200));
        let client = EngineClient::new(engine_api_url);
        let started = Instant::now();

        let update = poll_runtime(&client).await;

        assert!(started.elapsed() < Duration::from_millis(450));
        assert!(update.status.is_some());
        assert!(update.gates.is_some());
        assert!(update.monitor.is_some());
        assert_eq!(update.error, None);
    }

    #[test]
    fn drain_runtime_updates_applies_pending_status_gates_monitor_and_errors() {
        let (tx, mut rx) = mpsc::unbounded_channel();
        tx.send(RuntimeUpdate {
            status: Some(status("first")),
            gates: None,
            monitor: Some(monitor("65000.00")),
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
            error: None,
        })
        .unwrap();

        let mut app = AppState::default();

        drain_runtime_updates(&mut app, &mut rx);

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
        assert_eq!(app.runtime_error, None);
    }

    #[test]
    fn apply_key_moves_market_selection_with_up_down() {
        let mut app = AppState {
            active_tab: MainTab::Market,
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![orderbook("BTC", "UP"), orderbook("BTC", "DOWN")],
            }),
            ..Default::default()
        };
        app.sync_market_selection();

        assert!(!apply_key(&mut app, KeyCode::Down));
        assert_eq!(app.selected_market_index(), Some(1));

        assert!(!apply_key(&mut app, KeyCode::Up));
        assert_eq!(app.selected_market_index(), Some(0));
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
        if path.starts_with("/api/runtime/status") {
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
