use serde::{Deserialize, Serialize, de};

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeStatus {
    pub ok: bool,
    pub schema_kind: String,
    pub mode: String,
    pub age_ms: Option<u64>,
    pub counts: RuntimeCounts,
    pub latency_marks: Vec<RuntimeLatencyMark>,
    pub health_flags: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeCounts {
    pub prices: usize,
    pub orderbooks: usize,
    pub current: usize,
    pub next: usize,
    pub next_next: usize,
    pub websocket_status: usize,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeLatencyMark {
    pub name: String,
    pub elapsed_ms: Option<u64>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeGates {
    pub ok: bool,
    pub failures: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeMonitor {
    pub generated_at: String,
    #[serde(default)]
    pub price_rows: Vec<RuntimePriceRow>,
    #[serde(default)]
    pub orderbooks: Vec<RuntimeOrderbookRow>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimePriceRow {
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub source_key: Option<String>,
    pub symbol: String,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub event_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub observed_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub price: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeOrderbookRow {
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub venue: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub source_key: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub market_slug: Option<String>,
    pub contract_id: String,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub token_id: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub asset: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub side: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub event_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub observed_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub best_bid: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub best_ask: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub spread: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub bid_size_top: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub ask_size_top: Option<String>,
    #[serde(default)]
    pub bids: Vec<RuntimeBookLevel>,
    #[serde(default)]
    pub asks: Vec<RuntimeBookLevel>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeBookLevel {
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub price: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub size: Option<String>,
}

fn deserialize_optional_scalar_string<'de, D>(deserializer: D) -> Result<Option<String>, D::Error>
where
    D: de::Deserializer<'de>,
{
    let value = Option::<serde_json::Value>::deserialize(deserializer)?;
    match value {
        None | Some(serde_json::Value::Null) => Ok(None),
        Some(serde_json::Value::String(value)) => Ok(Some(value)),
        Some(serde_json::Value::Number(value)) => Ok(Some(value.to_string())),
        Some(serde_json::Value::Bool(value)) => Ok(Some(value.to_string())),
        Some(other) => Err(de::Error::custom(format!(
            "expected scalar string/number/null, got {other}"
        ))),
    }
}

impl RuntimeStatus {
    pub fn state_label(&self) -> &'static str {
        if self.ok && self.health_flags.is_empty() {
            "OK"
        } else {
            "BLOCKED"
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{RuntimeGates, RuntimeMonitor, RuntimeStatus};

    #[test]
    fn status_payload_parses_and_labels_ok() {
        let payload = r#"{
            "ok": true,
            "schema_kind": "rust-live-probe-state-manager-v1",
            "mode": "state-manager",
            "age_ms": 10,
            "counts": {"prices": 2, "orderbooks": 4, "current": 2, "next": 2, "next_next": 0, "websocket_status": 2},
            "latency_marks": [{"name": "current_orderbook_age_ms", "elapsed_ms": 3}],
            "health_flags": []
        }"#;

        let status: RuntimeStatus = serde_json::from_str(payload).unwrap();

        assert_eq!(status.state_label(), "OK");
        assert_eq!(status.counts.current, 2);
    }

    #[test]
    fn gate_payload_keeps_block_reasons() {
        let payload = r#"{"ok": false, "failures": ["status file stale"]}"#;

        let gates: RuntimeGates = serde_json::from_str(payload).unwrap();

        assert!(!gates.ok);
        assert_eq!(gates.failures, vec!["status file stale"]);
    }

    #[test]
    fn monitor_payload_parses_prices_contracts_and_book_levels() {
        let payload = r#"{
            "generated_at": "2026-06-03T20:43:20.744215+00:00",
            "price_rows": [
                {
                    "source_key": "polymarket_rtds_chainlink",
                    "symbol": "BTC/USD",
                    "event_ts": "2026-06-03T20:43:16Z",
                    "observed_ts": "2026-06-03T20:43:19.789163241Z",
                    "price": "65185.18675916348"
                },
                {
                    "source_key": "polymarket_rtds_chainlink",
                    "symbol": "ETH/USD",
                    "event_ts": "2026-06-03T20:43:16Z",
                    "observed_ts": "2026-06-03T20:43:19.887210668Z",
                    "price": "1795.02822"
                }
            ],
            "orderbooks": [
                {
                    "venue": "polymarket",
                    "source_key": "polymarket_rust_sdk",
                    "market_slug": "eth-updown-5m-1780519200",
                    "contract_id": "0x0abe644dd79156eeeb5e4e3be9f8f78953d9907316c57e014c3598f2ae99e3cc",
                    "token_id": "100783333159874947931352697222477663764026407100859257224541015812712077669400",
                    "asset": "ETH",
                    "side": "DOWN",
                    "event_ts": "2026-06-03T20:43:12.101Z",
                    "observed_ts": "2026-06-03T20:43:20.616043736Z",
                    "best_bid": "0.86",
                    "best_ask": "0.87",
                    "spread": "0.01",
                    "bid_size_top": "33",
                    "ask_size_top": "14.46",
                    "bids": [{"price": "0.86", "size": "33"}],
                    "asks": [{"price": "0.87", "size": "14.46"}]
                }
            ]
        }"#;

        let monitor: RuntimeMonitor = serde_json::from_str(payload).unwrap();

        assert_eq!(monitor.price_rows[0].symbol, "BTC/USD");
        assert_eq!(
            monitor.price_rows[0].price.as_deref(),
            Some("65185.18675916348")
        );
        assert_eq!(
            monitor.orderbooks[0].market_slug.as_deref(),
            Some("eth-updown-5m-1780519200")
        );
        assert_eq!(monitor.orderbooks[0].best_bid.as_deref(), Some("0.86"));
        assert_eq!(monitor.orderbooks[0].asks[0].size.as_deref(), Some("14.46"));
    }

    #[test]
    fn monitor_payload_accepts_status_fallback_numbers_nulls_and_missing_metadata() {
        let payload = r#"{
            "generated_at": "2026-06-03T20:43:20.744215+00:00",
            "price_rows": [
                {
                    "source_key": "polymarket_rtds_chainlink",
                    "symbol": "BTC/USD",
                    "observed_ts": "2026-06-03T20:43:19.789163241Z",
                    "price": 65031.04957227011
                }
            ],
            "orderbooks": [
                {
                    "venue": "polymarket",
                    "contract_id": "btc-5m-up",
                    "token_id": "token-1",
                    "observed_ts": "2026-06-03T20:43:20.616043736Z",
                    "best_bid": 0.44,
                    "best_ask": null,
                    "spread": null
                }
            ]
        }"#;

        let monitor: RuntimeMonitor = serde_json::from_str(payload).unwrap();

        assert_eq!(
            monitor.price_rows[0].price.as_deref(),
            Some("65031.04957227011")
        );
        assert_eq!(monitor.orderbooks[0].contract_id, "btc-5m-up");
        assert_eq!(monitor.orderbooks[0].best_bid, Some("0.44".to_string()));
        assert_eq!(monitor.orderbooks[0].best_ask, None);
        assert!(monitor.orderbooks[0].market_slug.is_none());
        assert!(monitor.orderbooks[0].bids.is_empty());
    }
}
