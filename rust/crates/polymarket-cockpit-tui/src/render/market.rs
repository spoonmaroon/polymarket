use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{render::orderbook, state::AppState};

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let [counts_area, orderbook_area] = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(8), Constraint::Min(3)])
        .areas(area);
    let counts = app.runtime_status.as_ref().map(|status| &status.counts);
    let rows = [
        Row::new(vec![
            Cell::from("Current"),
            Cell::from(counts.map_or("-".to_string(), |value| value.current.to_string())),
        ]),
        Row::new(vec![
            Cell::from("Next"),
            Cell::from(counts.map_or("-".to_string(), |value| value.next.to_string())),
        ]),
        Row::new(vec![
            Cell::from("Next next"),
            Cell::from(counts.map_or("-".to_string(), |value| value.next_next.to_string())),
        ]),
        Row::new(vec![
            Cell::from("Websocket"),
            Cell::from(counts.map_or("-".to_string(), |value| value.websocket_status.to_string())),
        ]),
    ];
    let table = Table::new(rows, [Constraint::Length(14), Constraint::Min(10)])
        .header(Row::new(vec!["Window", "Count"]).style(Style::default().fg(Color::Cyan)))
        .block(Block::bordered().title("Market"));

    frame.render_widget(table, counts_area);
    orderbook::render(frame, orderbook_area);
}
