# Rust Live Probe Normalizer And Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Rust live probe that proves we can discover the current BTC/ETH short-dated Polymarket contracts, fetch all usable order-book data, normalize it, pull BTC truth/proxy price data, cross-check price disagreement, and time the full process end-to-end.

**Architecture:** Add a Rust workspace under `/Users/goon/polymarket/rust/` without deleting the existing Python engine. Rust owns this probe’s live runtime path: Polymarket SDK integration, order-book normalization, Chainlink RTDS price collection, Kraken BTC/USD proxy collection, and latency reporting. Python remains the research/modeling layer and gets a small verifier script that validates the Rust probe output against the existing normalized-data contract.

**Tech Stack:** Rust 1.88+, `polymarket_client_sdk_v2` from the official Polymarket Rust repo, `tokio`, `serde`, `serde_json`, `chrono`, `rust_decimal`, `reqwest`, `anyhow`, `tracing`, Python 3.11 verifier, existing `uv` test tooling.

---

## Design Decision

Use Rust for the runtime probe and normalization path, not for Monte Carlo, DuckDB research queries, or XGBoost yet.

Rust should prove:
- active BTC/ETH 5m contract discovery works;
- order books can be pulled through the official Polymarket Rust SDK;
- order books can be normalized into our schema;
- Chainlink BTC/USD can be pulled through Polymarket RTDS;
- Kraken BTC/USD can be pulled as an independent proxy;
- disagreement in basis points can be calculated;
- full elapsed time can be measured and reported.

Python should verify the emitted JSON report and keep research/modeling flexible.

Official references used for this plan:
- Polymarket official client list: `https://docs.polymarket.com/api-reference/clients-sdks`
- Polymarket official Rust SDK: `https://github.com/Polymarket/rs-clob-client-v2`
- Polymarket market WebSocket docs: `https://docs.polymarket.com/market-data/websocket/market-channel`
- Kraken ticker endpoint: `https://docs.kraken.com/api/docs/rest-api/get-ticker-information/`

---

## Parallel Execution Strategy

Use subagent-driven execution with disjoint write sets:

- **Agent A: Rust schema and normalizer**
  - Owns: `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/`
  - Does not touch live network code.

- **Agent B: Polymarket discovery and orderbook fetch**
  - Owns: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/polymarket.rs`
  - Does not touch price proxy code.

- **Agent C: Chainlink/Kraken price cross-check and latency report**
  - Owns: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/prices.rs`
  - Owns: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/report.rs`
  - Does not touch Polymarket orderbook code.

- **Parent integrator**
  - Owns: workspace manifests, binary `main.rs`, Python verifier, docs, and final live smoke test.

Do not modify the currently dirty Python collector files in this plan. This Rust probe is additive.

---

## File Structure

Create:
- `/Users/goon/polymarket/rust/Cargo.toml`  
  Rust workspace root.
- `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/Cargo.toml`  
  Shared typed event/schema crate.
- `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/lib.rs`  
  Re-exports shared modules.
- `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/orderbook.rs`  
  Normalized order-book structs and book-derived calculations.
- `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/price.rs`  
  Normalized price tick and price-disagreement structs.
- `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/probe.rs`  
  Probe report and latency timing structs.
- `/Users/goon/polymarket/rust/crates/polymarket-live-probe/Cargo.toml`  
  Binary crate using official Polymarket SDK.
- `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/main.rs`  
  CLI orchestration.
- `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/polymarket.rs`  
  Gamma/CLOB discovery and order-book fetch.
- `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/prices.rs`  
  Chainlink RTDS and Kraken BTC/USD proxy fetch.
- `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/report.rs`  
  Report assembly, JSON write, and latency summary.
- `/Users/goon/polymarket/rust/crates/polymarket-live-probe/tests/fixtures/orderbook_summary.json`  
  SDK-like order-book fixture.
- `/Users/goon/polymarket/rust/crates/polymarket-live-probe/tests/fixtures/kraken_xbtusd_ticker.json`  
  Kraken ticker fixture.
- `/Users/goon/polymarket/scripts/verify_rust_probe_output.py`  
  Python output verifier for live smoke reports.

Modify:
- `/Users/goon/polymarket/.gitignore`  
  Ignore Rust build output and generated probe reports.
- `/Users/goon/polymarket/README.md`  
  Add one command showing how to run the Rust live probe.

---

### Task 1: Rust Workspace Skeleton

**Files:**
- Create: `/Users/goon/polymarket/rust/Cargo.toml`
- Create: `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/Cargo.toml`
- Create: `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/lib.rs`
- Create: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/Cargo.toml`
- Create: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/main.rs`

- [ ] **Step 1: Check Rust toolchain**

Run:
```bash
rustc --version
cargo --version
```

Expected: Rust 1.88 or newer. If Rust is missing, install with:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup toolchain install stable
rustup default stable
```

- [ ] **Step 2: Create workspace manifest**

Create `/Users/goon/polymarket/rust/Cargo.toml`:

```toml
[workspace]
resolver = "2"
members = [
    "crates/polymarket-runtime-types",
    "crates/polymarket-live-probe",
]

[workspace.package]
edition = "2024"
rust-version = "1.88"
license = "MIT"

[workspace.dependencies]
anyhow = "1.0"
chrono = { version = "0.4", features = ["serde"] }
clap = { version = "4.5", features = ["derive"] }
futures = "0.3"
reqwest = { version = "0.12", features = ["json", "rustls-tls"], default-features = false }
rust_decimal = { version = "1.36", features = ["serde"] }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
tokio = { version = "1.40", features = ["rt-multi-thread", "macros", "time"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
polymarket_client_sdk_v2 = { git = "https://github.com/Polymarket/rs-clob-client-v2", features = ["clob", "gamma", "rtds", "ws", "tracing"] }
```

- [ ] **Step 3: Create runtime-types crate manifest**

Create `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/Cargo.toml`:

```toml
[package]
name = "polymarket-runtime-types"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true

[dependencies]
chrono.workspace = true
rust_decimal.workspace = true
serde.workspace = true
serde_json.workspace = true
```

- [ ] **Step 4: Create runtime-types lib**

Create `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/lib.rs`:

```rust
pub mod orderbook;
pub mod price;
pub mod probe;

pub use orderbook::{BookLevel, NormalizedOrderBook};
pub use price::{NormalizedPriceTick, PriceDisagreement};
pub use probe::{LatencyMark, ProbeReport};
```

- [ ] **Step 5: Create live-probe crate manifest**

Create `/Users/goon/polymarket/rust/crates/polymarket-live-probe/Cargo.toml`:

```toml
[package]
name = "polymarket-live-probe"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true

[dependencies]
anyhow.workspace = true
chrono.workspace = true
clap.workspace = true
futures.workspace = true
polymarket-runtime-types = { path = "../polymarket-runtime-types" }
polymarket_client_sdk_v2.workspace = true
reqwest.workspace = true
rust_decimal.workspace = true
serde.workspace = true
serde_json.workspace = true
tokio.workspace = true
tracing.workspace = true
tracing-subscriber.workspace = true
```

- [ ] **Step 6: Create initial CLI that compiles**

Create `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/main.rs`:

```rust
use anyhow::Result;
use clap::Parser;

mod polymarket;
mod prices;
mod report;

#[derive(Debug, Parser)]
struct Args {
    #[arg(long, default_value = "BTC")]
    assets: String,
    #[arg(long, default_value = "5m")]
    interval: String,
    #[arg(long, default_value_t = 1)]
    windows: u8,
    #[arg(long, default_value_t = 20)]
    timeout_seconds: u64,
    #[arg(long, default_value = "reports/live_probe/latest.json")]
    out: String,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    println!(
        "rust live probe scaffold assets={} interval={} windows={} timeout_seconds={} out={}",
        args.assets, args.interval, args.windows, args.timeout_seconds, args.out
    );
    Ok(())
}
```

- [ ] **Step 7: Add empty module files**

Create `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/polymarket.rs`:

```rust
pub const CLOB_HOST: &str = "https://clob-v2.polymarket.com";
```

Create `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/prices.rs`:

```rust
pub const KRAKEN_TICKER_URL: &str = "https://api.kraken.com/0/public/Ticker";
```

Create `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/report.rs`:

```rust
pub const REPORT_SCHEMA_VERSION: &str = "rust-live-probe-v1";
```

- [ ] **Step 8: Verify workspace compiles**

Run:
```bash
cd /Users/goon/polymarket/rust
cargo check --workspace
```

Expected: workspace compiles.

---

### Task 2: Runtime Types And Normalizer

**Files:**
- Create: `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/orderbook.rs`
- Create: `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/price.rs`
- Create: `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/probe.rs`

- [ ] **Step 1: Write orderbook types and tests**

Create `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/orderbook.rs`:

```rust
use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BookLevel {
    pub price: Decimal,
    pub size: Decimal,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NormalizedOrderBook {
    pub venue: String,
    pub source_key: String,
    pub market_slug: String,
    pub contract_id: String,
    pub token_id: String,
    pub asset: String,
    pub side: String,
    pub event_ts: DateTime<Utc>,
    pub observed_ts: DateTime<Utc>,
    pub best_bid: Option<Decimal>,
    pub best_ask: Option<Decimal>,
    pub spread: Option<Decimal>,
    pub bid_size_top: Option<Decimal>,
    pub ask_size_top: Option<Decimal>,
    pub bids: Vec<BookLevel>,
    pub asks: Vec<BookLevel>,
    pub depth_json: serde_json::Value,
}

impl NormalizedOrderBook {
    pub fn from_levels(
        market_slug: String,
        contract_id: String,
        token_id: String,
        asset: String,
        side: String,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
        bids: Vec<BookLevel>,
        asks: Vec<BookLevel>,
    ) -> Self {
        let best_bid_level = bids.iter().max_by_key(|level| level.price);
        let best_ask_level = asks.iter().min_by_key(|level| level.price);
        let best_bid = best_bid_level.map(|level| level.price);
        let best_ask = best_ask_level.map(|level| level.price);
        let spread = match (best_bid, best_ask) {
            (Some(bid), Some(ask)) => Some(ask - bid),
            _ => None,
        };
        let bid_size_top = best_bid_level.map(|level| level.size);
        let ask_size_top = best_ask_level.map(|level| level.size);
        let depth_json = serde_json::json!({
            "bids": bids,
            "asks": asks
        });

        Self {
            venue: "polymarket".to_owned(),
            source_key: "polymarket_rust_sdk".to_owned(),
            market_slug,
            contract_id,
            token_id,
            asset,
            side,
            event_ts,
            observed_ts,
            best_bid,
            best_ask,
            spread,
            bid_size_top,
            ask_size_top,
            bids,
            asks,
            depth_json,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rust_decimal::Decimal;

    #[test]
    fn derives_best_prices_and_spread_from_depth() {
        let ts = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let book = NormalizedOrderBook::from_levels(
            "btc-updown-5m-1780301700".to_owned(),
            "market-1".to_owned(),
            "token-1".to_owned(),
            "BTC".to_owned(),
            "UP".to_owned(),
            ts,
            ts,
            vec![
                BookLevel { price: Decimal::new(48, 2), size: Decimal::new(20, 0) },
                BookLevel { price: Decimal::new(50, 2), size: Decimal::new(8, 0) },
            ],
            vec![
                BookLevel { price: Decimal::new(52, 2), size: Decimal::new(9, 0) },
                BookLevel { price: Decimal::new(54, 2), size: Decimal::new(30, 0) },
            ],
        );

        assert_eq!(book.best_bid, Some(Decimal::new(50, 2)));
        assert_eq!(book.best_ask, Some(Decimal::new(52, 2)));
        assert_eq!(book.spread, Some(Decimal::new(2, 2)));
        assert_eq!(book.bid_size_top, Some(Decimal::new(8, 0)));
        assert_eq!(book.ask_size_top, Some(Decimal::new(9, 0)));
    }
}
```

- [ ] **Step 2: Write price types and tests**

Create `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/price.rs`:

```rust
use chrono::{DateTime, Utc};
use rust_decimal::prelude::ToPrimitive;
use rust_decimal::Decimal;
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
    pub fn calculate(asset: &str, primary: &NormalizedPriceTick, proxy: &NormalizedPriceTick) -> Self {
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

    #[test]
    fn calculates_price_disagreement_in_bps() {
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
```

- [ ] **Step 3: Write probe report types**

Create `/Users/goon/polymarket/rust/crates/polymarket-runtime-types/src/probe.rs`:

```rust
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::{NormalizedOrderBook, NormalizedPriceTick, PriceDisagreement};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LatencyMark {
    pub name: String,
    pub elapsed_ms: u128,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProbeReport {
    pub schema_version: String,
    pub generated_at: DateTime<Utc>,
    pub elapsed_ms: u128,
    pub assets: Vec<String>,
    pub interval: String,
    pub windows: u8,
    pub orderbooks: Vec<NormalizedOrderBook>,
    pub prices: Vec<NormalizedPriceTick>,
    pub source_disagreements: Vec<PriceDisagreement>,
    pub latency_marks: Vec<LatencyMark>,
}
```

- [ ] **Step 4: Verify runtime types**

Run:
```bash
cd /Users/goon/polymarket/rust
cargo test -p polymarket-runtime-types
```

Expected: all runtime type tests pass.

---

### Task 3: Polymarket Contract Discovery And Orderbook Normalization

**Files:**
- Create: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/polymarket.rs`
- Create: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/tests/fixtures/orderbook_summary.json`

- [ ] **Step 1: Write fixture for orderbook normalization**

Create `/Users/goon/polymarket/rust/crates/polymarket-live-probe/tests/fixtures/orderbook_summary.json`:

```json
{
  "market": "0xabc",
  "asset_id": "111",
  "timestamp": "1780301701000",
  "bids": [
    { "price": "0.48", "size": "20" },
    { "price": "0.50", "size": "8" }
  ],
  "asks": [
    { "price": "0.52", "size": "9" },
    { "price": "0.54", "size": "30" }
  ],
  "min_order_size": "5",
  "tick_size": "0.01",
  "neg_risk": false
}
```

- [ ] **Step 2: Implement slug generation and token metadata structs**

Replace `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/polymarket.rs` with:

```rust
use anyhow::{anyhow, Result};
use chrono::{DateTime, TimeZone, Utc};
use polymarket_runtime_types::{BookLevel, NormalizedOrderBook};
use rust_decimal::Decimal;
use serde::Deserialize;
use std::str::FromStr;

pub const CLOB_HOST: &str = "https://clob-v2.polymarket.com";

#[derive(Debug, Clone, PartialEq)]
pub struct MarketToken {
    pub slug: String,
    pub contract_id: String,
    pub token_id: String,
    pub asset: String,
    pub side: String,
}

pub fn floor_to_interval_epoch(now: DateTime<Utc>, interval_minutes: i64) -> i64 {
    let timestamp = now.timestamp();
    let interval_seconds = interval_minutes * 60;
    timestamp - timestamp.rem_euclid(interval_seconds)
}

pub fn updown_slug(asset: &str, interval: &str, epoch: i64) -> String {
    format!("{}-updown-{}-{}", asset.to_lowercase(), interval, epoch)
}

pub fn current_window_slugs(now: DateTime<Utc>, assets: &[String], interval: &str, windows: u8) -> Result<Vec<String>> {
    let minutes = match interval {
        "5m" => 5,
        "15m" => 15,
        other => return Err(anyhow!("unsupported interval {other}")),
    };
    let start = floor_to_interval_epoch(now, minutes);
    let mut slugs = Vec::new();
    for window in 0..windows {
        let epoch = start + i64::from(window) * minutes * 60;
        for asset in assets {
            slugs.push(updown_slug(asset, interval, epoch));
        }
    }
    Ok(slugs)
}

#[derive(Debug, Deserialize)]
pub struct RawBookLevel {
    pub price: String,
    pub size: String,
}

#[derive(Debug, Deserialize)]
pub struct RawOrderBook {
    pub market: String,
    pub asset_id: String,
    pub timestamp: String,
    pub bids: Vec<RawBookLevel>,
    pub asks: Vec<RawBookLevel>,
}

pub fn normalize_raw_orderbook(raw: &RawOrderBook, token: &MarketToken, observed_ts: DateTime<Utc>) -> Result<NormalizedOrderBook> {
    let event_ts = Utc
        .timestamp_millis_opt(raw.timestamp.parse::<i64>()?)
        .single()
        .ok_or_else(|| anyhow!("invalid orderbook timestamp"))?;
    let bids = raw.bids
        .iter()
        .map(|level| Ok(BookLevel {
            price: Decimal::from_str(&level.price)?,
            size: Decimal::from_str(&level.size)?,
        }))
        .collect::<Result<Vec<_>>>()?;
    let asks = raw.asks
        .iter()
        .map(|level| Ok(BookLevel {
            price: Decimal::from_str(&level.price)?,
            size: Decimal::from_str(&level.size)?,
        }))
        .collect::<Result<Vec<_>>>()?;
    Ok(NormalizedOrderBook::from_levels(
        token.slug.clone(),
        raw.market.clone(),
        raw.asset_id.clone(),
        token.asset.clone(),
        token.side.clone(),
        event_ts,
        observed_ts,
        bids,
        asks,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_current_window_slugs() {
        let now = "2026-06-01T20:03:45Z".parse::<DateTime<Utc>>().unwrap();
        let slugs = current_window_slugs(now, &["BTC".to_owned(), "ETH".to_owned()], "5m", 2).unwrap();
        assert_eq!(slugs[0], "btc-updown-5m-1780344000");
        assert_eq!(slugs[1], "eth-updown-5m-1780344000");
        assert_eq!(slugs[2], "btc-updown-5m-1780344300");
        assert_eq!(slugs[3], "eth-updown-5m-1780344300");
    }

    #[test]
    fn normalizes_orderbook_fixture() {
        let raw: RawOrderBook = serde_json::from_str(include_str!("../tests/fixtures/orderbook_summary.json")).unwrap();
        let token = MarketToken {
            slug: "btc-updown-5m-1780301700".to_owned(),
            contract_id: "0xabc".to_owned(),
            token_id: "111".to_owned(),
            asset: "BTC".to_owned(),
            side: "UP".to_owned(),
        };
        let observed = "2026-06-01T20:00:02Z".parse::<DateTime<Utc>>().unwrap();
        let normalized = normalize_raw_orderbook(&raw, &token, observed).unwrap();

        assert_eq!(normalized.best_bid.unwrap().to_string(), "0.50");
        assert_eq!(normalized.best_ask.unwrap().to_string(), "0.52");
        assert_eq!(normalized.spread.unwrap().to_string(), "0.02");
        assert_eq!(normalized.bid_size_top.unwrap().to_string(), "8");
        assert_eq!(normalized.ask_size_top.unwrap().to_string(), "9");
    }
}
```

- [ ] **Step 3: Add live SDK discovery/fetch function signatures**

Append to `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/polymarket.rs`:

```rust
pub async fn discover_market_tokens(
    assets: &[String],
    interval: &str,
    windows: u8,
    now: DateTime<Utc>,
) -> Result<Vec<MarketToken>> {
    let _ = (assets, interval, windows, now);
    Err(anyhow!("discover_market_tokens live SDK wiring is completed in Task 7"))
}

pub async fn fetch_normalized_orderbooks(tokens: &[MarketToken]) -> Result<Vec<NormalizedOrderBook>> {
    let _ = tokens;
    Err(anyhow!("fetch_normalized_orderbooks live SDK wiring is completed in Task 7"))
}
```

Do not implement authenticated order placement in this task.

- [ ] **Step 4: Verify Polymarket module tests**

Run:
```bash
cd /Users/goon/polymarket/rust
cargo test -p polymarket-live-probe polymarket::
```

Expected: slug and fixture normalization tests pass.

---

### Task 4: BTC Chainlink And Kraken Price Cross-Check

**Files:**
- Create: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/prices.rs`
- Create: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/tests/fixtures/kraken_xbtusd_ticker.json`

- [ ] **Step 1: Write Kraken fixture**

Create `/Users/goon/polymarket/rust/crates/polymarket-live-probe/tests/fixtures/kraken_xbtusd_ticker.json`:

```json
{
  "error": [],
  "result": {
    "XXBTZUSD": {
      "c": ["100100.0", "0.01000000"],
      "b": ["100099.9", "1", "1.000"],
      "a": ["100100.1", "1", "1.000"]
    }
  }
}
```

- [ ] **Step 2: Implement Kraken parser and disagreement helper**

Replace `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/prices.rs` with:

```rust
use anyhow::{anyhow, Result};
use chrono::{DateTime, Utc};
use polymarket_runtime_types::{NormalizedPriceTick, PriceDisagreement};
use rust_decimal::Decimal;
use serde::Deserialize;
use std::collections::HashMap;
use std::str::FromStr;

pub const KRAKEN_TICKER_URL: &str = "https://api.kraken.com/0/public/Ticker";

#[derive(Debug, Deserialize)]
struct KrakenTickerResponse {
    error: Vec<String>,
    result: HashMap<String, KrakenTickerPair>,
}

#[derive(Debug, Deserialize)]
struct KrakenTickerPair {
    c: [String; 2],
}

pub fn parse_kraken_xbtusd_ticker(json: &str, observed_ts: DateTime<Utc>) -> Result<NormalizedPriceTick> {
    let response: KrakenTickerResponse = serde_json::from_str(json)?;
    if !response.error.is_empty() {
        return Err(anyhow!("kraken returned error: {}", response.error.join(",")));
    }
    let pair = response
        .result
        .get("XXBTZUSD")
        .or_else(|| response.result.get("XBTUSD"))
        .ok_or_else(|| anyhow!("missing Kraken XBT/USD ticker"))?;
    Ok(NormalizedPriceTick {
        source_key: "kraken_rest".to_owned(),
        symbol: "XBT/USD".to_owned(),
        event_ts: observed_ts,
        observed_ts,
        price: Decimal::from_str(&pair.c[0])?,
    })
}

pub fn compare_btc_sources(chainlink: &NormalizedPriceTick, kraken: &NormalizedPriceTick) -> PriceDisagreement {
    PriceDisagreement::calculate("BTC", chainlink, kraken)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_kraken_xbtusd_ticker() {
        let observed = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let tick = parse_kraken_xbtusd_ticker(
            include_str!("../tests/fixtures/kraken_xbtusd_ticker.json"),
            observed,
        )
        .unwrap();

        assert_eq!(tick.source_key, "kraken_rest");
        assert_eq!(tick.symbol, "XBT/USD");
        assert_eq!(tick.price.to_string(), "100100.0");
    }

    #[test]
    fn compares_chainlink_to_kraken() {
        let observed = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let chainlink = NormalizedPriceTick {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            symbol: "BTC/USD".to_owned(),
            event_ts: observed,
            observed_ts: observed,
            price: Decimal::new(100_000, 0),
        };
        let kraken = NormalizedPriceTick {
            source_key: "kraken_rest".to_owned(),
            symbol: "XBT/USD".to_owned(),
            event_ts: observed,
            observed_ts: observed,
            price: Decimal::new(100_100, 0),
        };

        let row = compare_btc_sources(&chainlink, &kraken);
        assert!((row.diff_bps - 10.0).abs() < 0.0001);
    }
}
```

- [ ] **Step 3: Add live price fetch functions**

Append to `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/prices.rs`:

```rust
pub async fn fetch_kraken_btc_usd(client: &reqwest::Client) -> Result<NormalizedPriceTick> {
    let observed = Utc::now();
    let text = client
        .get(KRAKEN_TICKER_URL)
        .query(&[("pair", "XBTUSD")])
        .send()
        .await?
        .error_for_status()?
        .text()
        .await?;
    parse_kraken_xbtusd_ticker(&text, observed)
}

pub async fn fetch_chainlink_btc_usd(_timeout_seconds: u64) -> Result<NormalizedPriceTick> {
    Err(anyhow!("fetch_chainlink_btc_usd RTDS SDK wiring is completed in Task 7"))
}
```

- [ ] **Step 4: Verify price parser tests**

Run:
```bash
cd /Users/goon/polymarket/rust
cargo test -p polymarket-live-probe prices::
```

Expected: Kraken parsing and BTC disagreement tests pass.

---

### Task 5: Probe Report Assembly And Timing

**Files:**
- Create: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/report.rs`
- Modify: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/main.rs`

- [ ] **Step 1: Implement report writer**

Replace `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/report.rs` with:

```rust
use anyhow::Result;
use chrono::Utc;
use polymarket_runtime_types::{LatencyMark, NormalizedOrderBook, NormalizedPriceTick, PriceDisagreement, ProbeReport};
use std::path::Path;
use std::time::Instant;

pub const REPORT_SCHEMA_VERSION: &str = "rust-live-probe-v1";

pub struct ProbeTimer {
    started: Instant,
    marks: Vec<LatencyMark>,
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

    pub fn marks(self) -> Vec<LatencyMark> {
        self.marks
    }
}

pub fn build_report(
    assets: Vec<String>,
    interval: String,
    windows: u8,
    elapsed_ms: u128,
    latency_marks: Vec<LatencyMark>,
    orderbooks: Vec<NormalizedOrderBook>,
    prices: Vec<NormalizedPriceTick>,
    source_disagreements: Vec<PriceDisagreement>,
) -> ProbeReport {
    ProbeReport {
        schema_version: REPORT_SCHEMA_VERSION.to_owned(),
        generated_at: Utc::now(),
        elapsed_ms,
        assets,
        interval,
        windows,
        orderbooks,
        prices,
        source_disagreements,
        latency_marks,
    }
}

pub fn write_report(path: &Path, report: &ProbeReport) -> Result<()> {
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

    #[test]
    fn report_has_schema_version() {
        let report = build_report(
            vec!["BTC".to_owned()],
            "5m".to_owned(),
            1,
            10,
            vec![LatencyMark { name: "start".to_owned(), elapsed_ms: 0 }],
            vec![],
            vec![],
            vec![],
        );

        assert_eq!(report.schema_version, "rust-live-probe-v1");
        assert_eq!(report.elapsed_ms, 10);
    }
}
```

- [ ] **Step 2: Wire main orchestration**

Replace `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/main.rs` with:

```rust
use anyhow::Result;
use clap::Parser;
use std::path::PathBuf;
use tracing::info;

mod polymarket;
mod prices;
mod report;

#[derive(Debug, Parser)]
struct Args {
    #[arg(long, default_value = "BTC")]
    assets: String,
    #[arg(long, default_value = "5m")]
    interval: String,
    #[arg(long, default_value_t = 1)]
    windows: u8,
    #[arg(long, default_value_t = 20)]
    timeout_seconds: u64,
    #[arg(long, default_value = "reports/live_probe/latest.json")]
    out: PathBuf,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt::init();
    let args = Args::parse();
    let assets = args
        .assets
        .split(',')
        .map(|asset| asset.trim().to_uppercase())
        .filter(|asset| !asset.is_empty())
        .collect::<Vec<_>>();

    let mut timer = report::ProbeTimer::start();
    timer.mark("start");

    let orderbooks = Vec::new();
    let prices = Vec::new();
    let source_disagreements = Vec::new();

    let final_report = report::build_report(
        assets,
        args.interval,
        args.windows,
        timer.elapsed_ms(),
        timer.marks(),
        orderbooks,
        prices,
        source_disagreements,
    );
    report::write_report(&args.out, &final_report)?;
    info!(path = %args.out.display(), "wrote rust live probe report");
    Ok(())
}
```

- [ ] **Step 3: Verify report tests**

Run:
```bash
cd /Users/goon/polymarket/rust
cargo test -p polymarket-live-probe report::
```

Expected: report tests pass.

---

### Task 6: Python Output Verifier

**Files:**
- Create: `/Users/goon/polymarket/scripts/verify_rust_probe_output.py`
- Test manually with generated report.

- [ ] **Step 1: Create verifier script**

Create `/Users/goon/polymarket/scripts/verify_rust_probe_output.py`:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--max-btc-disagreement-bps", type=float, default=100.0)
    parser.add_argument("--require-orderbooks", action="store_true")
    parser.add_argument("--require-btc-prices", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "rust-live-probe-v1":
        raise SystemExit("wrong schema_version")
    if not isinstance(payload.get("elapsed_ms"), int) or payload["elapsed_ms"] < 0:
        raise SystemExit("elapsed_ms missing or invalid")
    if args.require_orderbooks and not payload.get("orderbooks"):
        raise SystemExit("expected at least one normalized orderbook")
    if args.require_btc_prices:
        symbols = {
            (row.get("source_key"), row.get("symbol"))
            for row in payload.get("prices", [])
        }
        if ("polymarket_rtds_chainlink", "BTC/USD") not in symbols:
            raise SystemExit("missing Chainlink BTC/USD price")
        if ("kraken_rest", "XBT/USD") not in symbols:
            raise SystemExit("missing Kraken XBT/USD price")
    for row in payload.get("source_disagreements", []):
        if row.get("asset") == "BTC" and float(row["diff_bps"]) > args.max_btc_disagreement_bps:
            raise SystemExit(f"BTC disagreement too high: {row['diff_bps']} bps")
    print(
        "ok",
        f"elapsed_ms={payload['elapsed_ms']}",
        f"orderbooks={len(payload.get('orderbooks', []))}",
        f"prices={len(payload.get('prices', []))}",
        f"disagreements={len(payload.get('source_disagreements', []))}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify script on an empty scaffold report**

Run:
```bash
cd /Users/goon/polymarket
python scripts/verify_rust_probe_output.py reports/live_probe/latest.json
```

Expected: passes only after the Rust binary writes a scaffold report.

---

### Task 7: Live Probe Completion

**Files:**
- Modify: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/polymarket.rs`
- Modify: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/prices.rs`
- Modify: `/Users/goon/polymarket/rust/crates/polymarket-live-probe/src/main.rs`

- [ ] **Step 1: Implement live Polymarket orderbook fetch**

Use the official SDK examples from `/tmp/rs-clob-client-v2-inspect/examples/clob/market/get_orderbook.rs` and `/tmp/rs-clob-client-v2-inspect/examples/gamma/client.rs`.

Required behavior:
- `discover_market_tokens(...)` generates current BTC/ETH 5m slugs with `current_window_slugs(...)`;
- for each slug, use `polymarket_client_sdk_v2::gamma::Client` and the SDK market-by-slug request type shown in the official repo examples to fetch one market row;
- reject a slug if outcomes and token ids are missing or have different lengths;
- convert each UP/DOWN side into `MarketToken { slug, contract_id, token_id, asset, side }`;
- `fetch_normalized_orderbooks(...)` uses `polymarket_client_sdk_v2::clob::Client::order_book` with `OrderBookSummaryRequest`;
- convert each SDK order-book level into `BookLevel`;
- normalize every returned order book with `NormalizedOrderBook::from_levels(...)`.

- [ ] **Step 2: Implement live Chainlink and Kraken price fetch**

Required behavior:
- `fetch_chainlink_btc_usd(timeout_seconds)` uses `polymarket_client_sdk_v2::rtds::Client::subscribe_chainlink_prices(Some("btc/usd".to_owned()))`;
- it takes the first BTC/USD Chainlink tick before the timeout expires;
- it returns `NormalizedPriceTick { source_key: "polymarket_rtds_chainlink", symbol: "BTC/USD", ... }`;
- `fetch_kraken_btc_usd(...)` calls Kraken `/public/Ticker?pair=XBTUSD`;
- `main.rs` normalizes both prices and computes `PriceDisagreement::calculate("BTC", &chainlink, &kraken)`.

- [ ] **Step 3: Add latency marks**

`main.rs` must mark:
- `start`
- `contracts_discovered`
- `orderbooks_normalized`
- `chainlink_btc_received`
- `kraken_btc_received`
- `source_disagreement_calculated`
- `report_written`

- [ ] **Step 4: Run live smoke test**

Run:
```bash
cd /Users/goon/polymarket/rust
cargo run -p polymarket-live-probe -- \
  --assets BTC,ETH \
  --interval 5m \
  --windows 1 \
  --timeout-seconds 25 \
  --out /Users/goon/polymarket/reports/live_probe/latest.json
```

Expected:
- report file exists;
- at least two BTC orderbooks and two ETH orderbooks if both current markets are available;
- Chainlink BTC/USD price exists;
- Kraken XBT/USD price exists;
- BTC disagreement row exists;
- elapsed time is printed in the JSON report.

- [ ] **Step 5: Verify live report**

Run:
```bash
cd /Users/goon/polymarket
python scripts/verify_rust_probe_output.py \
  reports/live_probe/latest.json \
  --require-orderbooks \
  --require-btc-prices \
  --max-btc-disagreement-bps 100
```

Expected:
```text
ok elapsed_ms=<number> orderbooks=<number> prices=<number> disagreements=<number>
```

---

### Task 8: Verification And Docs

**Files:**
- Modify: `/Users/goon/polymarket/.gitignore`
- Modify: `/Users/goon/polymarket/README.md`

- [ ] **Step 1: Ignore Rust build artifacts and live reports**

Append to `/Users/goon/polymarket/.gitignore`:

```gitignore
rust/target/
reports/live_probe/
```

- [ ] **Step 2: Document probe command**

Append to `/Users/goon/polymarket/README.md`:

```markdown
## Rust Live Probe

The Rust live probe is a read-only runtime test. It uses the official Polymarket Rust SDK to discover current BTC/ETH 5m markets, fetch CLOB order books, normalize them, pull Chainlink BTC/USD, pull Kraken XBT/USD as a proxy, calculate source disagreement, and write a latency report.

Run:

```bash
cd rust
cargo run -p polymarket-live-probe -- \
  --assets BTC,ETH \
  --interval 5m \
  --windows 1 \
  --timeout-seconds 25 \
  --out ../reports/live_probe/latest.json
```

Verify:

```bash
python scripts/verify_rust_probe_output.py \
  reports/live_probe/latest.json \
  --require-orderbooks \
  --require-btc-prices \
  --max-btc-disagreement-bps 100
```
```

- [ ] **Step 3: Run full Rust verification**

Run:
```bash
cd /Users/goon/polymarket/rust
cargo fmt --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

Expected: all pass.

- [ ] **Step 4: Run existing Python verification**

Run:
```bash
cd /Users/goon/polymarket
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```

Expected: existing Python suite still passes. If it fails only because of pre-existing dirty Python collector-audit changes, report that separately and do not mix those fixes into this Rust probe PR.

- [ ] **Step 5: Produce final timing summary**

Read `/Users/goon/polymarket/reports/live_probe/latest.json` and report:
- total elapsed milliseconds;
- number of normalized orderbooks;
- Chainlink BTC/USD price and timestamp;
- Kraken XBT/USD price and timestamp;
- BTC disagreement in bps;
- slowest latency mark;
- whether the report passed `verify_rust_probe_output.py`.

---

## Acceptance Criteria

This plan is complete only when:
- Rust workspace compiles.
- Rust tests pass.
- Existing Python checks still pass or pre-existing Python failures are explicitly separated.
- Live probe report has non-empty orderbooks.
- Live probe report has Chainlink BTC/USD and Kraken XBT/USD.
- Live probe report has BTC source disagreement in bps.
- Live probe report includes end-to-end elapsed time and named latency marks.
- No authenticated Polymarket trading code is added.
- No Python collector code is deleted in this milestone.
