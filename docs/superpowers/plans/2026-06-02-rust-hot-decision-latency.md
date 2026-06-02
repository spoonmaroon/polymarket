# Rust Hot Decision Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move pre-probability decision-state construction into the Rust state-manager hot path and measure the remaining hypothetical order-submit latency before deciding whether a VPS is required.

**Architecture:** Keep Chainlink and CLOB collection on WebSockets, update in-memory Rust state first, emit a hot-path event, build exact current `DecisionState` equivalents in Rust, persist them asynchronously, and expose latency marks in the existing state-manager report. DuckDB/Python remain replay and research layers; they must not sit on the live decision path.

**Tech Stack:** Rust 2024, Tokio, `chrono`, `rust_decimal`, `serde`, `serde_json`, `reqwest`, existing Polymarket Rust SDK, existing Python verification scripts for deployed status checks.

---

## File Structure

- Create `rust/crates/polymarket-runtime-types/src/decision.rs`
  - Owns serializable Rust hot-decision types shared by report, journal, and future model code.
- Modify `rust/crates/polymarket-runtime-types/src/lib.rs`
  - Re-export hot-decision types.
- Create `rust/crates/polymarket-live-probe/src/hot_decision.rs`
  - Owns pure in-memory hot decision builder, event channel, telemetry, and worker loop.
- Modify `rust/crates/polymarket-live-probe/src/prices.rs`
  - Keeps a bounded in-memory Chainlink price history ring, not just the latest tick, so start-price thresholds remain available after the window starts.
- Create `rust/crates/polymarket-live-probe/src/decision_journal.rs`
  - Owns async append-only JSONL persistence for hot decision states.
- Create `rust/crates/polymarket-live-probe/src/order_latency_probe.rs`
  - Owns no-auth hypothetical submit latency measurement. It must not place orders or load private keys.
- Modify `rust/crates/polymarket-live-probe/src/state_manager.rs`
  - Shares warmed contracts with the hot worker and starts/stops the worker.
- Modify `rust/crates/polymarket-live-probe/src/clob_ws.rs`
  - Emits hot events after in-memory orderbook updates.
- Modify `rust/crates/polymarket-live-probe/src/prices.rs`
  - Emits hot events after in-memory Chainlink price updates.
- Modify `rust/crates/polymarket-live-probe/src/report.rs`
  - Adds hot-decision telemetry to `status.json`.
- Modify `rust/crates/polymarket-live-probe/src/main.rs`
  - Adds CLI flags for decision-state journaling and a separate latency probe mode.
- Modify `scripts/verify_state_manager_report.py`
  - Verifies hot-decision telemetry fields exist when enabled.
- Modify `docs/PART_TWO_LIVE_COLLECTORS.md`
  - Documents the hot path and explicitly says DuckDB/Python are not live decision dependencies.
- Modify `docs/SPOON_DEPLOYMENT.md`
  - Adds Spoon command flags and post-deploy checks.
- Modify `tests/docs/test_active_runtime_docs.py`
  - Locks the docs against regressing to file-polling/Python hot decisions.

## Scope Boundary

This plan does not add probability outputs, trading, private keys, real order placement, or Polymarket authenticated order signing. It builds a fast, read-only, pre-probability decision-state lane and a no-auth latency probe so we can decide whether hosting is the remaining bottleneck.

---

### Task 1: Add Shared Rust Hot Decision Types

**Files:**
- Create: `rust/crates/polymarket-runtime-types/src/decision.rs`
- Modify: `rust/crates/polymarket-runtime-types/src/lib.rs`

- [ ] **Step 1: Write the failing runtime type tests**

Add this file:

```rust
// rust/crates/polymarket-runtime-types/src/decision.rs
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
    use crate::{ContractToken, ContractWindow};
    use chrono::{Duration, TimeZone};

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
```

Modify exports:

```rust
// rust/crates/polymarket-runtime-types/src/lib.rs
mod contract;
mod decision;
mod orderbook;
mod price;
mod probe;
mod state;

pub use contract::{ContractSide, ContractToken, ContractWindow, WarmedContract};
pub use decision::{
    HOT_DECISION_STATE_SCHEMA_VERSION, HotDecisionLatency, HotDecisionQualityFlag,
    HotDecisionState, HotDecisionTriggerKind,
};
pub use orderbook::{BookLevel, NormalizedOrderBook, OrderBookMeta};
pub use price::{NormalizedPriceTick, PriceDisagreement};
pub use probe::{LatencyMark, ProbeReport};
pub use state::{FeedFreshness, WarmStateSnapshot};
```

- [ ] **Step 2: Run the focused failing test**

Run:

```bash
cd rust
cargo test -p polymarket-runtime-types hot_decision_state_serializes_schema_and_flags
```

Expected: pass if the new file and exports are correct. If it fails with `unresolved import`, fix `src/lib.rs` exactly as shown above.

- [ ] **Step 3: Commit**

```bash
git add rust/crates/polymarket-runtime-types/src/decision.rs rust/crates/polymarket-runtime-types/src/lib.rs
git commit -m "Add Rust hot decision state types"
```

---

### Task 2A: Add Bounded In-Memory Chainlink Price History

**Files:**
- Modify: `rust/crates/polymarket-live-probe/src/prices.rs`

- [ ] **Step 1: Write the failing history test**

Add a test proving `LatestPrices` keeps enough as-of history to recover a start-price threshold:

```rust
#[tokio::test]
async fn latest_prices_keeps_bounded_history_for_start_thresholds() {
    let latest = LatestPrices::with_history_limit(3);
    let t0 = ts(0);
    latest.update(chainlink_tick("BTC/USD", t0, Decimal::new(70_000, 0))).await;
    latest.update(chainlink_tick("BTC/USD", ts(1), Decimal::new(70_100, 0))).await;
    latest.update(chainlink_tick("BTC/USD", ts(2), Decimal::new(70_200, 0))).await;
    latest.update(chainlink_tick("BTC/USD", ts(3), Decimal::new(70_300, 0))).await;

    let history = latest.history_snapshot().await;

    assert_eq!(history.len(), 3);
    assert_eq!(history[0].price, Decimal::new(70_100, 0));
    assert_eq!(
        latest
            .latest_at_or_before("BTC/USD", ts(2), ts(2))
            .await
            .unwrap()
            .price,
        Decimal::new(70_200, 0)
    );
}
```

- [ ] **Step 2: Run the focused failing test**

Run:

```bash
cd rust
cargo test -p polymarket-live-probe latest_prices_keeps_bounded_history_for_start_thresholds
```

Expected: FAIL because `with_history_limit`, `history_snapshot`, and `latest_at_or_before` do not exist yet.

- [ ] **Step 3: Implement the minimal bounded history**

Change `LatestPrices` to keep `latest` plus a bounded `history` vector. Every `update()` inserts into latest and appends to history, sorts by `(symbol, event_ts, observed_ts)`, and trims the oldest rows over the configured limit.

- [ ] **Step 4: Run the focused test**

Run:

```bash
cd rust
cargo test -p polymarket-live-probe latest_prices_keeps_bounded_history_for_start_thresholds
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/prices.rs
git commit -m "Keep Chainlink price history in Rust memory"
```

---

### Task 2B: Build Pure Hot Decision States In Memory

**Files:**
- Create: `rust/crates/polymarket-live-probe/src/hot_decision.rs`
- Modify: `rust/crates/polymarket-live-probe/src/main.rs`

- [ ] **Step 1: Add the module declaration**

In `rust/crates/polymarket-live-probe/src/main.rs`, add the module beside the existing modules:

```rust
mod hot_decision;
```

- [ ] **Step 2: Create the failing builder test and implementation shell**

Create `rust/crates/polymarket-live-probe/src/hot_decision.rs` with this complete content:

```rust
use anyhow::{Result, anyhow};
use chrono::{DateTime, Utc};
use polymarket_runtime_types::{
    ContractSide, HOT_DECISION_STATE_SCHEMA_VERSION, HotDecisionLatency,
    HotDecisionQualityFlag, HotDecisionState, HotDecisionTriggerKind, NormalizedOrderBook,
    NormalizedPriceTick, WarmedContract,
};
use rust_decimal::Decimal;
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
    pub fn observed_ts(&self) -> DateTime<Utc> {
        match self {
            Self::ChainlinkPrice { observed_ts, .. } => *observed_ts,
            Self::OrderBookTopOfBook { observed_ts, .. } => *observed_ts,
        }
    }

    pub fn event_ts(&self) -> DateTime<Utc> {
        match self {
            Self::ChainlinkPrice { event_ts, .. } => *event_ts,
            Self::OrderBookTopOfBook { event_ts, .. } => *event_ts,
        }
    }
}

#[derive(Clone)]
pub struct HotPathEventSink {
    sender: mpsc::Sender<HotPathEvent>,
}

impl HotPathEventSink {
    pub fn channel(buffer_size: usize) -> (Self, mpsc::Receiver<HotPathEvent>) {
        let (sender, receiver) = mpsc::channel(buffer_size);
        (Self { sender }, receiver)
    }

    pub fn try_send(&self, event: HotPathEvent) -> Result<()> {
        self.sender
            .try_send(event)
            .map_err(|error| anyhow!("hot decision event queue unavailable: {error}"))
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
        chainlink_prices: &[NormalizedPriceTick],
        orderbooks: &[NormalizedOrderBook],
        asof_ts: DateTime<Utc>,
    ) -> Vec<HotDecisionState> {
        let started = Instant::now();
        let current_contracts = warmed_contracts
            .iter()
            .filter(|contract| contract.window.start_ts <= asof_ts && contract.window.end_ts > asof_ts)
            .collect::<Vec<_>>();
        let impacted = current_contracts
            .into_iter()
            .flat_map(|contract| impacted_sides(event, contract).into_iter().map(move |side| (contract, side)))
            .collect::<Vec<_>>();

        impacted
            .into_iter()
            .map(|(contract, side)| {
                self.build_one(
                    event,
                    contract,
                    side,
                    chainlink_prices,
                    orderbooks,
                    asof_ts,
                    started.elapsed().as_micros(),
                )
            })
            .collect()
    }

    fn build_one(
        &self,
        event: &HotPathEvent,
        contract: &WarmedContract,
        side: ContractSide,
        chainlink_prices: &[NormalizedPriceTick],
        orderbooks: &[NormalizedOrderBook],
        asof_ts: DateTime<Utc>,
        observed_to_state_us: u128,
    ) -> HotDecisionState {
        let symbol = format!("{}/USD", contract.window.asset);
        let threshold = latest_price_at_or_before(
            chainlink_prices,
            &symbol,
            contract.window.start_ts,
            asof_ts,
        );
        let settlement = latest_price_at_or_before(chainlink_prices, &symbol, asof_ts, asof_ts);
        let token_id = match side {
            ContractSide::Up => contract.up.token_id.clone(),
            ContractSide::Down => contract.down.token_id.clone(),
        };
        let book = latest_book_for_token(orderbooks, &token_id, asof_ts);
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
        if let Some(book) = book {
            if book.best_bid.is_none() || book.best_ask.is_none() {
                flags.push(HotDecisionQualityFlag::IncompleteOrderbook);
            }
        }
        let source_age_ms = settlement.map(|tick| age_ms(asof_ts, tick.event_ts));
        let book_age_ms = book.map(|book| age_ms(asof_ts, book.event_ts));
        if source_age_ms.is_some_and(|age| age < 0 || age > self.config.stale_source_after_ms) {
            flags.push(HotDecisionQualityFlag::StaleSource);
        }
        if book_age_ms.is_some_and(|age| age < 0 || age > self.config.stale_orderbook_after_ms) {
            flags.push(HotDecisionQualityFlag::StaleOrderbook);
        }
        let trigger_kind = match event {
            HotPathEvent::ChainlinkPrice { .. } => HotDecisionTriggerKind::ChainlinkPrice,
            HotPathEvent::OrderBookTopOfBook { .. } => HotDecisionTriggerKind::OrderBookTopOfBook,
        };
        let trigger_symbol = match event {
            HotPathEvent::ChainlinkPrice { symbol, .. } => Some(symbol.clone()),
            HotPathEvent::OrderBookTopOfBook { .. } => None,
        };
        let trigger_token_id = match event {
            HotPathEvent::ChainlinkPrice { .. } => None,
            HotPathEvent::OrderBookTopOfBook { token_id, .. } => Some(token_id.clone()),
        };
        HotDecisionState {
            schema_version: HOT_DECISION_STATE_SCHEMA_VERSION.to_owned(),
            state_id: format!("{}:{side:?}:{}", contract.window.slug(), asof_ts.to_rfc3339()),
            trigger_kind,
            trigger_symbol,
            trigger_token_id,
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
                observed_to_state_us,
                state_to_persist_us: None,
                total_event_to_persist_ms: None,
            },
        }
    }
}

fn impacted_sides(event: &HotPathEvent, contract: &WarmedContract) -> Vec<ContractSide> {
    match event {
        HotPathEvent::ChainlinkPrice { symbol, .. } => {
            if symbol_asset(symbol) == contract.window.asset {
                vec![ContractSide::Up, ContractSide::Down]
            } else {
                vec![]
            }
        }
        HotPathEvent::OrderBookTopOfBook { token_id, .. } => {
            if token_id == &contract.up.token_id {
                vec![ContractSide::Up]
            } else if token_id == &contract.down.token_id {
                vec![ContractSide::Down]
            } else {
                vec![]
            }
        }
    }
}

fn latest_price_at_or_before<'a>(
    prices: &'a [NormalizedPriceTick],
    symbol: &str,
    event_ts_lte: DateTime<Utc>,
    observed_ts_lte: DateTime<Utc>,
) -> Option<&'a NormalizedPriceTick> {
    prices
        .iter()
        .filter(|tick| tick.source_key == "polymarket_rtds_chainlink")
        .filter(|tick| tick.symbol.eq_ignore_ascii_case(symbol))
        .filter(|tick| tick.event_ts <= event_ts_lte && tick.observed_ts <= observed_ts_lte)
        .max_by_key(|tick| (tick.event_ts, tick.observed_ts))
}

fn latest_book_for_token<'a>(
    books: &'a [NormalizedOrderBook],
    token_id: &str,
    asof_ts: DateTime<Utc>,
) -> Option<&'a NormalizedOrderBook> {
    books
        .iter()
        .filter(|book| book.token_id == token_id)
        .filter(|book| book.event_ts <= asof_ts && book.observed_ts <= asof_ts)
        .max_by_key(|book| (book.event_ts, book.observed_ts))
}

fn symbol_asset(symbol: &str) -> String {
    symbol.to_ascii_uppercase().replace('-', "/").split('/').next().unwrap_or("").to_owned()
}

fn age_ms(later: DateTime<Utc>, earlier: DateTime<Utc>) -> i64 {
    later.signed_duration_since(earlier).num_milliseconds()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Duration, TimeZone};
    use polymarket_runtime_types::{
        BookLevel, ContractToken, ContractWindow, NormalizedOrderBook, NormalizedPriceTick,
        OrderBookMeta,
    };

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
        let books = vec![book("up-token", asof - Duration::milliseconds(80), asof, 61, 64)];
        let event = HotPathEvent::OrderBookTopOfBook {
            token_id: "up-token".to_owned(),
            event_ts: asof - Duration::milliseconds(80),
            observed_ts: asof,
        };

        let states = HotDecisionBuilder::new(HotDecisionConfig::default())
            .build_for_event(&event, &[contract], &prices, &books, asof);

        assert_eq!(states.len(), 1);
        assert_eq!(states[0].side, ContractSide::Up);
        assert_eq!(states[0].threshold_price, Some(Decimal::new(70_000, 0)));
        assert_eq!(states[0].settlement_price, Some(Decimal::new(70_050, 0)));
        assert_eq!(states[0].best_ask, Some(Decimal::new(64, 2)));
        assert_eq!(states[0].data_quality_flags, Vec::<HotDecisionQualityFlag>::new());
        assert_eq!(states[0].latency.trigger_event_to_observed_ms, 80);
    }

    fn price(symbol: &str, event_ts: DateTime<Utc>, observed_ts: DateTime<Utc>, value: i64) -> NormalizedPriceTick {
        NormalizedPriceTick {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            symbol: symbol.to_owned(),
            event_ts,
            observed_ts,
            price: Decimal::new(value, 0),
        }
    }

    fn book(token_id: &str, event_ts: DateTime<Utc>, observed_ts: DateTime<Utc>, bid: i64, ask: i64) -> NormalizedOrderBook {
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
            vec![BookLevel { price: Decimal::new(bid, 2), size: Decimal::new(10, 0) }],
            vec![BookLevel { price: Decimal::new(ask, 2), size: Decimal::new(11, 0) }],
        )
    }
}
```

- [ ] **Step 3: Run the focused test**

Run:

```bash
cd rust
cargo test -p polymarket-live-probe builds_current_hot_decision_for_orderbook_event_without_python_or_duckdb
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/main.rs rust/crates/polymarket-live-probe/src/hot_decision.rs
git commit -m "Build hot decisions in Rust memory"
```

---

### Task 3: Persist Hot Decisions Asynchronously

**Files:**
- Create: `rust/crates/polymarket-live-probe/src/decision_journal.rs`
- Modify: `rust/crates/polymarket-live-probe/src/main.rs`

- [ ] **Step 1: Add the module declaration**

In `rust/crates/polymarket-live-probe/src/main.rs`, add:

```rust
mod decision_journal;
```

- [ ] **Step 2: Add the async journal with tests**

Create `rust/crates/polymarket-live-probe/src/decision_journal.rs`:

```rust
use anyhow::{Result, anyhow};
use chrono::{Datelike, Timelike};
use polymarket_runtime_types::HotDecisionState;
use std::io::Write;
use std::path::PathBuf;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

#[derive(Clone)]
pub struct HotDecisionJournal {
    root: PathBuf,
}

impl HotDecisionJournal {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn append(&self, state: &HotDecisionState) -> Result<PathBuf> {
        let path = self.partition_path(state);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)?;
        serde_json::to_writer(&mut file, state)?;
        file.write_all(b"\n")?;
        Ok(path)
    }

    fn partition_path(&self, state: &HotDecisionState) -> PathBuf {
        let ts = state.asof_ts;
        self.root
            .join("polymarket_decision_state")
            .join("hot_state")
            .join(format!("date={:04}-{:02}-{:02}", ts.year(), ts.month(), ts.day()))
            .join(format!("hour={:02}", ts.hour()))
            .join("decision-state.jsonl")
    }
}

#[derive(Clone)]
pub struct HotDecisionSink {
    sender: mpsc::Sender<HotDecisionState>,
}

impl HotDecisionSink {
    pub fn start(root: PathBuf, buffer_size: usize) -> (Self, JoinHandle<Result<()>>) {
        let (sender, mut receiver) = mpsc::channel(buffer_size);
        let journal = HotDecisionJournal::new(root);
        let handle = tokio::spawn(async move {
            while let Some(state) = receiver.recv().await {
                journal.append(&state)?;
            }
            Ok(())
        });
        (Self { sender }, handle)
    }

    pub fn try_record(&self, state: HotDecisionState) -> Result<()> {
        self.sender
            .try_send(state)
            .map_err(|error| anyhow!("hot decision journal queue unavailable: {error}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Duration, TimeZone, Utc};
    use polymarket_runtime_types::{
        ContractSide, ContractToken, ContractWindow, HOT_DECISION_STATE_SCHEMA_VERSION,
        HotDecisionLatency, HotDecisionQualityFlag, HotDecisionTriggerKind, WarmedContract,
    };

    #[tokio::test]
    async fn sink_records_hot_decision_without_writing_on_hot_path() {
        let root = std::env::temp_dir().join(format!(
            "polymarket-hot-decision-journal-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let (sink, handle) = HotDecisionSink::start(root.clone(), 16);
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        sink.try_record(sample_state(start)).unwrap();
        drop(sink);
        handle.await.unwrap().unwrap();

        let path = root
            .join("polymarket_decision_state")
            .join("hot_state")
            .join("date=2026-06-01")
            .join("hour=08")
            .join("decision-state.jsonl");
        let row: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        assert_eq!(row["schema_version"], HOT_DECISION_STATE_SCHEMA_VERSION);
        assert_eq!(row["data_quality_flags"], serde_json::json!([]));
    }

    fn sample_state(start: chrono::DateTime<Utc>) -> polymarket_runtime_types::HotDecisionState {
        let contract = WarmedContract::new(
            ContractWindow::new("BTC", "5m", start, start + Duration::seconds(300)).unwrap(),
            ContractToken::new("BTC", ContractSide::Up, "up-token"),
            ContractToken::new("BTC", ContractSide::Down, "down-token"),
        )
        .unwrap();
        polymarket_runtime_types::HotDecisionState {
            schema_version: HOT_DECISION_STATE_SCHEMA_VERSION.to_owned(),
            state_id: "state-1".to_owned(),
            trigger_kind: HotDecisionTriggerKind::OrderBookTopOfBook,
            trigger_symbol: None,
            trigger_token_id: Some("up-token".to_owned()),
            asof_ts: start,
            contract,
            side: ContractSide::Up,
            token_id: "up-token".to_owned(),
            threshold_price: None,
            threshold_event_ts: None,
            settlement_price: None,
            settlement_event_ts: None,
            best_bid: None,
            best_ask: None,
            executable_price: None,
            spread: None,
            source_age_ms: None,
            book_age_ms: None,
            data_quality_flags: Vec::<HotDecisionQualityFlag>::new(),
            latency: HotDecisionLatency {
                trigger_event_to_observed_ms: 10,
                observed_to_state_us: 20,
                state_to_persist_us: None,
                total_event_to_persist_ms: None,
            },
        }
    }
}
```

- [ ] **Step 3: Run the focused test**

Run:

```bash
cd rust
cargo test -p polymarket-live-probe sink_records_hot_decision_without_writing_on_hot_path
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/main.rs rust/crates/polymarket-live-probe/src/decision_journal.rs
git commit -m "Persist hot decisions asynchronously"
```

---

### Task 4: Wire WebSocket Events Into the Hot Decision Worker

**Files:**
- Modify: `rust/crates/polymarket-live-probe/src/prices.rs`
- Modify: `rust/crates/polymarket-live-probe/src/clob_ws.rs`
- Modify: `rust/crates/polymarket-live-probe/src/state_manager.rs`

- [ ] **Step 1: Write the event-sink tests**

In `rust/crates/polymarket-live-probe/src/hot_decision.rs`, add:

```rust
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
```

- [ ] **Step 2: Run the failing event test**

Run:

```bash
cd rust
cargo test -p polymarket-live-probe hot_path_event_sink_queues_chainlink_and_orderbook_events
```

Expected: PASS because Task 2 already added the sink. If it fails, fix `HotPathEventSink::channel` and `try_send` in `hot_decision.rs`.

- [ ] **Step 3: Add hot-event sink fields to CLOB WebSocket manager**

Modify imports and struct in `rust/crates/polymarket-live-probe/src/clob_ws.rs`:

```rust
use crate::hot_decision::{HotPathEvent, HotPathEventSink};
```

Add this field:

```rust
hot_event_sink: Option<HotPathEventSink>,
```

Change constructors to keep the existing API and add the new sink:

```rust
pub fn new(book_state: LiveBookState) -> Self {
    Self::new_with_raw_events(book_state, None, None)
}

pub fn new_with_raw_events(
    book_state: LiveBookState,
    raw_event_sink: Option<RawEventSink>,
    hot_event_sink: Option<HotPathEventSink>,
) -> Self {
    let client = ClobWsClient::default();
    let telemetry = Arc::new(RwLock::new(WebSocketTelemetry::default()));
    Self {
        connection_monitor_task: tokio::spawn(monitor_market_connection_state(
            client.clone(),
            telemetry.clone(),
        )),
        client,
        book_state,
        token_streams: BTreeMap::new(),
        telemetry,
        raw_event_sink,
        hot_event_sink,
    }
}
```

Inside `update_tokens`, clone and emit the hot event only after `apply_clob_market_event` returns `true`:

```rust
let hot_event_sink = self.hot_event_sink.clone();
```

Inside the spawned loop, after `let applied = ...;`:

```rust
if applied {
    if let (
        Some(sink),
        ClobMarketEvent::TopOfBook {
            token_id,
            event_ts,
            observed_ts,
            ..
        },
    ) = (hot_event_sink.as_ref(), &event)
    {
        sink.try_send(HotPathEvent::OrderBookTopOfBook {
            token_id: token_id.clone(),
            event_ts: *event_ts,
            observed_ts: *observed_ts,
        })?;
    }
} else {
    tracing::warn!("received CLOB best_bid_ask for unseeded orderbook");
}
```

- [ ] **Step 4: Add hot-event sink fields to Chainlink stream manager**

Modify imports in `rust/crates/polymarket-live-probe/src/prices.rs`:

```rust
use crate::hot_decision::{HotPathEvent, HotPathEventSink};
```

Thread an `Option<HotPathEventSink>` through `ChainlinkStreamManager::start_with_raw_events`, `run_chainlink_symbols_stream_with_client`, and tests. In the loop, after `latest.update(tick.clone()).await` has happened through `update_latest_from_chainlink_message`, emit:

```rust
if let Some(sink) = &hot_event_sink {
    sink.try_send(HotPathEvent::ChainlinkPrice {
        symbol: tick.symbol.clone(),
        event_ts: tick.event_ts,
        observed_ts: tick.observed_ts,
    })?;
}
```

- [ ] **Step 5: Share warmed contracts and start the worker**

In `rust/crates/polymarket-live-probe/src/state_manager.rs`, add imports:

```rust
use crate::decision_journal::HotDecisionSink;
use crate::hot_decision::{HotDecisionBuilder, HotDecisionConfig, HotPathEventSink};
use std::sync::{Arc, RwLock};
use tokio::task::JoinHandle;
```

Add a shared type alias:

```rust
type SharedWarmedContracts = Arc<RwLock<Vec<WarmedContract>>>;
```

Change `StateManagerRuntime` fields:

```rust
warmed: SharedWarmedContracts,
hot_decision_worker: Option<JoinHandle<Result<()>>>,
```

Add a new constructor while preserving the existing public constructor:

```rust
pub async fn start_with_raw_events(
    config: StateManagerConfig,
    raw_event_sink: Option<RawEventSink>,
) -> Result<Self> {
    Self::start_with_raw_events_and_hot_decisions(config, raw_event_sink, None).await
}

pub async fn start_with_raw_events_and_hot_decisions(
    config: StateManagerConfig,
    raw_event_sink: Option<RawEventSink>,
    decision_sink: Option<HotDecisionSink>,
) -> Result<Self> {
    let latest_prices = prices::LatestPrices::default();
    let book_state = LiveBookState::default();
    let warmed = Arc::new(RwLock::new(Vec::new()));
    let (hot_event_sink, hot_event_receiver) = HotPathEventSink::channel(16_384);
    let hot_decision_worker = decision_sink.map(|sink| {
        start_hot_decision_worker(
            HotDecisionConfig::default(),
            latest_prices.clone(),
            book_state.clone(),
            warmed.clone(),
            hot_event_receiver,
            sink,
        )
    });
    let hot_event_sink = hot_decision_worker.as_ref().map(|_| hot_event_sink);
    let chainlink_streams = prices::ChainlinkStreamManager::start_with_raw_events(
        chainlink_symbols_for_assets(&config.assets),
        latest_prices.clone(),
        raw_event_sink.clone(),
        hot_event_sink.clone(),
    );
    let mut runtime = Self {
        config,
        latest_prices,
        orderbook_streams: clob_ws::BestBidAskStreamManager::new_with_raw_events(
            book_state.clone(),
            raw_event_sink,
            hot_event_sink,
        ),
        book_state,
        warmed,
        token_ids: Vec::new(),
        last_refresh: Utc::now(),
        chainlink_streams,
        hot_decision_worker,
    };
    runtime.refresh_contracts().await?;
    Ok(runtime)
}
```

Add the worker function:

```rust
fn start_hot_decision_worker(
    config: HotDecisionConfig,
    latest_prices: prices::LatestPrices,
    book_state: LiveBookState,
    warmed: SharedWarmedContracts,
    mut receiver: tokio::sync::mpsc::Receiver<crate::hot_decision::HotPathEvent>,
    decision_sink: HotDecisionSink,
) -> JoinHandle<Result<()>> {
    tokio::spawn(async move {
        let builder = HotDecisionBuilder::new(config);
        while let Some(event) = receiver.recv().await {
            let asof_ts = Utc::now();
            let warmed_snapshot = warmed.read().expect("warmed contracts lock poisoned").clone();
            let prices = latest_prices.snapshot().await;
            let orderbooks = orderbooks_for_warmed(book_state.snapshot().await, &warmed_snapshot);
            for state in builder.build_for_event(
                &event,
                &warmed_snapshot,
                &prices,
                &orderbooks,
                asof_ts,
            ) {
                decision_sink.try_record(state)?;
            }
        }
        Ok(())
    })
}
```

Update `refresh_contracts`:

```rust
*self.warmed.write().expect("warmed contracts lock poisoned") = warmed;
```

Update `snapshot`, `subscriptions`, `needs_contract_refresh`, and any direct `self.warmed.iter()` call to first clone:

```rust
let warmed = self.warmed.read().expect("warmed contracts lock poisoned").clone();
```

Use `&warmed` in existing helper calls.

- [ ] **Step 6: Run focused Rust tests**

Run:

```bash
cd rust
cargo test -p polymarket-live-probe state_manager clob_ws prices hot_decision
```

Expected: all targeted tests pass. If constructor signatures break old tests, update test calls to pass `None` for the new hot-event sink where the old raw-event sink is already passed.

- [ ] **Step 7: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/prices.rs rust/crates/polymarket-live-probe/src/clob_ws.rs rust/crates/polymarket-live-probe/src/state_manager.rs rust/crates/polymarket-live-probe/src/hot_decision.rs
git commit -m "Emit hot decisions from websocket events"
```

---

### Task 5: Add Status Telemetry For Hot Decisions

**Files:**
- Modify: `rust/crates/polymarket-live-probe/src/hot_decision.rs`
- Modify: `rust/crates/polymarket-live-probe/src/report.rs`
- Modify: `scripts/verify_state_manager_report.py`

- [ ] **Step 1: Add telemetry structs and tests**

In `rust/crates/polymarket-live-probe/src/hot_decision.rs`, add:

```rust
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize)]
pub struct HotDecisionTelemetrySnapshot {
    pub states_built: u64,
    pub states_persist_queued: u64,
    pub dropped_events: u64,
    pub last_state_age_ms: Option<i64>,
    pub last_observed_to_state_us: Option<u128>,
}

#[derive(Clone, Default)]
pub struct HotDecisionTelemetry {
    inner: std::sync::Arc<std::sync::RwLock<HotDecisionTelemetrySnapshot>>,
}

impl HotDecisionTelemetry {
    pub fn record_state_built(&self, asof_ts: DateTime<Utc>, observed_to_state_us: u128) {
        let mut inner = self.inner.write().expect("hot decision telemetry lock poisoned");
        inner.states_built = inner.states_built.saturating_add(1);
        inner.states_persist_queued = inner.states_persist_queued.saturating_add(1);
        inner.last_state_age_ms = Some(Utc::now().signed_duration_since(asof_ts).num_milliseconds());
        inner.last_observed_to_state_us = Some(observed_to_state_us);
    }

    pub fn record_dropped_event(&self) {
        let mut inner = self.inner.write().expect("hot decision telemetry lock poisoned");
        inner.dropped_events = inner.dropped_events.saturating_add(1);
    }

    pub fn snapshot(&self) -> HotDecisionTelemetrySnapshot {
        self.inner.read().expect("hot decision telemetry lock poisoned").clone()
    }
}
```

Add test:

```rust
#[test]
fn hot_decision_telemetry_counts_states_and_drops() {
    let telemetry = HotDecisionTelemetry::default();
    let ts = Utc::now();
    telemetry.record_state_built(ts, 900);
    telemetry.record_dropped_event();
    let snapshot = telemetry.snapshot();
    assert_eq!(snapshot.states_built, 1);
    assert_eq!(snapshot.states_persist_queued, 1);
    assert_eq!(snapshot.dropped_events, 1);
    assert_eq!(snapshot.last_observed_to_state_us, Some(900));
}
```

- [ ] **Step 2: Add telemetry to reports**

In `rust/crates/polymarket-live-probe/src/report.rs`, import:

```rust
use crate::hot_decision::HotDecisionTelemetrySnapshot;
```

Add to `StateManagerReportInput`:

```rust
pub hot_decision_telemetry: Option<HotDecisionTelemetrySnapshot>,
```

Add to `StateManagerReport`:

```rust
pub hot_decision_telemetry: Option<HotDecisionTelemetrySnapshot>,
```

Set it in `build_state_manager_report`:

```rust
hot_decision_telemetry: input.hot_decision_telemetry,
```

Update report tests so every `StateManagerReportInput` literal includes:

```rust
hot_decision_telemetry: None,
```

Add a test:

```rust
#[test]
fn state_manager_report_includes_hot_decision_telemetry_when_enabled() {
    let snapshot = WarmStateSnapshot {
        observed_ts: Utc.timestamp_opt(1_780_302_400, 0).unwrap(),
        current: vec![],
        next: vec![],
        next_next: vec![],
        chainlink_prices: vec![],
        proxy_prices: vec![],
        orderbooks: vec![],
        freshness: vec![],
        health_flags: vec![],
    };
    let report = build_state_manager_report(StateManagerReportInput {
        elapsed_ms: 1,
        snapshot,
        subscriptions: vec![],
        websocket_status: vec![],
        hot_decision_telemetry: Some(HotDecisionTelemetrySnapshot {
            states_built: 2,
            states_persist_queued: 2,
            dropped_events: 0,
            last_state_age_ms: Some(3),
            last_observed_to_state_us: Some(700),
        }),
    });

    assert_eq!(
        report.hot_decision_telemetry.unwrap().last_observed_to_state_us,
        Some(700)
    );
}
```

- [ ] **Step 3: Verify report schema in Python script**

In `scripts/verify_state_manager_report.py`, after latency mark validation, add:

```python
telemetry = payload.get("hot_decision_telemetry")
if telemetry is not None:
    for key in (
        "states_built",
        "states_persist_queued",
        "dropped_events",
        "last_state_age_ms",
        "last_observed_to_state_us",
    ):
        if key not in telemetry:
            fail(f"hot_decision_telemetry missing {key}")
    if telemetry["states_built"] < telemetry["states_persist_queued"]:
        fail("hot_decision_telemetry states_built is less than states_persist_queued")
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd rust
cargo test -p polymarket-live-probe hot_decision_telemetry state_manager_report_includes_hot_decision_telemetry_when_enabled
cd ..
uv run pytest tests/scripts/test_verify_state_manager_report.py -q
```

Expected: Rust focused tests pass. If `tests/scripts/test_verify_state_manager_report.py` does not exist, run:

```bash
uv run pytest tests -q -k verify_state_manager_report
```

Expected: existing verifier tests pass or report no matching tests.

- [ ] **Step 5: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/hot_decision.rs rust/crates/polymarket-live-probe/src/report.rs scripts/verify_state_manager_report.py
git commit -m "Expose hot decision telemetry"
```

---

### Task 6: Enable Hot Decision Persistence From CLI

**Files:**
- Modify: `rust/crates/polymarket-live-probe/src/main.rs`
- Modify: `rust/crates/polymarket-live-probe/src/state_manager.rs`

- [ ] **Step 1: Add CLI parser test**

In `rust/crates/polymarket-live-probe/src/main.rs` tests, extend `parses_raw_event_journal_cli_options` or add:

```rust
#[test]
fn parses_hot_decision_journal_cli_options() {
    let args = Args::parse_from([
        "polymarket-live-probe",
        "--decision-snapshot-dir",
        "/tmp/decision-states",
        "--decision-event-buffer-size",
        "4096",
    ]);

    assert_eq!(args.decision_snapshot_dir, Some(PathBuf::from("/tmp/decision-states")));
    assert_eq!(args.decision_event_buffer_size, 4096);
}
```

- [ ] **Step 2: Run parser test and see it fail**

Run:

```bash
cd rust
cargo test -p polymarket-live-probe parses_hot_decision_journal_cli_options
```

Expected: FAIL with missing `decision_snapshot_dir` or `decision_event_buffer_size`.

- [ ] **Step 3: Add CLI flags**

In `Args`, add:

```rust
#[arg(long)]
decision_snapshot_dir: Option<PathBuf>,
#[arg(long, default_value_t = 16_384)]
decision_event_buffer_size: usize,
```

In `run_state_manager`, create the sink:

```rust
let decision_writer = args
    .decision_snapshot_dir
    .clone()
    .map(|dir| decision_journal::HotDecisionSink::start(dir, args.decision_event_buffer_size));
let decision_sink = decision_writer.as_ref().map(|(sink, _handle)| sink.clone());
let mut runtime = state_manager::StateManagerRuntime::start_with_raw_events_and_hot_decisions(
    config,
    raw_event_sink,
    decision_sink,
)
.await?;
```

In the report input:

```rust
hot_decision_telemetry: runtime.hot_decision_telemetry(),
```

At shutdown, after raw event writer handling:

```rust
if let Some((sink, handle)) = decision_writer {
    drop(sink);
    handle
        .await
        .map_err(|error| anyhow!("hot decision journal task failed: {error}"))??;
}
```

Add this method to `StateManagerRuntime`:

```rust
pub fn hot_decision_telemetry(&self) -> Option<crate::hot_decision::HotDecisionTelemetrySnapshot> {
    self.hot_decision_telemetry.as_ref().map(|telemetry| telemetry.snapshot())
}
```

Add a field:

```rust
hot_decision_telemetry: Option<crate::hot_decision::HotDecisionTelemetry>,
```

When creating the worker, create and pass telemetry:

```rust
let hot_decision_telemetry = decision_sink.as_ref().map(|_| crate::hot_decision::HotDecisionTelemetry::default());
```

Pass `hot_decision_telemetry.clone()` into `start_hot_decision_worker` and record every built state.

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd rust
cargo test -p polymarket-live-probe parses_hot_decision_journal_cli_options
cargo test -p polymarket-live-probe state_manager_report_includes_hot_decision_telemetry_when_enabled
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/main.rs rust/crates/polymarket-live-probe/src/state_manager.rs
git commit -m "Enable hot decision journal from CLI"
```

---

### Task 7: Add No-Auth Hypothetical Order Latency Probe

**Files:**
- Create: `rust/crates/polymarket-live-probe/src/order_latency_probe.rs`
- Modify: `rust/crates/polymarket-live-probe/src/main.rs`

- [ ] **Step 1: Add module declaration**

In `rust/crates/polymarket-live-probe/src/main.rs`, add:

```rust
mod order_latency_probe;
```

- [ ] **Step 2: Create the probe module**

Create `rust/crates/polymarket-live-probe/src/order_latency_probe.rs`:

```rust
use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::time::Instant;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OrderLatencyProbeResult {
    pub schema_version: String,
    pub url: String,
    pub iterations: usize,
    pub payload_build_us: Vec<u128>,
    pub synthetic_sign_us: Vec<u128>,
    pub http_round_trip_ms: Vec<u128>,
}

impl OrderLatencyProbeResult {
    pub fn p50_http_round_trip_ms(&self) -> Option<u128> {
        percentile(&self.http_round_trip_ms, 50)
    }

    pub fn p95_http_round_trip_ms(&self) -> Option<u128> {
        percentile(&self.http_round_trip_ms, 95)
    }
}

pub async fn run_order_latency_probe(url: &str, iterations: usize) -> Result<OrderLatencyProbeResult> {
    let client = reqwest::Client::builder().build()?;
    let mut payload_build_us = Vec::with_capacity(iterations);
    let mut synthetic_sign_us = Vec::with_capacity(iterations);
    let mut http_round_trip_ms = Vec::with_capacity(iterations);
    for index in 0..iterations {
        let payload_started = Instant::now();
        let payload = serde_json::json!({
            "probe": "no-auth-order-latency",
            "iteration": index,
            "side": "BUY",
            "price": "0.50",
            "size": "1",
        });
        let payload_bytes = serde_json::to_vec(&payload)?;
        payload_build_us.push(payload_started.elapsed().as_micros());

        let sign_started = Instant::now();
        let mut hasher = DefaultHasher::new();
        payload_bytes.hash(&mut hasher);
        let _synthetic_signature = hasher.finish();
        synthetic_sign_us.push(sign_started.elapsed().as_micros());

        let http_started = Instant::now();
        let response = client.get(url).send().await?;
        let _status = response.status();
        let _body = response.bytes().await?;
        http_round_trip_ms.push(http_started.elapsed().as_millis());
    }
    Ok(OrderLatencyProbeResult {
        schema_version: "rust-order-latency-probe-v1".to_owned(),
        url: url.to_owned(),
        iterations,
        payload_build_us,
        synthetic_sign_us,
        http_round_trip_ms,
    })
}

fn percentile(values: &[u128], pct: u32) -> Option<u128> {
    if values.is_empty() {
        return None;
    }
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    let index = ((sorted.len() - 1) as u32 * pct / 100) as usize;
    sorted.get(index).copied()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentile_reports_p50_and_p95_from_sorted_copy() {
        let result = OrderLatencyProbeResult {
            schema_version: "rust-order-latency-probe-v1".to_owned(),
            url: "http://127.0.0.1:1".to_owned(),
            iterations: 5,
            payload_build_us: vec![1, 2, 3, 4, 5],
            synthetic_sign_us: vec![1, 2, 3, 4, 5],
            http_round_trip_ms: vec![100, 10, 50, 20, 30],
        };

        assert_eq!(result.p50_http_round_trip_ms(), Some(30));
        assert_eq!(result.p95_http_round_trip_ms(), Some(50));
    }
}
```

- [ ] **Step 3: Add CLI mode**

In `Args`, add:

```rust
#[arg(long, default_value = "https://clob-v2.polymarket.com")]
order_latency_probe_url: String,
#[arg(long, default_value_t = 10)]
order_latency_probe_iterations: usize,
```

In `main`, add a mode:

```rust
"latency-probe" => run_latency_probe(args).await,
```

Add:

```rust
async fn run_latency_probe(args: Args) -> Result<()> {
    let result = order_latency_probe::run_order_latency_probe(
        &args.order_latency_probe_url,
        args.order_latency_probe_iterations,
    )
    .await?;
    report::write_json_report(&args.out, &result)?;
    info!(
        path = %args.out.display(),
        p50_http_round_trip_ms = ?result.p50_http_round_trip_ms(),
        p95_http_round_trip_ms = ?result.p95_http_round_trip_ms(),
        "wrote no-auth order latency probe report"
    );
    Ok(())
}
```

If `write_json_report` is private, expose a specific function in `report.rs`:

```rust
pub fn write_order_latency_probe_report(
    path: &Path,
    report: &crate::order_latency_probe::OrderLatencyProbeResult,
) -> Result<()> {
    write_json_report(path, report)
}
```

Then call that function instead of `write_json_report`.

- [ ] **Step 4: Run focused tests**

Run:

```bash
cd rust
cargo test -p polymarket-live-probe percentile_reports_p50_and_p95_from_sorted_copy
cargo test -p polymarket-live-probe parses_raw_event_journal_cli_options
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/order_latency_probe.rs rust/crates/polymarket-live-probe/src/main.rs rust/crates/polymarket-live-probe/src/report.rs
git commit -m "Add no-auth order latency probe"
```

---

### Task 8: Document the Faster Architecture and Spoon Commands

**Files:**
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Modify: `docs/SPOON_DEPLOYMENT.md`
- Modify: `tests/docs/test_active_runtime_docs.py`

- [ ] **Step 1: Write doc tests first**

Modify `tests/docs/test_active_runtime_docs.py` by adding:

```python
def test_live_docs_keep_hot_decisions_inside_rust_state_manager() -> None:
    text = Path("docs/PART_TWO_LIVE_COLLECTORS.md").read_text()

    assert "--decision-snapshot-dir" in text
    assert "hot decision" in text.lower()
    assert "DuckDB owns normalized replay/research" in text
    assert "must not sit on the live decision path" in text


def test_spoon_docs_include_latency_probe_without_order_placement() -> None:
    text = Path("docs/SPOON_DEPLOYMENT.md").read_text()

    assert "--mode latency-probe" in text
    assert "no-auth" in text.lower()
    assert "does not place orders" in text
```

Ensure the file imports `Path`:

```python
from pathlib import Path
```

- [ ] **Step 2: Run doc tests and see them fail**

Run:

```bash
uv run pytest tests/docs/test_active_runtime_docs.py -q
```

Expected: FAIL on missing new doc text.

- [ ] **Step 3: Update live collector docs**

In `docs/PART_TWO_LIVE_COLLECTORS.md`, add this section after the durable boundary paragraph:

```markdown
## Hot Decision Path

The fast path is Rust-only:

1. Chainlink RTDS or CLOB WebSocket message arrives.
2. Rust parses the event and updates in-memory `LatestPrices` or `LiveBookState`.
3. Rust emits a hot-path event inside the state-manager process.
4. Rust builds exact current hot `DecisionState` rows from in-memory warmed contracts, prices, and orderbooks.
5. Rust queues those rows to the async hot-decision journal.

DuckDB owns normalized replay/research and must not sit on the live decision
path. Python may rebuild and audit the same states later, but live decisions
must not depend on DuckDB normalization, normalized health, or status-file
polling.
```

Update the active command block:

```bash
polymarket-live-probe \
  --mode state-manager \
  --assets BTC,ETH \
  --interval 5m \
  --prewarm-windows 2 \
  --forever \
  --raw-event-dir /var/lib/polymarket/raw \
  --state-snapshot-dir /var/lib/polymarket/raw/polymarket_state_manager/state_snapshot \
  --decision-snapshot-dir /var/lib/polymarket/raw \
  --out /var/lib/polymarket/live/status.json
```

- [ ] **Step 4: Update Spoon deployment docs**

In `docs/SPOON_DEPLOYMENT.md`, add:

````text
## No-Auth Latency Probe

This measures payload-build time, synthetic signing time, and public HTTP
round-trip time without loading private keys and without placing orders:

```bash
cd /home/spoon/polymarket/rust
cargo run -p polymarket-live-probe -- \
  --mode latency-probe \
  --order-latency-probe-url https://clob-v2.polymarket.com \
  --order-latency-probe-iterations 20 \
  --out /home/spoon/polymarket-data/live/order_latency_probe.json
```

The result is only a hosting/network benchmark. It does not prove trading
readiness and does not place orders.
````

- [ ] **Step 5: Run doc tests**

Run:

```bash
uv run pytest tests/docs/test_active_runtime_docs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/PART_TWO_LIVE_COLLECTORS.md docs/SPOON_DEPLOYMENT.md tests/docs/test_active_runtime_docs.py
git commit -m "Document Rust hot decision latency path"
```

---

### Task 9: Full Local Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run Python lint**

Run:

```bash
uv run ruff check .
```

Expected:

```text
All checks passed!
```

- [ ] **Step 2: Run Python type checks**

Run:

```bash
uv run mypy src tests
```

Expected:

```text
Success: no issues found in 78 source files
```

If the source count changes, accept the updated count only if there are no issues.

- [ ] **Step 3: Run Python tests**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass. The existing FastAPI/Starlette deprecation warning is acceptable if it is the only warning.

- [ ] **Step 4: Run Rust workspace tests**

Run:

```bash
cd rust
cargo test --workspace
```

Expected: all Rust unit and doc tests pass.

- [ ] **Step 5: Commit verification-only doc if needed**

If verification required no file edits, do not create a commit. If verification exposed and fixed a typo, commit only that typo:

```bash
git add <fixed-file>
git commit -m "Fix hot decision verification typo"
```

---

### Task 10: Deploy to Spoon and Benchmark

**Files:**
- Remote runtime only.

- [ ] **Step 1: Push branch**

Run:

```bash
git push
```

Expected: branch updates on GitHub.

- [ ] **Step 2: Deploy branch to Spoon**

Run:

```bash
ssh spoon 'cd /home/spoon/polymarket && POLYMARKET_DEPLOY_REF=origin/codex/rust-raw-normalizer DEPLOY_FORCE=1 ./scripts/deploy.sh'
```

Expected output includes:

```text
deploy OK
```

- [ ] **Step 3: Verify live status**

Run:

```bash
ssh spoon 'cd /home/spoon/polymarket && python3 scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000 --max-websocket-event-age-ms 30000 --raw-root /home/spoon/polymarket-data/raw'
ssh spoon 'cd /home/spoon/polymarket && python3 scripts/verify_state_manager_report.py /home/spoon/polymarket-data/live/status.json'
```

Expected:

```text
{'ok': True, ...}
ok mode=state-manager ... health_flags=0
```

- [ ] **Step 4: Confirm hot decision journal is live**

Run:

```bash
ssh spoon 'find /home/spoon/polymarket-data/raw/polymarket_decision_state/hot_state -type f -name "decision-state.jsonl" -mmin -5 -print -exec tail -n 2 {} \;'
```

Expected: at least one recent `decision-state.jsonl` path and JSON rows containing:

```json
{"schema_version":"rust-hot-decision-state-v1"}
```

The row may contain additional fields before or after `schema_version`.

- [ ] **Step 5: Sample hot decision telemetry**

Run:

```bash
ssh spoon 'cd /home/spoon/polymarket && .venv/bin/python - <<'"'"'PY'"'"'
import json, time
from pathlib import Path
p = Path("/home/spoon/polymarket-data/live/status.json")
for i in range(10):
    s = json.loads(p.read_text())
    print(i, s.get("hot_decision_telemetry"), s.get("health_flags"))
    time.sleep(1)
PY'
```

Expected: `states_built` increases or remains nonzero, `dropped_events` remains `0`, and `last_observed_to_state_us` is present after WebSocket events arrive.

- [ ] **Step 6: Run the no-auth latency probe on Spoon**

Run:

```bash
ssh spoon 'cd /home/spoon/polymarket/rust && cargo run -p polymarket-live-probe -- --mode latency-probe --order-latency-probe-url https://clob-v2.polymarket.com --order-latency-probe-iterations 20 --out /home/spoon/polymarket-data/live/order_latency_probe.json'
ssh spoon 'cat /home/spoon/polymarket-data/live/order_latency_probe.json'
```

Expected: JSON with:

```json
{
  "schema_version": "rust-order-latency-probe-v1",
  "iterations": 20,
  "payload_build_us": [],
  "synthetic_sign_us": [],
  "http_round_trip_ms": []
}
```

The arrays must contain 20 values each.

- [ ] **Step 7: Decide whether VPS is now justified**

Use these criteria:

- If `last_observed_to_state_us` is consistently under `5_000us` and CLOB WebSocket event-to-observed remains under `250ms`, the software hot path is good enough for the next paper-decision benchmark on Spoon.
- If public HTTP p95 round-trip from Spoon is consistently above `150ms`, run the same no-auth latency probe from one cheap VPS before moving the collector.
- If hot-decision event drops are nonzero, fix queue/backpressure before buying a VPS.
- If Chainlink event-to-observed stays around `900-1500ms`, do not blame Spoon until the same probe is run elsewhere; that may be feed cadence or RTDS delivery behavior.

---

## Self-Review

**Spec coverage:** The plan keeps hot decisions inside the Rust state-manager, removes DuckDB/Python/status-file polling from the hot path, builds and persists exact hot `DecisionState` rows on WebSocket events, and measures no-auth hypothetical order-submit latency phases.

**Placeholder scan:** Each code task includes concrete paths, code snippets, commands, and expected results.

**Type consistency:** The plan defines `HotDecisionState`, `HotPathEvent`, `HotDecisionSink`, `HotDecisionTelemetry`, and `OrderLatencyProbeResult` before later tasks reference them. Existing `WarmedContract`, `NormalizedPriceTick`, and `NormalizedOrderBook` types are used from `polymarket-runtime-types`.
