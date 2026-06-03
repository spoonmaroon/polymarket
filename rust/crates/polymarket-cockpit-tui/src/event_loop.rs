use std::io::{self, Stdout};
use std::time::Duration;

use anyhow::Result;
use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind},
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode},
};
use ratatui::{Terminal, backend::CrosstermBackend};

use crate::{render, state::AppState};

type Tui = Terminal<CrosstermBackend<Stdout>>;

pub fn run(mut app: AppState) -> Result<()> {
    let mut terminal = TerminalGuard::enter()?;
    run_loop(terminal.terminal_mut(), &mut app)
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

fn run_loop(terminal: &mut Tui, app: &mut AppState) -> Result<()> {
    loop {
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
