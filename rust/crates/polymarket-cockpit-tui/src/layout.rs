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
    pub systems: Rect,
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
    let [top, bottom] = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(7), Constraint::Length(7)])
        .areas(area);
    let [primary, secondary] = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(58), Constraint::Percentage(42)])
        .areas(top);
    let [systems, logs] = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(35), Constraint::Percentage(65)])
        .areas(bottom);

    BodyLayout {
        primary,
        secondary,
        systems,
        logs,
    }
}

#[cfg(test)]
mod tests {
    use ratatui::layout::Rect;

    use super::body;

    #[test]
    fn body_layout_splits_bottom_row_between_systems_and_logs() {
        let layout = body(Rect::new(0, 0, 100, 30));

        assert_eq!(layout.systems.y, layout.logs.y);
        assert!(layout.systems.width < layout.logs.width);
        assert_eq!(layout.systems.x, 0);
        assert_eq!(layout.logs.x, layout.systems.width);
    }
}
