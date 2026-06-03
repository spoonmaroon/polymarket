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
    state::AppState,
    status::{RuntimeGates, RuntimeStatus},
};

type Tui = Terminal<CrosstermBackend<Stdout>>;

pub async fn run(mut app: AppState, engine_api_url: String) -> Result<()> {
    let mut terminal = TerminalGuard::enter()?;
    let (runtime_tx, mut runtime_rx) = mpsc::unbounded_channel();
    let _poll_task = RuntimePollTask::spawn(engine_api_url, runtime_tx);

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

        match key.code {
            KeyCode::Char('q') | KeyCode::Esc => return Ok(()),
            KeyCode::Left | KeyCode::BackTab => app.previous_tab(),
            KeyCode::Right | KeyCode::Tab => app.next_tab(),
            _ => {}
        }
    }
}

#[derive(Debug)]
struct RuntimeUpdate {
    status: Option<RuntimeStatus>,
    gates: Option<RuntimeGates>,
    error: Option<String>,
}

struct RuntimePollTask {
    handle: JoinHandle<()>,
}

impl RuntimePollTask {
    fn spawn(engine_api_url: String, runtime_tx: mpsc::UnboundedSender<RuntimeUpdate>) -> Self {
        let handle = tokio::spawn(async move {
            let client = EngineClient::new(engine_api_url);
            let mut interval = tokio::time::interval(Duration::from_secs(1));

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

    app.runtime_error = update.error;
}

async fn poll_runtime(client: &EngineClient) -> RuntimeUpdate {
    let mut errors = Vec::new();
    let mut status = None;
    let mut gates = None;

    match client.status().await {
        Ok(next_status) => status = Some(next_status),
        Err(error) => errors.push(format!("status: {error}")),
    }

    match client.gates().await {
        Ok(next_gates) => gates = Some(next_gates),
        Err(error) => errors.push(format!("gates: {error}")),
    }

    RuntimeUpdate {
        status,
        gates,
        error: if errors.is_empty() {
            None
        } else {
            Some(errors.join("; "))
        },
    }
}

#[cfg(test)]
mod tests {
    use tokio::sync::mpsc;

    use super::{RuntimeUpdate, drain_runtime_updates};
    use crate::{
        state::AppState,
        status::{RuntimeCounts, RuntimeGates, RuntimeStatus},
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

    #[test]
    fn drain_runtime_updates_applies_pending_status_gates_and_errors() {
        let (tx, mut rx) = mpsc::unbounded_channel();
        tx.send(RuntimeUpdate {
            status: Some(status("first")),
            gates: None,
            error: Some("status: timeout".to_string()),
        })
        .unwrap();
        tx.send(RuntimeUpdate {
            status: Some(status("second")),
            gates: Some(RuntimeGates {
                ok: false,
                failures: vec!["stale orderbook".to_string()],
            }),
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
        assert_eq!(app.runtime_error, None);
    }
}
