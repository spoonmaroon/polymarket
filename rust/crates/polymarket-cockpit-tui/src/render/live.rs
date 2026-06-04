use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, List, ListItem},
};

use crate::{render::price, state::AppState};

fn live_lines(app: &AppState) -> Vec<String> {
    let mut lines = price::price_lines(app);

    let Some(status) = &app.runtime_status else {
        if lines.is_empty() {
            lines.push("Runtime status pending".to_string());
        } else {
            lines.push("Engine API UNKNOWN".to_string());
        }
        return lines;
    };

    lines.extend([
        format!("Engine API {}", status.state_label()),
        format!("prices={}", status.counts.prices),
        format!("orderbooks={}", status.counts.orderbooks),
    ]);

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

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{RuntimeCounts, RuntimeMonitor, RuntimePriceRow, RuntimeStatus},
    };

    use super::live_lines;

    #[test]
    fn live_lines_put_btc_and_eth_prices_first() {
        let app = AppState {
            runtime_status: Some(runtime_status()),
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T20:43:20.744215+00:00".to_string(),
                price_rows: vec![
                    RuntimePriceRow {
                        source_key: Some("polymarket_rtds_chainlink".to_string()),
                        symbol: "ETH/USD".to_string(),
                        event_ts: Some("2026-06-03T20:43:16Z".to_string()),
                        observed_ts: Some("2026-06-03T20:43:19.887210668Z".to_string()),
                        price: Some("1795.02822".to_string()),
                    },
                    RuntimePriceRow {
                        source_key: Some("polymarket_rtds_chainlink".to_string()),
                        symbol: "BTC/USD".to_string(),
                        event_ts: Some("2026-06-03T20:43:16Z".to_string()),
                        observed_ts: Some("2026-06-03T20:43:19.789163241Z".to_string()),
                        price: Some("65185.18675916348".to_string()),
                    },
                ],
                orderbooks: Vec::new(),
            }),
            ..Default::default()
        };

        let lines = live_lines(&app);

        assert_eq!(lines[0], "BTC/USD $65,185.19");
        assert_eq!(lines[1], "ETH/USD $1,795.03");
        assert!(lines[2].starts_with("Engine API"));
    }

    fn runtime_status() -> RuntimeStatus {
        RuntimeStatus {
            ok: true,
            schema_kind: "rust-live-probe-state-manager-v1".to_string(),
            mode: "state-manager".to_string(),
            age_ms: Some(12),
            counts: RuntimeCounts {
                prices: 2,
                orderbooks: 4,
                current: 2,
                next: 2,
                next_next: 0,
                websocket_status: 2,
            },
            latency_marks: Vec::new(),
            health_flags: Vec::new(),
        }
    }
}
