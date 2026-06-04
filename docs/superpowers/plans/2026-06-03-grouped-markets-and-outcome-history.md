# Grouped Markets And Outcome History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each BTC/ETH 5m window as one binary market in the cockpit while preserving separate UP/DOWN token books internally, and persist a replay-safe outcome history for every recorded market.

**Architecture:** Rust TUI gets a small market grouping layer that turns token-level order books into market-level display rows and selected market groups. Python/DuckDB gets a validation writer that derives post-expiry Chainlink labels after expiry, stores official oracle resolution fields separately, and exposes the history read-only through the runtime API.

**Tech Stack:** Rust, ratatui, serde, Python, DuckDB, Polars, FastAPI, pytest, cargo test.

---

## File Structure

- Create `rust/crates/polymarket-cockpit-tui/src/market_view.rs`
  - Owns market grouping, stable market keys, side lookup, sort order, and display labels.
- Modify `rust/crates/polymarket-cockpit-tui/src/main.rs`
  - Exports the new `market_view` module.
- Modify `rust/crates/polymarket-cockpit-tui/src/state.rs`
  - Tracks selected market group instead of selected token row.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
  - Renders one market row with UP and DOWN quotes.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/orderbook.rs`
  - Renders the selected market's UP and DOWN token books together.
- Modify `rust/crates/polymarket-cockpit-tui/src/status.rs`, `client.rs`, `event_loop.rs`
  - Adds outcome-history payload structs and polling if the TUI outcome tab is included.
- Create `rust/crates/polymarket-cockpit-tui/src/render/outcomes.rs`
  - Renders the read-only outcome history table.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`, `state.rs`
  - Adds the `Outcomes` tab.
- Create `src/polymarket_engine/validation/outcomes.py`
  - Owns computed Chainlink label derivation and runtime rows.
- Modify `src/polymarket_engine/validation/__init__.py`
  - Exports the outcome functions. Create package if missing.
- Modify `src/polymarket_engine/storage/schema.sql`
  - Adds `validation.market_outcome_history`.
- Modify `src/polymarket_engine/storage/duckdb_store.py`
  - Adds upsert/read helpers and normalized health coverage.
- Modify `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
  - Runs outcome derivation after state/probability work, only for expired markets.
- Modify `src/polymarket_engine/runtime_api.py`
  - Adds read-only `GET /api/runtime/outcomes`.
- Modify `tests/storage/test_schema.py`, `tests/storage/test_normalized_writes.py`, `tests/test_runtime_api.py`, `tests/validation/test_outcomes.py`
  - Covers schema, writer, derivation, and API behavior.
- Modify Rust TUI tests in `state.rs`, `render/market.rs`, `render/orderbook.rs`, `status.rs`, `event_loop.rs`
  - Covers grouping, selection, book rendering, and optional outcomes polling.
- Modify `docs/BINARY_CONTRACT_ENGINE_PLAN.md`, `docs/PART_TWO_LIVE_COLLECTORS.md`, `docs/SPOON_DEPLOYMENT.md`
  - Documents the internal token/display split and label-source semantics.

## Task 1: Add Market Grouping For The TUI

**Files:**
- Create: `rust/crates/polymarket-cockpit-tui/src/market_view.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/main.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`

- [ ] **Step 1: Write failing Rust tests for grouped market selection**

Add tests in `rust/crates/polymarket-cockpit-tui/src/market_view.rs`:

```rust
#[test]
fn market_groups_merge_up_and_down_token_books_for_one_window() {
    let books = vec![
        orderbook("BTC", "UP", "btc-updown-5m-1780521900", "up-token"),
        orderbook("BTC", "DOWN", "btc-updown-5m-1780521900", "down-token"),
    ];

    let groups = market_groups(&books);

    assert_eq!(groups.len(), 1);
    assert_eq!(groups[0].asset, "BTC");
    assert_eq!(groups[0].up.unwrap().token_id.as_deref(), Some("up-token"));
    assert_eq!(groups[0].down.unwrap().token_id.as_deref(), Some("down-token"));
}
```

Update `state.rs` tests so `select_next_market()` moves between grouped windows, not UP/DOWN token rows:

```rust
#[test]
fn market_selection_moves_by_market_group_not_outcome_token() {
    let mut app = AppState {
        runtime_monitor: Some(monitor(vec![
            orderbook("BTC", "UP", "btc-updown-5m-1780521900", "2026-06-03T21:22:15Z"),
            orderbook("BTC", "DOWN", "btc-updown-5m-1780521900", "2026-06-03T21:22:15Z"),
            orderbook("ETH", "UP", "eth-updown-5m-1780521900", "2026-06-03T21:22:15Z"),
            orderbook("ETH", "DOWN", "eth-updown-5m-1780521900", "2026-06-03T21:22:15Z"),
        ])),
        ..Default::default()
    };

    app.sync_market_selection();
    assert_eq!(app.selected_market_index(), Some(0));
    app.select_next_market();
    assert_eq!(app.selected_market_index(), Some(1));
}
```

- [ ] **Step 2: Run the failing Rust tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui market_groups_merge_up_and_down_token_books_for_one_window market_selection_moves_by_market_group_not_outcome_token
```

Expected: tests fail because `market_view` does not exist and selection still counts token rows.

- [ ] **Step 3: Implement `market_view.rs`**

Implement a helper with this public shape:

```rust
use chrono::{DateTime, Local, TimeZone, Utc};

use crate::status::RuntimeOrderbookRow;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MarketGroup<'a> {
    pub key: String,
    pub asset: String,
    pub market_slug: String,
    pub label: String,
    pub start_ts: Option<DateTime<Utc>>,
    pub expiry_ts: Option<DateTime<Utc>>,
    pub up: Option<&'a RuntimeOrderbookRow>,
    pub down: Option<&'a RuntimeOrderbookRow>,
}

pub fn market_groups(orderbooks: &[RuntimeOrderbookRow]) -> Vec<MarketGroup<'_>> {
    // Group by market_slug when present. Fall back to asset + start/expiry, then token identity.
    // Sort BTC before ETH, then chronological start/expiry.
}

pub fn market_key(orderbook: &RuntimeOrderbookRow) -> String {
    // Prefer market_slug. Include asset/start/expiry to survive missing slug.
}

pub fn local_expiry_label(expiry_ts: Option<DateTime<Utc>>) -> String {
    expiry_ts
        .map(|ts| ts.with_timezone(&Local).format("%H:%M %Z").to_string())
        .unwrap_or_else(|| "-".to_string())
}
```

Keep the grouping internal to the TUI. Do not change backend token-level order books.

- [ ] **Step 4: Switch `AppState` to selected market groups**

In `state.rs`:

```rust
pub fn selected_market_group(&self) -> Option<crate::market_view::MarketGroup<'_>> {
    let index = self.effective_market_index()?;
    self.runtime_monitor
        .as_ref()
        .and_then(|monitor| crate::market_view::market_groups(&monitor.orderbooks).get(index).copied())
}
```

Update `orderbook_count`, `default_market_index`, and `set_selected_market_index` to use `market_groups(&monitor.orderbooks)`.

- [ ] **Step 5: Run focused Rust tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui state::tests market_view
```

Expected: all targeted grouping and selection tests pass.

## Task 2: Render One Market Row And A Dual-Side Book

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/orderbook.rs`

- [ ] **Step 1: Write failing market-render tests**

Replace token-row assumptions with market-row assertions in `render/market.rs`:

```rust
#[test]
fn market_rows_show_one_binary_market_with_up_and_down_quotes() {
    let mut app = AppState {
        runtime_monitor: Some(RuntimeMonitor {
            generated_at: "2026-06-03T21:22:15Z".to_string(),
            price_rows: Vec::new(),
            orderbooks: vec![
                orderbook_with_quote("BTC", "UP", "btc-updown-5m-1780521900", "0.44", "0.45"),
                orderbook_with_quote("BTC", "DOWN", "btc-updown-5m-1780521900", "0.55", "0.56"),
            ],
        }),
        ..Default::default()
    };
    app.sync_market_selection();

    let rows = market_rows(&app);

    assert_eq!(rows.len(), 2); // BTC section + one market
    assert_eq!(rows[1].marker, ">");
    assert_eq!(rows[1].market, format!("BTC 5m {}", local_epoch_label(1_780_521_900)));
    assert_eq!(rows[1].up, "0.44/0.45");
    assert_eq!(rows[1].down, "0.55/0.56");
}
```

- [ ] **Step 2: Write failing dual-book tests**

Add an `orderbook.rs` test:

```rust
#[test]
fn book_rows_render_selected_market_up_and_down_books_together() {
    let mut app = app_with_selected_btc_group();
    app.sync_market_selection();

    let title = book_title(&app);
    let rows = book_rows(&app);

    assert!(title.starts_with("Book: BTC 5m"));
    assert_eq!(rows[0].contract, "UP");
    assert_eq!(rows[1].contract, "DOWN");
}
```

- [ ] **Step 3: Run failing render tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui render::market render::orderbook
```

Expected: tests fail because current rendering emits one row per token book.

- [ ] **Step 4: Implement grouped market rendering**

Change `MarketDisplayRow` to:

```rust
pub struct MarketDisplayRow {
    pub marker: String,
    pub market: String,
    pub up: String,
    pub down: String,
    pub spread: String,
    pub seen: String,
}
```

Render columns:

```rust
["", "Market", "UP bid/ask", "DOWN bid/ask", "Spread", "Seen"]
```

Compute `UP bid/ask` and `DOWN bid/ask` from the side token rows. If a side is missing or nonpositive, show `-`. The spread column should show the tighter positive spread across both sides if present.

- [ ] **Step 5: Implement dual-side book rendering**

Update `book_rows(app)` to read `app.selected_market_group()`. For each available side, emit up to 6 levels with `contract = "UP"` or `"DOWN"`. Keep bid sorting descending and ask sorting ascending.

- [ ] **Step 6: Run focused Rust tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui render::market render::orderbook state::tests
```

Expected: all targeted TUI tests pass.

## Task 3: Add Market Outcome History Storage

**Files:**
- Create: `src/polymarket_engine/validation/__init__.py`
- Create: `src/polymarket_engine/validation/outcomes.py`
- Modify: `src/polymarket_engine/storage/schema.sql`
- Modify: `src/polymarket_engine/storage/duckdb_store.py`
- Modify: `tests/storage/test_schema.py`
- Modify: `tests/storage/test_normalized_writes.py`
- Create: `tests/validation/test_outcomes.py`

- [ ] **Step 1: Write failing schema test**

In `tests/storage/test_schema.py`, add `"validation.market_outcome_history"` to the expected table set.

Expected table columns:

```sql
market_id, condition_id, market_slug, asset, interval, start_ts, expiry_ts,
up_token_id, down_token_id,
threshold_price, threshold_event_ts, threshold_observed_ts,
end_price, end_event_ts, end_observed_ts,
computed_winner, computed_label_source, computed_at,
official_winner, official_resolution_status, official_label_source, official_resolved_at,
rule_hash, mismatch, updated_at
```

- [ ] **Step 2: Write failing outcome derivation tests**

In `tests/validation/test_outcomes.py`:

```python
def test_computed_outcome_uses_exact_up_greater_than_or_equal_rule(tmp_path: Path) -> None:
    store = seeded_store_with_btc_market(tmp_path, start_price=65000.0, end_price=65000.0)

    written = upsert_computed_market_outcomes(store=store, asof_ts=UTC_EXPIRY_PLUS_ONE)

    assert written == 1
    row = fetch_outcome(store.db_path, "btc-updown-5m-1780521900")
    assert row["computed_winner"] == "UP"
    assert row["official_resolution_status"] == "pending"
    assert row["mismatch"] is None
```

```python
def test_computed_outcome_marks_down_when_end_price_is_below_start(tmp_path: Path) -> None:
    store = seeded_store_with_btc_market(tmp_path, start_price=65000.0, end_price=64999.99)

    upsert_computed_market_outcomes(store=store, asof_ts=UTC_EXPIRY_PLUS_ONE)

    assert fetch_outcome(store.db_path, "btc-updown-5m-1780521900")["computed_winner"] == "DOWN"
```

```python
def test_computed_outcome_waits_until_after_expiry(tmp_path: Path) -> None:
    store = seeded_store_with_btc_market(tmp_path, start_price=65000.0, end_price=64999.99)

    written = upsert_computed_market_outcomes(store=store, asof_ts=UTC_BEFORE_EXPIRY)

    assert written == 0
```

- [ ] **Step 3: Run failing Python tests**

Run:

```bash
uv run pytest -q tests/storage/test_schema.py tests/validation/test_outcomes.py
```

Expected: failures for missing package/table/functions.

- [ ] **Step 4: Add schema and writer**

Add table:

```sql
CREATE TABLE IF NOT EXISTS validation.market_outcome_history (
    market_id VARCHAR PRIMARY KEY,
    condition_id VARCHAR NOT NULL,
    market_slug VARCHAR NOT NULL,
    asset VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    start_ts TIMESTAMPTZ NOT NULL,
    expiry_ts TIMESTAMPTZ NOT NULL,
    up_token_id VARCHAR NOT NULL,
    down_token_id VARCHAR NOT NULL,
    threshold_price DOUBLE,
    threshold_event_ts TIMESTAMPTZ,
    threshold_observed_ts TIMESTAMPTZ,
    end_price DOUBLE,
    end_event_ts TIMESTAMPTZ,
    end_observed_ts TIMESTAMPTZ,
    computed_winner VARCHAR,
    computed_label_source VARCHAR,
    computed_at TIMESTAMPTZ,
    official_winner VARCHAR,
    official_resolution_status VARCHAR NOT NULL,
    official_label_source VARCHAR,
    official_resolved_at TIMESTAMPTZ,
    rule_hash VARCHAR NOT NULL,
    mismatch BOOLEAN,
    updated_at TIMESTAMPTZ NOT NULL
);
```

Add a `MarketOutcomeRecord` dataclass and `DuckDbIngestStore.upsert_market_outcome_records(records)`.

- [ ] **Step 5: Implement computed derivation**

In `validation/outcomes.py`, derive only after expiry:

```python
def computed_winner(*, threshold_price: float, end_price: float) -> str:
    return "UP" if end_price >= threshold_price else "DOWN"
```

Use `core.contracts` grouped by `market_id`, requiring both UP and DOWN token ids. Use `core.price_ticks` from `polymarket_rtds_chainlink`:

- threshold: latest tick with `event_ts <= start_ts` and `observed_ts <= asof_ts`
- end: latest tick with `event_ts <= expiry_ts` and `observed_ts <= asof_ts`
- do not write a computed winner if either price is missing
- set official fields to `official_resolution_status = "pending"` until an oracle adapter fills them

- [ ] **Step 6: Run focused Python tests**

Run:

```bash
uv run pytest -q tests/storage/test_schema.py tests/storage/test_normalized_writes.py tests/validation/test_outcomes.py
```

Expected: all targeted tests pass.

## Task 4: Wire Outcome Derivation Into The Normalizer And Runtime API

**Files:**
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Modify: `src/polymarket_engine/runtime_api.py`
- Modify: `tests/ingestion/test_rust_normalizer_sidecar.py`
- Modify: `tests/test_runtime_api.py`

- [ ] **Step 1: Write failing sidecar test**

In `tests/ingestion/test_rust_normalizer_sidecar.py`, seed an expired contract plus Chainlink start/end ticks, run one normalizer cycle, then assert:

```python
assert conn.execute("select computed_winner from validation.market_outcome_history").fetchone() == ("UP",)
```

- [ ] **Step 2: Write failing runtime API test**

In `tests/test_runtime_api.py`:

```python
def test_runtime_outcomes_returns_market_level_history(tmp_path: Path) -> None:
    store = seeded_store_with_outcome(tmp_path, computed_winner="UP")
    app = create_app(status_path=tmp_path / "missing-status.json", duckdb_path=store.db_path)

    payload = TestClient(app).get("/api/runtime/outcomes?limit=4").json()

    assert payload["ok"] is True
    assert payload["rows"][0]["market"] == "BTC 5m"
    assert payload["rows"][0]["computed_winner"] == "UP"
    assert payload["rows"][0]["official_resolution_status"] == "pending"
```

- [ ] **Step 3: Run failing tests**

Run:

```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py::test_normalizer_writes_market_outcome_history tests/test_runtime_api.py::test_runtime_outcomes_returns_market_level_history
```

Expected: failures because the sidecar and endpoint are not wired.

- [ ] **Step 4: Wire the sidecar**

After probability output computation in `_run_rust_normalizer_cycle_with_store`, `_run_changed_rust_normalizer_cycle_with_store`, and `_run_idle_rust_normalizer_cycle_with_store`, call:

```python
market_outcomes_written = upsert_computed_market_outcomes(
    store=store,
    asof_ts=datetime.now(timezone.utc),
)
```

Add `market_outcomes_written` to `RustNormalizerCycleResult` and `_cycle_log_line`.

- [ ] **Step 5: Add runtime API endpoint**

Add:

```python
@router.get("/outcomes")
def runtime_outcomes(limit: int = 20) -> dict[str, Any]:
    return build_outcome_history_payload(duckdb_path=duckdb_path, limit=limit)
```

Payload rows must be market-level, sorted by `expiry_ts desc`, and include local-display-safe strings only:

```json
{
  "ok": true,
  "state": "OK",
  "rows": [
    {
      "market": "BTC 5m",
      "market_id": "btc-updown-5m-1780521900",
      "asset": "BTC",
      "start_ts": "...",
      "expiry_ts": "...",
      "computed_winner": "UP",
      "official_winner": null,
      "official_resolution_status": "pending",
      "mismatch": null
    }
  ]
}
```

- [ ] **Step 6: Run targeted Python tests**

Run:

```bash
uv run pytest -q tests/validation/test_outcomes.py tests/ingestion/test_rust_normalizer_sidecar.py tests/test_runtime_api.py
```

Expected: all targeted tests pass.

## Task 5: Add A Read-Only Outcomes Tab To The TUI

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/client.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/render/outcomes.rs`

- [ ] **Step 1: Write failing payload parse test**

In `status.rs`:

```rust
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
            "computed_winner": "UP",
            "official_winner": null,
            "official_resolution_status": "pending",
            "mismatch": null
        }]
    }"#;

    let outcomes: RuntimeOutcomes = serde_json::from_str(payload).unwrap();

    assert_eq!(outcomes.rows[0].computed_winner.as_deref(), Some("UP"));
    assert_eq!(outcomes.rows[0].official_resolution_status, "pending");
}
```

- [ ] **Step 2: Write failing render test**

In `render/outcomes.rs`:

```rust
#[test]
fn outcome_rows_keep_computed_and_official_labels_separate() {
    let app = app_with_outcomes("UP", None, "pending");

    let rows = outcome_rows(&app);

    assert_eq!(rows[0].computed, "UP");
    assert_eq!(rows[0].official, "-");
    assert_eq!(rows[0].status, "pending");
}
```

- [ ] **Step 3: Run failing Rust tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui outcomes_payload_parses_market_level_history outcome_rows_keep_computed_and_official_labels_separate
```

Expected: failures because outcome types/tab do not exist.

- [ ] **Step 4: Add outcome structs, client method, and poll field**

Add `RuntimeOutcomes` and `RuntimeOutcomeRow` in `status.rs`. Add `EngineClient::outcomes(limit)`. Add an `outcomes: Option<RuntimeOutcomes>` field to `RuntimeUpdate`, `AppState`, and `poll_runtime` using the existing concurrent `tokio::join!` pattern.

- [ ] **Step 5: Add the `Outcomes` tab**

Add `MainTab::Outcomes` after `Probability`. Render columns:

```text
Market | Expiry | Computed | Official | Status | Mismatch
```

If official is pending, show `-` in Official and `pending` in Status.

- [ ] **Step 6: Run focused Rust tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: all TUI tests pass.

## Task 6: Document Label Semantics And Verify

**Files:**
- Modify: `docs/BINARY_CONTRACT_ENGINE_PLAN.md`
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Add documentation**

Document:

- One binary market has two outcome token books.
- TUI groups UP/DOWN because operator reasoning is market-level.
- Storage keeps token-level books because the CLOB and execution math are token-level.
- `computed_winner` is Chainlink-rule-derived after expiry.
- `official_winner` stays pending until Polymarket/UMA/onchain resolution is fetched.
- Any mismatch between computed and official is a validation incident.

- [ ] **Step 2: Run full focused verification**

Run:

```bash
uv run ruff check src/polymarket_engine/storage/duckdb_store.py src/polymarket_engine/validation src/polymarket_engine/runtime_api.py src/polymarket_engine/ingestion/rust_normalizer_sidecar.py tests/validation tests/test_runtime_api.py tests/storage
uv run pytest -q tests/validation tests/storage/test_schema.py tests/storage/test_normalized_writes.py tests/ingestion/test_rust_normalizer_sidecar.py tests/test_runtime_api.py tests/test_monitor.py
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: all commands pass.

- [ ] **Step 3: Deploy to THEPC only after tests pass**

Run:

```bash
PC_HOST=ender@100.72.104.49 PC_API_PORT=8000 ./scripts/deploy_pc.sh
```

Expected deploy check:

```text
{'ok': True, 'status_age_seconds': ..., 'price_age_ms': ..., 'orderbook_age_ms': ...}
```

- [ ] **Step 4: Verify THEPC runtime**

Run:

```bash
python3 - <<'PY'
import requests
for path in ("/api/runtime/monitor?limit=8", "/api/runtime/probabilities?limit=8", "/api/runtime/outcomes?limit=8"):
    r = requests.get("http://100.72.104.49:8000" + path, timeout=10)
    print(path, r.status_code, r.json().get("ok"), len(r.json().get("rows", r.json().get("orderbooks", []))))
PY
```

Expected: monitor and probabilities still work; outcomes returns `ok=True` with rows once expired recorded markets have enough Chainlink start/end data.

## Risk Areas

- Do not collapse token-level storage. Polymarket books are per outcome token, so execution/paper math still needs UP and DOWN token ids.
- Do not mark `official_winner` from our Chainlink computation. Official resolution is a separate Polymarket/UMA/onchain source.
- Do not compute labels before expiry. Future settlement is a label, never a live feature.
- Be careful with status-derived contracts: `market_id` is currently the slug for Rust status contracts, not a Gamma numeric id.
- If start/end Chainlink ticks are missing, leave the market pending instead of inventing a price from Coinbase/Binance.

## Self-Review

- Spec coverage: grouped display, token-level internal model, historical log, computed outcome, official resolution separation, and deploy verification are covered.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: `RuntimeOutcomes`, `RuntimeOutcomeRow`, `MarketGroup`, and `MarketOutcomeRecord` are introduced before use.
