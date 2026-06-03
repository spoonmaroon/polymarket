pub mod footer;
pub mod header;
pub mod logs;
pub mod orderbook;

use ratatui::{Frame, widgets::Paragraph};

use crate::{layout, state::AppState};

pub fn render(frame: &mut Frame<'_>, app: &AppState) {
    let shell = layout::cockpit(frame.area());
    let body = layout::body(shell.body);

    header::render(frame, shell.header, app);
    orderbook::render(frame, body.primary);
    render_status_panel(frame, body.secondary);
    logs::render(frame, body.logs, app);
    footer::render(frame, shell.footer);
}

fn render_status_panel(frame: &mut Frame<'_>, area: ratatui::layout::Rect) {
    let panel = Paragraph::new(
        "Runtime: waiting for read-only endpoints\nSource freshness: pending\nGate status: pending\nStorage: pending",
    )
    .block(ratatui::widgets::Block::bordered().title("Systems"));

    frame.render_widget(panel, area);
}
