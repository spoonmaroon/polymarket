use chrono::{DateTime, Utc};
use ratatui::{
    Frame,
    layout::{Constraint, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{market_view, state::AppState};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PricePathDisplayRow {
    pub asset: String,
    pub latest: String,
    pub target: String,
    pub path: String,
}

pub fn price_path_rows(app: &AppState) -> Vec<PricePathDisplayRow> {
    ["BTC", "ETH"]
        .into_iter()
        .map(|asset| {
            let symbol = format!("{asset}/USD");
            let history = app.price_history_for(&symbol);
            PricePathDisplayRow {
                asset: asset.to_string(),
                latest: history
                    .last()
                    .map(|point| format_usd_number(point.price))
                    .unwrap_or_else(|| "-".to_string()),
                target: current_target_label(app, asset),
                path: compact_path(&history),
            }
        })
        .collect()
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let rows = price_path_rows(app)
        .into_iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(row.asset),
                Cell::from(row.latest),
                Cell::from(row.target),
                Cell::from(row.path),
            ])
        })
        .collect::<Vec<_>>();
    let table = Table::new(
        rows,
        [
            Constraint::Length(5),
            Constraint::Length(12),
            Constraint::Length(14),
            Constraint::Min(8),
        ],
    )
    .header(
        Row::new(vec!["Asset", "Latest", "Target", "Path"]).style(Style::default().fg(Color::Cyan)),
    )
    .block(Block::bordered().title("Price Path"));

    frame.render_widget(table, area);
}

fn current_target_label(app: &AppState, asset: &str) -> String {
    app.runtime_monitor
        .as_ref()
        .and_then(|monitor| {
            let generated_at = parse_runtime_timestamp(&monitor.generated_at)?;
            market_view::market_groups(&monitor.orderbooks)
                .into_iter()
                .filter(|group| group.asset.eq_ignore_ascii_case(asset))
                .filter(|group| active_at(group, generated_at))
                .find_map(|group| threshold_price(&group))
        })
        .map(|price| format!("K {}", format_usd_number(price)))
        .unwrap_or_else(|| "K pending".to_string())
}

fn active_at(group: &market_view::MarketGroup<'_>, generated_at: DateTime<Utc>) -> bool {
    let Some(start_ts) = group.start_ts else {
        return false;
    };
    let Some(expiry_ts) = group.expiry_ts else {
        return false;
    };

    start_ts <= generated_at && generated_at < expiry_ts
}

fn threshold_price(group: &market_view::MarketGroup<'_>) -> Option<f64> {
    [group.up, group.down]
        .into_iter()
        .flatten()
        .find_map(|row| row.threshold_price.as_deref().and_then(parse_positive))
}

fn compact_path(history: &[&crate::state::PriceHistoryPoint]) -> String {
    let samples = history
        .iter()
        .rev()
        .take(4)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .map(|point| format_usd_number(point.price))
        .collect::<Vec<_>>();
    if samples.is_empty() {
        "-".to_string()
    } else {
        samples.join(" -> ")
    }
}

fn parse_positive(value: &str) -> Option<f64> {
    let number = value.trim().parse::<f64>().ok()?;
    (number > 0.0 && number.is_finite()).then_some(number)
}

fn parse_runtime_timestamp(timestamp: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(timestamp)
        .ok()
        .map(|timestamp| timestamp.with_timezone(&Utc))
}

fn format_usd_number(value: f64) -> String {
    add_thousands_separators(&format!("{value:.2}"))
}

fn add_thousands_separators(value: &str) -> String {
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    let (sign, digits) = whole
        .strip_prefix('-')
        .map_or(("", whole), |rest| ("-", rest));
    let mut grouped = String::new();

    for (index, character) in digits.chars().rev().enumerate() {
        if index > 0 && index % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(character);
    }

    let whole = grouped.chars().rev().collect::<String>();
    if fraction.is_empty() {
        format!("{sign}{whole}")
    } else {
        format!("{sign}{whole}.{fraction}")
    }
}

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{RuntimeMonitor, RuntimeOrderbookRow, RuntimePriceRow},
    };

    use super::price_path_rows;

    #[test]
    fn price_path_rows_include_latest_price_and_current_target() {
        let mut app = AppState::default();
        app.apply_runtime_monitor(RuntimeMonitor {
            generated_at: "2026-06-04T07:43:10Z".to_string(),
            price_rows: vec![price_row("BTC/USD", "64050")],
            orderbooks: vec![orderbook_with_threshold("BTC", "64000")],
        });

        let rows = price_path_rows(&app);

        assert_eq!(rows[0].asset, "BTC");
        assert_eq!(rows[0].latest, "64,050.00");
        assert_eq!(rows[0].target, "K 64,000.00");
    }

    #[test]
    fn price_path_uses_current_window_target_not_expired_retained_handoff() {
        let mut app = AppState::default();
        app.apply_runtime_monitor(RuntimeMonitor {
            generated_at: "2026-06-04T07:43:10Z".to_string(),
            price_rows: vec![price_row("BTC/USD", "64050")],
            orderbooks: vec![
                orderbook_with_window(
                    "BTC",
                    "btc-updown-5m-expired",
                    "2026-06-04T07:35:00Z",
                    "2026-06-04T07:40:00Z",
                    "63000",
                ),
                orderbook_with_window(
                    "BTC",
                    "btc-updown-5m-current",
                    "2026-06-04T07:40:00Z",
                    "2026-06-04T07:45:00Z",
                    "64000",
                ),
            ],
        });

        let rows = price_path_rows(&app);

        assert_eq!(rows[0].target, "K 64,000.00");
    }

    fn price_row(symbol: &str, price: &str) -> RuntimePriceRow {
        RuntimePriceRow {
            source_key: Some("polymarket_rtds_chainlink".to_string()),
            symbol: symbol.to_string(),
            event_ts: Some("2026-06-04T07:43:09Z".to_string()),
            observed_ts: Some("2026-06-04T07:43:10Z".to_string()),
            price: Some(price.to_string()),
        }
    }

    fn orderbook_with_threshold(asset: &str, threshold_price: &str) -> RuntimeOrderbookRow {
        let market_slug = format!("{}-updown-5m-1780558800", asset.to_ascii_lowercase());
        orderbook_with_window(
            asset,
            &market_slug,
            "2026-06-04T07:40:00Z",
            "2026-06-04T07:45:00Z",
            threshold_price,
        )
    }

    fn orderbook_with_window(
        asset: &str,
        market_slug: &str,
        start_ts: &str,
        expiry_ts: &str,
        threshold_price: &str,
    ) -> RuntimeOrderbookRow {
        RuntimeOrderbookRow {
            venue: Some("polymarket".to_string()),
            source_key: Some("polymarket_rust_sdk".to_string()),
            market_slug: Some(market_slug.to_string()),
            contract_id: format!("{market_slug}-UP"),
            token_id: Some(format!("{market_slug}-UP-token")),
            asset: Some(asset.to_string()),
            side: Some("UP".to_string()),
            event_ts: None,
            observed_ts: Some("2026-06-04T07:43:10Z".to_string()),
            start_ts: Some(start_ts.to_string()),
            expiry_ts: Some(expiry_ts.to_string()),
            threshold_price: Some(threshold_price.to_string()),
            threshold_event_ts: Some("2026-06-04T07:40:00Z".to_string()),
            threshold_observed_ts: Some("2026-06-04T07:40:00.005Z".to_string()),
            settlement_price: Some("64050".to_string()),
            settlement_event_ts: Some("2026-06-04T07:43:09Z".to_string()),
            best_bid: None,
            best_ask: None,
            spread: None,
            bid_size_top: None,
            ask_size_top: None,
            bids: Vec::new(),
            asks: Vec::new(),
        }
    }
}
