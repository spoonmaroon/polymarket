use chrono::{DateTime, Local, Utc};
use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{market_view, render::orderbook, state::AppState, status::RuntimeOrderbookRow};

#[cfg(test)]
const MARKET_VISIBLE_ROWS: usize = 10;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MarketDisplayRow {
    pub marker: String,
    pub market: String,
    pub up: String,
    pub down: String,
    pub spread: String,
    pub ttl: String,
    pub seen: String,
}

pub fn market_header_labels() -> [&'static str; 7] {
    [
        "",
        "Market",
        "UP bid/ask",
        "DOWN bid/ask",
        "Spread",
        "TTL",
        "Seen",
    ]
}

#[cfg(test)]
pub fn market_rows(app: &AppState) -> Vec<MarketDisplayRow> {
    market_rows_for_visible_count(app, MARKET_VISIBLE_ROWS)
}

pub fn market_rows_for_visible_count(app: &AppState, visible_rows: usize) -> Vec<MarketDisplayRow> {
    let selected_index = app.effective_market_index();
    let visible_rows = visible_rows.max(1);

    app.runtime_monitor
        .as_ref()
        .map(|monitor| {
            let display_rows =
                market_display_rows(&monitor.orderbooks, selected_index, &monitor.generated_at);
            let selected_display_index = display_rows.iter().position(|row| row.marker == ">");
            let start =
                visible_market_start(display_rows.len(), selected_display_index, visible_rows);
            display_rows
                .into_iter()
                .skip(start)
                .take(visible_rows)
                .collect()
        })
        .unwrap_or_default()
}

fn market_display_rows(
    orderbooks: &[RuntimeOrderbookRow],
    selected_index: Option<usize>,
    generated_at: &str,
) -> Vec<MarketDisplayRow> {
    let mut rows = Vec::new();
    let mut last_asset: Option<String> = None;
    for (index, group) in market_view::market_groups(orderbooks).iter().enumerate() {
        let asset = group.asset.clone();
        if last_asset.as_deref() != Some(asset.as_str()) {
            if last_asset.is_some() {
                rows.push(MarketDisplayRow {
                    marker: " ".to_string(),
                    market: String::new(),
                    up: String::new(),
                    down: String::new(),
                    spread: String::new(),
                    ttl: String::new(),
                    seen: String::new(),
                });
            }
            rows.push(MarketDisplayRow {
                marker: " ".to_string(),
                market: asset.clone(),
                up: String::new(),
                down: String::new(),
                spread: String::new(),
                ttl: String::new(),
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
            market: group.label.clone(),
            up: top_quote(group.up),
            down: top_quote(group.down),
            spread: tight_spread(group.up, group.down),
            ttl: countdown_to_expiry(group.expiry_ts, generated_at),
            seen: compact_timestamp(freshest_seen(group.up, group.down).as_deref()),
        });
    }
    rows
}

fn visible_market_start(count: usize, selected_index: Option<usize>, visible_rows: usize) -> usize {
    if count <= visible_rows {
        return 0;
    }

    let selected_index = selected_index.unwrap_or_default().min(count - 1);
    if selected_index < visible_rows {
        0
    } else {
        selected_index + 1 - visible_rows
    }
}

fn top_quote(orderbook: Option<&RuntimeOrderbookRow>) -> String {
    let Some(orderbook) = orderbook else {
        return "-".to_string();
    };
    match (
        positive_scalar(orderbook.best_bid.as_deref()),
        positive_scalar(orderbook.best_ask.as_deref()),
    ) {
        (Some(bid), Some(ask)) => format!("{bid}/{ask}"),
        (Some(bid), None) => format!("{bid}/-"),
        (None, Some(ask)) => format!("-/{ask}"),
        (None, None) => "-".to_string(),
    }
}

fn tight_spread(up: Option<&RuntimeOrderbookRow>, down: Option<&RuntimeOrderbookRow>) -> String {
    let mut spreads = [up, down]
        .into_iter()
        .flatten()
        .filter_map(|orderbook| {
            positive_number(orderbook.spread.as_deref()).or_else(|| {
                let bid = positive_number(orderbook.best_bid.as_deref())?;
                let ask = positive_number(orderbook.best_ask.as_deref())?;
                (ask - bid).is_sign_positive().then_some(ask - bid)
            })
        })
        .collect::<Vec<_>>();
    spreads.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
    if let Some(spread) = spreads.first() {
        format!("{spread:.4}")
    } else {
        "-".to_string()
    }
}

fn freshest_seen(
    up: Option<&RuntimeOrderbookRow>,
    down: Option<&RuntimeOrderbookRow>,
) -> Option<String> {
    [up, down]
        .into_iter()
        .flatten()
        .filter_map(|orderbook| orderbook.observed_ts.as_deref())
        .filter(|timestamp| !timestamp.is_empty())
        .max()
        .map(str::to_string)
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

fn positive_number(value: Option<&str>) -> Option<f64> {
    let number = value?.trim().parse::<f64>().ok()?;
    if number > 0.0 { Some(number) } else { None }
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

fn countdown_to_expiry(expiry_ts: Option<DateTime<Utc>>, generated_at: &str) -> String {
    let Some(expiry_ts) = expiry_ts else {
        return "-".to_string();
    };
    let Ok(generated_at) = DateTime::parse_from_rfc3339(generated_at) else {
        return "-".to_string();
    };
    let remaining = expiry_ts
        .signed_duration_since(generated_at.with_timezone(&Utc))
        .num_seconds();
    if remaining <= 0 {
        return "expired".to_string();
    }

    let hours = remaining / 3600;
    let minutes = (remaining % 3600) / 60;
    let seconds = remaining % 60;
    if hours > 0 {
        format!("{hours:02}:{minutes:02}:{seconds:02}")
    } else {
        format!("{minutes:02}:{seconds:02}")
    }
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let [counts_area, orderbook_area] = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(13), Constraint::Min(3)])
        .areas(area);
    let visible_rows = counts_area.height.saturating_sub(3).max(1) as usize;
    let rows = market_rows_for_visible_count(app, visible_rows)
        .into_iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(row.marker),
                Cell::from(row.market),
                Cell::from(row.up),
                Cell::from(row.down),
                Cell::from(row.spread),
                Cell::from(row.ttl),
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
            Constraint::Length(24),
            Constraint::Length(13),
            Constraint::Length(13),
            Constraint::Length(7),
            Constraint::Length(8),
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

    use super::{market_header_labels, market_rows, market_rows_for_visible_count};

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

        assert_eq!(rows[0].market, "ETH");
        assert_eq!(
            rows[1].market,
            format!("ETH 5m {}", local_epoch_label(1_780_519_200))
        );
        assert_eq!(rows[1].up, "-");
        assert_eq!(rows[1].down, "0.86/0.87");
        assert_eq!(rows[1].spread, "0.0100");
        assert_eq!(
            rows[1].seen,
            local_timestamp_label("2026-06-03T20:43:20.616043736Z")
        );
        assert!(!rows[1].market.ends_with('Z'));
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

        assert_eq!(rows[0].market, "OTHER");
        assert_eq!(rows[1].market, "btc-5m-up");
        assert_eq!(rows[1].up, "0.44/-");
        assert_eq!(rows[1].down, "-");
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

        assert!(rows[1].market.starts_with("BTC 5m "));
        assert_eq!(rows[1].up, "-");
        assert_eq!(rows[1].down, "-");
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
            [
                "",
                "Market",
                "UP bid/ask",
                "DOWN bid/ask",
                "Spread",
                "TTL",
                "Seen"
            ]
        );
        assert_eq!(rows[0].market, "BTC");
        assert_eq!(rows[1].marker, ">");
        assert_eq!(rows[1].seen, local_timestamp_label("2026-06-03T21:05:47Z"));
        assert_eq!(rows[2].market, "");
        assert_eq!(rows[3].market, "ETH");
        assert_eq!(rows[4].marker, " ");
    }

    #[test]
    fn market_rows_show_countdown_to_expiration_from_monitor_time() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:23:20Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: Some("polymarket_rust_sdk".to_string()),
                    market_slug: Some("btc-updown-5m-1780521900".to_string()),
                    contract_id: "btc-up".to_string(),
                    token_id: Some("btc-up-token".to_string()),
                    asset: Some("BTC".to_string()),
                    side: Some("UP".to_string()),
                    event_ts: None,
                    observed_ts: Some("2026-06-03T21:23:18Z".to_string()),
                    best_bid: Some("0.45".to_string()),
                    best_ask: Some("0.46".to_string()),
                    spread: Some("0.01".to_string()),
                    bid_size_top: None,
                    ask_size_top: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert_eq!(rows[1].ttl, "01:40");
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
        assert_eq!(rows.last().map(|row| row.down.as_str()), Some("0.71/0.72"));
    }

    #[test]
    fn market_rows_keep_selected_contract_visible_in_short_panels() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: (0..12)
                    .map(|index| RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some(format!("btc-updown-5m-1780522{index:03}")),
                        contract_id: format!("btc-row-{index}"),
                        token_id: Some(format!("btc-row-{index}-token")),
                        asset: Some("BTC".to_string()),
                        side: Some(if index % 2 == 0 { "UP" } else { "DOWN" }.to_string()),
                        event_ts: None,
                        observed_ts: Some(format!("2026-06-03T21:05:{index:02}Z")),
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

        let rows = market_rows_for_visible_count(&app, 4);

        assert_eq!(app.selected_market_index(), Some(11));
        assert_eq!(rows.len(), 4);
        assert_eq!(rows.last().map(|row| row.marker.as_str()), Some(">"));
        assert_eq!(
            rows.last().map(|row| row.down.as_str()),
            Some("0.111/0.112")
        );
    }

    #[test]
    fn market_rows_keep_selected_contract_visible_with_asset_spacers() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("btc-updown-5m-1780521900".to_string()),
                        contract_id: "btc-early".to_string(),
                        token_id: Some("btc-early-token".to_string()),
                        asset: Some("BTC".to_string()),
                        side: Some("UP".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:55Z".to_string()),
                        best_bid: Some("0.41".to_string()),
                        best_ask: Some("0.42".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("btc-updown-5m-1780522200".to_string()),
                        contract_id: "btc-late".to_string(),
                        token_id: Some("btc-late-token".to_string()),
                        asset: Some("BTC".to_string()),
                        side: Some("UP".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:56Z".to_string()),
                        best_bid: Some("0.51".to_string()),
                        best_ask: Some("0.52".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("eth-updown-5m-1780521900".to_string()),
                        contract_id: "eth-early".to_string(),
                        token_id: Some("eth-early-token".to_string()),
                        asset: Some("ETH".to_string()),
                        side: Some("UP".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:57Z".to_string()),
                        best_bid: Some("0.61".to_string()),
                        best_ask: Some("0.62".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("eth-updown-5m-1780522200".to_string()),
                        contract_id: "eth-late".to_string(),
                        token_id: Some("eth-late-token".to_string()),
                        asset: Some("ETH".to_string()),
                        side: Some("UP".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:58Z".to_string()),
                        best_bid: Some("0.71".to_string()),
                        best_ask: Some("0.72".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                ],
            }),
            ..Default::default()
        };
        app.sync_market_selection();
        app.select_previous_market();

        let rows = market_rows_for_visible_count(&app, 3);

        assert_eq!(app.selected_market_index(), Some(3));
        assert_eq!(rows.len(), 3);
        assert_eq!(rows.last().map(|row| row.marker.as_str()), Some(">"));
        assert_eq!(rows.last().map(|row| row.up.as_str()), Some("0.71/0.72"));
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
