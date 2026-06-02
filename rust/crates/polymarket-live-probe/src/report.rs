use anyhow::Result;
use chrono::Utc;
use polymarket_runtime_types::{
    FeedFreshness, LatencyMark, NormalizedOrderBook, NormalizedPriceTick, PriceDisagreement,
    ProbeReport, WarmStateSnapshot, WarmedContract,
};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::time::Instant;

pub const REPORT_SCHEMA_VERSION: &str = "rust-live-probe-v1";
pub const STATE_MANAGER_REPORT_SCHEMA_VERSION: &str = "rust-live-probe-state-manager-v1";
pub const STATE_MANAGER_REPORT_MODE: &str = "state-manager";

pub struct ProbeTimer {
    started: Instant,
    marks: Vec<LatencyMark>,
}

pub struct ReportInput {
    pub assets: Vec<String>,
    pub interval: String,
    pub windows: u8,
    pub elapsed_ms: u128,
    pub latency_marks: Vec<LatencyMark>,
    pub orderbooks: Vec<NormalizedOrderBook>,
    pub prices: Vec<NormalizedPriceTick>,
    pub source_disagreements: Vec<PriceDisagreement>,
}

pub struct StateManagerReportInput {
    pub elapsed_ms: u128,
    pub snapshot: WarmStateSnapshot,
    pub subscriptions: Vec<StateManagerSubscription>,
    pub websocket_status: Vec<WebSocketStatus>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StateManagerSubscription {
    pub source_key: String,
    pub channel: String,
    pub asset: String,
    pub token_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WebSocketStatus {
    pub source_key: String,
    pub channel: String,
    pub connection_state: String,
    pub reconnect_count: u64,
    pub subscription_count: usize,
    pub active_token_count: usize,
    pub ended_stream_count: usize,
    pub stream_error_count: u64,
    pub last_event_age_ms: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StateManagerReport {
    pub schema_version: String,
    pub mode: String,
    pub generated_at: chrono::DateTime<Utc>,
    pub elapsed_ms: u128,
    pub current: Vec<WarmedContract>,
    pub next: Vec<WarmedContract>,
    pub next_next: Vec<WarmedContract>,
    pub chainlink_prices: Vec<NormalizedPriceTick>,
    pub proxy_prices: Vec<NormalizedPriceTick>,
    pub orderbooks: Vec<NormalizedOrderBook>,
    pub freshness: Vec<FeedFreshness>,
    pub health_flags: Vec<String>,
    pub subscriptions: Vec<StateManagerSubscription>,
    pub websocket_status: Vec<WebSocketStatus>,
}

impl ProbeTimer {
    pub fn start() -> Self {
        Self {
            started: Instant::now(),
            marks: Vec::new(),
        }
    }

    pub fn mark(&mut self, name: &str) {
        self.marks.push(LatencyMark {
            name: name.to_owned(),
            elapsed_ms: self.started.elapsed().as_millis(),
        });
    }

    pub fn elapsed_ms(&self) -> u128 {
        self.started.elapsed().as_millis()
    }

    pub fn marks_snapshot(&self) -> Vec<LatencyMark> {
        self.marks.clone()
    }
}

pub fn build_report(input: ReportInput) -> ProbeReport {
    ProbeReport {
        schema_version: REPORT_SCHEMA_VERSION.to_owned(),
        generated_at: Utc::now(),
        elapsed_ms: input.elapsed_ms,
        assets: input.assets,
        interval: input.interval,
        windows: input.windows,
        orderbooks: input.orderbooks,
        prices: input.prices,
        source_disagreements: input.source_disagreements,
        latency_marks: input.latency_marks,
    }
}

pub fn build_state_manager_report(input: StateManagerReportInput) -> StateManagerReport {
    StateManagerReport {
        schema_version: STATE_MANAGER_REPORT_SCHEMA_VERSION.to_owned(),
        mode: STATE_MANAGER_REPORT_MODE.to_owned(),
        generated_at: Utc::now(),
        elapsed_ms: input.elapsed_ms,
        current: input.snapshot.current,
        next: input.snapshot.next,
        next_next: input.snapshot.next_next,
        chainlink_prices: input.snapshot.chainlink_prices,
        proxy_prices: input.snapshot.proxy_prices,
        orderbooks: input.snapshot.orderbooks,
        freshness: input.snapshot.freshness,
        health_flags: input.snapshot.health_flags,
        subscriptions: input.subscriptions,
        websocket_status: input.websocket_status,
    }
}

pub fn write_report(path: &Path, report: &ProbeReport) -> Result<()> {
    write_json_report(path, report)
}

pub fn write_state_manager_report(path: &Path, report: &StateManagerReport) -> Result<()> {
    write_json_report(path, report)
}

fn write_json_report<T: Serialize>(path: &Path, report: &T) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp_path = path.with_extension("json.tmp");
    std::fs::write(&tmp_path, serde_json::to_vec_pretty(report)?)?;
    std::fs::rename(tmp_path, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};
    use polymarket_runtime_types::{
        ContractSide, ContractToken, ContractWindow, FeedFreshness, WarmStateSnapshot,
        WarmedContract,
    };

    #[test]
    fn report_has_schema_version() {
        let report = build_report(ReportInput {
            assets: vec!["BTC".to_owned()],
            interval: "5m".to_owned(),
            windows: 1,
            elapsed_ms: 10,
            latency_marks: vec![LatencyMark {
                name: "start".to_owned(),
                elapsed_ms: 0,
            }],
            orderbooks: vec![],
            prices: vec![],
            source_disagreements: vec![],
        });

        assert_eq!(report.schema_version, "rust-live-probe-v1");
        assert_eq!(report.elapsed_ms, 10);
    }

    #[test]
    fn state_manager_report_serializes_required_schema_fields() {
        let observed_ts = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let current = sample_warmed_contract("BTC", 1_780_302_400);
        let snapshot = WarmStateSnapshot {
            observed_ts,
            current: vec![current.clone()],
            next: vec![sample_warmed_contract("BTC", 1_780_302_700)],
            next_next: vec![sample_warmed_contract("BTC", 1_780_303_000)],
            chainlink_prices: vec![],
            proxy_prices: vec![],
            orderbooks: vec![],
            freshness: vec![FeedFreshness {
                source_key: "polymarket_rtds_chainlink".to_owned(),
                symbol: "BTC/USD".to_owned(),
                age_ms: 100,
                stale: false,
            }],
            health_flags: vec![],
        };

        let report = build_state_manager_report(StateManagerReportInput {
            elapsed_ms: 250,
            snapshot,
            subscriptions: vec![StateManagerSubscription {
                source_key: "polymarket_clob_market_ws".to_owned(),
                channel: "market".to_owned(),
                asset: "BTC".to_owned(),
                token_id: current.up.token_id.clone(),
            }],
            websocket_status: vec![WebSocketStatus {
                source_key: "polymarket_clob_market_ws".to_owned(),
                channel: "market".to_owned(),
                connection_state: "Connected".to_owned(),
                reconnect_count: 2,
                subscription_count: 8,
                active_token_count: 8,
                ended_stream_count: 0,
                stream_error_count: 0,
                last_event_age_ms: Some(25),
            }],
        });
        let value = serde_json::to_value(&report).unwrap();

        assert_eq!(value["schema_version"], "rust-live-probe-state-manager-v1");
        assert_eq!(value["mode"], "state-manager");
        assert!(value.get("generated_at").unwrap().is_string());
        assert_eq!(value["elapsed_ms"], 250);
        assert!(value.get("current").unwrap().is_array());
        assert!(value.get("next").unwrap().is_array());
        assert!(value.get("next_next").unwrap().is_array());
        assert!(value.get("chainlink_prices").unwrap().is_array());
        assert!(value.get("proxy_prices").unwrap().is_array());
        assert!(value.get("orderbooks").unwrap().is_array());
        assert!(value.get("freshness").unwrap().is_array());
        assert!(value.get("health_flags").unwrap().is_array());
        assert_eq!(
            value["subscriptions"][0]["source_key"],
            "polymarket_clob_market_ws"
        );
        assert_eq!(
            value["websocket_status"][0]["connection_state"],
            "Connected"
        );
        assert_eq!(value["websocket_status"][0]["reconnect_count"], 2);
        assert_eq!(value["websocket_status"][0]["subscription_count"], 8);
        assert_eq!(value["websocket_status"][0]["active_token_count"], 8);
        assert_eq!(value["websocket_status"][0]["ended_stream_count"], 0);
        assert_eq!(value["websocket_status"][0]["stream_error_count"], 0);
        assert_eq!(value["websocket_status"][0]["last_event_age_ms"], 25);
    }

    #[test]
    fn state_manager_report_preserves_snapshot_health_flags() {
        let observed_ts = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let snapshot = WarmStateSnapshot {
            observed_ts,
            current: vec![],
            next: vec![],
            next_next: vec![],
            chainlink_prices: vec![],
            proxy_prices: vec![],
            orderbooks: vec![],
            freshness: vec![],
            health_flags: vec!["next_contract_not_warmed".to_owned()],
        };

        let report = build_state_manager_report(StateManagerReportInput {
            elapsed_ms: 42,
            snapshot,
            subscriptions: vec![],
            websocket_status: vec![],
        });

        assert_eq!(
            report.health_flags,
            vec!["next_contract_not_warmed".to_owned()]
        );
    }

    fn sample_warmed_contract(asset: &str, start_epoch: i64) -> WarmedContract {
        let start = Utc.timestamp_opt(start_epoch, 0).unwrap();
        let end = Utc.timestamp_opt(start_epoch + 300, 0).unwrap();
        let window = ContractWindow::new(asset, "5m", start, end).unwrap();
        WarmedContract::new(
            window,
            ContractToken::new(
                asset,
                ContractSide::Up,
                &format!("{asset}-{start_epoch}-up"),
            ),
            ContractToken::new(
                asset,
                ContractSide::Down,
                &format!("{asset}-{start_epoch}-down"),
            ),
        )
        .unwrap()
    }
}
