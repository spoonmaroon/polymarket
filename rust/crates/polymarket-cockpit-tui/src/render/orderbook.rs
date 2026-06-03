use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{render::market, state::AppState};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BookDisplayRow {
    pub contract: String,
    pub bid: String,
    pub ask: String,
}

pub fn book_title(app: &AppState) -> String {
    app.selected_orderbook()
        .map(|orderbook| format!("Book: {}", market::book_contract_label(orderbook)))
        .unwrap_or_else(|| "Book".to_string())
}

pub fn book_rows(app: &AppState) -> Vec<BookDisplayRow> {
    let Some(orderbook) = app.selected_orderbook() else {
        return Vec::new();
    };

    let bids = orderbook.bids.iter().rev().take(6).collect::<Vec<_>>();
    let asks = orderbook.asks.iter().take(6).collect::<Vec<_>>();
    let row_count = bids.len().max(asks.len());
    let contract = match (orderbook.asset.as_deref(), orderbook.side.as_deref()) {
        (Some(asset), Some(side)) if !asset.is_empty() && !side.is_empty() => {
            format!("{asset} {side}")
        }
        _ if !orderbook.contract_id.is_empty() => orderbook.contract_id.clone(),
        _ => "unknown".to_string(),
    };

    (0..row_count)
        .map(|index| BookDisplayRow {
            contract: contract.clone(),
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
    let Some(price) = price.filter(|value| !value.is_empty()) else {
        return "-".to_string();
    };

    if let Some(size) = size.filter(|value| !value.is_empty()) {
        format!("{price} x{size}")
    } else {
        price.to_string()
    }
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
    fn book_rows_pair_best_bid_and_ask_levels_for_first_contract() {
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

        assert_eq!(rows[0].contract, "ETH DOWN");
        assert_eq!(rows[0].bid, "0.86 x33");
        assert_eq!(rows[0].ask, "0.87 x14.46");
        assert_eq!(rows[1].bid, "0.84 x10");
        assert_eq!(rows[1].ask, "0.88 x20");
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

        assert_eq!(book_title(&app), "Book: BTC DOWN 21:10Z");
        assert_eq!(rows[0].contract, "BTC DOWN");
        assert_eq!(rows[0].bid, "0.49 x1256.68");
        assert_eq!(rows[0].ask, "0.50 x702.96");
    }
}
