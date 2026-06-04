# TUI K Strike Price Path and Outcome Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the current contract target `K` in the Market tab, render BTC/ETH price paths with current-window target lines, and keep expired Market rows visible until their official outcome has been shown for 30 seconds without letting stale pending outcome feeds pin rows forever.

**Status:** Implemented locally on `codex/polymarket-engine-tui`; awaiting final verification/deploy handoff.

**Architecture:** `K` is the venue-defined Chainlink start-reference price for the active window, so it must be surfaced from the Rust hot runtime/state-manager path and propagated through the Python runtime API into the read-only TUI. The TUI stores a small in-memory price history from runtime payloads and renders a compact right-side chart on the Market tab. Expired rows are retained by official outcome state instead of only by elapsed time since expiry.

**Tech Stack:** Rust 1.91 workspace crates (`polymarket-runtime-types`, `polymarket-live-probe`, `polymarket-cockpit-tui`), Python runtime API/monitor shaping, ratatui 0.30, pytest, cargo test, uv/ruff.

---

## File Structure

- Modify `rust/crates/polymarket-runtime-types/src/state.rs`
  - Add a serializable `ContractTarget` struct and `targets` field on `WarmStateSnapshot`.
- Modify `rust/crates/polymarket-runtime-types/src/lib.rs`
  - Re-export `ContractTarget`.
- Modify `rust/crates/polymarket-live-probe/src/state_manager.rs`
  - Compute current-window Chainlink start thresholds from bounded `LatestPrices` history.
  - Include current and next target rows; next rows have `threshold_price = None`.
- Modify `rust/crates/polymarket-live-probe/src/report.rs`
  - Include `targets` in `StateManagerReport`.
- Modify `src/polymarket_engine/monitor.py`
  - Preserve `targets` from status JSON and copy matching `threshold_price`, `threshold_event_ts`, and `threshold_observed_ts` onto runtime contract/orderbook rows.
- Modify `rust/crates/polymarket-cockpit-tui/src/status.rs`
  - Parse optional target fields on orderbook rows and parse runtime price rows for chart history.
- Modify `rust/crates/polymarket-cockpit-tui/src/state.rs`
  - Add bounded price-history storage.
  - Retain expired orderbooks while a fresh official outcome feed is still pending, and for 30 seconds after the TUI first sees a resolved official outcome.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
  - Add `K` column.
  - Filter expired rows through the new resolved-outcome retention rule.
- Create `rust/crates/polymarket-cockpit-tui/src/render/price_path.rs`
  - Render BTC and ETH mini price paths with current-window `K` marker text.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`
  - On Market tab, render `Market+Book` left and `Price Path` right, with `Systems` moved into the bottom row next to Logs.
- Modify `rust/crates/polymarket-cockpit-tui/src/layout.rs`
  - Add a bottom split for `systems` and `logs`; keep existing fallback dimensions.
- Update docs in `docs/SPOON_DEPLOYMENT.md`
  - Document that Market `K` is read-only Chainlink start-reference price and that next rows show pending.

---

### Task 1: Source-of-Truth K in Rust Status

**Files:**
- Modify: `rust/crates/polymarket-runtime-types/src/state.rs`
- Modify: `rust/crates/polymarket-runtime-types/src/lib.rs`
- Modify: `rust/crates/polymarket-live-probe/src/state_manager.rs`
- Modify: `rust/crates/polymarket-live-probe/src/report.rs`
- Test: `rust/crates/polymarket-live-probe/src/state_manager.rs`
- Test: `rust/crates/polymarket-live-probe/src/report.rs`

- [ ] **Step 1: Write failing state-manager tests**

Add a test near the existing `build_snapshot_from_warmed` tests in `rust/crates/polymarket-live-probe/src/state_manager.rs`:

```rust
#[test]
fn snapshot_reports_current_window_target_from_chainlink_start_tick() {
    let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
    let now = start + Duration::seconds(42);
    let config = StateManagerConfig {
        assets: vec!["BTC".to_owned()],
        interval: "5m".to_owned(),
        windows: 2,
        stale_after_ms: 3_000,
        market_refresh_interval_ms: 30_000,
        rest_backup_interval_ms: 1_000,
    };
    let warmed = vec![
        warmed_contract("BTC", start, "btc-current-up", "btc-current-down"),
        warmed_contract(
            "BTC",
            start + Duration::seconds(300),
            "btc-next-up",
            "btc-next-down",
        ),
    ];
    let history = vec![
        price_tick("BTC/USD", start - Duration::seconds(1), Decimal::new(63_900, 0)),
        price_tick("BTC/USD", start, Decimal::new(64_000, 0)),
        price_tick("BTC/USD", now, Decimal::new(64_050, 0)),
    ];
    let snapshot =
        build_snapshot_from_warmed_with_price_history(now, &config, &warmed, history, vec![])
            .unwrap();

    assert_eq!(snapshot.targets.len(), 2);
    assert_eq!(snapshot.targets[0].market_slug, "btc-updown-5m-1780302400");
    assert_eq!(snapshot.targets[0].asset, "BTC");
    assert_eq!(snapshot.targets[0].threshold_price, Some(Decimal::new(64_000, 0)));
    assert_eq!(snapshot.targets[0].threshold_event_ts, Some(start));
    assert_eq!(snapshot.targets[0].settlement_price, Some(Decimal::new(64_050, 0)));
    assert_eq!(snapshot.targets[1].market_slug, "btc-updown-5m-1780302700");
    assert_eq!(snapshot.targets[1].threshold_price, None);
}
```

Add a report serialization test in `rust/crates/polymarket-live-probe/src/report.rs`:

```rust
#[test]
fn state_manager_report_includes_contract_targets() {
    let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
    let snapshot = WarmStateSnapshot {
        observed_ts: start + chrono::Duration::seconds(1),
        current: vec![],
        next: vec![],
        next_next: vec![],
        chainlink_prices: vec![],
        proxy_prices: vec![],
        orderbooks: vec![],
        targets: vec![ContractTarget {
            asset: "BTC".to_owned(),
            interval: "5m".to_owned(),
            market_slug: "btc-updown-5m-1780302400".to_owned(),
            start_ts: start,
            end_ts: start + chrono::Duration::seconds(300),
            threshold_price: Some(Decimal::new(64_000, 0)),
            threshold_event_ts: Some(start),
            threshold_observed_ts: Some(start + chrono::Duration::milliseconds(5)),
            settlement_price: Some(Decimal::new(64_050, 0)),
            settlement_event_ts: Some(start + chrono::Duration::seconds(1)),
        }],
        freshness: vec![],
        health_flags: vec![],
    };

    let report = build_state_manager_report(StateManagerReportInput {
        elapsed_ms: 1,
        snapshot,
        subscriptions: vec![],
        websocket_status: vec![],
        hot_decision_telemetry: None,
    });

    assert_eq!(report.targets[0].threshold_price, Some(Decimal::new(64_000, 0)));
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-live-probe snapshot_reports_current_window_target_from_chainlink_start_tick state_manager_report_includes_contract_targets
```

Expected: FAIL because `ContractTarget`, `WarmStateSnapshot.targets`, `StateManagerReport.targets`, and `build_snapshot_from_warmed_with_price_history` do not exist.

- [ ] **Step 3: Implement source-of-truth target rows**

Add this struct and field in `rust/crates/polymarket-runtime-types/src/state.rs`:

```rust
use rust_decimal::Decimal;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ContractTarget {
    pub asset: String,
    pub interval: String,
    pub market_slug: String,
    pub start_ts: DateTime<Utc>,
    pub end_ts: DateTime<Utc>,
    pub threshold_price: Option<Decimal>,
    pub threshold_event_ts: Option<DateTime<Utc>>,
    pub threshold_observed_ts: Option<DateTime<Utc>>,
    pub settlement_price: Option<Decimal>,
    pub settlement_event_ts: Option<DateTime<Utc>>,
}
```

Add `pub targets: Vec<ContractTarget>,` to `WarmStateSnapshot` and update every test fixture that constructs `WarmStateSnapshot` with `targets: vec![]`.

In `rust/crates/polymarket-runtime-types/src/lib.rs`, change:

```rust
pub use state::{FeedFreshness, WarmStateSnapshot};
```

to:

```rust
pub use state::{ContractTarget, FeedFreshness, WarmStateSnapshot};
```

In `rust/crates/polymarket-live-probe/src/state_manager.rs`, add a public testable wrapper:

```rust
pub fn build_snapshot_from_warmed_with_price_history(
    now: DateTime<Utc>,
    config: &StateManagerConfig,
    warmed: &[WarmedContract],
    chainlink_history: Vec<NormalizedPriceTick>,
    orderbooks: Vec<NormalizedOrderBook>,
) -> Result<WarmStateSnapshot> {
    let latest_chainlink = latest_prices_by_symbol(&chainlink_history);
    build_snapshot_from_warmed_inner(
        now,
        config,
        warmed,
        latest_chainlink,
        chainlink_history,
        orderbooks,
    )
}
```

Keep existing `build_snapshot_from_warmed(...)` as a compatibility wrapper that passes the latest rows as both latest and history.

In `StateManagerRuntime::snapshot`, use:

```rust
let chainlink_prices = self.latest_prices.snapshot().await;
let chainlink_history = self
    .latest_prices
    .history_snapshot_for_assets(warmed.iter().map(|contract| contract.window.asset.as_str()))
    .await;
```

Then pass both latest rows and history into the inner builder.

Implement target construction:

```rust
fn contract_targets(
    warmed: &[WarmedContract],
    chainlink_history: &[NormalizedPriceTick],
    now: DateTime<Utc>,
) -> Vec<ContractTarget> {
    warmed
        .iter()
        .filter(|contract| contract.window.end_ts > now)
        .map(|contract| {
            let threshold = if contract.window.start_ts <= now {
                latest_price_at_or_before(
                    chainlink_history,
                    &contract.window.asset,
                    contract.window.start_ts,
                    now,
                )
            } else {
                None
            };
            let settlement = latest_price_at_or_before(
                chainlink_history,
                &contract.window.asset,
                now,
                now,
            );
            ContractTarget {
                asset: contract.window.asset.clone(),
                interval: contract.window.interval.clone(),
                market_slug: contract.window.slug(),
                start_ts: contract.window.start_ts,
                end_ts: contract.window.end_ts,
                threshold_price: threshold.map(|tick| tick.price),
                threshold_event_ts: threshold.map(|tick| tick.event_ts),
                threshold_observed_ts: threshold.map(|tick| tick.observed_ts),
                settlement_price: settlement.map(|tick| tick.price),
                settlement_event_ts: settlement.map(|tick| tick.event_ts),
            }
        })
        .collect()
}
```

In `rust/crates/polymarket-live-probe/src/report.rs`, add `pub targets: Vec<ContractTarget>,` to `StateManagerReport` and set `targets: input.snapshot.targets`.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-live-probe snapshot_reports_current_window_target_from_chainlink_start_tick state_manager_report_includes_contract_targets
```

Expected: PASS.

- [ ] **Step 5: Run crate tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-runtime-types -p polymarket-live-probe
```

Expected: PASS.

---

### Task 2: Propagate K Through Python Runtime API

**Files:**
- Modify: `src/polymarket_engine/monitor.py`
- Test: `tests/test_monitor.py`

- [ ] **Step 1: Write failing monitor test**

Add this test in `tests/test_monitor.py` near the state-manager status tests:

```python
def test_state_manager_orderbook_rows_include_matching_contract_target() -> None:
    payload = {
        "generated_at": "2026-06-04T07:43:10Z",
        "current": [
            {
                "window": {
                    "asset": "BTC",
                    "interval": "5m",
                    "start_ts": "2026-06-04T07:40:00Z",
                    "end_ts": "2026-06-04T07:45:00Z",
                },
                "up": {"asset": "BTC", "side": "Up", "token_id": "btc-up"},
                "down": {"asset": "BTC", "side": "Down", "token_id": "btc-down"},
            }
        ],
        "next": [],
        "orderbooks": [],
        "chainlink_prices": [],
        "targets": [
            {
                "asset": "BTC",
                "interval": "5m",
                "market_slug": "btc-updown-5m-1780558800",
                "start_ts": "2026-06-04T07:40:00Z",
                "end_ts": "2026-06-04T07:45:00Z",
                "threshold_price": "64000",
                "threshold_event_ts": "2026-06-04T07:40:00Z",
                "threshold_observed_ts": "2026-06-04T07:40:00.005Z",
                "settlement_price": "64050",
                "settlement_event_ts": "2026-06-04T07:43:09Z",
            }
        ],
    }

    snapshot = snapshot_from_status_payload(payload, limit=8)

    assert snapshot.orderbooks[0]["threshold_price"] == "64000"
    assert snapshot.orderbooks[0]["threshold_event_ts"] == "2026-06-04T07:40:00Z"
    assert snapshot.orderbooks[0]["threshold_observed_ts"] == "2026-06-04T07:40:00.005Z"
    assert snapshot.orderbooks[0]["settlement_price"] == "64050"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_monitor.py::test_state_manager_orderbook_rows_include_matching_contract_target -q
```

Expected: FAIL because target fields are not attached to orderbook rows.

- [ ] **Step 3: Implement target propagation**

In `src/polymarket_engine/monitor.py`, build a target map in `_state_manager_orderbook_rows`:

```python
targets_by_slug = {
    str(row.get("market_slug", "")).lower(): dict(row)
    for row in payload.get("targets", ())
    if isinstance(row, dict) and row.get("market_slug")
}
```

After contract fields are copied onto `row`, copy matching target fields:

```python
target = targets_by_slug.get(str(contract.get("market_slug", "")).lower())
if target is not None:
    for key in (
        "threshold_price",
        "threshold_event_ts",
        "threshold_observed_ts",
        "settlement_price",
        "settlement_event_ts",
    ):
        row[key] = target.get(key)
```

Also preserve these fields in `_state_manager_contract_rows` if a future API consumer uses `snapshot.contracts`.

- [ ] **Step 4: Run targeted monitor tests**

Run:

```bash
uv run pytest tests/test_monitor.py::test_state_manager_orderbook_rows_include_matching_contract_target -q
uv run pytest tests/test_monitor.py -q
```

Expected: PASS.

---

### Task 3: Market K Column, Price Path Chart, and Bottom Systems Split

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/layout.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/render/price_path.rs`
- Test: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Test: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Test: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
- Test: `rust/crates/polymarket-cockpit-tui/src/render/price_path.rs`

- [ ] **Step 1: Write failing TUI parsing and state tests**

In `rust/crates/polymarket-cockpit-tui/src/status.rs`, extend `RuntimeOrderbookRow` tests to assert parsing:

```rust
assert_eq!(monitor.orderbooks[0].threshold_price.as_deref(), Some("64000"));
assert_eq!(monitor.orderbooks[0].settlement_price.as_deref(), Some("64050"));
```

In `rust/crates/polymarket-cockpit-tui/src/state.rs`, add:

```rust
#[test]
fn apply_runtime_monitor_appends_changed_price_history() {
    let mut app = AppState::default();
    let first = RuntimeMonitor {
        generated_at: "2026-06-04T07:43:10Z".to_string(),
        price_rows: vec![price_row("BTC/USD", "64050")],
        orderbooks: Vec::new(),
    };
    let second = RuntimeMonitor {
        generated_at: "2026-06-04T07:43:11Z".to_string(),
        price_rows: vec![price_row("BTC/USD", "64051")],
        orderbooks: Vec::new(),
    };

    app.apply_runtime_monitor(first);
    app.apply_runtime_monitor(second);

    assert_eq!(app.price_history_for("BTC/USD").len(), 2);
}
```

- [ ] **Step 2: Write failing Market/Price Path render helper tests**

In `rust/crates/polymarket-cockpit-tui/src/render/market.rs`, extend header and row tests:

```rust
assert_eq!(
    market_header_labels(),
    ["", "Expires", "Market", "K", "UP bid/ask", "DOWN bid/ask", "Spread", "TTE", "Outcome"]
);
assert_eq!(rows[0].k, "64,000.00");
assert_eq!(rows[1].k, "pending");
```

Create `rust/crates/polymarket-cockpit-tui/src/render/price_path.rs` with tests first:

```rust
#[test]
fn price_path_rows_include_latest_price_and_current_target() {
    let mut app = AppState::default();
    app.apply_runtime_monitor(RuntimeMonitor {
        generated_at: "2026-06-04T07:43:10Z".to_string(),
        price_rows: vec![price_row("BTC/USD", "64050")],
        orderbooks: vec![orderbook_with_threshold("BTC", "64000")],
    });

    let rows = price_path_rows(&app);

    assert_eq!(rows[0].asset, "BTC");
    assert_eq!(rows[0].latest, "64,050.00");
    assert_eq!(rows[0].target, "K 64,000.00");
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui status state render::market render::price_path
```

Expected: FAIL because threshold fields, price history, `K` column, and `price_path` module do not exist.

- [ ] **Step 4: Implement TUI parsing/state/rendering**

In `RuntimeOrderbookRow`, add optional scalar string fields:

```rust
pub threshold_price: Option<String>,
pub threshold_event_ts: Option<String>,
pub threshold_observed_ts: Option<String>,
pub settlement_price: Option<String>,
pub settlement_event_ts: Option<String>,
```

In `AppState`, add:

```rust
pub price_history: Vec<PriceHistoryPoint>,
```

with:

```rust
#[derive(Debug, Clone, PartialEq)]
pub struct PriceHistoryPoint {
    pub symbol: String,
    pub observed_at: String,
    pub price: f64,
}
```

Add `apply_runtime_monitor`, `price_history_for`, and bounded append logic that:

- parses `RuntimePriceRow.price`;
- uses `observed_ts` or monitor `generated_at`;
- appends only when price changes for the symbol;
- keeps at most 240 points total.

Update existing runtime update application to call `app.apply_runtime_monitor(next_monitor)` instead of replacing `runtime_monitor` directly.

In `market.rs`, add `k: String` to `MarketDisplayRow`, `K` to headers, and a formatter:

```rust
fn market_k(group: &market_view::MarketGroup<'_>) -> String {
    group
        .up
        .or(group.down)
        .and_then(|row| row.threshold_price.as_deref())
        .and_then(positive_number)
        .map(format_usd)
        .unwrap_or_else(|| "pending".to_string())
}
```

Create `price_path.rs` with a compact render based on `ratatui::widgets::Sparkline` or text rows. Keep it cheap: no API calls, no DuckDB, no filesystem access.

In `layout.rs`, extend `BodyLayout`:

```rust
pub struct BodyLayout {
    pub primary: Rect,
    pub secondary: Rect,
    pub systems: Rect,
    pub logs: Rect,
}
```

Split the bottom row horizontally:

```rust
let [systems, logs] = Layout::default()
    .direction(Direction::Horizontal)
    .constraints([Constraint::Percentage(35), Constraint::Percentage(65)])
    .areas(bottom);
```

In `render/mod.rs`, render `price_path::render(frame, body.secondary, app)` on `MainTab::Market`, render `systems` in `body.systems`, and render `logs` in `body.logs`.

- [ ] **Step 5: Run TUI tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui status state render::market render::price_path layout
```

Expected: PASS.

---

### Task 4: Retain Expired Market Rows Until Outcome Is Shown for 30 Seconds

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
- Test: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Test: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`

- [ ] **Step 1: Write failing outcome-retention tests**

In `state.rs`, add a test:

```rust
#[test]
fn expired_market_stays_until_resolved_outcome_has_been_visible_for_30_seconds() {
    let mut app = app_with_expired_market("btc-updown-5m-1780521900");

    app.apply_runtime_outcomes(RuntimeOutcomes {
        ok: true,
        state: "OK".to_string(),
        generated_at: Some("2026-06-03T21:26:10Z".to_string()),
        rows: vec![pending_outcome("BTC", "btc-updown-5m-1780521900")],
    });
    assert!(app.should_retain_expired_market("BTC", "btc-updown-5m-1780521900", "2026-06-03T21:26:10Z"));

    app.apply_runtime_outcomes(RuntimeOutcomes {
        ok: true,
        state: "OK".to_string(),
        generated_at: Some("2026-06-03T21:26:20Z".to_string()),
        rows: vec![resolved_outcome("BTC", "btc-updown-5m-1780521900", "UP")],
    });
    assert!(app.should_retain_expired_market("BTC", "btc-updown-5m-1780521900", "2026-06-03T21:26:49Z"));
    assert!(!app.should_retain_expired_market("BTC", "btc-updown-5m-1780521900", "2026-06-03T21:26:51Z"));
}
```

In `market.rs`, add a test that a market row with official winner remains when generated time is more than 60 seconds after expiry but less than 30 seconds after first resolved outcome observation.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui state render::market
```

Expected: FAIL because retention is still based only on expiry age.

- [ ] **Step 3: Implement outcome-resolution retention**

In `AppState`, add:

```rust
pub resolved_outcome_seen_at: std::collections::HashMap<String, String>,
```

Add `apply_runtime_outcomes` that records the first `generated_at` timestamp for each market key when `official_winner` is non-empty.

Define market keys with slug first, fallback to `asset|expiry_ts`:

```rust
fn outcome_market_key(outcome: &RuntimeOutcomeRow) -> Option<String>
fn group_market_key(group: &market_view::MarketGroup<'_>) -> String
```

Expose:

```rust
pub fn should_retain_group_after_expiry(
    &self,
    group: &market_view::MarketGroup<'_>,
    generated_at: &str,
) -> bool
```

Rules:

- if not expired, retain;
- if expired and matching outcome has no official winner, retain;
- if expired and matching outcome has official winner, retain until 30 seconds after first seen resolved timestamp;
- if there is no matching outcome and expiry is less than 60 seconds ago, retain as a discovery handoff;
- otherwise drop from Market.

Update `monitor_with_expiration_handoff` and `market_display_rows` to use the new retention method instead of `expired_beyond_handoff`.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui state render::market
```

Expected: PASS.

---

### Task 5: Documentation and Full Verification

**Files:**
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Update docs**

Add to the TUI section:

```markdown
The Market tab displays `K`, the Chainlink start-reference price for the active
window. This value comes from the Rust state-manager hot path. Next-window rows
show `pending` until the window starts and the state-manager can prove the
start-reference tick. The Market price chart is a read-only TUI-side view of
recent runtime price rows, with a target marker for the active window `K`.
Expired rows stay on Market while official outcome is pending and for 30 seconds
after the TUI first sees the official winner; the full history remains in the
Outcomes tab.
```

- [ ] **Step 2: Run full focused verification**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-runtime-types -p polymarket-live-probe -p polymarket-cockpit-tui
uv run pytest tests/test_monitor.py -q
uv run ruff check src/polymarket_engine/monitor.py tests/test_monitor.py
```

Expected: all pass.

- [ ] **Step 3: Manual runtime smoke locally or on THEPC**

After deploy, verify:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd /home/ender/polymarket && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml ps && python3 scripts/check_collector_status.py --status-path /home/ender/polymarket-data/live/status.json --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000 --max-websocket-event-age-ms 30000 --raw-root /home/ender/polymarket-data/raw --max-raw-event-age-ms 30000 --normalized-health-path /home/ender/polymarket-data/live/normalized_health.json --max-normalized-health-age-ms 30000 --expected-prewarm-windows 2"'
```

Expected: containers healthy and status check `ok=True`.

---

## Self-Review

- Spec coverage: covers Market `K`, price path chart with current-window target, no guessed threshold from labels, Systems moved to bottom next to Logs, and expired row retention until 30 seconds after resolved official outcome is observed.
- Placeholder scan: no `TBD`, `TODO`, or vague "add tests" placeholders are present.
- Type consistency: Rust target fields use `threshold_price`, `threshold_event_ts`, `threshold_observed_ts`, `settlement_price`, and `settlement_event_ts` consistently across status JSON, Python runtime rows, and TUI parsing.
