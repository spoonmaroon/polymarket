use chrono::{DateTime, Local, TimeZone, Utc};
use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{render::orderbook, state::AppState, status::RuntimeOrderbookRow};

const MARKET_VISIBLE_ROWS: usize = 10;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MarketDisplayRow {
    pub marker: String,
    pub contract: String,
    pub side: String,
    pub bid: String,
    pub ask: String,
    pub spread: String,
    pub seen: String,
}

pub fn market_header_labels() -> [&'static str; 7] {
    ["", "Contract", "Side", "Bid", "Ask", "Spread", "Seen"]
}

pub fn market_rows(app: &AppState) -> Vec<MarketDisplayRow> {
    let selected_index = app.effective_market_index();

    app.runtime_monitor
        .as_ref()
        .map(|monitor| {
            let display_rows = market_display_rows(&monitor.orderbooks, selected_index);
            let selected_display_index = display_rows.iter().position(|row| row.marker == ">");
            let start = visible_market_start(display_rows.len(), selected_display_index);
            display_rows
                .into_iter()
                .skip(start)
                .take(MARKET_VISIBLE_ROWS)
                .collect()
        })
        .unwrap_or_default()
}

fn market_display_rows(
    orderbooks: &[RuntimeOrderbookRow],
    selected_index: Option<usize>,
) -> Vec<MarketDisplayRow> {
    let mut rows = Vec::new();
    let mut last_asset: Option<String> = None;
    let mut indexed_orderbooks = orderbooks.iter().enumerate().collect::<Vec<_>>();
    indexed_orderbooks.sort_by(|(left_index, left), (right_index, right)| {
        market_sort_key(left)
            .cmp(&market_sort_key(right))
            .then_with(|| left_index.cmp(right_index))
    });
    for (index, orderbook) in indexed_orderbooks {
        let asset = orderbook
            .asset
            .as_deref()
            .filter(|asset| !asset.is_empty())
            .map(str::to_ascii_uppercase)
            .unwrap_or_else(|| "OTHER".to_string());
        if last_asset.as_deref() != Some(asset.as_str()) {
            rows.push(MarketDisplayRow {
                marker: " ".to_string(),
                contract: asset.clone(),
                side: String::new(),
                bid: String::new(),
                ask: String::new(),
                spread: String::new(),
                seen: String::new(),
            });
            last_asset = Some(asset);
        }
        rows.push(MarketDisplayRow {
            marker: if selected_index == Some(index) {
                ">"
            } else {
                " "
            }
            .to_string(),
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
            spread: positive_as_dash(orderbook.spread.as_deref()),
            seen: compact_timestamp(orderbook.observed_ts.as_deref()),
        });
    }
    rows
}

fn market_sort_key(orderbook: &RuntimeOrderbookRow) -> (u8, i64, u8) {
    let asset_order = match orderbook.asset.as_deref().map(str::to_ascii_uppercase) {
        Some(asset) if asset == "BTC" => 0,
        Some(asset) if asset == "ETH" => 1,
        Some(_) => 2,
        None => 3,
    };
    let expiry = orderbook
        .market_slug
        .as_deref()
        .and_then(market_slug_epoch)
        .unwrap_or(i64::MAX);
    let side_order = match orderbook.side.as_deref().map(str::to_ascii_uppercase) {
        Some(side) if side == "UP" => 0,
        Some(side) if side == "DOWN" => 1,
        Some(_) => 2,
        None => 3,
    };
    (asset_order, expiry, side_order)
}

fn market_slug_epoch(market_slug: &str) -> Option<i64> {
    let parts = market_slug.split('-').collect::<Vec<_>>();
    if parts.len() >= 4 && parts[1] == "updown" {
        parts[3].parse::<i64>().ok()
    } else {
        None
    }
}

fn visible_market_start(count: usize, selected_index: Option<usize>) -> usize {
    if count <= MARKET_VISIBLE_ROWS {
        return 0;
    }

    let selected_index = selected_index.unwrap_or_default().min(count - 1);
    if selected_index < MARKET_VISIBLE_ROWS {
        0
    } else {
        selected_index + 1 - MARKET_VISIBLE_ROWS
    }
}

pub(crate) fn book_contract_label(orderbook: &RuntimeOrderbookRow) -> String {
    let label = match (orderbook.asset.as_deref(), orderbook.side.as_deref()) {
        (Some(asset), Some(side)) if !asset.is_empty() && !side.is_empty() => {
            format!("{asset} {side}")
        }
        _ if !orderbook.contract_id.is_empty() => orderbook.contract_id.clone(),
        _ => "unknown".to_string(),
    };

    match market_slug_expiry_label(orderbook) {
        Some(expiry) => format!("{label} {expiry}"),
        None => label,
    }
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

fn market_slug_expiry_label(orderbook: &RuntimeOrderbookRow) -> Option<String> {
    let market_slug = orderbook
        .market_slug
        .as_deref()
        .filter(|slug| !slug.is_empty())?;
    let parts = market_slug.split('-').collect::<Vec<_>>();
    if parts.len() >= 4 && parts[1] == "updown" {
        Some(expiry_label(parts[3]))
    } else {
        None
    }
}

fn expiry_label(raw: &str) -> String {
    let Ok(epoch_seconds) = raw.parse::<i64>() else {
        return raw.to_string();
    };
    let Some(timestamp) = Utc.timestamp_opt(epoch_seconds, 0).single() else {
        return raw.to_string();
    };

    timestamp
        .with_timezone(&Local)
        .format("%H:%M %Z")
        .to_string()
}

fn price_size(price: Option<&str>, size: Option<&str>) -> String {
    let Some(price) = positive_scalar(price) else {
        return "-".to_string();
    };
    if let Some(size) = positive_scalar(size) {
        format!("{price} x{size}")
    } else {
        price
    }
}

fn positive_as_dash(value: Option<&str>) -> String {
    positive_scalar(value).unwrap_or_else(|| "-".to_string())
}

fn positive_scalar(value: Option<&str>) -> Option<String> {
    let value = value?.trim();
    if value.is_empty() {
        return None;
    }
    let Ok(number) = value.parse::<f64>() else {
        return Some(value.to_string());
    };
    if number <= 0.0 {
        None
    } else {
        Some(value.to_string())
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
    if let Ok(parsed) = DateTime::parse_from_rfc3339(timestamp) {
        return parsed
            .with_timezone(&Local)
            .format("%H:%M:%S %Z")
            .to_string();
    }

    let Some((_date, time)) = timestamp.split_once('T') else {
        return timestamp.to_string();
    };

    let time = time
        .split(['.', '+'])
        .next()
        .unwrap_or(time)
        .trim_end_matches('Z');
    time.to_string()
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let [counts_area, orderbook_area] = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(13), Constraint::Min(3)])
        .areas(area);
    let rows = market_rows(app)
        .into_iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(row.marker),
                Cell::from(row.contract),
                Cell::from(row.side),
                Cell::from(row.bid),
                Cell::from(row.ask),
                Cell::from(row.spread),
                Cell::from(row.seen),
            ])
        })
        .collect::<Vec<_>>();
    let rows = if rows.is_empty() {
        vec![Row::new(vec![
            Cell::from(" "),
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
            Constraint::Length(2),
            Constraint::Length(22),
            Constraint::Length(6),
            Constraint::Length(14),
            Constraint::Length(14),
            Constraint::Length(7),
            Constraint::Min(9),
        ],
    )
    .header(Row::new(market_header_labels().to_vec()).style(Style::default().fg(Color::Cyan)))
    .block(Block::bordered().title("Market"));

    frame.render_widget(table, counts_area);
    orderbook::render(frame, orderbook_area, app);
}

#[cfg(test)]
mod tests {
    use chrono::{DateTime, Local, TimeZone, Utc};

    use crate::{
        state::AppState,
        status::{RuntimeBookLevel, RuntimeMonitor, RuntimeOrderbookRow},
    };

    use super::{market_header_labels, market_rows};

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

        assert_eq!(rows[0].contract, "ETH");
        assert_eq!(
            rows[1].contract,
            format!("ETH 5m {}", local_epoch_label(1_780_519_200))
        );
        assert_eq!(rows[1].side, "DOWN");
        assert_eq!(rows[1].bid, "0.86 x33");
        assert_eq!(rows[1].ask, "0.87 x14.46");
        assert_eq!(rows[1].spread, "0.01");
        assert_eq!(
            rows[1].seen,
            local_timestamp_label("2026-06-03T20:43:20.616043736Z")
        );
        assert!(!rows[1].contract.ends_with('Z'));
        assert!(!rows[1].seen.ends_with('Z'));
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

        assert_eq!(rows[0].contract, "OTHER");
        assert_eq!(rows[1].contract, "btc-5m-up");
        assert_eq!(rows[1].side, "-");
        assert_eq!(rows[1].bid, "0.44");
        assert_eq!(rows[1].ask, "-");
        assert_eq!(rows[1].spread, "-");
    }

    #[test]
    fn market_rows_hide_nonpositive_top_of_book_values() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: Some("polymarket_rust_sdk".to_string()),
                    market_slug: Some("btc-updown-5m-1780519500".to_string()),
                    contract_id: "btc-up".to_string(),
                    token_id: Some("btc-up-token".to_string()),
                    asset: Some("BTC".to_string()),
                    side: Some("UP".to_string()),
                    event_ts: None,
                    observed_ts: Some("2026-06-03T21:05:58Z".to_string()),
                    best_bid: Some("0".to_string()),
                    best_ask: Some("-0.01".to_string()),
                    spread: Some("0".to_string()),
                    bid_size_top: Some("100".to_string()),
                    ask_size_top: Some("200".to_string()),
                    bids: Vec::new(),
                    asks: Vec::new(),
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert!(rows[1].contract.starts_with("BTC 5m "));
        assert_eq!(rows[1].bid, "-");
        assert_eq!(rows[1].ask, "-");
        assert_eq!(rows[1].spread, "-");
    }

    #[test]
    fn market_rows_label_seen_and_mark_selected_contract() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("eth-updown-5m-1780519500".to_string()),
                        contract_id: "eth-up".to_string(),
                        token_id: Some("eth-up-token".to_string()),
                        asset: Some("ETH".to_string()),
                        side: Some("UP".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:58Z".to_string()),
                        best_bid: Some("0.88".to_string()),
                        best_ask: Some("0.89".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: Some("102".to_string()),
                        ask_size_top: Some("26".to_string()),
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("btc-updown-5m-1780519800".to_string()),
                        contract_id: "btc-down".to_string(),
                        token_id: Some("btc-down-token".to_string()),
                        asset: Some("BTC".to_string()),
                        side: Some("DOWN".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:47Z".to_string()),
                        best_bid: Some("0.49".to_string()),
                        best_ask: Some("0.50".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: Some("1256.68".to_string()),
                        ask_size_top: Some("702.96".to_string()),
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                ],
            }),
            ..Default::default()
        };
        app.sync_market_selection();

        let rows = market_rows(&app);

        assert_eq!(
            market_header_labels(),
            ["", "Contract", "Side", "Bid", "Ask", "Spread", "Seen"]
        );
        assert_eq!(rows[0].contract, "BTC");
        assert_eq!(rows[1].marker, ">");
        assert_eq!(rows[1].seen, local_timestamp_label("2026-06-03T21:05:47Z"));
        assert_eq!(rows[2].contract, "ETH");
        assert_eq!(rows[3].marker, " ");
    }

    #[test]
    fn market_rows_keep_selected_contract_inside_visible_window() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: (0..8)
                    .map(|index| RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some(format!("btc-updown-5m-1780521{index:03}")),
                        contract_id: format!("btc-row-{index}"),
                        token_id: Some(format!("btc-row-{index}-token")),
                        asset: Some("BTC".to_string()),
                        side: Some(if index % 2 == 0 { "UP" } else { "DOWN" }.to_string()),
                        event_ts: None,
                        observed_ts: Some(format!("2026-06-03T21:05:5{index}Z")),
                        best_bid: Some(format!("0.{index}1")),
                        best_ask: Some(format!("0.{index}2")),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    })
                    .collect(),
            }),
            ..Default::default()
        };
        app.sync_market_selection();
        app.select_previous_market();

        let rows = market_rows(&app);

        assert_eq!(app.selected_market_index(), Some(7));
        assert!(rows.len() <= 10);
        assert_eq!(rows.last().map(|row| row.marker.as_str()), Some(">"));
        assert_eq!(rows.last().map(|row| row.bid.as_str()), Some("0.71"));
    }

    fn local_epoch_label(epoch_seconds: i64) -> String {
        Utc.timestamp_opt(epoch_seconds, 0)
            .single()
            .unwrap()
            .with_timezone(&Local)
            .format("%H:%M %Z")
            .to_string()
    }

    fn local_timestamp_label(timestamp: &str) -> String {
        DateTime::parse_from_rfc3339(timestamp)
            .unwrap()
            .with_timezone(&Local)
            .format("%H:%M:%S %Z")
            .to_string()
    }
}
