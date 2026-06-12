use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, List, ListItem},
};

use crate::state::AppState;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum LogView {
    Full,
    Compact,
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    render_with_view(frame, area, app, LogView::Full);
}

pub fn render_compact(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    render_with_view(frame, area, app, LogView::Compact);
}

fn render_with_view(frame: &mut Frame<'_>, area: Rect, app: &AppState, view: LogView) {
    let visible_rows = usize::from(area.height.saturating_sub(2));
    let items = visible_log_lines(app, visible_rows, view)
        .into_iter()
        .map(ListItem::new)
        .collect::<Vec<_>>();

    let list = List::new(items)
        .block(Block::bordered().title("Logs"))
        .style(Style::default().fg(Color::Gray));

    frame.render_widget(list, area);
}

pub(crate) fn visible_log_lines(app: &AppState, visible_rows: usize, view: LogView) -> Vec<String> {
    if app.logs.is_empty() {
        return vec!["read-only shell initialized".to_string()];
    }

    match view {
        LogView::Full => {
            let visible_rows = visible_rows.max(1);
            let start = app.log_window_start(visible_rows);
            let end = (start + visible_rows).min(app.logs.len());
            app.logs[start..end].to_vec()
        }
        LogView::Compact => {
            let visible_rows = visible_rows.max(1);
            let count = app.logs.len().min(visible_rows).min(5);
            let start = app.logs.len() - count;
            app.logs[start..].to_vec()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{LogView, visible_log_lines};
    use crate::state::AppState;

    #[test]
    fn full_logs_follow_bottom_with_newest_entry_last() {
        let mut app = AppState::default();
        app.append_log("one");
        app.append_log("two");
        app.append_log("three");

        assert_eq!(
            visible_log_lines(&app, 2, LogView::Full),
            vec!["two".to_string(), "three".to_string()]
        );
    }

    #[test]
    fn full_logs_respect_scroll_offset() {
        let mut app = AppState::default();
        app.append_log("one");
        app.append_log("two");
        app.append_log("three");
        app.scroll_logs_up(1);

        assert_eq!(
            visible_log_lines(&app, 2, LogView::Full),
            vec!["one".to_string(), "two".to_string()]
        );
    }

    #[test]
    fn compact_logs_show_latest_five_in_chronological_order() {
        let mut app = AppState::default();
        for index in 1..=6 {
            app.append_log(format!("line-{index}"));
        }

        assert_eq!(
            visible_log_lines(&app, 10, LogView::Compact),
            vec![
                "line-2".to_string(),
                "line-3".to_string(),
                "line-4".to_string(),
                "line-5".to_string(),
                "line-6".to_string(),
            ]
        );
    }
}
