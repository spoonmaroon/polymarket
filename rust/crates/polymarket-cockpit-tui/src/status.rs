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
pub struct RuntimeLive {
    pub ok: bool,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub server_sent_at: Option<String>,
    pub status: RuntimeStatus,
    pub gates: RuntimeGates,
    pub monitor: RuntimeMonitor,
    #[serde(default)]
    pub recovery: RuntimeRecoverySummary,
    #[serde(default)]
    pub offload: RuntimeOffloadSummary,
    #[serde(default)]
    pub volatility: RuntimeVolatility,
    #[serde(default)]
    pub latency: RuntimeDisplayLag,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct RuntimeRecoverySummary {
    #[serde(default)]
    pub runtime_phase: String,
    #[serde(default)]
    pub ready: bool,
    #[serde(default)]
    pub reasons: Vec<String>,
    #[serde(default)]
    pub boot_id: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct RuntimeOffloadSummary {
    #[serde(default)]
    pub offload_allowed: bool,
    #[serde(default)]
    pub reason_codes: Vec<String>,
    #[serde(default)]
    pub recommended_worker_mode: String,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct RuntimeVolatility {
    #[serde(default)]
    pub state: String,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub generated_at: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub source_key: Option<String>,
    #[serde(default)]
    pub lookback_limit: Option<u64>,
    #[serde(default)]
    pub rows: Vec<RuntimeVolatilityRow>,
    #[serde(default)]
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeVolatilityRow {
    pub asset: String,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub asof_ts: Option<String>,
    #[serde(default)]
    pub sigma_tau: Option<f64>,
    #[serde(default)]
    pub short_realized_vol: Option<f64>,
    #[serde(default)]
    pub medium_realized_vol: Option<f64>,
    #[serde(default)]
    pub long_realized_vol: Option<f64>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub volatility_regime: Option<String>,
    #[serde(default)]
    pub age_ms: Option<u64>,
    #[serde(default)]
    pub flags: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct RuntimeDisplayLag {
    pub status_age_ms: Option<u64>,
    pub api_build_ms: Option<u64>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub server_sent_at: Option<String>,
    pub source_to_observed_ms: Option<u64>,
    pub observed_to_state_us: Option<u64>,
    pub tui_receive_lag_ms: Option<u64>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeProbabilities {
    #[serde(default = "default_true")]
    pub ok: bool,
    #[serde(default)]
    pub state: String,
    pub generated_at: String,
    #[serde(default)]
    pub cached: bool,
    #[serde(default)]
    pub rows: Vec<RuntimeProbabilityRow>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub error: Option<String>,
    #[serde(default)]
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeOutcomes {
    pub ok: bool,
    pub state: String,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub generated_at: Option<String>,
    #[serde(default)]
    pub rows: Vec<RuntimeOutcomeRow>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeOutcomeRow {
    pub market: String,
    pub market_id: String,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub market_slug: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub asset: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub start_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub expiry_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub threshold_price: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub threshold_event_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub threshold_observed_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub computed_winner: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub official_winner: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub winning_token_id: Option<String>,
    pub official_resolution_status: String,
    pub mismatch: Option<bool>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeProbabilityRow {
    pub contract: String,
    pub p_finish: f64,
    pub p_no_touch: f64,
    #[serde(default)]
    pub risk_adjusted_p_finish: Option<f64>,
    #[serde(default)]
    pub risk_adjusted_p_no_touch: Option<f64>,
    #[serde(default)]
    pub risk_adjustment: Option<f64>,
    #[serde(default)]
    pub pair_probability_sum_before: Option<f64>,
    #[serde(default)]
    pub pair_complement_gap: Option<f64>,
    #[serde(default)]
    pub pair_normalized: Option<bool>,
    pub z_path: f64,
    pub sigma_tau: f64,
    pub age_ms: u64,
    #[serde(default)]
    pub flags: Vec<String>,
    #[serde(default)]
    pub decision_hint: Option<String>,
    #[serde(default)]
    pub edge_after_costs: Option<f64>,
    #[serde(default)]
    pub required_edge: Option<f64>,
    #[serde(default)]
    pub skip_reasons: Vec<String>,
    #[serde(default)]
    pub model_version: Option<String>,
    #[serde(default)]
    pub generator_version: Option<String>,
    #[serde(default)]
    pub path_count: Option<u64>,
    #[serde(default)]
    pub generator_count: Option<u64>,
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
    pub start_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub expiry_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub threshold_price: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub threshold_event_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub threshold_observed_ts: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub settlement_price: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub settlement_event_ts: Option<String>,
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

fn default_true() -> bool {
    true
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
    use super::{
        RuntimeGates, RuntimeLive, RuntimeMonitor, RuntimeOutcomes, RuntimeProbabilities,
        RuntimeStatus,
    };

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
                    "threshold_price": "64000",
                    "threshold_event_ts": "2026-06-03T20:40:00Z",
                    "threshold_observed_ts": "2026-06-03T20:40:00.005Z",
                    "settlement_price": "64050",
                    "settlement_event_ts": "2026-06-03T20:43:16Z",
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
        assert_eq!(
            monitor.orderbooks[0].threshold_price.as_deref(),
            Some("64000")
        );
        assert_eq!(
            monitor.orderbooks[0].threshold_event_ts.as_deref(),
            Some("2026-06-03T20:40:00Z")
        );
        assert_eq!(
            monitor.orderbooks[0].threshold_observed_ts.as_deref(),
            Some("2026-06-03T20:40:00.005Z")
        );
        assert_eq!(
            monitor.orderbooks[0].settlement_price.as_deref(),
            Some("64050")
        );
        assert_eq!(
            monitor.orderbooks[0].settlement_event_ts.as_deref(),
            Some("2026-06-03T20:43:16Z")
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

    #[test]
    fn probabilities_payload_parses_cached_rows() {
        let payload = r#"{
            "generated_at": "2026-06-03T21:43:20.744215+00:00",
            "cached": true,
            "rows": [{
                "contract": "BTC 5m UP",
                "p_finish": 0.57,
                "p_no_touch": 0.31,
                "z_path": 0.42,
                "sigma_tau": 0.0123,
                "age_ms": 850,
                "flags": ["OK"]
            }]
        }"#;

        let probabilities: RuntimeProbabilities = serde_json::from_str(payload).unwrap();

        assert!(probabilities.cached);
        assert_eq!(probabilities.rows[0].contract, "BTC 5m UP");
        assert_eq!(probabilities.rows[0].p_finish, 0.57);
        assert_eq!(probabilities.rows[0].flags, vec!["OK"]);
    }

    #[test]
    fn live_payload_parses_combined_runtime_shape() {
        let payload = r#"{
            "ok": true,
            "server_sent_at": "2026-06-03T21:00:00+00:00",
            "status": {
                "ok": true,
                "schema_kind": "rust-live-probe-state-manager-v1",
                "mode": "state-manager",
                "age_ms": 12,
                "counts": {"prices": 2, "orderbooks": 4, "current": 2, "next": 2, "next_next": 0, "websocket_status": 2},
                "latency_marks": [],
                "health_flags": []
            },
            "gates": {"ok": true, "failures": []},
            "monitor": {
                "generated_at": "2026-06-03T21:00:00+00:00",
                "price_rows": [],
                "orderbooks": []
            },
            "volatility": {
                "state": "OK",
                "generated_at": "2026-06-03T21:00:00+00:00",
                "source_key": "polymarket_rtds_chainlink",
                "lookback_limit": 180,
                "rows": [{
                    "asset": "BTC",
                    "asof_ts": "2026-06-03T21:00:00+00:00",
                    "sigma_tau": 0.0012,
                    "short_realized_vol": 0.0001,
                    "medium_realized_vol": 0.0002,
                    "long_realized_vol": 0.0003,
                    "volatility_regime": "normal",
                    "age_ms": 120,
                    "flags": ["OK"]
                }],
                "errors": []
            },
            "latency": {
                "status_age_ms": 12,
                "api_build_ms": 1,
                "server_sent_at": "2026-06-03T21:00:00+00:00"
            }
        }"#;

        let live: RuntimeLive = serde_json::from_str(payload).unwrap();

        assert!(live.ok);
        assert_eq!(live.status.counts.orderbooks, 4);
        assert_eq!(live.latency.status_age_ms, Some(12));
        assert_eq!(live.volatility.rows[0].asset, "BTC");
        assert_eq!(live.volatility.rows[0].sigma_tau, Some(0.0012));
        assert_eq!(
            live.volatility.rows[0].volatility_regime.as_deref(),
            Some("normal")
        );
        assert_eq!(
            live.volatility.generated_at.as_deref(),
            Some("2026-06-03T21:00:00+00:00")
        );
        assert_eq!(
            live.volatility.source_key.as_deref(),
            Some("polymarket_rtds_chainlink")
        );
        assert_eq!(live.volatility.lookback_limit, Some(180));
    }

    #[test]
    fn live_payload_parses_recovery_and_offload_summaries() {
        let payload = r#"{
            "ok": false,
            "status": {
                "ok": false,
                "schema_kind": "rust-live-probe-state-manager-v1",
                "mode": "state-manager",
                "age_ms": 12,
                "counts": {"prices": 2, "orderbooks": 4, "current": 2, "next": 2, "next_next": 0, "websocket_status": 2},
                "latency_marks": [],
                "health_flags": ["warmup_active"]
            },
            "gates": {"ok": false, "failures": ["warmup_active"]},
            "monitor": {
                "generated_at": "2026-06-03T21:00:00+00:00",
                "price_rows": [],
                "orderbooks": []
            },
            "recovery": {"runtime_phase": "WARMING", "ready": false, "reasons": ["warmup_active"], "boot_id": "boot-1"},
            "offload": {"offload_allowed": false, "reason_codes": ["runtime_not_ready"], "recommended_worker_mode": "nowcast_only"}
        }"#;

        let live: RuntimeLive = serde_json::from_str(payload).unwrap();

        assert_eq!(live.recovery.runtime_phase, "WARMING");
        assert!(!live.offload.offload_allowed);
    }

    #[test]
    fn outcomes_payload_parses_market_level_history() {
        let payload = r#"{
            "ok": true,
            "state": "OK",
            "generated_at": "2026-06-03T22:00:00Z",
            "rows": [{
                "market": "BTC 5m",
                "market_id": "btc-updown-5m-1780521900",
                "asset": "BTC",
                "expiry_ts": "2026-06-03T21:25:00Z",
                "threshold_price": "64000",
                "threshold_event_ts": "2026-06-03T21:20:00Z",
                "threshold_observed_ts": "2026-06-03T21:20:03Z",
                "computed_winner": null,
                "official_winner": "UP",
                "winning_token_id": "up-token",
                "official_resolution_status": "resolved",
                "mismatch": null
            }]
        }"#;

        let outcomes: RuntimeOutcomes = serde_json::from_str(payload).unwrap();

        assert_eq!(outcomes.rows[0].computed_winner.as_deref(), None);
        assert_eq!(outcomes.rows[0].threshold_price.as_deref(), Some("64000"));
        assert_eq!(outcomes.rows[0].official_winner.as_deref(), Some("UP"));
        assert_eq!(
            outcomes.rows[0].winning_token_id.as_deref(),
            Some("up-token")
        );
        assert_eq!(outcomes.rows[0].official_resolution_status, "resolved");
    }
}
