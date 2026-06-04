use std::cmp::Ordering;

use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{
    state::AppState,
    status::{RuntimeBookLevel, RuntimeOrderbookRow},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BookDisplayRow {
    pub contract: String,
    pub bid: String,
    pub ask: String,
}

pub fn book_title(app: &AppState) -> String {
    app.selected_market_group()
        .map(|group| format!("Book: {}", group.label))
        .unwrap_or_else(|| "Book".to_string())
}

pub fn book_rows(app: &AppState) -> Vec<BookDisplayRow> {
    let Some(group) = app.selected_market_group() else {
        return Vec::new();
    };

    let mut rows = Vec::new();
    if let Some(up) = group.up {
        rows.extend(side_book_rows("UP", up));
    }
    if let Some(down) = group.down {
        rows.extend(side_book_rows("DOWN", down));
    }
    rows
}

fn side_book_rows(side: &str, orderbook: &RuntimeOrderbookRow) -> Vec<BookDisplayRow> {
    let mut bids = orderbook.bids.iter().collect::<Vec<_>>();
    bids.retain(|level| level_price(level).is_some() && level_size(level).is_some());
    bids.sort_by(|left, right| compare_level_price_desc(left, right));
    let bids = bids.into_iter().take(6).collect::<Vec<_>>();
    let mut asks = orderbook.asks.iter().collect::<Vec<_>>();
    asks.retain(|level| level_price(level).is_some() && level_size(level).is_some());
    asks.sort_by(|left, right| compare_level_price_asc(left, right));
    let asks = asks.into_iter().take(6).collect::<Vec<_>>();
    let row_count = bids.len().max(asks.len());

    (0..row_count)
        .map(|index| BookDisplayRow {
            contract: side.to_string(),
            bid: bids.get(index).map_or("-".to_string(), |level| {
                price_size(level.price.as_deref(), level.size.as_deref())
            }),
            ask: asks.get(index).map_or("-".to_string(), |level| {
                price_size(level.price.as_deref(), level.size.as_deref())
            }),
        })
        .collect()
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

fn compare_level_price_asc(left: &RuntimeBookLevel, right: &RuntimeBookLevel) -> Ordering {
    match (level_price(left), level_price(right)) {
        (Some(left), Some(right)) => left.partial_cmp(&right).unwrap_or(Ordering::Equal),
        (Some(_), None) => Ordering::Less,
        (None, Some(_)) => Ordering::Greater,
        (None, None) => Ordering::Equal,
    }
}

fn compare_level_price_desc(left: &RuntimeBookLevel, right: &RuntimeBookLevel) -> Ordering {
    match (level_price(left), level_price(right)) {
        (Some(left), Some(right)) => right.partial_cmp(&left).unwrap_or(Ordering::Equal),
        (Some(_), None) => Ordering::Less,
        (None, Some(_)) => Ordering::Greater,
        (None, None) => Ordering::Equal,
    }
}

fn level_price(level: &RuntimeBookLevel) -> Option<f64> {
    positive_number(level.price.as_deref())
}

fn level_size(level: &RuntimeBookLevel) -> Option<f64> {
    positive_number(level.size.as_deref())
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

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let rows = book_rows(app)
        .into_iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(row.contract),
                Cell::from(row.bid),
                Cell::from(row.ask),
            ])
        })
        .collect::<Vec<_>>();
    let rows = if rows.is_empty() {
        vec![Row::new(vec![
            Cell::from("monitor pending"),
            Cell::from("-"),
            Cell::from("-"),
        ])]
    } else {
        rows
    };
    let table = Table::new(
        rows,
        [
            ratatui::layout::Constraint::Length(12),
            ratatui::layout::Constraint::Length(16),
            ratatui::layout::Constraint::Min(16),
        ],
    )
    .header(Row::new(vec!["Contract", "Bid", "Ask"]).style(Style::default().fg(Color::Cyan)))
    .block(Block::bordered().title(book_title(app)));

    frame.render_widget(table, area);
}

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{RuntimeBookLevel, RuntimeMonitor, RuntimeOrderbookRow},
    };

    use super::{book_rows, book_title};

    #[test]
    fn book_rows_pair_best_bid_and_ask_levels_for_selected_market_side() {
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
                    bids: vec![
                        RuntimeBookLevel {
                            price: Some("0.84".to_string()),
                            size: Some("10".to_string()),
                        },
                        RuntimeBookLevel {
                            price: Some("0.86".to_string()),
                            size: Some("33".to_string()),
                        },
                    ],
                    asks: vec![
                        RuntimeBookLevel {
                            price: Some("0.87".to_string()),
                            size: Some("14.46".to_string()),
                        },
                        RuntimeBookLevel {
                            price: Some("0.88".to_string()),
                            size: Some("20".to_string()),
                        },
                    ],
                }],
            }),
            ..Default::default()
        };

        let rows = book_rows(&app);

        assert_eq!(rows[0].contract, "DOWN");
        assert_eq!(rows[0].bid, "0.86 x33");
        assert_eq!(rows[0].ask, "0.87 x14.46");
        assert_eq!(rows[1].bid, "0.84 x10");
        assert_eq!(rows[1].ask, "0.88 x20");
    }

    #[test]
    fn book_rows_sort_asks_to_show_best_ask_first_even_when_source_is_descending() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:22:15Z".to_string(),
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
                    observed_ts: Some("2026-06-03T21:22:15Z".to_string()),
                    best_bid: Some("0.50".to_string()),
                    best_ask: Some("0.51".to_string()),
                    spread: Some("0.01".to_string()),
                    bid_size_top: Some("639.47".to_string()),
                    ask_size_top: Some("603.36".to_string()),
                    bids: vec![
                        RuntimeBookLevel {
                            price: Some("0.45".to_string()),
                            size: Some("707.36".to_string()),
                        },
                        RuntimeBookLevel {
                            price: Some("0.50".to_string()),
                            size: Some("639.47".to_string()),
                        },
                    ],
                    asks: vec![
                        RuntimeBookLevel {
                            price: Some("0.99".to_string()),
                            size: Some("7690.54".to_string()),
                        },
                        RuntimeBookLevel {
                            price: Some("0.51".to_string()),
                            size: Some("603.36".to_string()),
                        },
                    ],
                }],
            }),
            ..Default::default()
        };

        let rows = book_rows(&app);

        assert_eq!(rows[0].bid, "0.50 x639.47");
        assert_eq!(rows[0].ask, "0.51 x603.36");
        assert_eq!(rows[1].ask, "0.99 x7690.54");
    }

    #[test]
    fn book_rows_hide_nonpositive_levels() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:22:15Z".to_string(),
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
                    observed_ts: Some("2026-06-03T21:22:15Z".to_string()),
                    best_bid: Some("0".to_string()),
                    best_ask: Some("0.51".to_string()),
                    spread: None,
                    bid_size_top: Some("100".to_string()),
                    ask_size_top: Some("603.36".to_string()),
                    bids: vec![
                        RuntimeBookLevel {
                            price: Some("0".to_string()),
                            size: Some("100".to_string()),
                        },
                        RuntimeBookLevel {
                            price: Some("0.49".to_string()),
                            size: Some("0".to_string()),
                        },
                    ],
                    asks: vec![RuntimeBookLevel {
                        price: Some("0.51".to_string()),
                        size: Some("603.36".to_string()),
                    }],
                }],
            }),
            ..Default::default()
        };

        let rows = book_rows(&app);

        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].bid, "-");
        assert_eq!(rows[0].ask, "0.51 x603.36");
    }

    #[test]
    fn book_rows_and_title_follow_selected_market_contract() {
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
                        bids: vec![RuntimeBookLevel {
                            price: Some("0.88".to_string()),
                            size: Some("102".to_string()),
                        }],
                        asks: vec![RuntimeBookLevel {
                            price: Some("0.89".to_string()),
                            size: Some("26".to_string()),
                        }],
                    },
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("btc-updown-5m-1780521000".to_string()),
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
                        bids: vec![RuntimeBookLevel {
                            price: Some("0.49".to_string()),
                            size: Some("1256.68".to_string()),
                        }],
                        asks: vec![RuntimeBookLevel {
                            price: Some("0.50".to_string()),
                            size: Some("702.96".to_string()),
                        }],
                    },
                ],
            }),
            ..Default::default()
        };
        app.sync_market_selection();

        let rows = book_rows(&app);

        assert!(book_title(&app).starts_with("Book: BTC 5m "));
        assert_eq!(rows[0].contract, "DOWN");
        assert_eq!(rows[0].bid, "0.49 x1256.68");
        assert_eq!(rows[0].ask, "0.50 x702.96");
    }

    #[test]
    fn book_rows_render_selected_market_up_and_down_books_together() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:22:15Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    orderbook_with_level("BTC", "UP", "btc-updown-5m-1780521900", "0.44", "0.45"),
                    orderbook_with_level("BTC", "DOWN", "btc-updown-5m-1780521900", "0.55", "0.56"),
                ],
            }),
            ..Default::default()
        };
        app.sync_market_selection();

        let title = book_title(&app);
        let rows = book_rows(&app);

        assert!(title.starts_with("Book: BTC 5m"));
        assert_eq!(rows[0].contract, "UP");
        assert_eq!(rows[1].contract, "DOWN");
    }

    fn orderbook_with_level(
        asset: &str,
        side: &str,
        market_slug: &str,
        bid: &str,
        ask: &str,
    ) -> RuntimeOrderbookRow {
        RuntimeOrderbookRow {
            venue: Some("polymarket".to_string()),
            source_key: Some("polymarket_rust_sdk".to_string()),
            market_slug: Some(market_slug.to_string()),
            contract_id: format!("{asset}-{side}"),
            token_id: Some(format!("{asset}-{side}-token")),
            asset: Some(asset.to_string()),
            side: Some(side.to_string()),
            event_ts: None,
            observed_ts: Some("2026-06-03T21:22:15Z".to_string()),
            best_bid: Some(bid.to_string()),
            best_ask: Some(ask.to_string()),
            spread: Some("0.01".to_string()),
            bid_size_top: None,
            ask_size_top: None,
            bids: vec![RuntimeBookLevel {
                price: Some(bid.to_string()),
                size: Some("100".to_string()),
            }],
            asks: vec![RuntimeBookLevel {
                price: Some(ask.to_string()),
                size: Some("200".to_string()),
            }],
        }
    }
}
