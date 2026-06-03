use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

pub fn render(frame: &mut Frame<'_>, area: Rect) {
    let rows = [
        Row::new(vec![
            Cell::from("Bid"),
            Cell::from("0.00"),
            Cell::from("pending"),
        ]),
        Row::new(vec![
            Cell::from("Ask"),
            Cell::from("0.00"),
            Cell::from("pending"),
        ]),
    ];
    let table = Table::new(
        rows,
        [
            ratatui::layout::Constraint::Length(10),
            ratatui::layout::Constraint::Length(10),
            ratatui::layout::Constraint::Min(12),
        ],
    )
    .header(
        Row::new(vec!["Side", "Price", "Depth"]).style(Style::default().fg(Color::Cyan)),
    )
    .block(Block::bordered().title("Market"));

    frame.render_widget(table, area);
}
