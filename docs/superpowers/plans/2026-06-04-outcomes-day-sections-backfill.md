# Outcomes Day Sections And Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Outcomes tab closable by day, split expanded days into large local-time sections, and add an explicit source-of-truth backfill command for missing official outcomes and `K` strikes.

**Architecture:** Keep the TUI tree state in Rust and the repair/backfill logic in Python validation code. The TUI renders a display tree built from `/api/runtime/outcomes`; the Python backfill command walks recorded contracts in bounded batches, repairs only official Polymarket/CLOB outcome fields and Chainlink start-reference `K`, then rewrites the low-contention outcome status file.

**Tech Stack:** Rust, ratatui, serde, chrono, cargo tests, Python, argparse, DuckDB, FastAPI, pytest, ruff.

---

## File Structure

- Modify `rust/crates/polymarket-cockpit-tui/src/outcome_view.rs`
  - Own outcome day grouping, four local-time sections, display-row items, expansion keys, and toggle target lookup.
- Modify `rust/crates/polymarket-cockpit-tui/src/state.rs`
  - Store explicit outcome expansion state so the current day opens once by default but can be closed.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/outcomes.rs`
  - Render day rows, section rows, and outcome rows with stable selected-row behavior.
- Modify `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
  - Route Enter/Space to the new day-or-section toggle method.
- Modify `src/polymarket_engine/validation/outcomes.py`
  - Preserve already-resolved `K`, add expiry-range filtering, build backfill reports, and expose a write/dry-run backfill function.
- Modify `src/polymarket_engine/cli.py`
  - Add `polymarket-engine backfill-outcomes`.
- Modify tests:
  - `rust/crates/polymarket-cockpit-tui/src/outcome_view.rs`
  - `rust/crates/polymarket-cockpit-tui/src/state.rs`
  - `rust/crates/polymarket-cockpit-tui/src/render/outcomes.rs`
  - `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
  - `tests/validation/test_outcomes.py`
  - `tests/ingestion/test_rust_normalizer_sidecar.py`
  - `tests/test_cli.py`
  - `tests/test_runtime_api.py`
- Modify docs:
  - `docs/PART_TWO_LIVE_COLLECTORS.md`
  - `docs/SPOON_DEPLOYMENT.md`

## Task 1: Build The Outcomes Display Tree

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/outcome_view.rs`

- [ ] **Step 1: Write failing display-tree tests**

Replace the current latest-day default test in `outcome_view.rs` with tests for explicit expansion and section grouping:

```rust
#[test]
fn outcome_display_items_allows_latest_day_to_be_closed() {
    let outcomes = RuntimeOutcomes {
        ok: true,
        state: "OK".to_string(),
        generated_at: Some("2026-06-04T20:00:00Z".to_string()),
        rows: vec![
            outcome("BTC 5m", "2026-06-04T20:00:00Z"),
            outcome("ETH 5m", "2026-06-03T20:00:00Z"),
        ],
    };
    let mut expansion = OutcomeExpansion::default();
    expansion.initialized_days.insert("2026-06-04".to_string());

    let items = outcome_display_items(Some(&outcomes), &expansion);

    assert!(matches!(
        &items[0],
        OutcomeDisplayItem::Day {
            label,
            expanded: false,
            ..
        } if label.contains("Jun 04")
    ));
    assert!(!items.iter().any(|item| {
        matches!(item, OutcomeDisplayItem::Outcome { row } if row.market == "BTC 5m")
    }));
}

#[test]
fn outcome_display_items_splits_expanded_day_into_large_sections() {
    let outcomes = RuntimeOutcomes {
        ok: true,
        state: "OK".to_string(),
        generated_at: Some("2026-06-04T20:00:00Z".to_string()),
        rows: vec![
            outcome("BTC 5m", "2026-06-04T05:05:00Z"),
            outcome("BTC 5m", "2026-06-04T13:05:00Z"),
        ],
    };
    let mut expansion = OutcomeExpansion::default();
    expansion.initialized_days.insert("2026-06-04".to_string());
    expansion.expanded_days.insert("2026-06-04".to_string());
    expansion.initialized_sections.insert("2026-06-04#afternoon".to_string());
    expansion.expanded_sections.insert("2026-06-04#afternoon".to_string());

    let items = outcome_display_items(Some(&outcomes), &expansion);

    assert!(matches!(&items[0], OutcomeDisplayItem::Day { expanded: true, .. }));
    assert!(items.iter().any(|item| {
        matches!(
            item,
            OutcomeDisplayItem::Section {
                key,
                label,
                count: 1,
                expanded: false,
                ..
            } if key == "2026-06-04#overnight" && label.contains("Overnight")
        )
    }));
    assert!(items.iter().any(|item| {
        matches!(
            item,
            OutcomeDisplayItem::Section {
                key,
                label,
                count: 1,
                expanded: true,
                ..
            } if key == "2026-06-04#afternoon" && label.contains("Afternoon")
        )
    }));
    assert!(items.iter().any(|item| {
        matches!(item, OutcomeDisplayItem::Outcome { row } if row.market == "BTC 5m")
    }));
}
```

- [ ] **Step 2: Run the failing Rust tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui outcome_display_items_allows_latest_day_to_be_closed outcome_display_items_splits_expanded_day_into_large_sections
```

Expected: FAIL because `OutcomeExpansion` and `OutcomeDisplayItem::Section` do not exist yet.

- [ ] **Step 3: Implement the display-tree types**

In `outcome_view.rs`, replace the `BTreeSet` argument with this expansion model:

```rust
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct OutcomeExpansion {
    pub expanded_days: BTreeSet<String>,
    pub initialized_days: BTreeSet<String>,
    pub expanded_sections: BTreeSet<String>,
    pub initialized_sections: BTreeSet<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OutcomeToggleTarget {
    Day(String),
    Section(String),
}

#[derive(Debug, Clone, PartialEq)]
pub enum OutcomeDisplayItem<'a> {
    Day {
        key: String,
        label: String,
        count: usize,
        expanded: bool,
    },
    Section {
        key: String,
        day_key: String,
        label: String,
        count: usize,
        expanded: bool,
    },
    Outcome {
        row: &'a RuntimeOutcomeRow,
    },
}
```

Add section helpers:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct OutcomeSection {
    key: &'static str,
    label: &'static str,
    start_hour: u32,
    end_hour: u32,
}

const OUTCOME_SECTIONS: [OutcomeSection; 4] = [
    OutcomeSection { key: "overnight", label: "Overnight 00:00-05:59", start_hour: 0, end_hour: 5 },
    OutcomeSection { key: "morning", label: "Morning 06:00-11:59", start_hour: 6, end_hour: 11 },
    OutcomeSection { key: "afternoon", label: "Afternoon 12:00-17:59", start_hour: 12, end_hour: 17 },
    OutcomeSection { key: "evening", label: "Evening 18:00-23:59", start_hour: 18, end_hour: 23 },
];

fn section_for_local_expiry(timestamp: DateTime<Local>) -> OutcomeSection {
    let hour = timestamp.hour();
    OUTCOME_SECTIONS
        .into_iter()
        .find(|section| (section.start_hour..=section.end_hour).contains(&hour))
        .unwrap_or(OUTCOME_SECTIONS[0])
}

pub fn outcome_section_key(day_key: &str, section_key: &str) -> String {
    format!("{day_key}#{section_key}")
}
```

Use `chrono::Timelike` for `hour()`.

- [ ] **Step 4: Implement grouped items with no forced default expansion**

Change the public helper signature:

```rust
pub fn outcome_display_items<'a>(
    outcomes: Option<&'a RuntimeOutcomes>,
    expansion: &OutcomeExpansion,
) -> Vec<OutcomeDisplayItem<'a>>
```

Build day groups in expiry-desc order, then section groups inside each day. A day is expanded only when `expansion.expanded_days.contains(&day_key)`. A section is expanded only when `expansion.expanded_sections.contains(&section_key)`.

Replace `outcome_day_key_at` with:

```rust
pub fn outcome_toggle_target_at(
    outcomes: Option<&RuntimeOutcomes>,
    expansion: &OutcomeExpansion,
    index: usize,
) -> Option<OutcomeToggleTarget> {
    match outcome_display_items(outcomes, expansion).get(index)? {
        OutcomeDisplayItem::Day { key, .. } => Some(OutcomeToggleTarget::Day(key.clone())),
        OutcomeDisplayItem::Section { key, .. } => {
            Some(OutcomeToggleTarget::Section(key.clone()))
        }
        OutcomeDisplayItem::Outcome { .. } => None,
    }
}
```

- [ ] **Step 5: Run focused display-tree tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui outcome_view
```

Expected: PASS for `outcome_view` tests.

- [ ] **Step 6: Commit Task 1**

```bash
git add rust/crates/polymarket-cockpit-tui/src/outcome_view.rs
git commit -m "Add outcomes day section display tree"
```

## Task 2: Wire Explicit Outcome Expansion Into App State

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`

- [ ] **Step 1: Write failing state tests**

Add these tests in `state.rs`:

```rust
#[test]
fn outcome_expansion_defaults_expand_latest_day_once() {
    let mut app = AppState {
        runtime_outcomes: Some(outcomes(vec!["BTC 5m", "ETH 5m"])),
        ..Default::default()
    };

    app.sync_outcome_expansion_defaults();
    let latest_key = app.outcome_expansion.expanded_days.iter().next().cloned();
    assert_eq!(latest_key.as_deref(), Some("2026-06-03"));

    app.toggle_selected_outcome_row();
    assert!(app.outcome_expansion.initialized_days.contains("2026-06-03"));
    assert!(!app.outcome_expansion.expanded_days.contains("2026-06-03"));

    app.sync_outcome_expansion_defaults();
    assert!(!app.outcome_expansion.expanded_days.contains("2026-06-03"));
}

#[test]
fn collapse_selected_day_keeps_selection_on_visible_header() {
    let mut app = AppState {
        runtime_outcomes: Some(outcomes(vec!["BTC 5m", "ETH 5m"])),
        ..Default::default()
    };
    app.sync_outcome_expansion_defaults();
    app.sync_outcome_selection();
    assert!(app.outcome_count().unwrap() > 1);

    assert!(app.toggle_selected_outcome_row());

    assert_eq!(app.effective_outcome_index(), Some(0));
    assert!(app.selected_outcome_display_row_is_visible());
    assert_eq!(app.outcome_count(), Some(1));
}
```

- [ ] **Step 2: Run the failing state tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui outcome_expansion_defaults_expand_latest_day_once collapse_selected_day_keeps_selection_on_visible_header
```

Expected: FAIL because `outcome_expansion`, `sync_outcome_expansion_defaults`, and `toggle_selected_outcome_row` do not exist.

- [ ] **Step 3: Replace the expansion fields**

In `AppState`, replace:

```rust
pub expanded_outcome_days: BTreeSet<String>,
```

with:

```rust
pub outcome_expansion: crate::outcome_view::OutcomeExpansion,
```

Update `Default` to use `OutcomeExpansion::default()`.

- [ ] **Step 4: Add default expansion sync**

Add methods:

```rust
pub fn sync_outcome_expansion_defaults(&mut self) {
    let Some(outcomes) = self.runtime_outcomes.as_ref() else {
        return;
    };
    let Some(latest_day_key) = crate::outcome_view::latest_outcome_day_key(outcomes) else {
        return;
    };
    if self.outcome_expansion.initialized_days.insert(latest_day_key.clone()) {
        self.outcome_expansion.expanded_days.insert(latest_day_key.clone());
    }
    self.sync_default_section_for_day(&latest_day_key);
}

fn sync_default_section_for_day(&mut self, day_key: &str) {
    let Some(section_key) =
        crate::outcome_view::default_outcome_section_key(self.runtime_outcomes.as_ref(), day_key)
    else {
        return;
    };
    if self.outcome_expansion.initialized_sections.insert(section_key.clone()) {
        self.outcome_expansion.expanded_sections.insert(section_key);
    }
}
```

Add `latest_outcome_day_key()` and `default_outcome_section_key()` in `outcome_view.rs` during this task if they were not added in Task 1.

- [ ] **Step 5: Toggle day or section rows**

Replace `toggle_selected_outcome_day()` with:

```rust
pub fn toggle_selected_outcome_row(&mut self) -> bool {
    let Some(index) = self.effective_outcome_index() else {
        return false;
    };
    let Some(target) = crate::outcome_view::outcome_toggle_target_at(
        self.runtime_outcomes.as_ref(),
        &self.outcome_expansion,
        index,
    ) else {
        return false;
    };
    match target {
        crate::outcome_view::OutcomeToggleTarget::Day(day_key) => {
            self.outcome_expansion.initialized_days.insert(day_key.clone());
            if !self.outcome_expansion.expanded_days.insert(day_key.clone()) {
                self.outcome_expansion.expanded_days.remove(&day_key);
            } else {
                self.sync_default_section_for_day(&day_key);
            }
        }
        crate::outcome_view::OutcomeToggleTarget::Section(section_key) => {
            self.outcome_expansion.initialized_sections.insert(section_key.clone());
            if !self.outcome_expansion.expanded_sections.insert(section_key.clone()) {
                self.outcome_expansion.expanded_sections.remove(&section_key);
            }
        }
    }
    self.sync_outcome_selection();
    true
}
```

Change `apply_runtime_outcomes()` to call `self.sync_outcome_expansion_defaults()` before returning.

Change `outcome_count()` to:

```rust
fn outcome_count(&self) -> Option<usize> {
    self.runtime_outcomes.as_ref().map(|_| {
        crate::outcome_view::outcome_display_items(
            self.runtime_outcomes.as_ref(),
            &self.outcome_expansion,
        )
        .len()
    })
}
```

- [ ] **Step 6: Update key handling**

In `event_loop.rs`, replace:

```rust
app.toggle_selected_outcome_day();
```

with:

```rust
app.toggle_selected_outcome_row();
```

- [ ] **Step 7: Run state and key tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui state::tests event_loop::tests::apply_key_moves_outcome_selection_with_up_down
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add rust/crates/polymarket-cockpit-tui/src/state.rs rust/crates/polymarket-cockpit-tui/src/event_loop.rs rust/crates/polymarket-cockpit-tui/src/outcome_view.rs
git commit -m "Make outcome day expansion explicit"
```

## Task 3: Render Section Rows In The Outcomes Tab

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/outcomes.rs`

- [ ] **Step 1: Write failing render tests**

Add:

```rust
#[test]
fn outcome_rows_render_day_and_section_headers() {
    let mut app = app_with_expiry_days(vec![
        ("BTC 5m", "2026-06-04T05:05:00Z"),
        ("ETH 5m", "2026-06-04T13:05:00Z"),
    ]);
    app.sync_outcome_expansion_defaults();

    let rows = outcome_rows(&app);

    assert!(rows[0].market.contains("Jun 04"));
    assert!(rows.iter().any(|row| row.market.contains("Overnight")));
    assert!(rows.iter().any(|row| row.market.contains("Afternoon")));
}

#[test]
fn outcome_rows_keep_selected_section_visible() {
    let mut app = app_with_expiry_days(vec![
        ("BTC 5m 00:05", "2026-06-04T05:05:00Z"),
        ("BTC 5m 06:05", "2026-06-04T11:05:00Z"),
        ("BTC 5m 12:05", "2026-06-04T17:05:00Z"),
        ("BTC 5m 18:05", "2026-06-04T23:05:00Z"),
    ]);
    app.sync_outcome_expansion_defaults();
    app.select_next_outcome();
    app.select_next_outcome();
    app.select_next_outcome();

    let rows = outcome_rows_for_visible_count(&app, 2);

    assert_eq!(rows.len(), 2);
    assert_eq!(rows.last().map(|row| row.marker.as_str()), Some(">"));
}
```

- [ ] **Step 2: Run the failing render tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui outcome_rows_render_day_and_section_headers outcome_rows_keep_selected_section_visible
```

Expected: FAIL until `render/outcomes.rs` consumes `OutcomeDisplayItem::Section`.

- [ ] **Step 3: Render sections with indentation**

Update the match in `outcome_rows_for_visible_count()`:

```rust
OutcomeDisplayItem::Section {
    label,
    count,
    expanded,
    ..
} => OutcomeDisplayRow {
    marker: marker(selected_index, index),
    market: format!("  {} {label} ({count})", if expanded { "-" } else { "+" }),
    expiry: String::new(),
    k: String::new(),
    winner: String::new(),
    token: String::new(),
    status: if expanded { "expanded".to_string() } else { "collapsed".to_string() },
},
OutcomeDisplayItem::Outcome { row } => OutcomeDisplayRow {
    marker: marker(selected_index, index),
    market: format!("    {}", row.market),
    expiry: compact_timestamp(row.expiry_ts.as_deref()),
    k: format_k(row.threshold_price.as_deref()),
    winner: optional_as_dash(row.official_winner.as_deref()),
    token: optional_as_dash(row.winning_token_id.as_deref()),
    status: row.official_resolution_status.clone(),
},
```

Widen the Market column if the current 22-char width cuts section labels:

```rust
Constraint::Length(30),
```

- [ ] **Step 4: Run all TUI tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: all TUI tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add rust/crates/polymarket-cockpit-tui/src/render/outcomes.rs
git commit -m "Render outcome history day sections"
```

## Task 4: Preserve Resolved K And Add Expiry-Range Backfill Primitives

**Files:**
- Modify: `src/polymarket_engine/validation/outcomes.py`
- Test: `tests/validation/test_outcomes.py`

- [ ] **Step 1: Write failing K preservation and range tests**

Add:

```python
def test_official_outcome_preserves_existing_threshold_when_later_price_exists(
    tmp_path: Path,
) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=65_000.0,
        end_price=65_100.0,
    )
    upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_EXPIRY_PLUS_ONE,
        market_payload_source=lambda _condition_id: _polymarket_market_payload(
            winning_token_id="up-token"
        ),
    )
    store.insert_price_ticks(
        (
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="BTC/USD",
                event_ts=UTC_START + timedelta(minutes=1),
                observed_ts=UTC_EXPIRY_PLUS_ONE + timedelta(seconds=1),
                price=66_000.0,
            ),
        )
    )

    upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_EXPIRY_PLUS_ONE + timedelta(seconds=2),
        market_payload_source=lambda _condition_id: _polymarket_market_payload(
            winning_token_id="up-token"
        ),
    )

    row = fetch_outcome(store.db_path, "btc-updown-5m-1780502400")
    assert row["threshold_price"] == 65_000.0
    assert row["threshold_event_ts"] == UTC_START
```

Add:

```python
def test_upsert_official_market_outcomes_filters_expiry_range(tmp_path: Path) -> None:
    store = seeded_store_with_two_btc_markets(tmp_path)

    written = upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_EXPIRY_PLUS_ONE + timedelta(minutes=5),
        market_payload_source=lambda _condition_id: _polymarket_market_payload(
            winning_token_id="up-token"
        ),
        expiry_start_ts=UTC_EXPIRY + timedelta(minutes=1),
        expiry_end_ts=UTC_EXPIRY + timedelta(minutes=6),
    )

    assert written == 1
    assert fetch_outcome(store.db_path, "btc-updown-5m-1780502400") is None
```

If `fetch_outcome()` currently assumes a row exists, add:

```python
def fetch_optional_outcome(db_path: Path, market_id: str) -> dict[str, object] | None:
    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            "select market_id from validation.market_outcome_history where market_id = ?",
            [market_id],
        ).fetchone()
    return None if row is None else fetch_outcome(db_path, market_id)
```

- [ ] **Step 2: Run the failing validation tests**

Run:

```bash
uv run pytest tests/validation/test_outcomes.py::test_official_outcome_preserves_existing_threshold_when_later_price_exists tests/validation/test_outcomes.py::test_upsert_official_market_outcomes_filters_expiry_range -q
```

Expected: FAIL because `expiry_start_ts` and `expiry_end_ts` are not accepted yet.

- [ ] **Step 3: Add existing threshold reads**

Replace `_official_fields()` with `_existing_outcome_fields()`:

```python
def _existing_outcome_fields(store: DuckDbIngestStore, *, market_id: str) -> dict[str, Any]:
    with store._connection() as conn:
        row = conn.execute(
            """
            select official_winner, winning_token_id, official_resolution_status,
                   official_label_source, official_resolved_at::VARCHAR,
                   threshold_price, threshold_event_ts::VARCHAR,
                   threshold_observed_ts::VARCHAR
            from validation.market_outcome_history
            where market_id = ?
            """,
            [market_id],
        ).fetchone()
    if row is None:
        return {
            "official_winner": None,
            "winning_token_id": None,
            "official_resolution_status": "pending",
            "official_label_source": None,
            "official_resolved_at": None,
            "threshold_price": None,
            "threshold_event_ts": None,
            "threshold_observed_ts": None,
        }
    return {
        "official_winner": row[0],
        "winning_token_id": row[1],
        "official_resolution_status": row[2],
        "official_label_source": row[3],
        "official_resolved_at": _parse_optional_duckdb_ts(row[4]),
        "threshold_price": row[5],
        "threshold_event_ts": _parse_optional_duckdb_ts(row[6]),
        "threshold_observed_ts": _parse_optional_duckdb_ts(row[7]),
    }
```

Use existing threshold fields when they are complete:

```python
def _preserved_threshold(existing: dict[str, Any]) -> dict[str, Any] | None:
    if (
        existing.get("threshold_price") is None
        or existing.get("threshold_event_ts") is None
        or existing.get("threshold_observed_ts") is None
    ):
        return None
    return {
        "price": existing["threshold_price"],
        "event_ts": existing["threshold_event_ts"],
        "observed_ts": existing["threshold_observed_ts"],
    }
```

Then set:

```python
existing = _existing_outcome_fields(store=store, market_id=up.market_id)
threshold = _preserved_threshold(existing) or _chainlink_tick_at_or_before(
    store=store,
    symbol=up.settlement_symbol,
    event_ts_lte=up.start_ts,
    observed_ts_lte=asof_ts,
)
```

- [ ] **Step 4: Add expiry range filters**

Extend `upsert_official_market_outcomes()`:

```python
def upsert_official_market_outcomes(
    *,
    store: DuckDbIngestStore,
    asof_ts: datetime,
    market_payload_source: MarketPayloadSource | None = None,
    max_markets: int | None = None,
    pending_sweep_limit: int | None = None,
    expiry_start_ts: datetime | None = None,
    expiry_end_ts: datetime | None = None,
) -> int:
```

Pass the bounds into `_expired_contract_rows()`:

```python
contract_rows = _expired_contract_rows(
    store=store,
    asof_ts=asof_ts,
    expiry_start_ts=expiry_start_ts,
    expiry_end_ts=expiry_end_ts,
)
```

Extend `_expired_contract_rows()` with SQL predicates:

```python
where_clauses = ["expiry_ts <= ?"]
params: list[object] = [asof_ts]
if expiry_start_ts is not None:
    where_clauses.append("expiry_ts >= ?")
    params.append(_to_utc(expiry_start_ts))
if expiry_end_ts is not None:
    where_clauses.append("expiry_ts < ?")
    params.append(_to_utc(expiry_end_ts))
```

Build the query with:

```python
where_sql = " and ".join(where_clauses)
```

- [ ] **Step 5: Run validation tests**

Run:

```bash
uv run pytest tests/validation/test_outcomes.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/polymarket_engine/validation/outcomes.py tests/validation/test_outcomes.py
git commit -m "Preserve outcome K during backfill"
```

## Task 5: Add The Backfill Command

**Files:**
- Modify: `src/polymarket_engine/validation/outcomes.py`
- Modify: `src/polymarket_engine/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Add:

```python
@pytest.mark.anyio
async def test_backfill_outcomes_dry_run_prints_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    def fake_backfill_outcome_history(**kwargs: object) -> object:
        calls.append(kwargs)
        return {
            "ok": True,
            "dry_run": True,
            "markets_scanned": 2,
            "rows_written": 0,
            "missing_k_before": 1,
            "pending_official_before": 1,
        }

    monkeypatch.setattr(
        "polymarket_engine.validation.outcomes.backfill_outcome_history",
        fake_backfill_outcome_history,
    )

    rc = await cli.run_collect_command(
        [
            "backfill-outcomes",
            "--duckdb-path",
            str(tmp_path / "db.duckdb"),
            "--outcomes-path",
            str(tmp_path / "outcomes.json"),
            "--start-date",
            "2026-06-01",
            "--end-date",
            "2026-06-04",
            "--limit",
            "500",
        ]
    )

    assert rc == 0
    assert calls[0]["write"] is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["rows_written"] == 0
```

Add:

```python
@pytest.mark.anyio
async def test_backfill_outcomes_write_mode_passes_write_true(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[bool] = []

    def fake_backfill_outcome_history(**kwargs: object) -> object:
        writes.append(bool(kwargs["write"]))
        return {"ok": True, "dry_run": False, "rows_written": 3}

    monkeypatch.setattr(
        "polymarket_engine.validation.outcomes.backfill_outcome_history",
        fake_backfill_outcome_history,
    )

    rc = await cli.run_collect_command(
        [
            "backfill-outcomes",
            "--duckdb-path",
            str(tmp_path / "db.duckdb"),
            "--outcomes-path",
            str(tmp_path / "outcomes.json"),
            "--write",
        ]
    )

    assert rc == 0
    assert writes == [True]
```

- [ ] **Step 2: Run the failing CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_backfill_outcomes_dry_run_prints_summary tests/test_cli.py::test_backfill_outcomes_write_mode_passes_write_true -q
```

Expected: FAIL because the command does not exist.

- [ ] **Step 3: Add backfill report function**

In `validation/outcomes.py`, add:

```python
def backfill_outcome_history(
    *,
    duckdb_path: Path,
    outcomes_path: Path,
    asof_ts: datetime | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int | None = None,
    write: bool = False,
    market_payload_source: MarketPayloadSource | None = None,
) -> dict[str, Any]:
    if asof_ts is None:
        asof_ts = datetime.now(timezone.utc)
    store = DuckDbIngestStore(duckdb_path)
    store.apply_schema()
    expiry_start_ts = _date_bound_start(start_date)
    expiry_end_ts = _date_bound_end(end_date)
    before = _outcome_gap_counts(
        store=store,
        asof_ts=asof_ts,
        expiry_start_ts=expiry_start_ts,
        expiry_end_ts=expiry_end_ts,
    )
    rows_written = 0
    if write:
        rows_written = upsert_official_market_outcomes(
            store=store,
            asof_ts=asof_ts,
            market_payload_source=market_payload_source,
            max_markets=limit,
            expiry_start_ts=expiry_start_ts,
            expiry_end_ts=expiry_end_ts,
        )
        rows = latest_market_outcome_rows(duckdb_path=duckdb_path, limit=5000)
        write_outcome_history_status(out_path=outcomes_path, rows=rows)
    after = _outcome_gap_counts(
        store=store,
        asof_ts=asof_ts,
        expiry_start_ts=expiry_start_ts,
        expiry_end_ts=expiry_end_ts,
    )
    return {
        "ok": True,
        "dry_run": not write,
        "asof_ts": _to_utc(asof_ts).isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "markets_scanned": before["markets_scanned"],
        "rows_written": rows_written,
        "missing_k_before": before["missing_k"],
        "missing_k_after": after["missing_k"],
        "pending_official_before": before["pending_official"],
        "pending_official_after": after["pending_official"],
    }
```

Add date helpers:

```python
def _date_bound_start(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)

def _date_bound_end(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc) + timedelta(days=1)
```

Import `timedelta`.

Add `_outcome_gap_counts()` using `core.contracts` left-joined to `validation.market_outcome_history` by market ID:

```python
def _outcome_gap_counts(
    *,
    store: DuckDbIngestStore,
    asof_ts: datetime,
    expiry_start_ts: datetime | None,
    expiry_end_ts: datetime | None,
) -> dict[str, int]:
    where_clauses = ["contracts.expiry_ts <= ?"]
    params: list[object] = [_to_utc(asof_ts)]
    if expiry_start_ts is not None:
        where_clauses.append("contracts.expiry_ts >= ?")
        params.append(expiry_start_ts)
    if expiry_end_ts is not None:
        where_clauses.append("contracts.expiry_ts < ?")
        params.append(expiry_end_ts)
    where_sql = " and ".join(where_clauses)
    with store._connection() as conn:
        rows = conn.execute(
            f"""
            select
                count(distinct contracts.market_id),
                count(distinct case
                    when outcomes.threshold_price is null then contracts.market_id
                end),
                count(distinct case
                    when outcomes.official_winner is null then contracts.market_id
                end)
            from core.contracts contracts
            left join validation.market_outcome_history outcomes
              on outcomes.market_id = contracts.market_id
            where {where_sql}
            """,
            params,
        ).fetchone()
    return {
        "markets_scanned": int(rows[0] or 0),
        "missing_k": int(rows[1] or 0),
        "pending_official": int(rows[2] or 0),
    }
```

- [ ] **Step 4: Add CLI parser and runner**

In `parse_args()`:

```python
backfill_outcomes = subparsers.add_parser("backfill-outcomes")
backfill_outcomes.add_argument("--duckdb-path", type=Path, required=True)
backfill_outcomes.add_argument("--outcomes-path", type=Path, required=True)
backfill_outcomes.add_argument("--start-date", default=None)
backfill_outcomes.add_argument("--end-date", default=None)
backfill_outcomes.add_argument("--limit", type=int, default=500)
backfill_outcomes.add_argument("--write", action="store_true")
backfill_outcomes.add_argument("--official-outcome-source", default="clob")
backfill_outcomes.add_argument("--official-timeout-seconds", type=float, default=2.0)
```

In `run_collect_command()`:

```python
if args.command == "backfill-outcomes":
    return _run_backfill_outcomes(args)
```

Add:

```python
def _run_backfill_outcomes(args: argparse.Namespace) -> int:
    from polymarket_engine.validation.outcomes import (
        PolymarketClobMarketPayloadSource,
        backfill_outcome_history,
    )

    payload_source = (
        PolymarketClobMarketPayloadSource(timeout_seconds=args.official_timeout_seconds)
        if args.official_outcome_source == "clob"
        else None
    )
    result = backfill_outcome_history(
        duckdb_path=args.duckdb_path,
        outcomes_path=args.outcomes_path,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
        write=args.write,
        market_payload_source=payload_source,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") is True else 1
```

- [ ] **Step 5: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_backfill_outcomes_dry_run_prints_summary tests/test_cli.py::test_backfill_outcomes_write_mode_passes_write_true -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/polymarket_engine/validation/outcomes.py src/polymarket_engine/cli.py tests/test_cli.py
git commit -m "Add outcome history backfill command"
```

## Task 6: Lock Target-Cache Retry Behavior For Missing K

**Files:**
- Modify: `tests/ingestion/test_rust_normalizer_sidecar.py`
- Modify if needed: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`

- [ ] **Step 1: Write failing or confirming retry test**

Add:

```python
def test_write_target_cache_status_retries_missing_k_on_later_cycle(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    target_path = tmp_path / "live" / "targets.json"
    start_ts = datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc)
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    _write_status(status_path, start_ts=start_ts, asof_ts=start_ts + timedelta(seconds=10))

    rust_normalizer_sidecar._write_target_cache_status(
        store=store,
        status_path=status_path,
        out_path=target_path,
        asof_ts=start_ts + timedelta(seconds=10),
    )
    assert json.loads(target_path.read_text(encoding="utf-8"))["rows"][0]["threshold_price"] is None

    store.insert_price_ticks(
        (
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="BTC/USD",
                event_ts=start_ts,
                observed_ts=start_ts + timedelta(seconds=20),
                price=63_500.12,
            ),
        )
    )
    rust_normalizer_sidecar._write_target_cache_status(
        store=store,
        status_path=status_path,
        out_path=target_path,
        asof_ts=start_ts + timedelta(seconds=21),
    )

    payload = json.loads(target_path.read_text(encoding="utf-8"))
    assert payload["rows"][0]["threshold_price"] == 63_500.12
    assert payload["rows"][0]["threshold_event_ts"] == "2026-06-04T20:00:00+00:00"
```

- [ ] **Step 2: Run the retry test**

Run:

```bash
uv run pytest tests/ingestion/test_rust_normalizer_sidecar.py::test_write_target_cache_status_retries_missing_k_on_later_cycle -q
```

Expected: PASS if the current target-cache writer already retries missing `K`; FAIL only if the writer skips the second lookup.

- [ ] **Step 3: Fix only if the test fails**

If the test fails because `_write_target_cache_status()` does not rewrite target cache on idle cycles, call it unconditionally in `_run_idle_rust_normalizer_cycle_with_store()`, `_run_changed_rust_normalizer_cycle_with_store()`, and `_run_rust_normalizer_cycle_with_store()` after outcome/probability work. Keep the lookup bounded by `event_ts <= start_ts` and `observed_ts <= asof_ts`.

- [ ] **Step 4: Run sidecar tests**

Run:

```bash
uv run pytest tests/ingestion/test_rust_normalizer_sidecar.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```bash
git add tests/ingestion/test_rust_normalizer_sidecar.py src/polymarket_engine/ingestion/rust_normalizer_sidecar.py
git commit -m "Test target cache K retry behavior"
```

## Task 7: Runtime API And Documentation

**Files:**
- Modify: `tests/test_runtime_api.py`
- Modify if needed: `src/polymarket_engine/validation/outcomes.py`
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Add API limit regression test**

Add:

```python
def test_runtime_outcomes_limits_large_status_file_rows(tmp_path: Path) -> None:
    outcome_status_path = tmp_path / "live" / "outcomes.json"
    outcome_status_path.parent.mkdir()
    rows = [_runtime_outcome_row("BTC", "UP"), _runtime_outcome_row("ETH", "DOWN")]
    outcome_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-outcome-runtime-v1",
                "ok": True,
                "state": "OK",
                "generated_at": datetime.now(UTC).isoformat(),
                "rows": rows,
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "missing.duckdb",
        outcome_status_path=outcome_status_path,
    )

    payload = TestClient(app).get("/api/runtime/outcomes?limit=1").json()

    assert payload["ok"] is True
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["asset"] == "BTC"
```

- [ ] **Step 2: Run the API regression test**

Run:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_outcomes_limits_large_status_file_rows -q
```

Expected: PASS if the current API already limits status-file rows.

- [ ] **Step 3: Document operator commands**

In `docs/PART_TWO_LIVE_COLLECTORS.md`, add a short paragraph after the outcome-history section:

```markdown
Historical outcome repair is explicit. Use `polymarket-engine backfill-outcomes`
for older ranges that need missing official winners or missing Chainlink
start-reference `K` repaired. The live normalizer keeps only the small pending
sweeper for recent expiries; deep backfill should not run inside the 0.1 second
runtime loop.
```

In `docs/SPOON_DEPLOYMENT.md`, add:

````markdown
Backfill on THEPC:

```bash
uv run polymarket-engine backfill-outcomes \
  --duckdb-path /var/lib/polymarket/db/polymarket.duckdb \
  --outcomes-path /var/lib/polymarket/live/outcomes.json \
  --start-date 2026-06-01 \
  --end-date 2026-06-04 \
  --limit 500 \
  --write
```

The command repairs source-backed outcome history and rewrites the status file
for the TUI. It does not compute local winners.
````

- [ ] **Step 4: Run docs and API tests**

Run:

```bash
uv run pytest tests/test_runtime_api.py tests/docs/test_active_runtime_docs.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 7**

```bash
git add tests/test_runtime_api.py docs/PART_TWO_LIVE_COLLECTORS.md docs/SPOON_DEPLOYMENT.md
git commit -m "Document outcome backfill workflow"
```

## Task 8: Final Verification And Deploy Prep

**Files:**
- No new files unless earlier tasks require small test adjustments.

- [ ] **Step 1: Run focused Python verification**

```bash
uv run pytest tests/validation/test_outcomes.py tests/ingestion/test_rust_normalizer_sidecar.py tests/test_cli.py tests/test_runtime_api.py -q
```

Expected: all selected Python tests pass.

- [ ] **Step 2: Run Rust TUI verification**

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: all TUI tests pass.

- [ ] **Step 3: Run lint**

```bash
uv run ruff check src tests
```

Expected: `All checks passed!`

- [ ] **Step 4: Check repo status**

```bash
git status --short --branch
```

Expected: clean working tree on the implementation branch, ahead by the task commits.

- [ ] **Step 5: Deploy only after approval**

After the user approves deployment, run the existing THEPC deploy path from the repo instructions. Do not deploy during plan execution unless explicitly approved.

## Self-Review Notes

- Spec coverage: current-day close, large day sections, visible-only navigation, K missing-to-resolved behavior, official-only outcomes, dry-run/write backfill, runtime API low-contention behavior, and docs are each covered by tasks.
- Placeholder scan: this plan avoids reserved placeholder markers and names concrete functions, files, commands, and expected outcomes.
- Type consistency: Rust uses `OutcomeExpansion`, `OutcomeDisplayItem::Section`, and `OutcomeToggleTarget` consistently; Python uses `backfill_outcome_history`, `expiry_start_ts`, and `expiry_end_ts` consistently.
