use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use rust_decimal::prelude::ToPrimitive;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NormalizedPriceTick {
    pub source_key: String,
    pub symbol: String,
    pub event_ts: DateTime<Utc>,
    pub observed_ts: DateTime<Utc>,
    pub price: Decimal,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PriceDisagreement {
    pub asset: String,
    pub primary_source_key: String,
    pub primary_symbol: String,
    pub primary_price: Decimal,
    pub proxy_source_key: String,
    pub proxy_symbol: String,
    pub proxy_price: Decimal,
    pub diff: Decimal,
    pub diff_bps: f64,
}

impl PriceDisagreement {
    pub fn calculate(
        asset: &str,
        primary: &NormalizedPriceTick,
        proxy: &NormalizedPriceTick,
    ) -> Self {
        let diff = proxy.price - primary.price;
        let diff_abs = diff.abs().to_f64().unwrap_or(f64::NAN);
        let primary_float = primary.price.to_f64().unwrap_or(f64::NAN);
        let diff_bps = diff_abs / primary_float * 10_000.0;
        Self {
            asset: asset.to_owned(),
            primary_source_key: primary.source_key.clone(),
            primary_symbol: primary.symbol.clone(),
            primary_price: primary.price,
            proxy_source_key: proxy.source_key.clone(),
            proxy_symbol: proxy.symbol.clone(),
            proxy_price: proxy.price,
            diff,
            diff_bps,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{DateTime, Utc};
    use rust_decimal::Decimal;

    #[test]
    fn calculates_btc_price_disagreement_in_bps() {
        let ts = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let primary = NormalizedPriceTick {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            symbol: "BTC/USD".to_owned(),
            event_ts: ts,
            observed_ts: ts,
            price: Decimal::new(100_000, 0),
        };
        let proxy = NormalizedPriceTick {
            source_key: "kraken_rest".to_owned(),
            symbol: "XBT/USD".to_owned(),
            event_ts: ts,
            observed_ts: ts,
            price: Decimal::new(100_100, 0),
        };

        let row = PriceDisagreement::calculate("BTC", &primary, &proxy);

        assert_eq!(row.diff, Decimal::new(100, 0));
        assert!((row.diff_bps - 10.0).abs() < 0.0001);
    }
}
