use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, List, ListItem},
};

use crate::state::AppState;

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let items = if app.logs.is_empty() {
        vec![ListItem::new("read-only shell initialized")]
    } else {
        app.logs
            .iter()
            .rev()
            .take(5)
            .map(|line| ListItem::new(line.as_str()))
            .collect()
    };

    let list = List::new(items)
        .block(Block::bordered().title("Logs"))
        .style(Style::default().fg(Color::Gray));

    frame.render_widget(list, area);
}
