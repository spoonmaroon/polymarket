use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{render::orderbook, state::AppState, status::RuntimeOrderbookRow};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MarketDisplayRow {
    pub contract: String,
    pub side: String,
    pub bid: String,
    pub ask: String,
    pub spread: String,
    pub observed: String,
}

pub fn market_rows(app: &AppState) -> Vec<MarketDisplayRow> {
    app.runtime_monitor
        .as_ref()
        .map(|monitor| {
            monitor
                .orderbooks
                .iter()
                .take(8)
                .map(|orderbook| MarketDisplayRow {
                    contract: contract_label(orderbook),
                    side: optional_as_dash(orderbook.side.as_deref()),
                    bid: price_size(
                        orderbook.best_bid.as_deref(),
                        orderbook.bid_size_top.as_deref(),
                    ),
                    ask: price_size(
                        orderbook.best_ask.as_deref(),
                        orderbook.ask_size_top.as_deref(),
                    ),
                    spread: optional_as_dash(orderbook.spread.as_deref()),
                    observed: compact_timestamp(orderbook.observed_ts.as_deref()),
                })
                .collect()
        })
        .unwrap_or_default()
}

fn contract_label(orderbook: &RuntimeOrderbookRow) -> String {
    if let Some(market_slug) = orderbook
        .market_slug
        .as_deref()
        .filter(|slug| !slug.is_empty())
    {
        let parts = market_slug.split('-').collect::<Vec<_>>();
        if parts.len() >= 4 && parts[1] == "updown" {
            let asset = orderbook
                .asset
                .as_deref()
                .filter(|asset| !asset.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| parts[0].to_ascii_uppercase());
            return format!("{asset} {} {}", parts[2], expiry_label(parts[3]));
        }

        return market_slug.to_string();
    }

    if !orderbook.contract_id.is_empty() {
        orderbook.contract_id.clone()
    } else {
        orderbook
            .token_id
            .clone()
            .unwrap_or_else(|| "unknown contract".to_string())
    }
}

fn expiry_label(raw: &str) -> String {
    let Ok(epoch_seconds) = raw.parse::<u64>() else {
        return raw.to_string();
    };
    let seconds_in_day = epoch_seconds % 86_400;
    let hour = seconds_in_day / 3_600;
    let minute = (seconds_in_day % 3_600) / 60;
    format!("{hour:02}:{minute:02}Z")
}

fn price_size(price: Option<&str>, size: Option<&str>) -> String {
    let price = optional_as_dash(price);
    if let Some(size) = size.filter(|value| !value.is_empty()) {
        format!("{price} x{size}")
    } else {
        price
    }
}

fn optional_as_dash(value: Option<&str>) -> String {
    value
        .filter(|value| !value.is_empty())
        .unwrap_or("-")
        .to_string()
}

fn compact_timestamp(timestamp: Option<&str>) -> String {
    let Some(timestamp) = timestamp.filter(|value| !value.is_empty()) else {
        return "-".to_string();
    };
    let Some((_date, time)) = timestamp.split_once('T') else {
        return timestamp.to_string();
    };

    let time = time
        .split(['.', '+'])
        .next()
        .unwrap_or(time)
        .trim_end_matches('Z');
    format!("{time}Z")
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let [counts_area, orderbook_area] = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(8), Constraint::Min(3)])
        .areas(area);
    let rows = market_rows(app)
        .into_iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(row.contract),
                Cell::from(row.side),
                Cell::from(row.bid),
                Cell::from(row.ask),
                Cell::from(row.spread),
                Cell::from(row.observed),
            ])
        })
        .collect::<Vec<_>>();
    let rows = if rows.is_empty() {
        vec![Row::new(vec![
            Cell::from("monitor pending"),
            Cell::from("-"),
            Cell::from("-"),
            Cell::from("-"),
            Cell::from("-"),
            Cell::from("-"),
        ])]
    } else {
        rows
    };
    let table = Table::new(
        rows,
        [
            Constraint::Length(22),
            Constraint::Length(6),
            Constraint::Length(14),
            Constraint::Length(14),
            Constraint::Length(7),
            Constraint::Min(9),
        ],
    )
    .header(
        Row::new(vec!["Contract", "Side", "Bid", "Ask", "Spread", "Obs"])
            .style(Style::default().fg(Color::Cyan)),
    )
    .block(Block::bordered().title("Market"));

    frame.render_widget(table, counts_area);
    orderbook::render(frame, orderbook_area, app);
}

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{RuntimeBookLevel, RuntimeMonitor, RuntimeOrderbookRow},
    };

    use super::market_rows;

    #[test]
    fn market_rows_show_contract_identity_and_top_of_book() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T20:43:20.744215+00:00".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: Some("polymarket_rust_sdk".to_string()),
                    market_slug: Some("eth-updown-5m-1780519200".to_string()),
                    contract_id: "0x0abe644dd79156eeeb5e4e3be9f8f78953d9907316c57e014c3598f2ae99e3cc".to_string(),
                    token_id: Some("100783333159874947931352697222477663764026407100859257224541015812712077669400".to_string()),
                    asset: Some("ETH".to_string()),
                    side: Some("DOWN".to_string()),
                    event_ts: Some("2026-06-03T20:43:12.101Z".to_string()),
                    observed_ts: Some("2026-06-03T20:43:20.616043736Z".to_string()),
                    best_bid: Some("0.86".to_string()),
                    best_ask: Some("0.87".to_string()),
                    spread: Some("0.01".to_string()),
                    bid_size_top: Some("33".to_string()),
                    ask_size_top: Some("14.46".to_string()),
                    bids: vec![RuntimeBookLevel {
                        price: Some("0.86".to_string()),
                        size: Some("33".to_string()),
                    }],
                    asks: vec![RuntimeBookLevel {
                        price: Some("0.87".to_string()),
                        size: Some("14.46".to_string()),
                    }],
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert_eq!(rows[0].contract, "ETH 5m 20:40Z");
        assert_eq!(rows[0].side, "DOWN");
        assert_eq!(rows[0].bid, "0.86 x33");
        assert_eq!(rows[0].ask, "0.87 x14.46");
        assert_eq!(rows[0].spread, "0.01");
        assert_eq!(rows[0].observed, "20:43:20Z");
    }

    #[test]
    fn market_rows_fall_back_to_contract_id_when_metadata_is_missing() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T20:43:20.744215+00:00".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: None,
                    market_slug: None,
                    contract_id: "btc-5m-up".to_string(),
                    token_id: Some("token-1".to_string()),
                    asset: None,
                    side: None,
                    event_ts: None,
                    observed_ts: Some("2026-06-03T20:43:20.616043736Z".to_string()),
                    best_bid: Some("0.44".to_string()),
                    best_ask: None,
                    spread: None,
                    bid_size_top: None,
                    ask_size_top: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert_eq!(rows[0].contract, "btc-5m-up");
        assert_eq!(rows[0].side, "-");
        assert_eq!(rows[0].bid, "0.44");
        assert_eq!(rows[0].ask, "-");
        assert_eq!(rows[0].spread, "-");
    }
}
