use crate::contract::WarmedContract;
use crate::orderbook::NormalizedOrderBook;
use crate::price::NormalizedPriceTick;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FeedFreshness {
    pub source_key: String,
    pub symbol: String,
    pub age_ms: i64,
    pub stale: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WarmStateSnapshot {
    pub observed_ts: DateTime<Utc>,
    pub current: Vec<WarmedContract>,
    pub next: Vec<WarmedContract>,
    pub next_next: Vec<WarmedContract>,
    pub chainlink_prices: Vec<NormalizedPriceTick>,
    pub proxy_prices: Vec<NormalizedPriceTick>,
    pub orderbooks: Vec<NormalizedOrderBook>,
    pub freshness: Vec<FeedFreshness>,
    pub health_flags: Vec<String>,
}

impl WarmStateSnapshot {
    pub fn blocks_trading(&self) -> bool {
        !self.health_flags.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};

    #[test]
    fn snapshot_with_health_flags_blocks_trading() {
        let observed_ts = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let snapshot = WarmStateSnapshot {
            observed_ts,
            current: vec![],
            next: vec![],
            next_next: vec![],
            chainlink_prices: vec![],
            proxy_prices: vec![],
            orderbooks: vec![],
            freshness: vec![FeedFreshness {
                source_key: "polymarket_rtds_chainlink".to_owned(),
                symbol: "BTC/USD".to_owned(),
                age_ms: 3_000,
                stale: true,
            }],
            health_flags: vec!["stale_chainlink_btc".to_owned()],
        };

        assert!(snapshot.blocks_trading());
    }
}
