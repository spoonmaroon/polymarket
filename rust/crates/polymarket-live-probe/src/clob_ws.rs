#![allow(dead_code)]

use anyhow::{Result, anyhow};
use chrono::{DateTime, TimeZone, Utc};
use polymarket_client_sdk_v2::clob::ws::WsMessage;
use polymarket_runtime_types::{BookLevel, NormalizedOrderBook, OrderBookMeta};
use rust_decimal::Decimal;
use serde_json::Value;

#[derive(Debug, Clone, PartialEq)]
pub enum ClobMarketEvent {
    Book(Box<NormalizedOrderBook>),
    TopOfBook {
        contract_id: String,
        token_id: String,
        best_bid: Decimal,
        best_ask: Decimal,
        spread: Decimal,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    },
    PriceChange {
        contract_id: String,
        token_id: String,
        price: Decimal,
        side: String,
        size: Option<Decimal>,
        best_bid: Option<Decimal>,
        best_ask: Option<Decimal>,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    },
}

pub fn market_subscription_payload(token_ids: &[String]) -> Value {
    let mut token_ids = token_ids
        .iter()
        .map(|token_id| token_id.trim().to_owned())
        .filter(|token_id| !token_id.is_empty())
        .collect::<Vec<_>>();
    token_ids.sort();
    token_ids.dedup();
    serde_json::json!({
        "type": "market",
        "assets_ids": token_ids,
        "operation": "subscribe",
        "initial_dump": true,
        "custom_feature_enabled": true
    })
}

pub fn parse_clob_market_events(
    message: &Value,
    observed_ts: DateTime<Utc>,
) -> Result<Vec<ClobMarketEvent>> {
    let event_type = message
        .get("event_type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !matches!(event_type, "book" | "best_bid_ask" | "price_change") {
        return Ok(vec![]);
    }

    let ws_message: WsMessage = serde_json::from_value(message.clone())?;
    match ws_message {
        WsMessage::Book(book) => Ok(vec![ClobMarketEvent::Book(Box::new(normalized_book(
            book,
            observed_ts,
        )?))]),
        WsMessage::BestBidAsk(top) => Ok(vec![ClobMarketEvent::TopOfBook {
            contract_id: top.market.to_string(),
            token_id: top.asset_id.to_string(),
            best_bid: top.best_bid,
            best_ask: top.best_ask,
            spread: top.spread,
            event_ts: timestamp_millis(top.timestamp)?,
            observed_ts,
        }]),
        WsMessage::PriceChange(price_change) => {
            let event_ts = timestamp_millis(price_change.timestamp)?;
            let contract_id = price_change.market.to_string();
            let events = price_change
                .price_changes
                .into_iter()
                .map(|change| ClobMarketEvent::PriceChange {
                    contract_id: contract_id.clone(),
                    token_id: change.asset_id.to_string(),
                    price: change.price,
                    side: format!("{:?}", change.side).to_ascii_uppercase(),
                    size: change.size,
                    best_bid: change.best_bid,
                    best_ask: change.best_ask,
                    event_ts,
                    observed_ts,
                })
                .collect();
            Ok(events)
        }
        _ => Ok(vec![]),
    }
}

fn normalized_book(
    book: polymarket_client_sdk_v2::clob::ws::BookUpdate,
    observed_ts: DateTime<Utc>,
) -> Result<NormalizedOrderBook> {
    let event_ts = timestamp_millis(book.timestamp)?;
    let bids = book
        .bids
        .into_iter()
        .map(|level| BookLevel {
            price: level.price,
            size: level.size,
        })
        .collect::<Vec<_>>();
    let asks = book
        .asks
        .into_iter()
        .map(|level| BookLevel {
            price: level.price,
            size: level.size,
        })
        .collect::<Vec<_>>();
    let mut normalized = NormalizedOrderBook::from_levels(
        OrderBookMeta {
            market_slug: String::new(),
            contract_id: book.market.to_string(),
            token_id: book.asset_id.to_string(),
            asset: String::new(),
            side: String::new(),
            event_ts,
            observed_ts,
        },
        bids,
        asks,
    );
    normalized.source_key = "polymarket_market_ws".to_owned();
    Ok(normalized)
}

fn timestamp_millis(timestamp: i64) -> Result<DateTime<Utc>> {
    Utc.timestamp_millis_opt(timestamp)
        .single()
        .ok_or_else(|| anyhow!("invalid CLOB websocket timestamp: {timestamp}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{DateTime, Utc};
    use rust_decimal::Decimal;

    fn observed_ts() -> DateTime<Utc> {
        "2026-06-01T20:00:01Z".parse::<DateTime<Utc>>().unwrap()
    }

    #[test]
    fn market_subscription_payload_sorts_and_deduplicates_tokens() {
        let payload =
            market_subscription_payload(&["222".to_owned(), "111".to_owned(), "222".to_owned()]);

        assert_eq!(
            payload,
            serde_json::json!({
                "type": "market",
                "assets_ids": ["111", "222"],
                "operation": "subscribe",
                "initial_dump": true,
                "custom_feature_enabled": true
            })
        );
    }

    #[test]
    fn parses_book_event_into_normalized_orderbook() {
        let message = serde_json::json!({
            "event_type": "book",
            "asset_id": "111",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000001",
            "timestamp": "1780352939000",
            "bids": [{"price": "0.49", "size": "20"}],
            "asks": [{"price": "0.51", "size": "30"}]
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert_eq!(events.len(), 1);
        match &events[0] {
            ClobMarketEvent::Book(book) => {
                assert_eq!(book.source_key, "polymarket_market_ws");
                assert_eq!(
                    book.contract_id,
                    "0x0000000000000000000000000000000000000000000000000000000000000001"
                );
                assert_eq!(book.token_id, "111");
                assert_eq!(book.best_bid, Some(Decimal::new(49, 2)));
                assert_eq!(book.best_ask, Some(Decimal::new(51, 2)));
                assert_eq!(book.spread, Some(Decimal::new(2, 2)));
                assert_eq!(book.event_ts.timestamp_millis(), 1780352939000);
            }
            other => panic!("expected book event, got {other:?}"),
        }
    }

    #[test]
    fn parses_best_bid_ask_event() {
        let message = serde_json::json!({
            "event_type": "best_bid_ask",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000002",
            "asset_id": "222",
            "best_bid": "0.73",
            "best_ask": "0.77",
            "spread": "0.04",
            "timestamp": "1780352939001"
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert_eq!(events.len(), 1);
        match &events[0] {
            ClobMarketEvent::TopOfBook {
                token_id,
                best_bid,
                best_ask,
                observed_ts: event_observed_ts,
                ..
            } => {
                assert_eq!(token_id, "222");
                assert_eq!(*best_bid, Decimal::new(73, 2));
                assert_eq!(*best_ask, Decimal::new(77, 2));
                assert_eq!(*event_observed_ts, observed_ts());
            }
            other => panic!("expected top-of-book event, got {other:?}"),
        }
    }

    #[test]
    fn parses_price_change_batch_events() {
        let message = serde_json::json!({
            "event_type": "price_change",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000003",
            "timestamp": "1780352939002",
            "price_changes": [{
                "asset_id": "333",
                "price": "0.52",
                "size": "10",
                "side": "BUY",
                "best_bid": "0.51",
                "best_ask": "0.53"
            }]
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert_eq!(events.len(), 1);
        match &events[0] {
            ClobMarketEvent::PriceChange {
                token_id,
                price,
                side,
                size,
                observed_ts: event_observed_ts,
                ..
            } => {
                assert_eq!(token_id, "333");
                assert_eq!(*price, Decimal::new(52, 2));
                assert_eq!(side, "BUY");
                assert_eq!(*size, Some(Decimal::new(10, 0)));
                assert_eq!(*event_observed_ts, observed_ts());
            }
            other => panic!("expected price-change event, got {other:?}"),
        }
    }

    #[test]
    fn unknown_event_type_is_ignored() {
        let message = serde_json::json!({
            "event_type": "new_market",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000004"
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert!(events.is_empty());
    }
}
