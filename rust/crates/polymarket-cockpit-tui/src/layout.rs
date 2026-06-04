use ratatui::layout::{Constraint, Direction, Layout, Rect};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CockpitLayout {
    pub header: Rect,
    pub body: Rect,
    pub footer: Rect,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BodyLayout {
    pub primary: Rect,
    pub secondary: Rect,
    pub logs: Rect,
}

pub fn cockpit(area: Rect) -> CockpitLayout {
    let [header, body, footer] = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(8),
            Constraint::Length(3),
        ])
        .areas(area);

    CockpitLayout {
        header,
        body,
        footer,
    }
}

pub fn body(area: Rect) -> BodyLayout {
    let [top, logs] = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(7), Constraint::Length(7)])
        .areas(area);
    let [primary, secondary] = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(58), Constraint::Percentage(42)])
        .areas(top);

    BodyLayout {
        primary,
        secondary,
        logs,
    }
}
