use anyhow::{Result, anyhow};
use chrono::{DateTime, Utc};
use polymarket_runtime_types::{
    ContractSide, HOT_DECISION_STATE_SCHEMA_VERSION, HotDecisionLatency, HotDecisionQualityFlag,
    HotDecisionState, HotDecisionTriggerKind, NormalizedOrderBook, NormalizedPriceTick,
    WarmedContract,
};
use std::sync::{Arc, RwLock};
use std::time::Instant;
use tokio::sync::mpsc;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HotPathEvent {
    ChainlinkPrice {
        symbol: String,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    },
    OrderBookTopOfBook {
        token_id: String,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    },
}

impl HotPathEvent {
    pub fn trigger_kind(&self) -> HotDecisionTriggerKind {
        match self {
            Self::ChainlinkPrice { .. } => HotDecisionTriggerKind::ChainlinkPrice,
            Self::OrderBookTopOfBook { .. } => HotDecisionTriggerKind::OrderBookTopOfBook,
        }
    }

    pub fn trigger_symbol(&self) -> Option<String> {
        match self {
            Self::ChainlinkPrice { symbol, .. } => Some(symbol.clone()),
            Self::OrderBookTopOfBook { .. } => None,
        }
    }

    pub fn trigger_token_id(&self) -> Option<String> {
        match self {
            Self::ChainlinkPrice { .. } => None,
            Self::OrderBookTopOfBook { token_id, .. } => Some(token_id.clone()),
        }
    }

    pub fn event_ts(&self) -> DateTime<Utc> {
        match self {
            Self::ChainlinkPrice { event_ts, .. } | Self::OrderBookTopOfBook { event_ts, .. } => {
                *event_ts
            }
        }
    }

    pub fn observed_ts(&self) -> DateTime<Utc> {
        match self {
            Self::ChainlinkPrice { observed_ts, .. }
            | Self::OrderBookTopOfBook { observed_ts, .. } => *observed_ts,
        }
    }
}

#[derive(Debug, Clone)]
pub struct HotPathEventSink {
    sender: mpsc::Sender<HotPathEvent>,
    telemetry: Option<HotDecisionTelemetry>,
}

impl HotPathEventSink {
    #[allow(dead_code)]
    pub fn channel(buffer_size: usize) -> (Self, mpsc::Receiver<HotPathEvent>) {
        Self::channel_with_telemetry(buffer_size, None)
    }

    pub fn channel_with_telemetry(
        buffer_size: usize,
        telemetry: Option<HotDecisionTelemetry>,
    ) -> (Self, mpsc::Receiver<HotPathEvent>) {
        let (sender, receiver) = mpsc::channel(buffer_size.max(1));
        (Self { sender, telemetry }, receiver)
    }

    pub fn try_send(&self, event: HotPathEvent) -> Result<()> {
        self.sender.try_send(event).map_err(|error| {
            if let Some(telemetry) = &self.telemetry {
                telemetry.record_dropped_event();
            }
            anyhow!("hot path event queue unavailable: {error}")
        })
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct HotDecisionTelemetrySnapshot {
    pub states_built: u64,
    pub states_persist_queued: u64,
    pub dropped_events: u64,
    pub last_state_age_ms: Option<i64>,
    pub last_observed_to_state_us: Option<u128>,
}

#[derive(Debug, Clone, Default)]
pub struct HotDecisionTelemetry {
    inner: Arc<RwLock<HotDecisionTelemetrySnapshot>>,
}

impl HotDecisionTelemetry {
    pub fn record_state_built(&self, asof_ts: DateTime<Utc>, observed_to_state_us: u128) {
        let mut inner = self
            .inner
            .write()
            .expect("hot decision telemetry lock poisoned");
        inner.states_built = inner.states_built.saturating_add(1);
        inner.last_state_age_ms =
            Some(Utc::now().signed_duration_since(asof_ts).num_milliseconds());
        inner.last_observed_to_state_us = Some(observed_to_state_us);
    }

    pub fn record_state_persist_queued(&self) {
        let mut inner = self
            .inner
            .write()
            .expect("hot decision telemetry lock poisoned");
        inner.states_persist_queued = inner.states_persist_queued.saturating_add(1);
    }

    pub fn record_state_persist_result(
        &self,
        asof_ts: DateTime<Utc>,
        observed_to_state_us: u128,
        persist_queued: bool,
    ) {
        let mut inner = self
            .inner
            .write()
            .expect("hot decision telemetry lock poisoned");
        inner.states_built = inner.states_built.saturating_add(1);
        inner.last_state_age_ms =
            Some(Utc::now().signed_duration_since(asof_ts).num_milliseconds());
        inner.last_observed_to_state_us = Some(observed_to_state_us);
        if persist_queued {
            inner.states_persist_queued = inner.states_persist_queued.saturating_add(1);
        } else {
            inner.dropped_events = inner.dropped_events.saturating_add(1);
        }
    }

    pub fn record_dropped_event(&self) {
        let mut inner = self
            .inner
            .write()
            .expect("hot decision telemetry lock poisoned");
        inner.dropped_events = inner.dropped_events.saturating_add(1);
    }

    pub fn snapshot(&self) -> HotDecisionTelemetrySnapshot {
        self.inner
            .read()
            .expect("hot decision telemetry lock poisoned")
            .clone()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HotDecisionConfig {
    pub stale_source_after_ms: i64,
    pub stale_orderbook_after_ms: i64,
    pub restart_started_at: Option<DateTime<Utc>>,
}

impl Default for HotDecisionConfig {
    fn default() -> Self {
        Self {
            stale_source_after_ms: 30_000,
            stale_orderbook_after_ms: 30_000,
            restart_started_at: None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct HotDecisionBuilder {
    config: HotDecisionConfig,
}

impl HotDecisionBuilder {
    pub fn new(config: HotDecisionConfig) -> Self {
        Self { config }
    }

    pub fn build_for_event(
        &self,
        event: &HotPathEvent,
        warmed_contracts: &[WarmedContract],
        chainlink_history: &[NormalizedPriceTick],
        orderbooks: &[NormalizedOrderBook],
        asof_ts: DateTime<Utc>,
    ) -> Vec<HotDecisionState> {
        let started = Instant::now();
        let asof_ts = asof_ts.max(event.observed_ts());
        let mut states = Vec::new();

        for contract in warmed_contracts
            .iter()
            .filter(|contract| is_current_window(contract, asof_ts))
        {
            let impacted_slots = impacted_token_slots(event, contract);
            if impacted_slots.iter().all(Option::is_none) {
                continue;
            }
            let price_context = contract_price_context(chainlink_history, contract, asof_ts);
            let state_id_context = StateIdContext::new(contract, asof_ts);
            for impacted in impacted_slots.into_iter().flatten() {
                let side = impacted.side;
                let token_id = impacted.token_id;
                let threshold = price_context.threshold;
                let settlement = price_context.settlement;
                let book = latest_orderbook_at_or_before(orderbooks, token_id, asof_ts);
                let mut flags = Vec::new();

                if threshold.is_none() {
                    flags.push(HotDecisionQualityFlag::MissingThreshold);
                    if self
                        .config
                        .restart_started_at
                        .is_some_and(|restart_started_at| {
                            contract.window.start_ts < restart_started_at
                        })
                    {
                        flags.push(HotDecisionQualityFlag::RestartWarmupBlocked);
                    }
                }
                if settlement.is_none() {
                    flags.push(HotDecisionQualityFlag::MissingSettlementPrice);
                }
                if book.is_none() {
                    flags.push(HotDecisionQualityFlag::MissingOrderbook);
                }

                let source_age_ms = settlement.map(|tick| age_ms(asof_ts, tick.observed_ts));
                if source_age_ms.is_some_and(|age| age > self.config.stale_source_after_ms) {
                    flags.push(HotDecisionQualityFlag::StaleSource);
                }

                let book_age_ms = book.map(|book| age_ms(asof_ts, book.observed_ts));
                if let Some(book) = book {
                    if book.best_bid.is_none() || book.best_ask.is_none() {
                        flags.push(HotDecisionQualityFlag::IncompleteOrderbook);
                    }
                }
                if book_age_ms.is_some_and(|age| age > self.config.stale_orderbook_after_ms) {
                    flags.push(HotDecisionQualityFlag::StaleOrderbook);
                }

                states.push(HotDecisionState {
                    schema_version: HOT_DECISION_STATE_SCHEMA_VERSION.to_owned(),
                    state_id: state_id_context.for_side(&side),
                    trigger_kind: event.trigger_kind(),
                    trigger_symbol: event.trigger_symbol(),
                    trigger_token_id: event.trigger_token_id(),
                    asof_ts,
                    contract: contract.clone(),
                    side,
                    token_id: token_id.to_owned(),
                    threshold_price: threshold.map(|tick| tick.price),
                    threshold_event_ts: threshold.map(|tick| tick.event_ts),
                    settlement_price: settlement.map(|tick| tick.price),
                    settlement_event_ts: settlement.map(|tick| tick.event_ts),
                    best_bid: book.and_then(|book| book.best_bid),
                    best_ask: book.and_then(|book| book.best_ask),
                    executable_price: book.and_then(|book| book.best_ask),
                    spread: book.and_then(|book| book.spread),
                    source_age_ms,
                    book_age_ms,
                    data_quality_flags: flags,
                    latency: HotDecisionLatency {
                        trigger_event_to_observed_ms: age_ms(event.observed_ts(), event.event_ts()),
                        observed_to_state_us: started.elapsed().as_micros(),
                        state_to_persist_us: None,
                        total_event_to_persist_ms: None,
                    },
                });
            }
        }

        states
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ImpactedToken<'a> {
    side: ContractSide,
    token_id: &'a str,
}

#[derive(Debug, Clone, Copy)]
struct ContractPriceContext<'a> {
    threshold: Option<&'a NormalizedPriceTick>,
    settlement: Option<&'a NormalizedPriceTick>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct StateIdContext {
    contract_slug: String,
    asof_ts: String,
}

impl StateIdContext {
    fn new(contract: &WarmedContract, asof_ts: DateTime<Utc>) -> Self {
        Self {
            contract_slug: contract.window.slug(),
            asof_ts: asof_ts.to_rfc3339(),
        }
    }

    fn for_side(&self, side: &ContractSide) -> String {
        format!(
            "{}:{}:{}",
            self.contract_slug,
            side_label(side),
            self.asof_ts
        )
    }
}

fn contract_price_context<'a>(
    chainlink_history: &'a [NormalizedPriceTick],
    contract: &WarmedContract,
    asof_ts: DateTime<Utc>,
) -> ContractPriceContext<'a> {
    ContractPriceContext {
        threshold: latest_price_at_or_before(
            chainlink_history,
            &contract.window.asset,
            contract.window.start_ts,
            asof_ts,
        ),
        settlement: latest_price_at_or_before(
            chainlink_history,
            &contract.window.asset,
            asof_ts,
            asof_ts,
        ),
    }
}

fn impacted_token_slots<'a>(
    event: &HotPathEvent,
    contract: &'a WarmedContract,
) -> [Option<ImpactedToken<'a>>; 2] {
    match event {
        HotPathEvent::ChainlinkPrice { symbol, .. } => {
            if symbol_matches_asset(symbol, &contract.window.asset) {
                [
                    Some(ImpactedToken {
                        side: ContractSide::Up,
                        token_id: contract.up.token_id.as_str(),
                    }),
                    Some(ImpactedToken {
                        side: ContractSide::Down,
                        token_id: contract.down.token_id.as_str(),
                    }),
                ]
            } else {
                [None, None]
            }
        }
        HotPathEvent::OrderBookTopOfBook { token_id, .. } => {
            if token_id == &contract.up.token_id {
                [
                    Some(ImpactedToken {
                        side: ContractSide::Up,
                        token_id: contract.up.token_id.as_str(),
                    }),
                    None,
                ]
            } else if token_id == &contract.down.token_id {
                [
                    Some(ImpactedToken {
                        side: ContractSide::Down,
                        token_id: contract.down.token_id.as_str(),
                    }),
                    None,
                ]
            } else {
                [None, None]
            }
        }
    }
}

fn latest_price_at_or_before<'a>(
    history: &'a [NormalizedPriceTick],
    asset: &str,
    event_ts_lte: DateTime<Utc>,
    observed_ts_lte: DateTime<Utc>,
) -> Option<&'a NormalizedPriceTick> {
    history
        .iter()
        .filter(|tick| symbol_matches_asset(&tick.symbol, asset))
        .filter(|tick| tick.event_ts <= event_ts_lte && tick.observed_ts <= observed_ts_lte)
        .max_by_key(|tick| (tick.event_ts, tick.observed_ts))
}

fn latest_orderbook_at_or_before<'a>(
    orderbooks: &'a [NormalizedOrderBook],
    token_id: &str,
    asof_ts: DateTime<Utc>,
) -> Option<&'a NormalizedOrderBook> {
    orderbooks
        .iter()
        .filter(|book| book.token_id == token_id)
        .filter(|book| book.event_ts <= asof_ts && book.observed_ts <= asof_ts)
        .max_by_key(|book| (book.event_ts, book.observed_ts))
}

fn is_current_window(contract: &WarmedContract, asof_ts: DateTime<Utc>) -> bool {
    contract.window.start_ts <= asof_ts && asof_ts < contract.window.end_ts
}

fn symbol_matches_asset(symbol: &str, asset: &str) -> bool {
    symbol_asset(symbol) == asset.trim().to_ascii_uppercase()
}

fn symbol_asset(symbol: &str) -> String {
    symbol
        .split_once('/')
        .map(|(asset, _)| asset)
        .unwrap_or(symbol)
        .trim()
        .to_ascii_uppercase()
}

fn side_label(side: &ContractSide) -> &'static str {
    match side {
        ContractSide::Up => "UP",
        ContractSide::Down => "DOWN",
    }
}

fn age_ms(later: DateTime<Utc>, earlier: DateTime<Utc>) -> i64 {
    later.signed_duration_since(earlier).num_milliseconds()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{DateTime, Duration, TimeZone, Utc};
    use polymarket_runtime_types::{
        BookLevel, ContractSide, ContractToken, ContractWindow, HotDecisionQualityFlag,
        NormalizedOrderBook, NormalizedPriceTick, OrderBookMeta, WarmedContract,
    };
    use rust_decimal::Decimal;

    #[test]
    fn builds_current_hot_decision_for_orderbook_event_without_python_or_duckdb() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let asof = start + Duration::seconds(12);
        let contract = WarmedContract::new(
            ContractWindow::new("BTC", "5m", start, start + Duration::seconds(300)).unwrap(),
            ContractToken::new("BTC", ContractSide::Up, "up-token"),
            ContractToken::new("BTC", ContractSide::Down, "down-token"),
        )
        .unwrap();
        let prices = vec![
            price("BTC/USD", start, start, 70_000),
            price("BTC/USD", asof - Duration::seconds(1), asof, 70_050),
        ];
        let books = vec![book(
            "up-token",
            asof - Duration::milliseconds(80),
            asof,
            61,
            64,
        )];
        let event = HotPathEvent::OrderBookTopOfBook {
            token_id: "up-token".to_owned(),
            event_ts: asof - Duration::milliseconds(80),
            observed_ts: asof,
        };

        let states = HotDecisionBuilder::new(HotDecisionConfig::default()).build_for_event(
            &event,
            &[contract],
            &prices,
            &books,
            asof,
        );

        assert_eq!(states.len(), 1);
        assert_eq!(states[0].side, ContractSide::Up);
        assert_eq!(states[0].threshold_price, Some(Decimal::new(70_000, 0)));
        assert_eq!(states[0].settlement_price, Some(Decimal::new(70_050, 0)));
        assert_eq!(states[0].best_ask, Some(Decimal::new(64, 2)));
        assert_eq!(
            states[0].data_quality_flags,
            Vec::<HotDecisionQualityFlag>::new()
        );
        assert_eq!(states[0].latency.trigger_event_to_observed_ms, 80);
    }

    #[test]
    fn orderbook_event_state_asof_includes_trigger_observation() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let trigger_observed = start + Duration::seconds(12);
        let early_worker_now = trigger_observed - Duration::microseconds(500);
        let contract = WarmedContract::new(
            ContractWindow::new("BTC", "5m", start, start + Duration::seconds(300)).unwrap(),
            ContractToken::new("BTC", ContractSide::Up, "up-token"),
            ContractToken::new("BTC", ContractSide::Down, "down-token"),
        )
        .unwrap();
        let prices = vec![
            price("BTC/USD", start, start, 70_000),
            price(
                "BTC/USD",
                trigger_observed - Duration::seconds(1),
                trigger_observed - Duration::milliseconds(200),
                70_050,
            ),
        ];
        let books = vec![book(
            "up-token",
            trigger_observed - Duration::milliseconds(80),
            trigger_observed,
            61,
            64,
        )];
        let event = HotPathEvent::OrderBookTopOfBook {
            token_id: "up-token".to_owned(),
            event_ts: trigger_observed - Duration::milliseconds(80),
            observed_ts: trigger_observed,
        };

        let states = HotDecisionBuilder::new(HotDecisionConfig::default()).build_for_event(
            &event,
            &[contract],
            &prices,
            &books,
            early_worker_now,
        );

        assert_eq!(states.len(), 1);
        assert_eq!(states[0].asof_ts, trigger_observed);
        assert_eq!(states[0].best_bid, Some(Decimal::new(61, 2)));
        assert_eq!(states[0].best_ask, Some(Decimal::new(64, 2)));
        assert!(
            !states[0]
                .data_quality_flags
                .contains(&HotDecisionQualityFlag::MissingOrderbook)
        );
    }

    #[test]
    fn restart_inside_current_window_without_threshold_blocks_hot_decision() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let restart_started_at = start + Duration::seconds(45);
        let asof = restart_started_at + Duration::seconds(12);
        let contract = WarmedContract::new(
            ContractWindow::new("BTC", "5m", start, start + Duration::seconds(300)).unwrap(),
            ContractToken::new("BTC", ContractSide::Up, "up-token"),
            ContractToken::new("BTC", ContractSide::Down, "down-token"),
        )
        .unwrap();
        let prices = vec![price(
            "BTC/USD",
            asof - Duration::seconds(1),
            asof - Duration::milliseconds(200),
            70_050,
        )];
        let books = vec![book(
            "up-token",
            asof - Duration::milliseconds(80),
            asof,
            61,
            64,
        )];
        let event = HotPathEvent::OrderBookTopOfBook {
            token_id: "up-token".to_owned(),
            event_ts: asof - Duration::milliseconds(80),
            observed_ts: asof,
        };
        let config = HotDecisionConfig {
            restart_started_at: Some(restart_started_at),
            ..HotDecisionConfig::default()
        };

        let states = HotDecisionBuilder::new(config).build_for_event(
            &event,
            &[contract],
            &prices,
            &books,
            asof,
        );

        assert_eq!(states.len(), 1);
        assert_eq!(states[0].settlement_price, Some(Decimal::new(70_050, 0)));
        assert_eq!(states[0].best_ask, Some(Decimal::new(64, 2)));
        assert!(
            states[0]
                .data_quality_flags
                .contains(&HotDecisionQualityFlag::MissingThreshold)
        );
        assert!(
            states[0]
                .data_quality_flags
                .contains(&HotDecisionQualityFlag::RestartWarmupBlocked)
        );
    }

    #[test]
    fn window_start_after_restart_missing_threshold_is_not_restart_blocked() {
        let restart_started_at = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let start = restart_started_at + Duration::seconds(300);
        let asof = start + Duration::seconds(12);
        let contract = WarmedContract::new(
            ContractWindow::new("BTC", "5m", start, start + Duration::seconds(300)).unwrap(),
            ContractToken::new("BTC", ContractSide::Up, "up-token"),
            ContractToken::new("BTC", ContractSide::Down, "down-token"),
        )
        .unwrap();
        let prices = vec![price(
            "BTC/USD",
            asof - Duration::seconds(1),
            asof - Duration::milliseconds(200),
            70_050,
        )];
        let books = vec![book(
            "up-token",
            asof - Duration::milliseconds(80),
            asof,
            61,
            64,
        )];
        let event = HotPathEvent::OrderBookTopOfBook {
            token_id: "up-token".to_owned(),
            event_ts: asof - Duration::milliseconds(80),
            observed_ts: asof,
        };
        let config = HotDecisionConfig {
            restart_started_at: Some(restart_started_at),
            ..HotDecisionConfig::default()
        };

        let states = HotDecisionBuilder::new(config).build_for_event(
            &event,
            &[contract],
            &prices,
            &books,
            asof,
        );

        assert_eq!(states.len(), 1);
        assert!(
            states[0]
                .data_quality_flags
                .contains(&HotDecisionQualityFlag::MissingThreshold)
        );
        assert!(
            !states[0]
                .data_quality_flags
                .contains(&HotDecisionQualityFlag::RestartWarmupBlocked)
        );
    }

    #[test]
    fn contract_price_context_reuses_threshold_and_settlement_for_both_sides() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let asof = start + Duration::seconds(12);
        let contract = WarmedContract::new(
            ContractWindow::new("BTC", "5m", start, start + Duration::seconds(300)).unwrap(),
            ContractToken::new("BTC", ContractSide::Up, "up-token"),
            ContractToken::new("BTC", ContractSide::Down, "down-token"),
        )
        .unwrap();
        let prices = vec![
            price("BTC/USD", start, start, 70_000),
            price("ETH/USD", asof, asof, 3_000),
            price("BTC/USD", asof - Duration::seconds(1), asof, 70_050),
        ];

        let context = contract_price_context(&prices, &contract, asof);

        assert_eq!(
            context.threshold.map(|tick| tick.price),
            Some(Decimal::new(70_000, 0))
        );
        assert_eq!(
            context.settlement.map(|tick| tick.price),
            Some(Decimal::new(70_050, 0))
        );
    }

    #[test]
    fn impacted_token_slots_cover_chainlink_orderbook_and_unknown_events() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let contract = WarmedContract::new(
            ContractWindow::new("BTC", "5m", start, start + Duration::seconds(300)).unwrap(),
            ContractToken::new("BTC", ContractSide::Up, "up-token"),
            ContractToken::new("BTC", ContractSide::Down, "down-token"),
        )
        .unwrap();
        let ts = start + Duration::seconds(12);

        let chainlink_slots = impacted_token_slots(
            &HotPathEvent::ChainlinkPrice {
                symbol: "btc/usd".to_owned(),
                event_ts: ts,
                observed_ts: ts,
            },
            &contract,
        );
        let orderbook_slots = impacted_token_slots(
            &HotPathEvent::OrderBookTopOfBook {
                token_id: "down-token".to_owned(),
                event_ts: ts,
                observed_ts: ts,
            },
            &contract,
        );
        let unknown_slots = impacted_token_slots(
            &HotPathEvent::OrderBookTopOfBook {
                token_id: "unknown-token".to_owned(),
                event_ts: ts,
                observed_ts: ts,
            },
            &contract,
        );

        assert_eq!(
            slot_pairs(chainlink_slots),
            vec![
                (ContractSide::Up, "up-token"),
                (ContractSide::Down, "down-token")
            ]
        );
        assert_eq!(
            slot_pairs(orderbook_slots),
            vec![(ContractSide::Down, "down-token")]
        );
        assert!(slot_pairs(unknown_slots).is_empty());
    }

    #[test]
    fn state_id_context_reuses_contract_slug_and_asof_timestamp() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let asof = start + Duration::seconds(12);
        let contract = WarmedContract::new(
            ContractWindow::new("BTC", "5m", start, start + Duration::seconds(300)).unwrap(),
            ContractToken::new("BTC", ContractSide::Up, "up-token"),
            ContractToken::new("BTC", ContractSide::Down, "down-token"),
        )
        .unwrap();

        let context = StateIdContext::new(&contract, asof);

        assert_eq!(
            context.for_side(&ContractSide::Up),
            "btc-updown-5m-1780302400:UP:2026-06-01T08:26:52+00:00"
        );
        assert_eq!(
            context.for_side(&ContractSide::Down),
            "btc-updown-5m-1780302400:DOWN:2026-06-01T08:26:52+00:00"
        );
    }

    fn price(
        symbol: &str,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
        value: i64,
    ) -> NormalizedPriceTick {
        NormalizedPriceTick {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            symbol: symbol.to_owned(),
            event_ts,
            observed_ts,
            price: Decimal::new(value, 0),
        }
    }

    fn book(
        token_id: &str,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
        bid: i64,
        ask: i64,
    ) -> NormalizedOrderBook {
        NormalizedOrderBook::from_levels(
            OrderBookMeta {
                market_slug: "btc-updown-5m-1780302400".to_owned(),
                contract_id: "market-1".to_owned(),
                token_id: token_id.to_owned(),
                asset: "BTC".to_owned(),
                side: "UP".to_owned(),
                event_ts,
                observed_ts,
            },
            vec![BookLevel {
                price: Decimal::new(bid, 2),
                size: Decimal::new(10, 0),
            }],
            vec![BookLevel {
                price: Decimal::new(ask, 2),
                size: Decimal::new(11, 0),
            }],
        )
    }

    fn slot_pairs(slots: [Option<ImpactedToken<'_>>; 2]) -> Vec<(ContractSide, &str)> {
        slots
            .into_iter()
            .flatten()
            .map(|token| (token.side, token.token_id))
            .collect()
    }
}

#[cfg(test)]
mod channel_tests {
    use super::*;
    use chrono::{TimeZone, Utc};

    #[tokio::test]
    async fn hot_path_event_sink_queues_chainlink_and_orderbook_events() {
        let (sink, mut receiver) = HotPathEventSink::channel(4);
        let ts = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        sink.try_send(HotPathEvent::ChainlinkPrice {
            symbol: "BTC/USD".to_owned(),
            event_ts: ts,
            observed_ts: ts,
        })
        .unwrap();
        sink.try_send(HotPathEvent::OrderBookTopOfBook {
            token_id: "up-token".to_owned(),
            event_ts: ts,
            observed_ts: ts,
        })
        .unwrap();

        assert!(matches!(
            receiver.recv().await.unwrap(),
            HotPathEvent::ChainlinkPrice { .. }
        ));
        assert!(matches!(
            receiver.recv().await.unwrap(),
            HotPathEvent::OrderBookTopOfBook { .. }
        ));
    }
}

#[cfg(test)]
mod telemetry_tests {
    use super::*;

    #[test]
    fn hot_decision_telemetry_counts_states_and_drops() {
        let telemetry = HotDecisionTelemetry::default();
        let ts = Utc::now();

        telemetry.record_state_built(ts, 900);
        telemetry.record_state_persist_queued();
        telemetry.record_dropped_event();

        let snapshot = telemetry.snapshot();
        assert_eq!(snapshot.states_built, 1);
        assert_eq!(snapshot.states_persist_queued, 1);
        assert_eq!(snapshot.dropped_events, 1);
        assert_eq!(snapshot.last_observed_to_state_us, Some(900));
    }

    #[test]
    fn hot_decision_telemetry_records_persist_result_with_one_call() {
        let telemetry = HotDecisionTelemetry::default();
        let ts = Utc::now();

        telemetry.record_state_persist_result(ts, 700, true);
        telemetry.record_state_persist_result(ts, 900, false);

        let snapshot = telemetry.snapshot();
        assert_eq!(snapshot.states_built, 2);
        assert_eq!(snapshot.states_persist_queued, 1);
        assert_eq!(snapshot.dropped_events, 1);
        assert_eq!(snapshot.last_observed_to_state_us, Some(900));
    }
}
