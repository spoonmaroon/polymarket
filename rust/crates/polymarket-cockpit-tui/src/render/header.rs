use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Modifier, Style},
    text::Line,
    widgets::{Block, Tabs},
};

use crate::state::{AppState, MainTab};

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let titles = MainTab::all()
        .iter()
        .map(|tab| Line::from(tab.label()))
        .collect::<Vec<_>>();
    let selected = MainTab::all()
        .iter()
        .position(|tab| *tab == app.active_tab)
        .unwrap_or_default();

    let title = match &app.runtime_status {
        Some(status) => format!("Polymarket Engine Cockpit | {}", status.state_label()),
        None => "Polymarket Engine Cockpit | WAITING".to_string(),
    };

    let tabs = Tabs::new(titles)
        .select(selected)
        .block(Block::bordered().title(title))
        .style(Style::default().fg(Color::Gray))
        .highlight_style(
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        );

    frame.render_widget(tabs, area);
}
