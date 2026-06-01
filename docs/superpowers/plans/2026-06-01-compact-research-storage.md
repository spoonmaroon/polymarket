# Compact Research Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add replay-safe compact storage so raw WebSocket/tick data can stay hot for 90 days while permanent research tables preserve the path information needed for volatility, Monte Carlo, and no-touch analysis.

**Architecture:** Keep live decision state in memory, keep raw events immutable for the hot window, and build permanent 1-second compact tables from normalized Chainlink prices and Polymarket top-of-book observations. Do not enable automatic deletion until compact replay tests prove sampled as-of state and path features can be reproduced from compact data.

**Tech Stack:** Python 3.14 project code, DuckDB, pytest, existing `DuckDbIngestStore`, existing normalized `core.price_ticks` and `core.orderbook_snapshots` tables, existing retention manifest.

---

## Design Decision: Live Ticks Versus Permanent History

The collector should consume every live Chainlink RTDS price update and every Polymarket CLOB book update in memory. Those updates are useful immediately for current price, rolling realized volatility, source freshness, order-book freshness, and future Monte Carlo path generation.

The system should not store every raw tick forever. Permanent research history should preserve the information needed to replay decisions and estimate future paths:

- Chainlink BTC/USD and ETH/USD 1-second OHLC bars.
- intrasecond high and low, because `p_no_touch` depends on whether the path touched the danger line, not only where it closed.
- update count per second, because update intensity is a useful noise/liquidity/regime signal.
- first and last event timestamps, first and last observed timestamps, and maximum observed lag.
- source-quality flags, stale-feed flags, and missing-data flags.
- Polymarket top-of-book first/last/min/max bid, ask, spread, and top sizes per second.

This means live operation is tick-driven, while long-term research is compact and replay-safe. Raw messages stay available for 90 days for parser audits, bug investigations, and exact replays. After that, compact bars become the durable historical source unless a partition is explicitly archived.

Important boundary: compact bars are historical artifacts. The live decision engine must not use a completed 1-second bar before that second has actually finished. For an as-of decision at `12:00:00.400`, the engine may use raw updates observed at or before `12:00:00.400`, but it may not use the final high, low, or close of the `12:00:00` compact bar because that would leak later intrasecond information.

## File Structure

- Modify `src/polymarket_engine/storage/schema.sql`
  - Add `research.price_bars_1s`, `research.orderbook_bars_1s`, and `research.compaction_runs`.
- Modify `src/polymarket_engine/storage/duckdb_store.py`
  - Add insert/query methods for compact bars and compaction run records.
- Create `src/polymarket_engine/storage/compaction.py`
  - Pure compaction logic from normalized ticks/books into 1-second bars.
- Modify `src/polymarket_engine/storage/retention.py`
  - Add explicit constants for compact retention and deletion-disabled policy.
- Create `tests/storage/test_compaction.py`
  - Unit tests for price and order-book compaction logic.
- Modify `tests/storage/test_schema.py`
  - Assert compact research tables exist.
- Modify `tests/storage/test_duckdb_store.py`
  - Assert compact rows round-trip through DuckDB.
- Modify `tests/storage/test_retention.py`
  - Assert raw hot retention is 90 days and compact research has no delete policy.
- Create `tests/storage/test_compact_replay_equivalence.py`
  - Prove compact bars preserve high/low path information that close-only data would lose.
- Modify `docs/PART_TWO_LIVE_COLLECTORS.md`
  - Document the storage policy: raw hot, compact forever, no automatic delete until replay equivalence.
- Modify `docs/BINARY_CONTRACT_ENGINE_PLAN.md`
  - Add the same policy in the data-retention section.

## Task 1: Add Compact Retention Constants

**Files:**
- Modify: `src/polymarket_engine/storage/retention.py`
- Modify: `tests/storage/test_retention.py`

- [ ] **Step 1: Write the failing retention test**

Replace `tests/storage/test_retention.py` with:

```python
from polymarket_engine.storage.retention import (
    COMPACT_RESEARCH_DELETE_AFTER_DAYS,
    COMPACT_RESEARCH_RETENTION_CLASS,
    RAW_HOT_RETENTION_DAYS,
    retention_manifest_class,
)


def test_raw_hot_retention_is_90_days() -> None:
    assert RAW_HOT_RETENTION_DAYS == 90
    assert retention_manifest_class("raw") == "raw_hot_90d"


def test_compact_research_retention_is_forever_and_delete_disabled() -> None:
    assert COMPACT_RESEARCH_RETENTION_CLASS == "compact_research_forever"
    assert retention_manifest_class("compact") == "compact_research_forever"
    assert COMPACT_RESEARCH_DELETE_AFTER_DAYS is None
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/goon/.config/superpowers/worktrees/polymarket/rust-live-probe
uv run pytest tests/storage/test_retention.py -q
```

Expected: failure importing `COMPACT_RESEARCH_DELETE_AFTER_DAYS`.

- [ ] **Step 3: Implement retention constants**

Update `src/polymarket_engine/storage/retention.py`:

```python
from __future__ import annotations

RAW_HOT_RETENTION_DAYS = 90
RAW_HOT_RETENTION_CLASS = "raw_hot_90d"
COMPACT_RESEARCH_RETENTION_CLASS = "compact_research_forever"
COMPACT_RESEARCH_DELETE_AFTER_DAYS: int | None = None


def retention_manifest_class(kind: str) -> str:
    if kind == "raw":
        return RAW_HOT_RETENTION_CLASS
    if kind == "compact":
        return COMPACT_RESEARCH_RETENTION_CLASS
    raise ValueError(f"unsupported retention kind: {kind}")
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/storage/test_retention.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/storage/retention.py tests/storage/test_retention.py
git commit -m "Clarify compact research retention policy"
```

## Task 2: Add Compact Research Schema

**Files:**
- Modify: `src/polymarket_engine/storage/schema.sql`
- Modify: `tests/storage/test_schema.py`

- [ ] **Step 1: Add failing schema test**

Append this test to `tests/storage/test_schema.py`:

```python
from pathlib import Path

import duckdb

from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_research_compact_tables_exist(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    with duckdb.connect(str(db_path)) as conn:
        tables = {
            f"{row[0]}.{row[1]}"
            for row in conn.execute(
                """
                select table_schema, table_name
                from information_schema.tables
                where table_schema = 'research'
                """
            ).fetchall()
        }

    assert "research.price_bars_1s" in tables
    assert "research.orderbook_bars_1s" in tables
    assert "research.compaction_runs" in tables
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/storage/test_schema.py::test_research_compact_tables_exist -q
```

Expected: failure because the `research` schema/tables do not exist.

- [ ] **Step 3: Add research schema and tables**

Append to `src/polymarket_engine/storage/schema.sql` after the validation tables:

```sql
CREATE SCHEMA IF NOT EXISTS research;

CREATE TABLE IF NOT EXISTS research.price_bars_1s (
    source_key VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    bucket_ts TIMESTAMPTZ NOT NULL,
    open_price DOUBLE NOT NULL,
    high_price DOUBLE NOT NULL,
    low_price DOUBLE NOT NULL,
    close_price DOUBLE NOT NULL,
    update_count UBIGINT NOT NULL,
    first_event_ts TIMESTAMPTZ NOT NULL,
    last_event_ts TIMESTAMPTZ NOT NULL,
    first_observed_ts TIMESTAMPTZ NOT NULL,
    last_observed_ts TIMESTAMPTZ NOT NULL,
    max_observed_lag_ms DOUBLE,
    missing_data_flag BOOLEAN NOT NULL,
    source_quality_flags_json VARCHAR NOT NULL,
    compaction_run_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source_key, symbol, bucket_ts)
);

CREATE TABLE IF NOT EXISTS research.orderbook_bars_1s (
    venue VARCHAR NOT NULL,
    contract_id VARCHAR NOT NULL,
    token_id VARCHAR NOT NULL,
    bucket_ts TIMESTAMPTZ NOT NULL,
    first_best_bid DOUBLE,
    last_best_bid DOUBLE,
    max_best_bid DOUBLE,
    min_best_bid DOUBLE,
    first_best_ask DOUBLE,
    last_best_ask DOUBLE,
    max_best_ask DOUBLE,
    min_best_ask DOUBLE,
    first_spread DOUBLE,
    last_spread DOUBLE,
    max_spread DOUBLE,
    min_spread DOUBLE,
    first_bid_size_top DOUBLE,
    last_bid_size_top DOUBLE,
    first_ask_size_top DOUBLE,
    last_ask_size_top DOUBLE,
    update_count UBIGINT NOT NULL,
    first_event_ts TIMESTAMPTZ NOT NULL,
    last_event_ts TIMESTAMPTZ NOT NULL,
    first_observed_ts TIMESTAMPTZ NOT NULL,
    last_observed_ts TIMESTAMPTZ NOT NULL,
    missing_data_flag BOOLEAN NOT NULL,
    source_quality_flags_json VARCHAR NOT NULL,
    compaction_run_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (venue, token_id, bucket_ts)
);

CREATE TABLE IF NOT EXISTS research.compaction_runs (
    compaction_run_id VARCHAR PRIMARY KEY,
    source_table VARCHAR NOT NULL,
    target_table VARCHAR NOT NULL,
    source_key VARCHAR,
    symbol VARCHAR,
    start_ts TIMESTAMPTZ NOT NULL,
    end_ts TIMESTAMPTZ NOT NULL,
    input_rows UBIGINT NOT NULL,
    output_rows UBIGINT NOT NULL,
    high_low_preserved BOOLEAN NOT NULL,
    delete_raw_allowed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

- [ ] **Step 4: Run schema test**

Run:

```bash
uv run pytest tests/storage/test_schema.py::test_research_compact_tables_exist -q
```

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/storage/schema.sql tests/storage/test_schema.py
git commit -m "Add compact research schema"
```

## Task 3: Implement Pure Price Compaction

**Files:**
- Create: `src/polymarket_engine/storage/compaction.py`
- Create: `tests/storage/test_compaction.py`

- [ ] **Step 1: Write failing price compaction tests**

Create `tests/storage/test_compaction.py`:

```python
from datetime import datetime, timezone

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.storage.compaction import build_price_bars_1s


def _ts(second: int, microsecond: int = 0) -> datetime:
    return datetime(2026, 6, 1, 12, 0, second, microsecond, tzinfo=timezone.utc)


def test_price_bar_preserves_high_low_close_and_count() -> None:
    ticks = (
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", _ts(0, 100_000), _ts(0, 150_000), 100.0),
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", _ts(0, 200_000), _ts(0, 250_000), 96.0),
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", _ts(0, 900_000), _ts(0, 950_000), 101.0),
    )

    bars = build_price_bars_1s(ticks, compaction_run_id="run-1")

    assert len(bars) == 1
    bar = bars[0]
    assert bar.open_price == 100.0
    assert bar.high_price == 101.0
    assert bar.low_price == 96.0
    assert bar.close_price == 101.0
    assert bar.update_count == 3
    assert bar.bucket_ts == _ts(0)


def test_price_bar_rejects_mixed_source_symbol_in_one_call() -> None:
    ticks = (
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", _ts(0), _ts(0), 100.0),
        PriceObservation("polymarket_rtds_chainlink", "ETH/USD", _ts(0), _ts(0), 10.0),
    )

    try:
        build_price_bars_1s(ticks, compaction_run_id="run-1")
    except ValueError as exc:
        assert "single source_key and symbol" in str(exc)
    else:
        raise AssertionError("expected mixed symbol rejection")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/storage/test_compaction.py -q
```

Expected: import failure because `polymarket_engine.storage.compaction` does not exist.

- [ ] **Step 3: Implement price compaction**

Create `src/polymarket_engine/storage/compaction.py`:

```python
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Sequence

from polymarket_engine.domain.market_state import OrderBookObservation, PriceObservation


def floor_to_second(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    value = value.astimezone(timezone.utc)
    return value.replace(microsecond=0)


@dataclass(frozen=True)
class PriceBar1s:
    source_key: str
    symbol: str
    bucket_ts: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    update_count: int
    first_event_ts: datetime
    last_event_ts: datetime
    first_observed_ts: datetime
    last_observed_ts: datetime
    max_observed_lag_ms: float | None
    missing_data_flag: bool
    source_quality_flags_json: str
    compaction_run_id: str


def build_price_bars_1s(
    ticks: Sequence[PriceObservation],
    *,
    compaction_run_id: str,
) -> Sequence[PriceBar1s]:
    if not ticks:
        return ()
    source_pairs = {(tick.source_key, tick.symbol) for tick in ticks}
    if len(source_pairs) != 1:
        raise ValueError("price compaction expects a single source_key and symbol")

    ordered = sorted(ticks, key=lambda tick: (tick.event_ts, tick.observed_ts))
    groups: dict[datetime, list[PriceObservation]] = defaultdict(list)
    for tick in ordered:
        groups[floor_to_second(tick.event_ts)].append(tick)

    bars: list[PriceBar1s] = []
    for bucket_ts in sorted(groups):
        bucket_ticks = groups[bucket_ts]
        prices = [tick.price for tick in bucket_ticks]
        lags = [
            (tick.observed_ts - tick.event_ts).total_seconds() * 1000.0
            for tick in bucket_ticks
        ]
        bars.append(
            PriceBar1s(
                source_key=bucket_ticks[0].source_key,
                symbol=bucket_ticks[0].symbol,
                bucket_ts=bucket_ts,
                open_price=prices[0],
                high_price=max(prices),
                low_price=min(prices),
                close_price=prices[-1],
                update_count=len(bucket_ticks),
                first_event_ts=bucket_ticks[0].event_ts,
                last_event_ts=bucket_ticks[-1].event_ts,
                first_observed_ts=min(tick.observed_ts for tick in bucket_ticks),
                last_observed_ts=max(tick.observed_ts for tick in bucket_ticks),
                max_observed_lag_ms=max(lags) if lags else None,
                missing_data_flag=False,
                source_quality_flags_json="[]",
                compaction_run_id=compaction_run_id,
            )
        )
    return tuple(bars)
```

- [ ] **Step 4: Run price compaction tests**

Run:

```bash
uv run pytest tests/storage/test_compaction.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/storage/compaction.py tests/storage/test_compaction.py
git commit -m "Add one-second price compaction"
```

## Task 4: Implement Pure Order-Book Compaction

**Files:**
- Modify: `src/polymarket_engine/storage/compaction.py`
- Modify: `tests/storage/test_compaction.py`

- [ ] **Step 1: Add failing order-book compaction test**

Append to `tests/storage/test_compaction.py`:

```python
from polymarket_engine.domain.market_state import OrderBookObservation
from polymarket_engine.storage.compaction import build_orderbook_bars_1s


def test_orderbook_bar_preserves_spread_and_top_of_book_ranges() -> None:
    books = (
        OrderBookObservation(
            venue="polymarket",
            contract_id="btc-1:UP",
            token_id="111",
            event_ts=_ts(1, 100_000),
            observed_ts=_ts(1, 120_000),
            best_bid=0.48,
            best_ask=0.51,
            bid_size_top=100.0,
            ask_size_top=120.0,
            spread=0.03,
            depth_json="{}",
        ),
        OrderBookObservation(
            venue="polymarket",
            contract_id="btc-1:UP",
            token_id="111",
            event_ts=_ts(1, 700_000),
            observed_ts=_ts(1, 750_000),
            best_bid=0.49,
            best_ask=0.50,
            bid_size_top=80.0,
            ask_size_top=90.0,
            spread=0.01,
            depth_json="{}",
        ),
    )

    bars = build_orderbook_bars_1s(books, compaction_run_id="run-book-1")

    assert len(bars) == 1
    bar = bars[0]
    assert bar.first_best_bid == 0.48
    assert bar.last_best_bid == 0.49
    assert bar.max_best_bid == 0.49
    assert bar.min_best_ask == 0.50
    assert bar.first_spread == 0.03
    assert bar.last_spread == 0.01
    assert bar.max_spread == 0.03
    assert bar.min_spread == 0.01
    assert bar.update_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/storage/test_compaction.py::test_orderbook_bar_preserves_spread_and_top_of_book_ranges -q
```

Expected: import failure because `build_orderbook_bars_1s` does not exist.

- [ ] **Step 3: Implement order-book compaction**

Append to `src/polymarket_engine/storage/compaction.py`:

```python
@dataclass(frozen=True)
class OrderBookBar1s:
    venue: str
    contract_id: str
    token_id: str
    bucket_ts: datetime
    first_best_bid: float | None
    last_best_bid: float | None
    max_best_bid: float | None
    min_best_bid: float | None
    first_best_ask: float | None
    last_best_ask: float | None
    max_best_ask: float | None
    min_best_ask: float | None
    first_spread: float | None
    last_spread: float | None
    max_spread: float | None
    min_spread: float | None
    first_bid_size_top: float | None
    last_bid_size_top: float | None
    first_ask_size_top: float | None
    last_ask_size_top: float | None
    update_count: int
    first_event_ts: datetime
    last_event_ts: datetime
    first_observed_ts: datetime
    last_observed_ts: datetime
    missing_data_flag: bool
    source_quality_flags_json: str
    compaction_run_id: str


def _values(values: list[float | None]) -> list[float]:
    return [value for value in values if value is not None]


def _max_or_none(values: list[float | None]) -> float | None:
    clean = _values(values)
    return max(clean) if clean else None


def _min_or_none(values: list[float | None]) -> float | None:
    clean = _values(values)
    return min(clean) if clean else None


def build_orderbook_bars_1s(
    books: Sequence[OrderBookObservation],
    *,
    compaction_run_id: str,
) -> Sequence[OrderBookBar1s]:
    if not books:
        return ()
    source_keys = {(book.venue, book.contract_id, book.token_id) for book in books}
    if len(source_keys) != 1:
        raise ValueError("order-book compaction expects a single venue, contract_id, and token_id")

    ordered = sorted(books, key=lambda book: (book.event_ts, book.observed_ts))
    groups: dict[datetime, list[OrderBookObservation]] = defaultdict(list)
    for book in ordered:
        groups[floor_to_second(book.event_ts)].append(book)

    bars: list[OrderBookBar1s] = []
    for bucket_ts in sorted(groups):
        bucket_books = groups[bucket_ts]
        bid_values = [book.best_bid for book in bucket_books]
        ask_values = [book.best_ask for book in bucket_books]
        spread_values = [book.spread for book in bucket_books]
        bars.append(
            OrderBookBar1s(
                venue=bucket_books[0].venue,
                contract_id=bucket_books[0].contract_id,
                token_id=bucket_books[0].token_id,
                bucket_ts=bucket_ts,
                first_best_bid=bucket_books[0].best_bid,
                last_best_bid=bucket_books[-1].best_bid,
                max_best_bid=_max_or_none(bid_values),
                min_best_bid=_min_or_none(bid_values),
                first_best_ask=bucket_books[0].best_ask,
                last_best_ask=bucket_books[-1].best_ask,
                max_best_ask=_max_or_none(ask_values),
                min_best_ask=_min_or_none(ask_values),
                first_spread=bucket_books[0].spread,
                last_spread=bucket_books[-1].spread,
                max_spread=_max_or_none(spread_values),
                min_spread=_min_or_none(spread_values),
                first_bid_size_top=bucket_books[0].bid_size_top,
                last_bid_size_top=bucket_books[-1].bid_size_top,
                first_ask_size_top=bucket_books[0].ask_size_top,
                last_ask_size_top=bucket_books[-1].ask_size_top,
                update_count=len(bucket_books),
                first_event_ts=bucket_books[0].event_ts,
                last_event_ts=bucket_books[-1].event_ts,
                first_observed_ts=min(book.observed_ts for book in bucket_books),
                last_observed_ts=max(book.observed_ts for book in bucket_books),
                missing_data_flag=False,
                source_quality_flags_json="[]",
                compaction_run_id=compaction_run_id,
            )
        )
    return tuple(bars)
```

- [ ] **Step 4: Run compaction tests**

Run:

```bash
uv run pytest tests/storage/test_compaction.py -q
```

Expected: all compaction tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/storage/compaction.py tests/storage/test_compaction.py
git commit -m "Add one-second orderbook compaction"
```

## Task 5: Add DuckDB Compact Row Writes

**Files:**
- Modify: `src/polymarket_engine/storage/duckdb_store.py`
- Modify: `tests/storage/test_duckdb_store.py`

- [ ] **Step 1: Write failing DuckDB round-trip test**

Append to `tests/storage/test_duckdb_store.py`:

```python
from polymarket_engine.storage.compaction import PriceBar1s


def test_duckdb_store_writes_price_bar_1s(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    bucket = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    store.insert_price_bar_1s(
        PriceBar1s(
            source_key="polymarket_rtds_chainlink",
            symbol="BTC/USD",
            bucket_ts=bucket,
            open_price=100.0,
            high_price=102.0,
            low_price=99.0,
            close_price=101.0,
            update_count=3,
            first_event_ts=bucket,
            last_event_ts=bucket,
            first_observed_ts=bucket,
            last_observed_ts=bucket,
            max_observed_lag_ms=25.0,
            missing_data_flag=False,
            source_quality_flags_json="[]",
            compaction_run_id="run-1",
        )
    )

    with duckdb.connect(str(db_path)) as conn:
        rows = conn.sql(
            "select open_price, high_price, low_price, close_price from research.price_bars_1s"
        ).fetchall()

    assert rows == [(100.0, 102.0, 99.0, 101.0)]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/storage/test_duckdb_store.py::test_duckdb_store_writes_price_bar_1s -q
```

Expected: failure because `insert_price_bar_1s` does not exist.

- [ ] **Step 3: Add compact insert methods**

Import compact bar types in `duckdb_store.py`:

```python
from polymarket_engine.storage.compaction import OrderBookBar1s, PriceBar1s
```

Add methods inside `DuckDbIngestStore`:

```python
    def insert_price_bar_1s(self, bar: PriceBar1s) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into research.price_bars_1s
                (source_key, symbol, bucket_ts, open_price, high_price, low_price, close_price,
                 update_count, first_event_ts, last_event_ts, first_observed_ts, last_observed_ts,
                 max_observed_lag_ms, missing_data_flag, source_quality_flags_json,
                 compaction_run_id, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    bar.source_key,
                    bar.symbol,
                    bar.bucket_ts,
                    bar.open_price,
                    bar.high_price,
                    bar.low_price,
                    bar.close_price,
                    bar.update_count,
                    bar.first_event_ts,
                    bar.last_event_ts,
                    bar.first_observed_ts,
                    bar.last_observed_ts,
                    bar.max_observed_lag_ms,
                    bar.missing_data_flag,
                    bar.source_quality_flags_json,
                    bar.compaction_run_id,
                    datetime.now(timezone.utc),
                ],
            )
```

Add equivalent `insert_orderbook_bar_1s(self, bar: OrderBookBar1s) -> None` using every field in `research.orderbook_bars_1s`.

- [ ] **Step 4: Run DuckDB tests**

Run:

```bash
uv run pytest tests/storage/test_duckdb_store.py::test_duckdb_store_writes_price_bar_1s -q
```

Expected: test passes.

- [ ] **Step 5: Add order-book round-trip test**

Add a test that creates `OrderBookBar1s`, calls `store.insert_orderbook_bar_1s(bar)`, and asserts `min_spread` and `max_spread` round-trip from `research.orderbook_bars_1s`.

- [ ] **Step 6: Run storage tests**

Run:

```bash
uv run pytest tests/storage/test_duckdb_store.py tests/storage/test_schema.py -q
```

Expected: storage tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/polymarket_engine/storage/duckdb_store.py tests/storage/test_duckdb_store.py
git commit -m "Write compact research bars to DuckDB"
```

## Task 6: Add Store-Level Compaction Jobs

**Files:**
- Modify: `src/polymarket_engine/storage/duckdb_store.py`
- Modify: `tests/storage/test_duckdb_store.py`

- [ ] **Step 1: Write failing compaction job test**

Append to `tests/storage/test_duckdb_store.py`:

```python
from polymarket_engine.domain.market_state import PriceObservation


def test_store_compacts_price_ticks_to_one_second_bars(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    store.insert_price_tick(PriceObservation("polymarket_rtds_chainlink", "BTC/USD", t0, t0, 100.0))
    store.insert_price_tick(PriceObservation("polymarket_rtds_chainlink", "BTC/USD", t0.replace(microsecond=500000), t0.replace(microsecond=500000), 95.0))

    run_id = store.compact_price_ticks_to_1s(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        start_ts=t0,
        end_ts=t0.replace(second=1),
    )

    with duckdb.connect(str(db_path)) as conn:
        bars = conn.sql(
            "select low_price, close_price from research.price_bars_1s where compaction_run_id = ?",
            [run_id],
        ).fetchall()
        runs = conn.sql(
            "select input_rows, output_rows, high_low_preserved, delete_raw_allowed from research.compaction_runs where compaction_run_id = ?",
            [run_id],
        ).fetchall()

    assert bars == [(95.0, 95.0)]
    assert runs == [(2, 1, True, False)]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/storage/test_duckdb_store.py::test_store_compacts_price_ticks_to_one_second_bars -q
```

Expected: failure because `compact_price_ticks_to_1s` does not exist.

- [ ] **Step 3: Implement store compaction method**

Add a method to `DuckDbIngestStore` that:

1. queries `core.price_ticks` between `start_ts <= event_ts < end_ts`;
2. builds `PriceObservation` values;
3. calls `build_price_bars_1s`;
4. inserts every compact bar;
5. writes `research.compaction_runs` with `delete_raw_allowed=False`.

Use a run id:

```python
run_id = f"price:{source_key}:{symbol}:{start_ts.isoformat()}:{end_ts.isoformat()}"
```

- [ ] **Step 4: Run compaction job test**

Run:

```bash
uv run pytest tests/storage/test_duckdb_store.py::test_store_compacts_price_ticks_to_one_second_bars -q
```

Expected: test passes.

- [ ] **Step 5: Add order-book compaction job test and method**

Add `compact_orderbooks_to_1s(venue, token_id, start_ts, end_ts) -> str` that reads from `core.orderbook_snapshots`, builds `OrderBookObservation` values, compacts them, inserts `research.orderbook_bars_1s`, and records `delete_raw_allowed=False`.

- [ ] **Step 6: Run store tests**

Run:

```bash
uv run pytest tests/storage/test_duckdb_store.py -q
```

Expected: all DuckDB store tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/polymarket_engine/storage/duckdb_store.py tests/storage/test_duckdb_store.py
git commit -m "Add compact storage jobs"
```

## Task 7: Add Compact Replay Equivalence Tests

**Files:**
- Create: `tests/storage/test_compact_replay_equivalence.py`

- [ ] **Step 1: Write no-touch path preservation test**

Create `tests/storage/test_compact_replay_equivalence.py`:

```python
from datetime import datetime, timezone

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.storage.compaction import build_price_bars_1s


def _ts(microsecond: int) -> datetime:
    return datetime(2026, 6, 1, 12, 0, 0, microsecond, tzinfo=timezone.utc)


def test_compact_price_bar_preserves_intrasecond_barrier_touch() -> None:
    threshold = 100.0
    ticks = (
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", _ts(100_000), _ts(100_000), 101.0),
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", _ts(400_000), _ts(400_000), 99.5),
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", _ts(900_000), _ts(900_000), 101.5),
    )

    bar = build_price_bars_1s(ticks, compaction_run_id="run-1")[0]

    close_only_would_miss_touch = bar.close_price > threshold
    compact_bar_detects_touch = bar.low_price <= threshold

    assert close_only_would_miss_touch is True
    assert compact_bar_detects_touch is True
```

- [ ] **Step 2: Run test**

Run:

```bash
uv run pytest tests/storage/test_compact_replay_equivalence.py -q
```

Expected: test passes after prior compaction implementation.

- [ ] **Step 3: Add as-of latest compact price test**

Add a test showing `close_price` is acceptable for terminal price approximation at a 1-second bucket, while `high_price`/`low_price` are required for path/no-touch reconstruction.

- [ ] **Step 4: Run replay tests**

Run:

```bash
uv run pytest tests/storage/test_compact_replay_equivalence.py tests/storage/test_compaction.py -q
```

Expected: replay equivalence tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/storage/test_compact_replay_equivalence.py
git commit -m "Prove compact bars preserve path touches"
```

## Task 8: Add Documentation And Deletion Guardrails

**Files:**
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Modify: `docs/BINARY_CONTRACT_ENGINE_PLAN.md`

- [ ] **Step 1: Update collector docs**

In `docs/PART_TWO_LIVE_COLLECTORS.md`, replace the retention paragraph with:

```markdown
Raw event data remains hot for 90 days. Raw hot data includes Chainlink RTDS messages, CLOB market WebSocket events, REST backup snapshots, source errors, and raw payloads needed for parser audits.

Permanent research storage is compact. The permanent layer stores 1-second Chainlink BTC/USD and ETH/USD bars with open, high, low, close, update count, timestamp range, source-lag metrics, and quality flags. It also stores 1-second top-of-book bars with first/last/range fields for bid, ask, spread, and top sizes.

The compact layer must preserve high/low path information because `p_no_touch` can fail even when the final close looks safe. Close-only historical bars are not acceptable for no-touch replay.

Automatic deletion is disabled. Raw partitions may be archived or deleted only after replay-equivalence tests prove sampled as-of states and path-touch labels can be reproduced from compact tables.
```

- [ ] **Step 2: Update engine plan docs**

In `docs/BINARY_CONTRACT_ENGINE_PLAN.md`, add the same policy to the data retention section and explicitly say:

```markdown
The engine should use every live WebSocket update in memory, but it should not store every raw tick forever. The permanent storage target is compact, replay-safe data rather than infinite raw tick retention.
```

- [ ] **Step 3: Run doc grep check**

Run:

```bash
rg -n "close-only|Automatic deletion is disabled|1-second Chainlink" docs/PART_TWO_LIVE_COLLECTORS.md docs/BINARY_CONTRACT_ENGINE_PLAN.md
```

Expected: both docs contain the updated policy.

- [ ] **Step 4: Commit**

```bash
git add docs/PART_TWO_LIVE_COLLECTORS.md docs/BINARY_CONTRACT_ENGINE_PLAN.md
git commit -m "Document compact research storage policy"
```

## Task 9: Full Verification

**Files:**
- No new source edits unless verification exposes a defect.

- [ ] **Step 1: Run targeted tests**

```bash
cd /Users/goon/.config/superpowers/worktrees/polymarket/rust-live-probe
uv run pytest tests/storage/test_retention.py tests/storage/test_schema.py tests/storage/test_compaction.py tests/storage/test_duckdb_store.py tests/storage/test_compact_replay_equivalence.py -q
```

Expected: all targeted storage tests pass.

- [ ] **Step 2: Run Python quality gates**

```bash
uv run ruff check src tests
uv run mypy src tests
uv run pytest -q
```

Expected: ruff, mypy, and full pytest pass.

- [ ] **Step 3: Run Rust gates**

```bash
cd /Users/goon/.config/superpowers/worktrees/polymarket/rust-live-probe/rust
cargo fmt --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

Expected: Rust formatting, clippy, and tests pass.

- [ ] **Step 4: Commit any verification fixes**

If verification required fixes:

```bash
git add <fixed-files>
git commit -m "Fix compact research storage verification"
```

Expected: no fixes needed after earlier tasks if all tasks were followed.

## Self-Review Checklist

- Spec coverage: this plan implements raw hot retention, permanent 1-second Chainlink bars, permanent 1-second top-of-book bars, high/low path preservation for `p_no_touch`, compaction run audit records, and deletion-disabled guardrails.
- Placeholder scan: the plan provides concrete files, tests, commands, and code snippets for each implementation task.
- Type consistency: `PriceBar1s`, `OrderBookBar1s`, `build_price_bars_1s`, `build_orderbook_bars_1s`, `insert_price_bar_1s`, and `compact_price_ticks_to_1s` are defined before later tasks use them.
