use crate::{ContractSide, WarmedContract};
use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

pub const HOT_DECISION_STATE_SCHEMA_VERSION: &str = "rust-hot-decision-state-v1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum HotDecisionTriggerKind {
    ChainlinkPrice,
    OrderBookTopOfBook,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum HotDecisionQualityFlag {
    MissingThreshold,
    MissingSettlementPrice,
    MissingOrderbook,
    IncompleteOrderbook,
    StaleSource,
    StaleOrderbook,
    NotCurrentWindow,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HotDecisionLatency {
    pub trigger_event_to_observed_ms: i64,
    pub observed_to_state_us: u128,
    pub state_to_persist_us: Option<u128>,
    pub total_event_to_persist_ms: Option<u128>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HotDecisionState {
    pub schema_version: String,
    pub state_id: String,
    pub trigger_kind: HotDecisionTriggerKind,
    pub trigger_symbol: Option<String>,
    pub trigger_token_id: Option<String>,
    pub asof_ts: DateTime<Utc>,
    pub contract: WarmedContract,
    pub side: ContractSide,
    pub token_id: String,
    pub threshold_price: Option<Decimal>,
    pub threshold_event_ts: Option<DateTime<Utc>>,
    pub settlement_price: Option<Decimal>,
    pub settlement_event_ts: Option<DateTime<Utc>>,
    pub best_bid: Option<Decimal>,
    pub best_ask: Option<Decimal>,
    pub executable_price: Option<Decimal>,
    pub spread: Option<Decimal>,
    pub source_age_ms: Option<i64>,
    pub book_age_ms: Option<i64>,
    pub data_quality_flags: Vec<HotDecisionQualityFlag>,
    pub latency: HotDecisionLatency,
}

impl HotDecisionState {
    pub fn blocks_decision(&self) -> bool {
        !self.data_quality_flags.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ContractSide, ContractToken, ContractWindow, WarmedContract};
    use chrono::{Duration, TimeZone, Utc};
    use rust_decimal::Decimal;

    #[test]
    fn hot_decision_state_serializes_schema_and_flags() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let end = start + Duration::seconds(300);
        let contract = WarmedContract::new(
            ContractWindow::new("BTC", "5m", start, end).unwrap(),
            ContractToken::new("BTC", ContractSide::Up, "up-token"),
            ContractToken::new("BTC", ContractSide::Down, "down-token"),
        )
        .unwrap();
        let state = HotDecisionState {
            schema_version: HOT_DECISION_STATE_SCHEMA_VERSION.to_owned(),
            state_id: "btc-updown-5m-1780302400:UP:2026-06-01T08:26:42Z".to_owned(),
            trigger_kind: HotDecisionTriggerKind::OrderBookTopOfBook,
            trigger_symbol: None,
            trigger_token_id: Some("up-token".to_owned()),
            asof_ts: start + Duration::seconds(2),
            contract,
            side: ContractSide::Up,
            token_id: "up-token".to_owned(),
            threshold_price: Some(Decimal::new(70_000, 0)),
            threshold_event_ts: Some(start),
            settlement_price: Some(Decimal::new(70_050, 0)),
            settlement_event_ts: Some(start + Duration::seconds(2)),
            best_bid: Some(Decimal::new(61, 2)),
            best_ask: Some(Decimal::new(64, 2)),
            executable_price: Some(Decimal::new(64, 2)),
            spread: Some(Decimal::new(3, 2)),
            source_age_ms: Some(500),
            book_age_ms: Some(60),
            data_quality_flags: vec![],
            latency: HotDecisionLatency {
                trigger_event_to_observed_ms: 55,
                observed_to_state_us: 700,
                state_to_persist_us: None,
                total_event_to_persist_ms: None,
            },
        };

        assert!(!state.blocks_decision());
        let value = serde_json::to_value(&state).unwrap();
        assert_eq!(value["schema_version"], "rust-hot-decision-state-v1");
        assert_eq!(value["trigger_kind"], "OrderBookTopOfBook");
        assert_eq!(value["latency"]["observed_to_state_us"], 700);
    }
}
