use anyhow::Result;
use chrono::{DateTime, Utc};
use polymarket_runtime_types::{
    ContractSide, HOT_DECISION_STATE_SCHEMA_VERSION, HotDecisionLatency, HotDecisionQualityFlag,
    HotDecisionState, HotDecisionTriggerKind, NormalizedOrderBook, NormalizedPriceTick,
    WarmedContract,
};
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
}

impl HotPathEventSink {
    pub fn channel(buffer_size: usize) -> (Self, mpsc::Receiver<HotPathEvent>) {
        let (sender, receiver) = mpsc::channel(buffer_size.max(1));
        (Self { sender }, receiver)
    }

    pub fn try_send(&self, event: HotPathEvent) -> Result<()> {
        self.sender.try_send(event)?;
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HotDecisionConfig {
    pub stale_source_after_ms: i64,
    pub stale_orderbook_after_ms: i64,
}

impl Default for HotDecisionConfig {
    fn default() -> Self {
        Self {
            stale_source_after_ms: 30_000,
            stale_orderbook_after_ms: 30_000,
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
        let mut states = Vec::new();

        for contract in warmed_contracts
            .iter()
            .filter(|contract| is_current_window(contract, asof_ts))
        {
            for (side, token_id) in impacted_tokens(event, contract) {
                let threshold = latest_price_at_or_before(
                    chainlink_history,
                    &contract.window.asset,
                    contract.window.start_ts,
                    asof_ts,
                );
                let settlement = latest_price_at_or_before(
                    chainlink_history,
                    &contract.window.asset,
                    asof_ts,
                    asof_ts,
                );
                let book = latest_orderbook_at_or_before(orderbooks, &token_id, asof_ts);
                let mut flags = Vec::new();

                if threshold.is_none() {
                    flags.push(HotDecisionQualityFlag::MissingThreshold);
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
                    state_id: state_id(contract, &side, asof_ts),
                    trigger_kind: event.trigger_kind(),
                    trigger_symbol: event.trigger_symbol(),
                    trigger_token_id: event.trigger_token_id(),
                    asof_ts,
                    contract: contract.clone(),
                    side,
                    token_id,
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

fn impacted_tokens(event: &HotPathEvent, contract: &WarmedContract) -> Vec<(ContractSide, String)> {
    match event {
        HotPathEvent::ChainlinkPrice { symbol, .. } => {
            if symbol_matches_asset(symbol, &contract.window.asset) {
                vec![
                    (ContractSide::Up, contract.up.token_id.clone()),
                    (ContractSide::Down, contract.down.token_id.clone()),
                ]
            } else {
                vec![]
            }
        }
        HotPathEvent::OrderBookTopOfBook { token_id, .. } => {
            if token_id == &contract.up.token_id {
                vec![(ContractSide::Up, contract.up.token_id.clone())]
            } else if token_id == &contract.down.token_id {
                vec![(ContractSide::Down, contract.down.token_id.clone())]
            } else {
                vec![]
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

fn state_id(contract: &WarmedContract, side: &ContractSide, asof_ts: DateTime<Utc>) -> String {
    format!(
        "{}:{}:{}",
        contract.window.slug(),
        side_label(side),
        asof_ts.to_rfc3339()
    )
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
}
