use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BookLevel {
    pub price: Decimal,
    pub size: Decimal,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OrderBookMeta {
    pub market_slug: String,
    pub contract_id: String,
    pub token_id: String,
    pub asset: String,
    pub side: String,
    pub event_ts: DateTime<Utc>,
    pub observed_ts: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NormalizedOrderBook {
    pub venue: String,
    pub source_key: String,
    pub market_slug: String,
    pub contract_id: String,
    pub token_id: String,
    pub asset: String,
    pub side: String,
    pub event_ts: DateTime<Utc>,
    pub observed_ts: DateTime<Utc>,
    pub best_bid: Option<Decimal>,
    pub best_ask: Option<Decimal>,
    pub spread: Option<Decimal>,
    pub bid_size_top: Option<Decimal>,
    pub ask_size_top: Option<Decimal>,
    pub bids: Vec<BookLevel>,
    pub asks: Vec<BookLevel>,
    #[serde(skip_serializing, default)]
    pub depth_json: serde_json::Value,
}

impl NormalizedOrderBook {
    pub fn from_levels(meta: OrderBookMeta, bids: Vec<BookLevel>, asks: Vec<BookLevel>) -> Self {
        let best_bid_level = bids.iter().max_by_key(|level| level.price);
        let best_ask_level = asks.iter().min_by_key(|level| level.price);
        let best_bid = best_bid_level.map(|level| level.price);
        let best_ask = best_ask_level.map(|level| level.price);
        let spread = match (best_bid, best_ask) {
            (Some(bid), Some(ask)) => Some(ask - bid),
            _ => None,
        };
        let bid_size_top = best_bid_level.map(|level| level.size);
        let ask_size_top = best_ask_level.map(|level| level.size);
        let depth_json = serde_json::json!({
            "bids": bids,
            "asks": asks,
        });

        Self {
            venue: "polymarket".to_owned(),
            source_key: "polymarket_rust_sdk".to_owned(),
            market_slug: meta.market_slug,
            contract_id: meta.contract_id,
            token_id: meta.token_id,
            asset: meta.asset,
            side: meta.side,
            event_ts: meta.event_ts,
            observed_ts: meta.observed_ts,
            best_bid,
            best_ask,
            spread,
            bid_size_top,
            ask_size_top,
            bids,
            asks,
            depth_json,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{DateTime, Utc};
    use rust_decimal::Decimal;

    #[test]
    fn derives_best_prices_and_spread_from_depth() {
        let ts = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let book = NormalizedOrderBook::from_levels(
            OrderBookMeta {
                market_slug: "btc-updown-5m-1780301700".to_owned(),
                contract_id: "market-1".to_owned(),
                token_id: "token-1".to_owned(),
                asset: "BTC".to_owned(),
                side: "UP".to_owned(),
                event_ts: ts,
                observed_ts: ts,
            },
            vec![
                BookLevel {
                    price: Decimal::new(48, 2),
                    size: Decimal::new(20, 0),
                },
                BookLevel {
                    price: Decimal::new(50, 2),
                    size: Decimal::new(8, 0),
                },
            ],
            vec![
                BookLevel {
                    price: Decimal::new(52, 2),
                    size: Decimal::new(9, 0),
                },
                BookLevel {
                    price: Decimal::new(54, 2),
                    size: Decimal::new(30, 0),
                },
            ],
        );

        assert_eq!(book.best_bid, Some(Decimal::new(50, 2)));
        assert_eq!(book.best_ask, Some(Decimal::new(52, 2)));
        assert_eq!(book.spread, Some(Decimal::new(2, 2)));
        assert_eq!(book.bid_size_top, Some(Decimal::new(8, 0)));
        assert_eq!(book.ask_size_top, Some(Decimal::new(9, 0)));
    }
}
