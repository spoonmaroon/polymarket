# Rust WebSocket State Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an always-on Rust state manager that keeps BTC/ETH current, next, and next-next Polymarket 5-minute binary contracts warmed through CLOB market WebSocket order books and Chainlink RTDS BTC/USD + ETH/USD prices.

**Architecture:** Keep REST out of the hot rollover path. Use deterministic window scheduling plus Gamma prefetch for token IDs, long-lived CLOB market WebSocket subscriptions for order books, long-lived RTDS subscriptions for Chainlink reference prices, and in-memory state snapshots for the future decision engine.

**Tech Stack:** Rust, Tokio, Polymarket Rust SDK, serde/serde_json, chrono, rust_decimal, tokio-tungstenite or SDK WebSocket helpers if exposed, existing `polymarket-runtime-types` crate.

---

## File Structure

- Modify `rust/crates/polymarket-runtime-types/src/lib.rs`
  - Export new contract, state, and health types.
- Create `rust/crates/polymarket-runtime-types/src/contract.rs`
  - Contract window, side, token, and warmed-contract structs.
- Create `rust/crates/polymarket-runtime-types/src/state.rs`
  - Runtime state snapshot structs for current/next windows, price freshness, book freshness, and health flags.
- Modify `rust/crates/polymarket-runtime-types/src/orderbook.rs`
  - Add incremental top-of-book update helpers used by WebSocket events.
- Modify `rust/crates/polymarket-live-probe/Cargo.toml`
  - Add WebSocket dependencies only if the SDK does not expose the needed market channel stream.
- Create `rust/crates/polymarket-live-probe/src/windows.rs`
  - Deterministic current/next/next-next window scheduler.
- Modify `rust/crates/polymarket-live-probe/src/polymarket.rs`
  - Reuse token discovery for arbitrary scheduled windows and expose subscription asset IDs.
- Create `rust/crates/polymarket-live-probe/src/clob_ws.rs`
  - CLOB market WebSocket subscription payloads, parser, and in-memory order-book update loop.
- Modify `rust/crates/polymarket-live-probe/src/prices.rs`
  - Add always-on RTDS Chainlink BTC/USD and ETH/USD stream support.
- Create `rust/crates/polymarket-live-probe/src/state_manager.rs`
  - Orchestrates scheduler, contract resolver, CLOB WS, RTDS price stream, stale checks, and report snapshots.
- Modify `rust/crates/polymarket-live-probe/src/report.rs`
  - Add state-manager report schema.
- Modify `rust/crates/polymarket-live-probe/src/main.rs`
  - Add `--mode probe|state-manager`, state-manager CLI flags, and finite smoke option.
- Modify `README.md`
  - Document Rust state manager usage.
- Modify `docs/PART_TWO_LIVE_COLLECTORS.md`
  - Mark WebSocket state manager as the intended low-latency collector path.

## Task 1: Add Runtime Contract And State Types

**Files:**
- Create: `rust/crates/polymarket-runtime-types/src/contract.rs`
- Create: `rust/crates/polymarket-runtime-types/src/state.rs`
- Modify: `rust/crates/polymarket-runtime-types/src/lib.rs`
- Test: `rust/crates/polymarket-runtime-types/src/contract.rs`
- Test: `rust/crates/polymarket-runtime-types/src/state.rs`

- [x] **Step 1: Write contract type tests**

Add this test module to the new `contract.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};

    #[test]
    fn contract_window_tracks_start_end_and_slug() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let end = Utc.timestamp_opt(1_780_302_700, 0).unwrap();
        let window = ContractWindow::new("BTC", "5m", start, end).unwrap();

        assert_eq!(window.asset, "BTC");
        assert_eq!(window.interval, "5m");
        assert_eq!(window.slug(), "btc-updown-5m-1780302400");
    }

    #[test]
    fn warmed_contract_requires_up_and_down_tokens() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let end = Utc.timestamp_opt(1_780_302_700, 0).unwrap();
        let window = ContractWindow::new("ETH", "5m", start, end).unwrap();
        let contract = WarmedContract::new(
            window,
            ContractToken::new("ETH", ContractSide::Up, "111"),
            ContractToken::new("ETH", ContractSide::Down, "222"),
        )
        .unwrap();

        assert_eq!(contract.token_ids(), vec!["111".to_owned(), "222".to_owned()]);
    }
}
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/goon/.config/superpowers/worktrees/polymarket/rust-live-probe/rust
cargo test -p polymarket-runtime-types contract::tests -q
```

Expected: compile failure because `ContractWindow`, `ContractToken`, `ContractSide`, and `WarmedContract` are not defined.

- [x] **Step 3: Implement contract types**

Create `contract.rs` with:

```rust
use anyhow::{Result, bail};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ContractSide {
    Up,
    Down,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContractWindow {
    pub asset: String,
    pub interval: String,
    pub start_ts: DateTime<Utc>,
    pub end_ts: DateTime<Utc>,
}

impl ContractWindow {
    pub fn new(asset: &str, interval: &str, start_ts: DateTime<Utc>, end_ts: DateTime<Utc>) -> Result<Self> {
        if end_ts <= start_ts {
            bail!("contract window end must be after start");
        }
        let asset = asset.trim().to_ascii_uppercase();
        if asset != "BTC" && asset != "ETH" {
            bail!("unsupported asset for warmed contract window: {asset}");
        }
        Ok(Self {
            asset,
            interval: interval.to_owned(),
            start_ts,
            end_ts,
        })
    }

    pub fn slug(&self) -> String {
        format!(
            "{}-updown-{}-{}",
            self.asset.to_ascii_lowercase(),
            self.interval,
            self.start_ts.timestamp()
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContractToken {
    pub asset: String,
    pub side: ContractSide,
    pub token_id: String,
}

impl ContractToken {
    pub fn new(asset: &str, side: ContractSide, token_id: &str) -> Self {
        Self {
            asset: asset.to_ascii_uppercase(),
            side,
            token_id: token_id.to_owned(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WarmedContract {
    pub window: ContractWindow,
    pub up: ContractToken,
    pub down: ContractToken,
}

impl WarmedContract {
    pub fn new(window: ContractWindow, up: ContractToken, down: ContractToken) -> Result<Self> {
        if up.side != ContractSide::Up || down.side != ContractSide::Down {
            bail!("warmed contract requires one UP token and one DOWN token");
        }
        Ok(Self { window, up, down })
    }

    pub fn token_ids(&self) -> Vec<String> {
        vec![self.up.token_id.clone(), self.down.token_id.clone()]
    }
}
```

- [x] **Step 4: Export types**

Modify `lib.rs`:

```rust
pub mod contract;
pub mod orderbook;
pub mod price;
pub mod probe;
pub mod state;
```

- [x] **Step 5: Add state snapshot types and tests**

Create `state.rs`:

```rust
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
```

Add a test that a snapshot with `stale_chainlink_btc` blocks trading.

- [x] **Step 6: Run tests**

Run:

```bash
cd /Users/goon/.config/superpowers/worktrees/polymarket/rust-live-probe/rust
cargo test -p polymarket-runtime-types -q
```

Expected: all runtime type tests pass.

- [x] **Step 7: Commit**

```bash
git add rust/crates/polymarket-runtime-types/src/lib.rs rust/crates/polymarket-runtime-types/src/contract.rs rust/crates/polymarket-runtime-types/src/state.rs
git commit -m "Add runtime contract state types"
```

## Task 2: Add Deterministic Window Scheduler

**Files:**
- Create: `rust/crates/polymarket-live-probe/src/windows.rs`
- Modify: `rust/crates/polymarket-live-probe/src/main.rs`

- [x] **Step 1: Write scheduler tests**

Create `windows.rs` with tests first:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};

    #[test]
    fn schedules_current_next_and_next_next_5m_windows() {
        let now = Utc.timestamp_opt(1_780_302_456, 0).unwrap();
        let windows = schedule_windows(now, &["BTC", "ETH"], "5m", 3).unwrap();

        assert_eq!(windows.len(), 6);
        assert_eq!(windows[0].slug(), "btc-updown-5m-1780302300");
        assert_eq!(windows[1].slug(), "eth-updown-5m-1780302300");
        assert_eq!(windows[2].slug(), "btc-updown-5m-1780302600");
    }
}
```

- [x] **Step 2: Run failing test**

Run:

```bash
cd /Users/goon/.config/superpowers/worktrees/polymarket/rust-live-probe/rust
cargo test -p polymarket-live-probe windows::tests -q
```

Expected: compile failure because `schedule_windows` does not exist.

- [x] **Step 3: Implement scheduler**

Add:

```rust
use anyhow::{Result, bail};
use chrono::{DateTime, Duration, Utc};
use polymarket_runtime_types::contract::ContractWindow;

pub fn schedule_windows(
    now: DateTime<Utc>,
    assets: &[&str],
    interval: &str,
    count: u8,
) -> Result<Vec<ContractWindow>> {
    let seconds = match interval {
        "5m" => 300,
        "15m" => 900,
        other => bail!("unsupported interval: {other}"),
    };
    let start_epoch = now.timestamp() - now.timestamp().rem_euclid(seconds);
    let mut out = Vec::with_capacity(assets.len() * usize::from(count));
    for index in 0..i64::from(count) {
        let start = DateTime::<Utc>::from_timestamp(start_epoch + seconds * index, 0)
            .ok_or_else(|| anyhow::anyhow!("invalid start epoch"))?;
        let end = start + Duration::seconds(seconds);
        for asset in assets {
            out.push(ContractWindow::new(asset, interval, start, end)?);
        }
    }
    Ok(out)
}
```

- [x] **Step 4: Wire module**

Add to `main.rs` module declarations:

```rust
mod windows;
```

- [x] **Step 5: Run tests**

```bash
cargo test -p polymarket-live-probe windows::tests -q
```

Expected: scheduler tests pass.

- [x] **Step 6: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/windows.rs rust/crates/polymarket-live-probe/src/main.rs
git commit -m "Add deterministic contract window scheduler"
```

## Task 3: Refactor Gamma Discovery For Prewarmed Windows

**Files:**
- Modify: `rust/crates/polymarket-live-probe/src/polymarket.rs`
- Test: `rust/crates/polymarket-live-probe/src/polymarket.rs`

- [x] **Step 1: Add test for resolving scheduled windows**

Add a unit test that converts two `MarketToken` values for a BTC window into one `WarmedContract` with UP/DOWN tokens.

- [x] **Step 2: Implement converter**

Add a function:

```rust
pub fn warmed_contracts_from_tokens(tokens: &[MarketToken]) -> Result<Vec<WarmedContract>> {
    // group by slug, require exactly one UP and one DOWN, preserve asset/window metadata
}
```

Use `ContractWindow::new(asset, interval, start_ts, end_ts)` by parsing the slug epoch. Reject malformed slugs.

- [x] **Step 3: Add discovery for explicit windows**

Add:

```rust
pub async fn discover_windows(windows: &[ContractWindow]) -> Result<Vec<WarmedContract>> {
    let gamma = GammaClient::new(GAMMA_HOST)?;
    let slugs = windows.iter().map(ContractWindow::slug).collect::<Vec<_>>();
    let request = MarketsRequest::builder().slug(slugs).closed(false).build();
    let markets = gamma.markets(&request).await?;
    let mut tokens = Vec::new();
    for market in markets {
        tokens.extend(market_tokens_from_gamma_market(&market)?);
    }
    warmed_contracts_from_tokens(&tokens)
}
```

- [x] **Step 4: Run tests**

```bash
cargo test -p polymarket-live-probe polymarket::tests -q
```

Expected: all Polymarket discovery tests pass.

- [x] **Step 5: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/polymarket.rs
git commit -m "Support prewarmed contract discovery"
```

## Task 4: Add Chainlink BTC/USD And ETH/USD Always-On Stream

**Files:**
- Modify: `rust/crates/polymarket-live-probe/src/prices.rs`

- [x] **Step 1: Add tests for BTC and ETH Chainlink parsing**

Add fixtures for `crypto_prices_chainlink` containing both `btc/usd` and `eth/usd`, then assert both normalize into `polymarket_rtds_chainlink` rows.

- [x] **Step 2: Add latest-price store**

Implement:

```rust
#[derive(Debug, Default, Clone)]
pub struct LatestPrices {
    inner: std::sync::Arc<std::sync::RwLock<std::collections::HashMap<String, NormalizedPriceTick>>>,
}
```

Expose `update(tick)`, `get(symbol)`, and `snapshot()` methods.

- [x] **Step 3: Add always-on Chainlink loop**

Implement:

```rust
pub async fn run_chainlink_stream(symbols: Vec<String>, latest: LatestPrices) -> Result<()>
```

Subscribe to Chainlink RTDS and update the store for `btc/usd` and `eth/usd`. If filtered multi-symbol subscriptions are unreliable, subscribe broadly and filter locally.

- [x] **Step 4: Add freshness helper**

Implement:

```rust
pub fn chainlink_freshness(
    latest: &[NormalizedPriceTick],
    now: DateTime<Utc>,
    max_age_ms: i64,
) -> Vec<FeedFreshness>
```

Mark BTC/USD or ETH/USD stale when `now - observed_ts > max_age_ms`.

- [x] **Step 5: Run tests**

```bash
cargo test -p polymarket-live-probe prices::tests -q
```

Expected: existing BTC tests and new ETH tests pass.

- [x] **Step 6: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/prices.rs
git commit -m "Stream Chainlink BTC and ETH prices"
```

## Task 5: Add CLOB Market WebSocket Parser And Subscription Builder

**Files:**
- Create: `rust/crates/polymarket-live-probe/src/clob_ws.rs`
- Modify: `rust/crates/polymarket-live-probe/src/main.rs`

- [x] **Step 1: Write subscription payload test**

Test that token IDs `111` and `222` produce:

```json
{"type":"market","assets_ids":["111","222"]}
```

Use the exact field name expected by Polymarket market channel docs.

- [x] **Step 2: Write parser tests**

Add JSON fixtures for:

- `book`
- `price_change`
- `best_bid_ask`

Each test should assert token ID, best bid, best ask, spread, and observed timestamp behavior.

- [x] **Step 3: Implement subscription builder**

Add:

```rust
pub fn market_subscription_payload(token_ids: &[String]) -> serde_json::Value
```

Sort and deduplicate token IDs before building the payload so reconnects are deterministic.

- [x] **Step 4: Implement event parser**

Add:

```rust
pub enum ClobMarketEvent {
    Book(NormalizedOrderBook),
    TopOfBook { token_id: String, best_bid: Decimal, best_ask: Decimal, observed_ts: DateTime<Utc> },
    PriceChange { token_id: String, price: Decimal, side: String, size: Decimal, observed_ts: DateTime<Utc> },
}
```

Parse only known events. Unknown events return `Ok(None)` so new venue messages do not crash the runtime.

- [x] **Step 5: Wire module**

Add to `main.rs`:

```rust
mod clob_ws;
```

- [x] **Step 6: Run tests**

```bash
cargo test -p polymarket-live-probe clob_ws::tests -q
```

Expected: subscription and parser tests pass.

- [x] **Step 7: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/clob_ws.rs rust/crates/polymarket-live-probe/src/main.rs
git commit -m "Parse CLOB market websocket events"
```

## Task 6: Add In-Memory Order Book State

**Files:**
- Modify: `rust/crates/polymarket-runtime-types/src/orderbook.rs`
- Create: `rust/crates/polymarket-live-probe/src/book_state.rs`
- Modify: `rust/crates/polymarket-live-probe/src/main.rs`

- [x] **Step 1: Write book-state tests**

Test that:

- a full `book` event initializes state;
- `best_bid_ask` updates top of book without losing token metadata;
- stale book age produces `stale_orderbook` health.

- [x] **Step 2: Implement `LiveBookState`**

Create:

```rust
#[derive(Debug, Default, Clone)]
pub struct LiveBookState {
    inner: Arc<RwLock<HashMap<String, NormalizedOrderBook>>>,
}
```

Expose `upsert_book`, `apply_top_of_book`, `snapshot`, and `freshness`.

- [x] **Step 3: Run tests**

```bash
cargo test -p polymarket-live-probe book_state::tests -q
```

Expected: book-state tests pass.

- [x] **Step 4: Commit**

```bash
git add rust/crates/polymarket-runtime-types/src/orderbook.rs rust/crates/polymarket-live-probe/src/book_state.rs rust/crates/polymarket-live-probe/src/main.rs
git commit -m "Maintain live orderbook state"
```

## Task 7: Add Always-On State Manager

**Files:**
- Create: `rust/crates/polymarket-live-probe/src/state_manager.rs`
- Modify: `rust/crates/polymarket-live-probe/src/report.rs`
- Modify: `rust/crates/polymarket-live-probe/src/main.rs`

- [x] **Step 1: Write rollover test**

Use a fake clock and fake contract resolver. Assert:

- before rollover, current and next are both populated;
- after rollover, next becomes current without calling resolver during the transition;
- missing next contract before cutoff adds `next_contract_not_warmed`.

- [x] **Step 2: Implement `StateManagerConfig`**

Include:

```rust
pub struct StateManagerConfig {
    pub assets: Vec<String>,
    pub interval: String,
    pub windows: u8,
    pub prewarm_before_expiry_ms: i64,
    pub stale_chainlink_after_ms: i64,
    pub stale_orderbook_after_ms: i64,
    pub rest_backup_interval_ms: i64,
}
```

- [x] **Step 3: Implement `StateManager`**

The manager owns:

- latest Chainlink prices;
- live order books;
- warmed contracts;
- subscription token set;
- health flags.

Expose:

```rust
pub async fn run_until_report(config: StateManagerConfig, run_for: Duration) -> Result<WarmStateSnapshot>
```

This finite method is for smoke tests and the terminal monitor.

- [x] **Step 4: Add report schema**

Extend `report.rs` with a state-manager report containing:

- `schema_version`
- `mode`
- `elapsed_ms`
- `current`
- `next`
- `next_next`
- `chainlink_prices`
- `proxy_prices`
- `orderbooks`
- `freshness`
- `health_flags`
- `subscriptions`

- [x] **Step 5: Wire CLI**

Add CLI flags:

```text
--mode probe|state-manager
--run-for-seconds 30
--prewarm-windows 3
--prewarm-before-expiry-ms 30000
--stale-chainlink-after-ms 2500
--stale-orderbook-after-ms 2000
--rest-backup-interval-ms 15000
```

Default mode should remain `probe` so existing scripts do not break.

- [x] **Step 6: Run tests**

```bash
cargo test -p polymarket-live-probe state_manager::tests report::tests -q
```

Expected: state manager and report tests pass.

- [x] **Step 7: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/state_manager.rs rust/crates/polymarket-live-probe/src/report.rs rust/crates/polymarket-live-probe/src/main.rs
git commit -m "Add always-on websocket state manager"
```

## Task 8: Add Live Smoke And Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Create: `scripts/verify_state_manager_report.py`

- [x] **Step 1: Add verifier**

Create a Python verifier that checks:

- mode is `state-manager`;
- current and next contain BTC and ETH;
- at least four active order books exist once persistent CLOB subscriptions are present;
- Chainlink BTC/USD and ETH/USD exist;
- health flags are empty or explicitly printed.

- [x] **Step 2: Run local smoke**

Run:

```bash
cd /Users/goon/.config/superpowers/worktrees/polymarket/rust-live-probe/rust
cargo run -p polymarket-live-probe -- \
  --mode state-manager \
  --assets BTC,ETH \
  --interval 5m \
  --run-for-seconds 20 \
  --out ../reports/state_manager_local.json
python3 ../scripts/verify_state_manager_report.py ../reports/state_manager_local.json
```

Expected: report verifies or prints exact stale feed flags.

- [x] **Step 3: Update docs**

Document:

- Spoon is collection/monitoring, not execution;
- Dublin/London is the execution target;
- state manager uses CLOB WebSocket and RTDS Chainlink;
- REST is startup/backup only;
- current, next, and next-next windows are prewarmed.

- [ ] **Step 4: Run full verification**

```bash
cd /Users/goon/.config/superpowers/worktrees/polymarket/rust-live-probe/rust
cargo fmt --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo build -p polymarket-live-probe
```

Expected: all commands pass.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/PART_TWO_LIVE_COLLECTORS.md scripts/verify_state_manager_report.py
git commit -m "Document websocket state manager"
```

## Task 8.5: Migrate Docker Deployment To Rust Collector

**Files:**
- Modify: `deploy/collector/Dockerfile`
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `deploy/collector/collector-entrypoint.sh`
- Modify: `deploy/collector/.env.example`
- Modify: `scripts/deploy.sh`
- Modify: `scripts/check_collector_status.py`
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [x] **Step 1: Replace Python collector image with Rust multi-stage image**

Expected: Docker builds `polymarket-live-probe` release binary and runtime image has no Python collector entrypoint.

- [x] **Step 2: Add read-only Rust collector entrypoint**

Expected: entrypoint checks persistent data sentinel, removes stale tmp status files, and runs `--mode state-manager --forever`.

- [x] **Step 3: Update compose service**

Expected: stable service name `collector`, Rust image, persistent data mounts, local status-file healthcheck, no trading secrets.

- [x] **Step 4: Update deploy script**

Expected: deploy stops known legacy Python collector containers, fast-forwards to configured ref, starts Rust collector, and validates status.

- [x] **Step 5: Update status checker**

Expected: checker accepts the new Rust state-manager report while preserving old status compatibility.

- [ ] **Step 6: Verify local Docker build and smoke**

```bash
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml build collector
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d collector
python3 scripts/check_collector_status.py --status-path <data-dir>/live/status.json
```

Expected: Rust collector writes fresh Chainlink BTC/ETH and CLOB order-book status.

## Task 9: Spoon Verification

**Files:**
- No source edits unless deployment docs need a discovered correction.

- [ ] **Step 1: Push branch**

```bash
git push -u origin codex/ws-state-manager
```

- [ ] **Step 2: Update Spoon test checkout**

```bash
ssh spoon 'cd /home/spoon/polymarket && git fetch origin codex/ws-state-manager && git checkout codex/ws-state-manager && git pull --ff-only'
```

- [ ] **Step 3: Build on Spoon**

```bash
ssh spoon 'set -euo pipefail; cd /home/spoon/polymarket/rust; . "$HOME/.cargo/env"; cargo build -p polymarket-live-probe'
```

- [ ] **Step 4: Run Spoon state-manager smoke**

```bash
ssh spoon 'set -euo pipefail; cd /home/spoon/polymarket/rust; . "$HOME/.cargo/env"; cargo run -q -p polymarket-live-probe -- --mode state-manager --assets BTC,ETH --interval 5m --run-for-seconds 30 --out /tmp/state_manager_spoon.json; cd /home/spoon/polymarket; python3 scripts/verify_state_manager_report.py /tmp/state_manager_spoon.json'
```

Expected: current and next contracts are warmed, Chainlink BTC/USD and ETH/USD are present, CLOB order books are present, and any health flags are explicit.

- [ ] **Step 5: Measure path latency**

Record:

- Chainlink RTDS message age;
- CLOB WebSocket message age;
- local state snapshot read time;
- REST backup timing separately.

Expected: hot state reads are local and should not include Gamma/CLOB REST timing.

## Self-Review Checklist

- Spec coverage: this plan covers prewarmed current/next/next-next windows, CLOB WebSocket books, Chainlink BTC/USD and ETH/USD RTDS, proxy separation, rollover without REST in the hot path, stale-feed blocking, local smoke, and Spoon smoke.
- Placeholder scan: no implementation step is left with missing detail.
- Type consistency: `ContractWindow`, `WarmedContract`, `LatestPrices`, `LiveBookState`, and `WarmStateSnapshot` are introduced before later tasks use them.
