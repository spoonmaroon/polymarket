# Outcome History Day Groups and K Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show outcome history across all recorded days with collapsible local-day groups, show today's group expanded by default, and display source-backed `K` in both Market and Outcomes without using local computed outcomes as truth.

**Architecture:** Keep official winner resolution sourced only from Polymarket/CLOB winner flags. Compute and persist `K` as the Chainlink reference observation at-or-before the market start, with event and observed timestamps preserved, but do not use that local price comparison to decide the official outcome. Let the normalizer write compact status files from its DuckDB writer lane so the TUI does not fight DuckDB locks or pull a huge DB query on every repaint.

**Tech Stack:** Python/FastAPI/DuckDB/pytest for runtime outcome and target status; Rust/ratatui/serde/cargo tests for the cockpit TUI; Docker Compose on THEPC for deployment.

---

## Root Cause Findings

- `/api/runtime/outcomes?limit=500` still returns only 20 rows because `data/live/outcomes.json` is written with `OUTCOME_OUTPUT_LIMIT = 20`, and the API prefers that status file over DuckDB.
- `src/polymarket_engine/validation/outcomes.py::_runtime_row()` currently omits `threshold_price`, `threshold_event_ts`, and `threshold_observed_ts`.
- `upsert_official_market_outcomes()` explicitly writes `threshold_price=None`, so even resolved rows cannot show K later.
- The live Market tab gets K from Rust state-manager `targets`. If the hot process missed the Chainlink start reference because of restart or stream timing, the TUI sees `K pending`. The correct fallback is an as-of normalized-data target cache, not a post-start estimate.
- Gamma metadata describes the market rule and Chainlink source but does not expose the numeric start K; K must come from Chainlink reference observations.

## File Structure

- Modify `src/polymarket_engine/validation/outcomes.py`
  - Persist threshold/end Chainlink observations into `validation.market_outcome_history`.
  - Include K fields in runtime rows and tolerate legacy rows.
- Modify `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
  - Make outcome status output limit configurable and high enough for day grouping.
  - Write a separate live target cache from normalized Chainlink ticks for current/next market rows.
- Modify `src/polymarket_engine/monitor.py`
  - Merge sidecar target cache into runtime monitor rows only when Rust status target fields are missing.
- Modify `src/polymarket_engine/runtime_api.py`
  - Expose the enriched outcome status payload without DB lock reads in the normal path.
- Modify `rust/crates/polymarket-cockpit-tui/src/status.rs`
  - Add optional K fields to `RuntimeOutcomeRow`.
- Modify `rust/crates/polymarket-cockpit-tui/src/client.rs`
  - Request a larger, slower-polled outcome history limit.
- Modify `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
  - Split outcome polling cadence from fast live polling.
  - Add key handling for expanding/collapsing outcome day groups.
- Modify `rust/crates/polymarket-cockpit-tui/src/state.rs`
  - Track expanded outcome local-day keys; current local day is expanded by default.
  - Move outcome selection over visible display rows so arrows cannot select hidden collapsed rows.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/outcomes.rs`
  - Render day headers, current day expanded, older days collapsed, and add a K column.
- Tests:
  - `tests/validation/test_outcomes.py`
  - `tests/ingestion/test_rust_normalizer_sidecar.py`
  - `tests/test_runtime_api.py`
  - `tests/test_monitor.py`
  - Rust TUI tests in `status.rs`, `client.rs`, `event_loop.rs`, `state.rs`, and `render/outcomes.rs`

## Task 1: Persist Source-Backed K in Outcome History

**Files:**
- Modify: `src/polymarket_engine/validation/outcomes.py`
- Test: `tests/validation/test_outcomes.py`

- [ ] **Step 1: Write failing tests**

Add tests proving:

```python
def test_official_outcome_records_chainlink_threshold_without_computing_winner(tmp_path: Path) -> None:
    store = seeded_store_with_btc_market(tmp_path, start_price=65_000.0, end_price=65_100.0)

    upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_EXPIRY_PLUS_ONE,
        market_payload_source=lambda _condition_id: _polymarket_market_payload(winning_token_id="up-token"),
    )

    row = fetch_outcome(store.db_path, "btc-updown-5m-1780502400")
    assert row["threshold_price"] == 65_000.0
    assert row["official_winner"] == "UP"
    assert row["computed_winner"] is None
```

Also add a missing-K test:

```python
def test_official_outcome_leaves_threshold_null_when_chainlink_start_reference_missing(tmp_path: Path) -> None:
    store = seeded_store_with_btc_market(tmp_path, start_price=None, end_price=65_100.0)

    upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_EXPIRY_PLUS_ONE,
        market_payload_source=lambda _condition_id: _polymarket_market_payload(winning_token_id="up-token"),
    )

    row = fetch_outcome(store.db_path, "btc-updown-5m-1780502400")
    assert row["threshold_price"] is None
    assert row["official_winner"] == "UP"
    assert row["computed_winner"] is None
```

Run:

```bash
uv run pytest tests/validation/test_outcomes.py::test_official_outcome_records_chainlink_threshold_without_computing_winner tests/validation/test_outcomes.py::test_official_outcome_leaves_threshold_null_when_chainlink_start_reference_missing -q
```

Expected: FAIL because the writer currently records threshold fields as `None`.

- [ ] **Step 2: Implement as-of threshold lookup**

Add helpers in `outcomes.py`:

```python
def _chainlink_tick_at_or_before(
    *,
    store: DuckDbIngestStore,
    symbol: str,
    event_ts_lte: datetime,
    observed_ts_lte: datetime,
) -> dict[str, Any] | None:
    with store._connection() as conn:
        row = conn.execute(
            """
            select price, event_ts::VARCHAR, observed_ts::VARCHAR
            from core.price_ticks
            where source_key = 'polymarket_rtds_chainlink'
              and symbol = ?
              and event_ts <= ?
              and observed_ts <= ?
            order by event_ts desc, observed_ts desc
            limit 1
            """,
            [symbol, event_ts_lte, observed_ts_lte],
        ).fetchone()
    if row is None:
        return None
    return {
        "price": row[0],
        "event_ts": _parse_duckdb_ts(row[1]),
        "observed_ts": _parse_duckdb_ts(row[2]),
    }
```

Use it when building `MarketOutcomeRecord`, filling `threshold_*` and `end_*` fields. Keep `computed_winner`, `computed_label_source`, `computed_at`, and `mismatch` unchanged unless a future task explicitly reintroduces local computed labels.

- [ ] **Step 3: Run validation tests**

```bash
uv run pytest tests/validation/test_outcomes.py -q
```

Expected: PASS.

## Task 2: Expose K Fields and More History Through Runtime Status

**Files:**
- Modify: `src/polymarket_engine/validation/outcomes.py`
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Modify: `tests/test_runtime_api.py`
- Modify: `tests/ingestion/test_rust_normalizer_sidecar.py`

- [ ] **Step 1: Write failing API and sidecar tests**

Add a runtime API test:

```python
def test_runtime_outcomes_includes_threshold_fields(tmp_path: Path) -> None:
    store = _seeded_store_with_outcome(tmp_path, official_winner="UP", threshold_price=70_000.0)
    app = create_app(status_path=tmp_path / "missing-status.json", duckdb_path=store.db_path)

    payload = TestClient(app).get("/api/runtime/outcomes?limit=4").json()

    assert payload["rows"][0]["threshold_price"] == 70_000.0
    assert "threshold_event_ts" in payload["rows"][0]
    assert "threshold_observed_ts" in payload["rows"][0]
```

Add a sidecar env test:

```python
def test_upsert_market_outcomes_uses_output_limit_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYMARKET_OUTCOME_OUTPUT_LIMIT", "1440")
    assert rust_normalizer_sidecar._outcome_output_limit_from_env() == 1440
```

Run:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_outcomes_includes_threshold_fields tests/ingestion/test_rust_normalizer_sidecar.py::test_upsert_market_outcomes_uses_output_limit_from_env -q
```

Expected: FAIL because the API omits threshold fields and output limit is hard-coded.

- [ ] **Step 2: Extend runtime row shape**

Add optional fields to `_runtime_row()`:

```python
"threshold_price": row.get("threshold_price"),
"threshold_event_ts": _iso_string(row.get("threshold_event_ts")),
"threshold_observed_ts": _iso_string(row.get("threshold_observed_ts")),
"end_price": row.get("end_price"),
"end_event_ts": _iso_string(row.get("end_event_ts")),
"end_observed_ts": _iso_string(row.get("end_observed_ts")),
```

Add these fields to `_normalize_outcome_status_rows()` for legacy status-file rows with `None` defaults. Do not make them hard-required for old files.

- [ ] **Step 3: Make sidecar output history configurable**

In `rust_normalizer_sidecar.py`:

```python
OUTCOME_OUTPUT_LIMIT = 5000
OUTCOME_OUTPUT_LIMIT_ENV = "POLYMARKET_OUTCOME_OUTPUT_LIMIT"

def _outcome_output_limit_from_env() -> int:
    raw = os.environ.get(OUTCOME_OUTPUT_LIMIT_ENV)
    if raw is None or raw.strip() == "":
        return OUTCOME_OUTPUT_LIMIT
    return max(20, int(raw))
```

Use it in `_upsert_market_outcomes()`:

```python
rows = latest_market_outcome_rows_from_connection(
    conn=conn,
    limit=_outcome_output_limit_from_env(),
)
```

- [ ] **Step 4: Run focused tests**

```bash
uv run pytest tests/test_runtime_api.py tests/ingestion/test_rust_normalizer_sidecar.py -q
```

Expected: PASS.

## Task 3: Add Normalizer Target Cache for Live K Backfill

**Files:**
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Modify: `src/polymarket_engine/monitor.py`
- Test: `tests/ingestion/test_rust_normalizer_sidecar.py`
- Test: `tests/test_monitor.py`

- [ ] **Step 1: Write failing monitor test**

Add:

```python
def test_monitor_uses_target_cache_when_state_manager_target_is_missing(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    target_cache_path = tmp_path / "targets.json"
    status_path.write_text(json.dumps(_state_manager_status_without_k()), encoding="utf-8")
    target_cache_path.write_text(json.dumps({
        "schema_version": "polymarket-target-cache-v1",
        "generated_at": "2026-06-04T20:02:00Z",
        "rows": [{
            "market_slug": "btc-updown-5m-1780603200",
            "threshold_price": "63500.12",
            "threshold_event_ts": "2026-06-04T20:00:00Z",
            "threshold_observed_ts": "2026-06-04T20:00:03Z",
        }],
    }), encoding="utf-8")

    snapshot = fetch_monitor_snapshot(
        tmp_path / "missing.duckdb",
        status_path=status_path,
        target_cache_path=target_cache_path,
    )

    assert snapshot.orderbooks[0]["threshold_price"] == "63500.12"
```

Run:

```bash
uv run pytest tests/test_monitor.py::test_monitor_uses_target_cache_when_state_manager_target_is_missing -q
```

Expected: FAIL because no target cache is read.

- [ ] **Step 2: Implement cache merge**

Add an optional `target_cache_path` to `fetch_monitor_snapshot()` and `_snapshot_from_status()`. Merge cache target fields only when:

- row `market_slug` matches,
- cached `threshold_price` is present,
- status row `threshold_price` is missing,
- cached `threshold_observed_ts <= status.generated_at`.

Do not overwrite non-null Rust state-manager fields.

- [ ] **Step 3: Write sidecar cache from normalized DB**

Use the normalizer writer connection to build rows for active current/next markets and write `data/live/targets.json` atomically. Each row must preserve:

```python
market_slug
asset
interval
start_ts
expiry_ts
threshold_price
threshold_event_ts
threshold_observed_ts
```

The query must use `source_key='polymarket_rtds_chainlink'`, symbol `BTC/USD` or `ETH/USD`, `event_ts <= start_ts`, and `observed_ts <= asof_ts`.

- [ ] **Step 4: Run monitor and sidecar tests**

```bash
uv run pytest tests/test_monitor.py tests/ingestion/test_rust_normalizer_sidecar.py -q
```

Expected: PASS.

## Task 4: Group Outcomes by Local Day in the TUI

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/client.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/outcomes.rs`

- [ ] **Step 1: Write failing Rust render/state tests**

Add tests proving:

```rust
#[test]
fn outcomes_group_by_local_day_with_today_expanded() {
    let app = app_with_outcome_days(vec![
        ("2026-06-04T20:00:00Z", "BTC 5m"),
        ("2026-06-03T20:00:00Z", "ETH 5m"),
    ]);

    let rows = outcome_rows(&app);

    assert!(rows.iter().any(|row| row.market.contains("Jun 04")));
    assert!(rows.iter().any(|row| row.market == "BTC 5m"));
    assert!(rows.iter().any(|row| row.market.contains("Jun 03")));
    assert!(!rows.iter().any(|row| row.market == "ETH 5m"));
}
```

Add a selection test:

```rust
#[test]
fn outcome_selection_moves_only_across_visible_rows() {
    let mut app = app_with_collapsed_prior_day();
    app.sync_outcome_selection();
    app.select_next_outcome();
    assert!(app.selected_outcome_display_row_is_visible());
}
```

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui outcomes_group_by_local_day_with_today_expanded outcome_selection_moves_only_across_visible_rows
```

Expected: FAIL because outcomes render flat rows and selection indexes hidden raw rows.

- [ ] **Step 2: Add outcome K parsing**

Add to `RuntimeOutcomeRow`:

```rust
#[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
pub threshold_price: Option<String>,
#[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
pub threshold_event_ts: Option<String>,
#[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
pub threshold_observed_ts: Option<String>,
```

- [ ] **Step 3: Add grouped outcome state**

Add to `AppState`:

```rust
pub expanded_outcome_days: BTreeSet<String>,
pub selected_outcome_display_index: Option<usize>,
```

Current local day is treated as expanded even if it is not in the set. `select_next_outcome()` and `select_previous_outcome()` must use the rendered display-row count, not raw outcome row count.

- [ ] **Step 4: Render day headers and K**

Render rows like:

```text
> - Jun 04 2026 Today (expanded)  BTC=... ETH=...
    BTC 5m  15:00 CDT  K 63500.12  Winner UP  Status resolved
  + Jun 03 2026 (576 rows)
```

Headers are selectable. Press `Enter` or `Space` on a day header to toggle it. Current day starts expanded.

- [ ] **Step 5: Slow outcome polling and increase limit**

Keep live Market SSE/poll cadence unchanged. Fetch outcomes on a slower auxiliary cadence and request the configured history window:

```rust
const OUTCOME_POLL_INTERVAL: Duration = Duration::from_secs(15);
const OUTCOME_HISTORY_LIMIT: usize = 5000;
```

Probabilities can remain on the existing 3-second auxiliary cadence.

- [ ] **Step 6: Run Rust TUI tests**

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: PASS.

## Task 5: Verify, Deploy, and Refresh Launchers

**Files:**
- Modify only if needed: `scripts/open_tui_mac.sh`
- Modify only if needed: THEPC desktop launcher script

- [ ] **Step 1: Run focused Python verification**

```bash
uv run pytest tests/validation/test_outcomes.py tests/ingestion/test_rust_normalizer_sidecar.py tests/test_runtime_api.py tests/test_monitor.py -q
uv run ruff check src/polymarket_engine/validation/outcomes.py src/polymarket_engine/ingestion/rust_normalizer_sidecar.py src/polymarket_engine/monitor.py src/polymarket_engine/runtime_api.py tests/validation/test_outcomes.py tests/ingestion/test_rust_normalizer_sidecar.py tests/test_runtime_api.py tests/test_monitor.py
```

Expected: PASS.

- [ ] **Step 2: Run focused Rust verification**

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-live-probe
```

Expected: PASS.

- [ ] **Step 3: Build release TUI**

```bash
cargo build --release --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: binary builds successfully.

- [ ] **Step 4: Deploy to THEPC after approval**

Deploy with the existing THEPC path, then verify:

```bash
curl -fsS 'http://100.72.104.49:8000/api/runtime/outcomes?limit=200' | jq '.rows | length'
curl -fsS 'http://100.72.104.49:8000/api/runtime/live?limit=8' | jq '.monitor.orderbooks[] | {market_slug, threshold_price}'
```

Expected: outcome row count is greater than 20 once history exists, outcome rows include `threshold_price`, and live monitor rows use source-backed K when available.

## Risk Areas

- Do not display a post-start Chainlink tick as unqualified `K`; if no event/observed-safe start reference exists, keep `K` pending.
- Do not compute official winners locally. Official outcome remains Polymarket/CLOB winner flags only.
- Keep outcome history polling slower than live market updates so the chart/book latency stays tight.
- Grouping by local day must use `America/Chicago`/system local time behavior consistently with the rest of the TUI.

## Subagent Split

- Subagent A: Python outcome K persistence and runtime row fields.
- Subagent B: Python target cache and monitor merge.
- Subagent C: Rust TUI day grouping, K column, selection, and polling cadence.
- Main agent: integration review, verification, deploy, launcher check.

