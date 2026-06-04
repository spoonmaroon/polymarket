use crate::state::AppState;
use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, List, ListItem},
};

pub fn systems_summary_lines(app: &AppState) -> Vec<String> {
    let mut lines = if let Some(status) = &app.runtime_status {
        vec![
            format!("Engine API {}", status.state_label()),
            format!(
                "status_age_ms={}",
                status
                    .age_ms
                    .map_or("-".to_string(), |value| value.to_string())
            ),
            format!("prices={}", status.counts.prices),
            format!("orderbooks={}", status.counts.orderbooks),
        ]
    } else {
        vec!["Engine API UNKNOWN".to_string()]
    };

    if let Some(gates) = &app.runtime_gates {
        for failure in &gates.failures {
            lines.push(format!("block={failure}"));
        }
    }

    if let Some(lag) = app.runtime_display_lag.as_ref() {
        if let Some(value) = lag.status_age_ms {
            lines.push(format!("status_age_ms={value}"));
        }
        if let Some(value) = lag.observed_to_state_us {
            lines.push(format!("state_us={value}"));
        }
        if let Some(value) = lag.api_build_ms {
            lines.push(format!("api_build_ms={value}"));
        }
        if let Some(value) = lag.tui_receive_lag_ms {
            lines.push(format!("tui_rx_ms={value}"));
        }
    }

    if let Some(error) = &app.runtime_error {
        lines.push(format!("runtime_error={error}"));
    }

    lines
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let items = systems_summary_lines(app)
        .into_iter()
        .map(ListItem::new)
        .collect::<Vec<_>>();
    let list = List::new(items)
        .block(Block::bordered().title("Systems"))
        .style(Style::default().fg(Color::Gray));

    frame.render_widget(list, area);
}

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{RuntimeCounts, RuntimeDisplayLag, RuntimeGates, RuntimeStatus},
    };

    use super::systems_summary_lines;

    #[test]
    fn systems_summary_shows_counts_and_gate_failures() {
        let app = AppState {
            runtime_status: Some(runtime_status(false, vec!["source stale".to_string()])),
            runtime_gates: Some(RuntimeGates {
                ok: false,
                failures: vec!["status file stale".to_string()],
            }),
            ..Default::default()
        };

        let text = systems_summary_lines(&app).join("\n");

        assert!(text.contains("Engine API BLOCKED"));
        assert!(text.contains("status_age_ms=42"));
        assert!(text.contains("prices=2"));
        assert!(text.contains("block=status file stale"));
    }

    #[test]
    fn systems_summary_keeps_counts_without_gates() {
        let app = AppState {
            runtime_status: Some(runtime_status(true, vec![])),
            ..Default::default()
        };

        let text = systems_summary_lines(&app).join("\n");

        assert!(text.contains("Engine API OK"));
        assert!(text.contains("status_age_ms=42"));
        assert!(text.contains("prices=2"));
        assert!(text.contains("orderbooks=4"));
        assert!(!text.contains("block="));
    }

    #[test]
    fn systems_summary_shows_runtime_lag_metrics() {
        let app = AppState {
            runtime_display_lag: Some(RuntimeDisplayLag {
                status_age_ms: Some(57),
                api_build_ms: Some(1),
                observed_to_state_us: Some(220),
                tui_receive_lag_ms: Some(8),
                ..RuntimeDisplayLag::default()
            }),
            ..Default::default()
        };

        let text = systems_summary_lines(&app).join("\n");

        assert!(text.contains("status_age_ms=57"));
        assert!(text.contains("state_us=220"));
        assert!(text.contains("tui_rx_ms=8"));
    }

    fn runtime_status(ok: bool, health_flags: Vec<String>) -> RuntimeStatus {
        RuntimeStatus {
            ok,
            schema_kind: "rust-live-probe-state-manager-v1".to_string(),
            mode: "state-manager".to_string(),
            age_ms: Some(42),
            counts: RuntimeCounts {
                prices: 2,
                orderbooks: 4,
                current: 2,
                next: 2,
                next_next: 0,
                websocket_status: 2,
            },
            latency_marks: vec![],
            health_flags,
        }
    }
}
