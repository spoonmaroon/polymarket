use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, List, ListItem},
};

use crate::state::AppState;

fn live_lines(app: &AppState) -> Vec<String> {
    let Some(status) = &app.runtime_status else {
        return vec!["Runtime status pending".to_string()];
    };

    let mut lines = vec![
        format!("Engine API {}", status.state_label()),
        format!("prices={}", status.counts.prices),
        format!("orderbooks={}", status.counts.orderbooks),
    ];

    if status.latency_marks.is_empty() {
        lines.push("latency_marks=0".to_string());
    } else {
        for mark in &status.latency_marks {
            let elapsed = mark
                .elapsed_ms
                .map_or("-".to_string(), |value| value.to_string());
            lines.push(format!("latency {}={elapsed}ms", mark.name));
        }
    }

    lines
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let items = live_lines(app)
        .into_iter()
        .map(ListItem::new)
        .collect::<Vec<_>>();
    let list = List::new(items)
        .block(Block::bordered().title("Live"))
        .style(Style::default().fg(Color::Gray));

    frame.render_widget(list, area);
}
