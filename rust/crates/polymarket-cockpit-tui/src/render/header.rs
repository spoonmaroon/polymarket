use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Modifier, Style},
    text::Line,
    widgets::{Block, Tabs},
};

use crate::{
    render::price,
    state::{AppState, MainTab},
};

fn header_title(app: &AppState) -> String {
    let mut title = match &app.runtime_status {
        Some(status) => format!("Polymarket Engine Cockpit | {}", status.state_label()),
        None => "Polymarket Engine Cockpit | WAITING".to_string(),
    };

    let prices = price::price_lines(app)
        .into_iter()
        .map(compact_header_price)
        .collect::<Vec<_>>();
    if !prices.is_empty() {
        title.push_str(" | ");
        title.push_str(&prices.join(" | "));
    }

    title
}

fn compact_header_price(line: String) -> String {
    line.replace("BTC/USD", "BTC").replace("ETH/USD", "ETH")
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let titles = MainTab::all()
        .iter()
        .map(|tab| Line::from(tab.label()))
        .collect::<Vec<_>>();
    let selected = MainTab::all()
        .iter()
        .position(|tab| *tab == app.active_tab)
        .unwrap_or_default();

    let tabs = Tabs::new(titles)
        .select(selected)
        .block(Block::bordered().title(header_title(app)))
        .style(Style::default().fg(Color::Gray))
        .highlight_style(
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        );

    frame.render_widget(tabs, area);
}

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{RuntimeCounts, RuntimeMonitor, RuntimePriceRow, RuntimeStatus},
    };

    use super::header_title;

    #[test]
    fn header_title_keeps_btc_and_eth_prices_on_top() {
        let app = AppState {
            runtime_status: Some(RuntimeStatus {
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
            }),
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T20:43:20.744215+00:00".to_string(),
                price_rows: vec![
                    RuntimePriceRow {
                        source_key: Some("polymarket_rtds_chainlink".to_string()),
                        symbol: "BTC/USD".to_string(),
                        event_ts: Some("2026-06-03T20:43:16Z".to_string()),
                        observed_ts: Some("2026-06-03T20:43:19.789163241Z".to_string()),
                        price: Some("65185.18675916348".to_string()),
                    },
                    RuntimePriceRow {
                        source_key: Some("polymarket_rtds_chainlink".to_string()),
                        symbol: "ETH/USD".to_string(),
                        event_ts: Some("2026-06-03T20:43:16Z".to_string()),
                        observed_ts: Some("2026-06-03T20:43:19.887210668Z".to_string()),
                        price: Some("1795.02822".to_string()),
                    },
                ],
                orderbooks: Vec::new(),
            }),
            ..Default::default()
        };

        assert_eq!(
            header_title(&app),
            "Polymarket Engine Cockpit | OK | BTC $65,185.19 | ETH $1,795.03"
        );
    }
}
