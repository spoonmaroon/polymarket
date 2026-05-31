# Part Two Live Collectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only live collector command that records BTC/ETH Polymarket 5-minute contracts, Polymarket order books, Coinbase BTC/ETH ticker updates, and attempted Polymarket RTDS/reference updates into the Part One raw Parquet + DuckDB storage contract.

**Architecture:** Keep Part Two as data collection only: no probability engine, no orders, no model decisions. Each source adapter emits a common `CollectorEvent` with source timestamp, receive timestamp, lag, quality flags, and raw payload; buffered writers persist immutable Parquet batches and register files in DuckDB. The collector borrows GEX's storage discipline: write `.tmp`, fsync, atomically publish, fsync the parent directory, clean orphaned temporary files on startup, checkpoint only after publication, and reconnect with bounded exponential backoff so power loss or Wi-Fi loss creates a recoverable gap instead of corrupt data.

**Tech Stack:** Python 3.11, asyncio, websockets, httpx, DuckDB, Polars, PyArrow, argparse, pytest, ruff, mypy.

---

## Current Evidence And Source Decisions

Part One proved the repository can write ignored raw Parquet samples under `data/raw/`.

Live smoke results on 2026-05-31:

- Coinbase Advanced Trade WebSocket returned live BTC/ETH ticker data for `BTC-USD` and `ETH-USD`.
- Polymarket Gamma returned BTC/ETH 5-minute markets when queried by deterministic slug.
- Polymarket CLOB REST returned order book snapshots by `token_id`.
- Binance.com WebSocket rejected the connection with `HTTP 451`, so Binance.com is not a reliable first live collector from this machine.
- Polymarket website chart price can lag, so the collector must never treat the website chart as model truth.

Part Two source priority:

1. **Polymarket Gamma** for current and near-future BTC/ETH 5m contract discovery.
2. **Polymarket CLOB REST snapshots** for executable order book snapshots.
3. **Polymarket CLOB market WebSocket** for low-latency order book events after token ids are known.
4. **Coinbase Advanced Trade WebSocket** for live BTC/ETH proxy ticks and realized movement.
5. **Polymarket RTDS** for Chainlink-style or venue-supported crypto reference ticks when messages are available.
6. **Binance.com WebSocket** stays disabled by default because this machine received `HTTP 451`.

Database decision remains unchanged:

- Parquet is the immutable raw event lake.
- DuckDB records schema, ingest files, checkpoints, source health, and collector runs.
- No Postgres, Redis, ClickHouse, Timescale, or queue service in Part Two.

GEX-inspired durability rules:

1. **Final Parquet paths are truth.** Temporary files are never queryable and never registered in DuckDB.
2. **Atomic publish is required.** Raw files are written as `.parquet.tmp`, fsynced, linked or replaced into the final path, then the parent directory is fsynced.
3. **Startup recovery is required.** The collector removes orphaned `.parquet.tmp` files before opening live streams.
4. **Archive sentinel is required outside tests.** The raw root must contain `.polymarket_archive_root` so a power-cycle or unmounted drive does not silently write to the wrong volume.
5. **Flush by rows and time.** A quiet stream still flushes within a bounded time window; data should not sit in RAM for minutes just because messages are sparse.
6. **Checkpoint after durable publish.** DuckDB checkpoints advance only after the raw Parquet file exists and is registered.
7. **Wi-Fi loss is a source state, not a process death.** WebSocket loops reconnect with capped exponential backoff and jitter, record source errors, and keep other sources running.
8. **Gap flags are explicit.** If a stream reconnects without replay support, the next event carries `gap_detected` so downstream replay knows the path is incomplete.
9. **Graceful shutdown flushes.** `KeyboardInterrupt`, `SIGTERM`, or service stop must flush all buffers before exit.
10. **Service restart is planned but not trading-critical.** The first systemd/launchd wrapper only restarts read-only collection; it never places orders.

---

## File Structure

```text
/Users/goon/polymarket/
├── config/
│   └── local.example.toml
├── docs/
│   ├── BINARY_CONTRACT_ENGINE_PLAN.md
│   ├── PART_TWO_LIVE_COLLECTORS.md
│   └── superpowers/
│       └── plans/
│           └── 2026-05-31-part-two-live-collectors.md
├── pyproject.toml
├── ops/
│   └── systemd/
│       └── polymarket-live-collector.service
├── src/
│   └── polymarket_engine/
│       ├── cli.py
│       ├── domain/
│       │   └── sources.py
│       ├── ingestion/
│       │   ├── collector_events.py
│       │   ├── contract_discovery.py
│       │   ├── coinbase_ws.py
│       │   ├── live_collector.py
│       │   ├── polymarket_clob.py
│       │   ├── polymarket_rtds.py
│       │   └── reconnect.py
│       └── storage/
│           ├── atomic.py
│           ├── buffered_writer.py
│           ├── duckdb_store.py
│           ├── recovery.py
│           └── schema.sql
└── tests/
    ├── ingestion/
    │   ├── test_coinbase_ws.py
    │   ├── test_collector_events.py
    │   ├── test_contract_discovery.py
    │   ├── test_live_collector.py
    │   ├── test_polymarket_clob.py
    │   ├── test_polymarket_rtds.py
    │   └── test_reconnect.py
    ├── storage/
    │   ├── test_atomic.py
    │   ├── test_buffered_writer.py
    │   ├── test_duckdb_store.py
    │   └── test_recovery.py
    └── test_cli.py
```

---

## Task 0: Crash-Durable Storage Foundation

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/storage/atomic.py`
- Create: `/Users/goon/polymarket/src/polymarket_engine/storage/recovery.py`
- Modify: `/Users/goon/polymarket/src/polymarket_engine/storage/raw_writer.py`
- Test: `/Users/goon/polymarket/tests/storage/test_atomic.py`
- Test: `/Users/goon/polymarket/tests/storage/test_recovery.py`
- Test: `/Users/goon/polymarket/tests/storage/test_raw_writer.py`

- [ ] **Step 1: Write failing tests for durable publish and cleanup**

Create `/Users/goon/polymarket/tests/storage/test_atomic.py`:

```python
from pathlib import Path

from polymarket_engine.storage.atomic import durable_replace


def test_durable_replace_consumes_tmp_and_publishes_final(tmp_path: Path) -> None:
    tmp = tmp_path / "sample.parquet.tmp"
    final = tmp_path / "sample.parquet"
    tmp.write_bytes(b"payload")

    durable_replace(tmp, final)

    assert final.read_bytes() == b"payload"
    assert not tmp.exists()
```

Create `/Users/goon/polymarket/tests/storage/test_recovery.py`:

```python
from pathlib import Path

from polymarket_engine.storage.recovery import cleanup_orphaned_tmp, ensure_archive_sentinel


def test_cleanup_orphaned_tmp_removes_only_tmp_files(tmp_path: Path) -> None:
    tmp_file = tmp_path / "source=coinbase" / "event.parquet.tmp"
    final_file = tmp_path / "source=coinbase" / "event.parquet"
    tmp_file.parent.mkdir(parents=True)
    tmp_file.write_bytes(b"partial")
    final_file.write_bytes(b"complete")

    removed = cleanup_orphaned_tmp(tmp_path)

    assert removed == (tmp_file,)
    assert not tmp_file.exists()
    assert final_file.exists()


def test_ensure_archive_sentinel_rejects_missing_sentinel(tmp_path: Path) -> None:
    try:
        ensure_archive_sentinel(tmp_path)
    except RuntimeError as exc:
        assert ".polymarket_archive_root" in str(exc)
    else:
        raise AssertionError("missing sentinel should raise")


def test_ensure_archive_sentinel_accepts_existing_sentinel(tmp_path: Path) -> None:
    (tmp_path / ".polymarket_archive_root").touch()

    ensure_archive_sentinel(tmp_path)
```

Append this test to `/Users/goon/polymarket/tests/storage/test_raw_writer.py`:

```python
def test_write_raw_events_leaves_no_tmp_files(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    (raw_root / ".polymarket_archive_root").parent.mkdir(parents=True, exist_ok=True)
    (raw_root / ".polymarket_archive_root").touch()
    event = RawEvent(
        source_key="coinbase_advanced_ws",
        stream_key="ticker",
        symbol="BTC-USD",
        event_ts=datetime(2026, 5, 31, 21, 0, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc),
        payload={"price": "104000"},
    )

    result = write_raw_events(raw_root, [event], require_archive_sentinel=True)

    assert result.path.exists()
    assert list(raw_root.rglob("*.parquet.tmp")) == []
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_atomic.py tests/storage/test_recovery.py tests/storage/test_raw_writer.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.storage.atomic'
```

- [ ] **Step 3: Implement durable atomic helpers**

Create `/Users/goon/polymarket/src/polymarket_engine/storage/atomic.py`:

```python
from __future__ import annotations

import os
from pathlib import Path


def durable_link(tmp: Path, final: Path, *, parent_fsync: bool = True) -> None:
    _fsync_file(tmp)
    os.link(tmp, final)
    if parent_fsync:
        _fsync_dir(final.parent)


def durable_replace(tmp: Path, final: Path, *, parent_fsync: bool = True) -> None:
    _fsync_file(tmp)
    os.replace(tmp, final)
    if parent_fsync:
        _fsync_dir(final.parent)


def _fsync_file(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)
```

Create `/Users/goon/polymarket/src/polymarket_engine/storage/recovery.py`:

```python
from __future__ import annotations

from pathlib import Path


ARCHIVE_SENTINEL = ".polymarket_archive_root"
TMP_SUFFIX = ".tmp"


def ensure_archive_sentinel(raw_root: Path) -> None:
    sentinel = raw_root / ARCHIVE_SENTINEL
    if not sentinel.exists():
        raise RuntimeError(
            f"archive sentinel missing at {sentinel}. "
            "Create it once with `touch data/raw/.polymarket_archive_root` "
            "after confirming this is the intended raw event volume."
        )


def cleanup_orphaned_tmp(raw_root: Path) -> tuple[Path, ...]:
    removed: list[Path] = []
    for path in raw_root.rglob(f"*{TMP_SUFFIX}"):
        if path.is_file():
            path.unlink()
            removed.append(path)
    return tuple(removed)
```

- [ ] **Step 4: Modify raw writer to publish atomically**

Replace `/Users/goon/polymarket/src/polymarket_engine/storage/raw_writer.py` with:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import polars as pl

from polymarket_engine.storage.atomic import durable_link
from polymarket_engine.storage.paths import RawPartition
from polymarket_engine.storage.recovery import cleanup_orphaned_tmp, ensure_archive_sentinel


@dataclass(frozen=True)
class RawEvent:
    source_key: str
    stream_key: str
    symbol: str
    event_ts: datetime
    observed_ts: datetime
    payload: dict[str, object]


@dataclass(frozen=True)
class RawWriteResult:
    file_id: str
    path: Path
    sha256: str
    row_count: int
    first_event_ts: datetime
    last_event_ts: datetime


def write_raw_events(
    raw_root: Path,
    events: list[RawEvent],
    *,
    require_archive_sentinel: bool = False,
) -> RawWriteResult:
    if not events:
        raise ValueError("events must not be empty")
    if require_archive_sentinel:
        ensure_archive_sentinel(raw_root)
    cleanup_orphaned_tmp(raw_root)

    first = min(event.event_ts for event in events)
    last = max(event.event_ts for event in events)
    source_keys = {event.source_key for event in events}
    stream_keys = {event.stream_key for event in events}
    if len(source_keys) != 1 or len(stream_keys) != 1:
        raise ValueError("one source_key and one stream_key required per raw file")

    source_key = next(iter(source_keys))
    stream_key = next(iter(stream_keys))
    partition = RawPartition(raw_root, source_key, stream_key, first)
    partition.directory.mkdir(parents=True, exist_ok=True)

    file_id = uuid4().hex
    output_path = partition.directory / f"{file_id}.parquet"
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp_path.unlink(missing_ok=True)
    rows = [
        {
            **asdict(event),
            "event_ts": event.event_ts.astimezone(timezone.utc),
            "observed_ts": event.observed_ts.astimezone(timezone.utc),
            "payload": json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
        }
        for event in events
    ]

    pl.DataFrame(rows).write_parquet(tmp_path, compression="zstd")
    try:
        durable_link(tmp_path, output_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()

    return RawWriteResult(
        file_id=file_id,
        path=output_path,
        sha256=sha256,
        row_count=len(events),
        first_event_ts=first,
        last_event_ts=last,
    )
```

Keep `require_archive_sentinel=False` as the default so existing tests that use `tmp_path` remain simple. The live collector must pass `require_archive_sentinel=True` unless the CLI exposes an explicit test-only flag.

- [ ] **Step 5: Run tests and verify GREEN**

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_atomic.py tests/storage/test_recovery.py tests/storage/test_raw_writer.py -v
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 6: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/storage/atomic.py src/polymarket_engine/storage/recovery.py src/polymarket_engine/storage/raw_writer.py tests/storage/test_atomic.py tests/storage/test_recovery.py tests/storage/test_raw_writer.py
git commit -m "Add crash-durable raw event writes"
```

## Task 1: Collector Event Model And Source Health

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/ingestion/collector_events.py`
- Test: `/Users/goon/polymarket/tests/ingestion/test_collector_events.py`

- [ ] **Step 1: Write failing tests**

```python
from datetime import datetime, timezone

from polymarket_engine.ingestion.collector_events import (
    CollectorEvent,
    SourceHealth,
    SourceLag,
    SourceQualityFlag,
)


def test_collector_event_calculates_lag_ms() -> None:
    event = CollectorEvent(
        source_key="coinbase_advanced_ws",
        stream_key="ticker",
        symbol="BTC-USD",
        event_ts=datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 21, 0, 1, 250000, tzinfo=timezone.utc),
        payload={"price": "104000.0"},
    )

    assert event.lag_ms == 1250
    assert event.to_raw_event().source_key == "coinbase_advanced_ws"


def test_source_lag_flags_stale_and_negative_clock_values() -> None:
    fresh = SourceLag(source_key="coinbase_advanced_ws", lag_ms=900, stale_after_ms=2000)
    stale = SourceLag(source_key="polymarket_rtds_chainlink", lag_ms=6000, stale_after_ms=5000)
    bad_clock = SourceLag(source_key="coinbase_advanced_ws", lag_ms=-50, stale_after_ms=2000)

    assert fresh.quality_flags() == ()
    assert stale.quality_flags() == (SourceQualityFlag.STALE_SOURCE,)
    assert bad_clock.quality_flags() == (SourceQualityFlag.CLOCK_SKEW,)


def test_source_health_is_unhealthy_when_recent_error_exists() -> None:
    health = SourceHealth(
        source_key="binance_spot_ws",
        connected=False,
        last_event_ts=None,
        last_observed_ts=None,
        last_error="HTTP 451",
        quality_flags=(SourceQualityFlag.SOURCE_BLOCKED,),
    )

    assert health.is_healthy is False
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_collector_events.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.ingestion.collector_events'
```

- [ ] **Step 3: Implement collector event model**

Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/collector_events.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from polymarket_engine.storage.raw_writer import RawEvent


class SourceQualityFlag(str, Enum):
    STALE_SOURCE = "stale_source"
    CLOCK_SKEW = "clock_skew"
    SOURCE_BLOCKED = "source_blocked"
    EMPTY_MESSAGE = "empty_message"
    PARSE_ERROR = "parse_error"
    GAP_DETECTED = "gap_detected"


@dataclass(frozen=True)
class CollectorEvent:
    source_key: str
    stream_key: str
    symbol: str
    event_ts: datetime
    observed_ts: datetime
    payload: dict[str, Any]
    quality_flags: tuple[SourceQualityFlag, ...] = ()

    @property
    def lag_ms(self) -> int:
        return int((self.observed_ts - self.event_ts).total_seconds() * 1000)

    def to_raw_event(self) -> RawEvent:
        return RawEvent(
            source_key=self.source_key,
            stream_key=self.stream_key,
            symbol=self.symbol,
            event_ts=self.event_ts,
            observed_ts=self.observed_ts,
            payload={
                **self.payload,
                "quality_flags": [flag.value for flag in self.quality_flags],
                "lag_ms": self.lag_ms,
            },
        )


@dataclass(frozen=True)
class SourceLag:
    source_key: str
    lag_ms: int
    stale_after_ms: int

    def quality_flags(self) -> tuple[SourceQualityFlag, ...]:
        flags: list[SourceQualityFlag] = []
        if self.lag_ms < 0:
            flags.append(SourceQualityFlag.CLOCK_SKEW)
        if self.lag_ms > self.stale_after_ms:
            flags.append(SourceQualityFlag.STALE_SOURCE)
        return tuple(flags)


@dataclass(frozen=True)
class SourceHealth:
    source_key: str
    connected: bool
    last_event_ts: datetime | None
    last_observed_ts: datetime | None
    last_error: str | None = None
    quality_flags: tuple[SourceQualityFlag, ...] = field(default_factory=tuple)

    @property
    def is_healthy(self) -> bool:
        return self.connected and self.last_error is None and not self.quality_flags
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_collector_events.py -v
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/collector_events.py tests/ingestion/test_collector_events.py
git commit -m "Add live collector event model"
```

---

## Task 2: BTC/ETH 5-Minute Contract Discovery

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/ingestion/contract_discovery.py`
- Test: `/Users/goon/polymarket/tests/ingestion/test_contract_discovery.py`

- [ ] **Step 1: Write failing tests**

```python
import json
from datetime import datetime, timezone

import httpx
import pytest

from polymarket_engine.ingestion.contract_discovery import (
    MarketToken,
    crypto_5m_slugs,
    extract_market_tokens,
    fetch_crypto_5m_markets,
    floor_to_5m_epoch,
)


def test_floor_to_5m_epoch() -> None:
    now = datetime(2026, 5, 31, 21, 4, 59, tzinfo=timezone.utc)

    assert floor_to_5m_epoch(now) == 1780261200


def test_crypto_5m_slugs_use_polymarket_epoch_pattern() -> None:
    now = datetime(2026, 5, 31, 21, 4, 0, tzinfo=timezone.utc)

    assert crypto_5m_slugs(now, assets=("BTC", "ETH"), windows_ahead=2) == (
        "btc-updown-5m-1780261200",
        "eth-updown-5m-1780261200",
        "btc-updown-5m-1780261500",
        "eth-updown-5m-1780261500",
    )


def test_extract_market_tokens_from_gamma_payload() -> None:
    market = {
        "slug": "btc-updown-5m-1780261200",
        "question": "Bitcoin Up or Down - May 31, 5:00PM-5:05PM ET",
        "outcomes": json.dumps(["Up", "Down"]),
        "clobTokenIds": json.dumps(["111", "222"]),
    }

    assert extract_market_tokens(market) == (
        MarketToken(slug="btc-updown-5m-1780261200", outcome="Up", token_id="111"),
        MarketToken(slug="btc-updown-5m-1780261200", outcome="Down", token_id="222"),
    )


@pytest.mark.anyio
async def test_fetch_crypto_5m_markets_fetches_by_slug() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            json=[
                {
                    "slug": request.url.params["slug"],
                    "question": "Bitcoin Up or Down - May 31, 5:00PM-5:05PM ET",
                    "outcomes": json.dumps(["Up", "Down"]),
                    "clobTokenIds": json.dumps(["111", "222"]),
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    markets = await fetch_crypto_5m_markets(
        client=client,
        base_url="https://gamma-api.polymarket.com",
        now=datetime(2026, 5, 31, 21, 4, 0, tzinfo=timezone.utc),
        assets=("BTC",),
        windows_ahead=1,
    )
    await client.aclose()

    assert len(markets) == 1
    assert markets[0]["slug"] == "btc-updown-5m-1780261200"
    assert requested_urls == [
        "https://gamma-api.polymarket.com/markets?slug=btc-updown-5m-1780261200"
    ]
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_contract_discovery.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.ingestion.contract_discovery'
```

- [ ] **Step 3: Implement contract discovery**

Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/contract_discovery.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx


@dataclass(frozen=True)
class MarketToken:
    slug: str
    outcome: str
    token_id: str


def floor_to_5m_epoch(now: datetime) -> int:
    floored = now.replace(second=0, microsecond=0)
    floored = floored.replace(minute=(floored.minute // 5) * 5)
    return int(floored.timestamp())


def crypto_5m_slugs(
    now: datetime,
    assets: tuple[str, ...] = ("BTC", "ETH"),
    windows_ahead: int = 3,
) -> tuple[str, ...]:
    start = datetime.fromtimestamp(floor_to_5m_epoch(now), tz=now.tzinfo)
    slugs: list[str] = []
    for window_index in range(windows_ahead):
        epoch = int((start + timedelta(minutes=5 * window_index)).timestamp())
        for asset in assets:
            slugs.append(f"{asset.lower()}-updown-5m-{epoch}")
    return tuple(slugs)


def _decode_json_list(value: object) -> list[str]:
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError("expected JSON list")
    return [str(item) for item in parsed]


def extract_market_tokens(market: dict[str, Any]) -> tuple[MarketToken, ...]:
    slug = str(market["slug"])
    outcomes = _decode_json_list(market["outcomes"])
    token_ids = _decode_json_list(market["clobTokenIds"])
    if len(outcomes) != len(token_ids):
        raise ValueError("outcomes and clobTokenIds length mismatch")
    return tuple(
        MarketToken(slug=slug, outcome=outcome, token_id=token_id)
        for outcome, token_id in zip(outcomes, token_ids, strict=True)
    )


async def fetch_crypto_5m_markets(
    client: httpx.AsyncClient,
    base_url: str,
    now: datetime,
    assets: tuple[str, ...] = ("BTC", "ETH"),
    windows_ahead: int = 3,
) -> tuple[dict[str, Any], ...]:
    markets: list[dict[str, Any]] = []
    for slug in crypto_5m_slugs(now, assets=assets, windows_ahead=windows_ahead):
        response = await client.get(f"{base_url.rstrip('/')}/markets", params={"slug": slug})
        response.raise_for_status()
        payload = response.json()
        items = payload if isinstance(payload, list) else payload.get("markets", [])
        markets.extend(dict(item) for item in items)
    return tuple(markets)
```

- [ ] **Step 4: Run tests and verify GREEN**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_contract_discovery.py -v
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/contract_discovery.py tests/ingestion/test_contract_discovery.py
git commit -m "Add Polymarket crypto 5m contract discovery"
```

---

## Task 3: Coinbase BTC/ETH Ticker Parser

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/ingestion/coinbase_ws.py`
- Test: `/Users/goon/polymarket/tests/ingestion/test_coinbase_ws.py`

- [ ] **Step 1: Write failing tests**

```python
from datetime import datetime, timezone

from polymarket_engine.ingestion.coinbase_ws import (
    build_coinbase_ticker_subscription,
    coinbase_ticker_events,
)


def test_build_coinbase_ticker_subscription() -> None:
    assert build_coinbase_ticker_subscription(("BTC-USD", "ETH-USD")) == {
        "type": "subscribe",
        "product_ids": ["BTC-USD", "ETH-USD"],
        "channel": "ticker",
    }


def test_coinbase_ticker_events_parse_real_channel_shape() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc)
    message = {
        "channel": "ticker",
        "timestamp": "2026-05-31T21:00:00.500000Z",
        "events": [
            {
                "type": "update",
                "tickers": [
                    {"product_id": "BTC-USD", "price": "104000.10"},
                    {"product_id": "ETH-USD", "price": "3900.20"},
                ],
            }
        ],
    }

    events = coinbase_ticker_events(message, observed)

    assert [event.symbol for event in events] == ["BTC-USD", "ETH-USD"]
    assert events[0].source_key == "coinbase_advanced_ws"
    assert events[0].stream_key == "ticker"
    assert events[0].payload["price"] == "104000.10"
    assert events[0].lag_ms == 500
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_coinbase_ws.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.ingestion.coinbase_ws'
```

- [ ] **Step 3: Implement Coinbase parser**

Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/coinbase_ws.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any

from polymarket_engine.ingestion.collector_events import CollectorEvent


def build_coinbase_ticker_subscription(product_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "type": "subscribe",
        "product_ids": list(product_ids),
        "channel": "ticker",
    }


def coinbase_ticker_events(
    message: dict[str, Any],
    observed_ts: datetime,
) -> tuple[CollectorEvent, ...]:
    if message.get("channel") != "ticker":
        return ()
    event_ts = datetime.fromisoformat(str(message["timestamp"]).replace("Z", "+00:00"))
    events: list[CollectorEvent] = []
    for event in message.get("events", []):
        tickers = event.get("tickers", []) if isinstance(event, dict) else []
        for ticker in tickers:
            events.append(
                CollectorEvent(
                    source_key="coinbase_advanced_ws",
                    stream_key="ticker",
                    symbol=str(ticker["product_id"]),
                    event_ts=event_ts,
                    observed_ts=observed_ts,
                    payload=dict(ticker),
                )
            )
    return tuple(events)
```

- [ ] **Step 4: Run tests and verify GREEN**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_coinbase_ws.py -v
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/coinbase_ws.py tests/ingestion/test_coinbase_ws.py
git commit -m "Add Coinbase ticker collector parser"
```

---

## Task 4: Polymarket RTDS Reference Price Parser

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/ingestion/polymarket_rtds.py`
- Test: `/Users/goon/polymarket/tests/ingestion/test_polymarket_rtds.py`

- [ ] **Step 1: Write failing tests**

```python
from datetime import datetime, timezone

from polymarket_engine.ingestion.polymarket_rtds import (
    build_rtds_subscriptions,
    rtds_price_events,
)


def test_build_rtds_subscriptions_includes_chainlink_and_crypto_topics() -> None:
    subscriptions = build_rtds_subscriptions(("BTC", "ETH"))

    assert subscriptions["action"] == "subscribe"
    assert {
        item["topic"] for item in subscriptions["subscriptions"]
    } == {"crypto_prices_chainlink", "crypto_prices"}


def test_rtds_price_events_parse_chainlink_payload() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc)
    message = {
        "topic": "crypto_prices_chainlink",
        "type": "update",
        "timestamp": 1780261201000,
        "payload": {
            "symbol": "btc/usd",
            "value": "104000.12",
            "timestamp": 1780261200500,
        },
    }

    events = rtds_price_events(message, observed)

    assert len(events) == 1
    assert events[0].source_key == "polymarket_rtds_chainlink"
    assert events[0].symbol == "BTC/USD"
    assert events[0].payload["value"] == "104000.12"
    assert events[0].lag_ms == 500
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_polymarket_rtds.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.ingestion.polymarket_rtds'
```

- [ ] **Step 3: Implement RTDS parser**

Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/polymarket_rtds.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from polymarket_engine.ingestion.collector_events import CollectorEvent


def build_rtds_subscriptions(assets: tuple[str, ...]) -> dict[str, object]:
    chainlink_filters = [
        {"topic": "crypto_prices_chainlink", "type": "*", "filters": f'{{"symbol":"{asset.lower()}/usd"}}'}
        for asset in assets
    ]
    crypto_symbols = ",".join(f"{asset.lower()}usdt" for asset in assets)
    return {
        "action": "subscribe",
        "subscriptions": [
            *chainlink_filters,
            {"topic": "crypto_prices", "type": "update", "filters": crypto_symbols},
        ],
    }


def _source_key(topic: str) -> str:
    if topic == "crypto_prices_chainlink":
        return "polymarket_rtds_chainlink"
    return "polymarket_rtds_crypto"


def _symbol(raw_symbol: str) -> str:
    normalized = raw_symbol.upper()
    if "/" in normalized:
        return normalized
    if normalized.endswith("USDT"):
        return f"{normalized[:-4]}/USDT"
    return normalized


def rtds_price_events(
    message: dict[str, Any],
    observed_ts: datetime,
) -> tuple[CollectorEvent, ...]:
    topic = str(message.get("topic", ""))
    if topic not in {"crypto_prices_chainlink", "crypto_prices"}:
        return ()
    payload = message.get("payload", {})
    if not isinstance(payload, dict) or "symbol" not in payload:
        return ()
    source_timestamp = int(str(payload.get("timestamp", message.get("timestamp"))))
    event_ts = datetime.fromtimestamp(source_timestamp / 1000, tz=timezone.utc)
    return (
        CollectorEvent(
            source_key=_source_key(topic),
            stream_key="price_update",
            symbol=_symbol(str(payload["symbol"])),
            event_ts=event_ts,
            observed_ts=observed_ts,
            payload=dict(payload),
        ),
    )
```

- [ ] **Step 4: Run tests and verify GREEN**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_polymarket_rtds.py -v
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/polymarket_rtds.py tests/ingestion/test_polymarket_rtds.py
git commit -m "Add Polymarket RTDS price parser"
```

---

## Task 5: Polymarket CLOB Snapshot And Market WebSocket Helpers

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/ingestion/polymarket_clob.py`
- Test: `/Users/goon/polymarket/tests/ingestion/test_polymarket_clob.py`

- [ ] **Step 1: Write failing tests**

```python
from datetime import datetime, timezone

from polymarket_engine.ingestion.contract_discovery import MarketToken
from polymarket_engine.ingestion.polymarket_clob import (
    build_market_ws_subscription,
    clob_book_event,
    clob_book_top,
)


def test_clob_book_top_uses_highest_bid_and_lowest_ask() -> None:
    book = {
        "bids": [{"price": "0.01", "size": "10"}, {"price": "0.66", "size": "7"}],
        "asks": [{"price": "0.68", "size": "4"}, {"price": "0.99", "size": "1"}],
    }

    top = clob_book_top(book)

    assert top.best_bid == 0.66
    assert top.best_ask == 0.68
    assert top.bid_size_top == 7.0
    assert top.ask_size_top == 4.0


def test_clob_book_event_preserves_contract_slug_and_outcome() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 2, tzinfo=timezone.utc)
    book = {
        "asset_id": "111",
        "timestamp": "1780261201000",
        "bids": [{"price": "0.66", "size": "7"}],
        "asks": [{"price": "0.68", "size": "4"}],
    }
    token = MarketToken(slug="btc-updown-5m-1780261200", outcome="Up", token_id="111")

    event = clob_book_event(book, token, observed)

    assert event.source_key == "polymarket_clob"
    assert event.stream_key == "orderbook_snapshot"
    assert event.symbol == "btc-updown-5m-1780261200:Up"
    assert event.payload["best_bid"] == 0.66
    assert event.payload["best_ask"] == 0.68


def test_build_market_ws_subscription_uses_asset_ids() -> None:
    assert build_market_ws_subscription(("111", "222")) == {"assets_ids": ["111", "222"], "type": "market"}
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_polymarket_clob.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.ingestion.polymarket_clob'
```

- [ ] **Step 3: Implement CLOB helpers**

Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/polymarket_clob.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.ingestion.contract_discovery import MarketToken


@dataclass(frozen=True)
class BookTop:
    best_bid: float | None
    best_ask: float | None
    bid_size_top: float | None
    ask_size_top: float | None


def _best_bid(levels: list[dict[str, object]]) -> dict[str, object] | None:
    if not levels:
        return None
    return max(levels, key=lambda level: float(str(level["price"])))


def _best_ask(levels: list[dict[str, object]]) -> dict[str, object] | None:
    if not levels:
        return None
    return min(levels, key=lambda level: float(str(level["price"])))


def clob_book_top(book: dict[str, Any]) -> BookTop:
    bid = _best_bid(list(book.get("bids", [])))
    ask = _best_ask(list(book.get("asks", [])))
    return BookTop(
        best_bid=None if bid is None else float(str(bid["price"])),
        best_ask=None if ask is None else float(str(ask["price"])),
        bid_size_top=None if bid is None else float(str(bid["size"])),
        ask_size_top=None if ask is None else float(str(ask["size"])),
    )


def clob_book_event(
    book: dict[str, Any],
    token: MarketToken,
    observed_ts: datetime,
) -> CollectorEvent:
    timestamp_ms = int(str(book["timestamp"]))
    event_ts = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    top = clob_book_top(book)
    return CollectorEvent(
        source_key="polymarket_clob",
        stream_key="orderbook_snapshot",
        symbol=f"{token.slug}:{token.outcome}",
        event_ts=event_ts,
        observed_ts=observed_ts,
        payload={
            **book,
            "contract_slug": token.slug,
            "outcome": token.outcome,
            "token_id": token.token_id,
            "best_bid": top.best_bid,
            "best_ask": top.best_ask,
            "bid_size_top": top.bid_size_top,
            "ask_size_top": top.ask_size_top,
        },
    )


def build_market_ws_subscription(asset_ids: tuple[str, ...]) -> dict[str, object]:
    return {"assets_ids": list(asset_ids), "type": "market"}
```

- [ ] **Step 4: Run tests and verify GREEN**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_polymarket_clob.py -v
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/polymarket_clob.py tests/ingestion/test_polymarket_clob.py
git commit -m "Add Polymarket CLOB collection helpers"
```

---

## Task 6: Buffered Raw Writer And DuckDB Ingest Ledger

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/storage/buffered_writer.py`
- Create: `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`
- Modify: `/Users/goon/polymarket/src/polymarket_engine/storage/schema.sql`
- Test: `/Users/goon/polymarket/tests/storage/test_buffered_writer.py`
- Test: `/Users/goon/polymarket/tests/storage/test_duckdb_store.py`

- [ ] **Step 1: Write failing tests**

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.storage.buffered_writer import BufferedRawEventWriter
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


def test_buffered_writer_flushes_by_source_and_stream(tmp_path: Path) -> None:
    writer = BufferedRawEventWriter(raw_root=tmp_path, max_batch_size=2)
    event_ts = datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc)

    result_none = writer.add(
        CollectorEvent("coinbase_advanced_ws", "ticker", "BTC-USD", event_ts, event_ts, {"price": "1"})
    )
    result = writer.add(
        CollectorEvent("coinbase_advanced_ws", "ticker", "ETH-USD", event_ts, event_ts, {"price": "2"})
    )

    assert result_none is None
    assert result is not None
    assert result.row_count == 2
    assert result.path.exists()


def test_buffered_writer_flushes_by_time_when_stream_is_quiet(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc))
    writer = BufferedRawEventWriter(
        raw_root=tmp_path,
        max_batch_size=100,
        flush_after_seconds=5.0,
        clock=clock,
    )
    event_ts = datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc)

    assert writer.add(
        CollectorEvent("coinbase_advanced_ws", "ticker", "BTC-USD", event_ts, event_ts, {"price": "1"})
    ) is None
    clock.advance(6.0)
    result = writer.maybe_flush()

    assert result is not None
    assert result.row_count == 1
    assert writer.buffered_count == 0


def test_duckdb_ingest_store_registers_written_file(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.duckdb"
    raw_path = tmp_path / "file.parquet"
    raw_path.write_bytes(b"abc")
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    store.register_ingest_file(
        file_id="file-1",
        source_key="coinbase_advanced_ws",
        stream_key="ticker",
        partition_date="2026-05-31",
        partition_hour=21,
        path=str(raw_path),
        sha256="abc123",
        row_count=2,
        first_event_ts=datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc),
        last_event_ts=datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc),
    )

    with duckdb.connect(str(db_path)) as conn:
        rows = conn.sql("select source_key, stream_key, row_count from ops.ingest_files").fetchall()

    assert rows == [("coinbase_advanced_ws", "ticker", 2)]
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_buffered_writer.py tests/storage/test_duckdb_store.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.storage.buffered_writer'
```

- [ ] **Step 3: Implement buffered writer and DuckDB store**

Create `/Users/goon/polymarket/src/polymarket_engine/storage/buffered_writer.py`:

```python
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.storage.raw_writer import RawWriteResult, write_raw_events


class BufferedRawEventWriter:
    def __init__(
        self,
        raw_root: Path,
        max_batch_size: int = 100,
        flush_after_seconds: float = 5.0,
        require_archive_sentinel: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.raw_root = raw_root
        self.max_batch_size = max_batch_size
        self.flush_after_seconds = flush_after_seconds
        self.require_archive_sentinel = require_archive_sentinel
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._buffers: dict[tuple[str, str], list[CollectorEvent]] = defaultdict(list)
        self._first_buffered_at: dict[tuple[str, str], datetime] = {}

    @property
    def buffered_count(self) -> int:
        return sum(len(events) for events in self._buffers.values())

    def add(self, event: CollectorEvent) -> RawWriteResult | None:
        key = (event.source_key, event.stream_key)
        if key not in self._first_buffered_at:
            self._first_buffered_at[key] = self._clock()
        self._buffers[key].append(event)
        if len(self._buffers[key]) >= self.max_batch_size:
            return self.flush_key(key)
        return None

    def maybe_flush(self) -> RawWriteResult | None:
        now = self._clock()
        for key, first_seen in list(self._first_buffered_at.items()):
            if (now - first_seen).total_seconds() >= self.flush_after_seconds:
                return self.flush_key(key)
        return None

    def flush_key(self, key: tuple[str, str]) -> RawWriteResult | None:
        events = self._buffers.pop(key, [])
        self._first_buffered_at.pop(key, None)
        if not events:
            return None
        return write_raw_events(
            self.raw_root,
            [event.to_raw_event() for event in events],
            require_archive_sentinel=self.require_archive_sentinel,
        )

    def flush_all(self) -> tuple[RawWriteResult, ...]:
        results: list[RawWriteResult] = []
        for key in list(self._buffers):
            result = self.flush_key(key)
            if result is not None:
                results.append(result)
        return tuple(results)
```

Create `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb


class DuckDbIngestStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def apply_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema.sql")
        with duckdb.connect(str(self.db_path)) as conn:
            conn.sql(schema_path.read_text())

    def register_ingest_file(
        self,
        file_id: str,
        source_key: str,
        stream_key: str,
        partition_date: str,
        partition_hour: int,
        path: str,
        sha256: str,
        row_count: int,
        first_event_ts: datetime,
        last_event_ts: datetime,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into ops.ingest_files
                (file_id, source_key, stream_key, partition_date, partition_hour, path, sha256,
                 row_count, first_event_ts, last_event_ts, written_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    file_id,
                    source_key,
                    stream_key,
                    partition_date,
                    partition_hour,
                    path,
                    sha256,
                    row_count,
                    first_event_ts,
                    last_event_ts,
                    datetime.now(timezone.utc),
                ],
            )
```

- [ ] **Step 4: Run tests and verify GREEN**

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_buffered_writer.py tests/storage/test_duckdb_store.py -v
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/storage/buffered_writer.py src/polymarket_engine/storage/duckdb_store.py tests/storage/test_buffered_writer.py tests/storage/test_duckdb_store.py
git commit -m "Add buffered raw writer and ingest ledger"
```

---

## Task 7: Live Collector Orchestrator

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`
- Test: `/Users/goon/polymarket/tests/ingestion/test_live_collector.py`

- [ ] **Step 1: Write failing tests**

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.ingestion.live_collector import LiveCollectorConfig, run_fake_collection


@pytest.mark.anyio
async def test_run_fake_collection_writes_events_and_registers_files(tmp_path: Path) -> None:
    event_ts = datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc)
    events = (
        CollectorEvent("coinbase_advanced_ws", "ticker", "BTC-USD", event_ts, event_ts, {"price": "1"}),
        CollectorEvent("coinbase_advanced_ws", "ticker", "ETH-USD", event_ts, event_ts, {"price": "2"}),
    )
    config = LiveCollectorConfig(
        assets=("BTC", "ETH"),
        duration_seconds=1,
        raw_root=tmp_path / "raw",
        duckdb_path=tmp_path / "collector.duckdb",
        max_batch_size=2,
    )

    result = await run_fake_collection(config, events)

    assert result.events_written == 2
    assert result.files_written == 1
    assert result.source_errors == {}
```

- [ ] **Step 2: Run test and verify RED**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_live_collector.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.ingestion.live_collector'
```

- [ ] **Step 3: Implement orchestrator shell**

Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.storage.buffered_writer import BufferedRawEventWriter
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore
from polymarket_engine.storage.raw_writer import RawWriteResult


@dataclass(frozen=True)
class LiveCollectorConfig:
    assets: tuple[str, ...]
    duration_seconds: int
    raw_root: Path
    duckdb_path: Path
    max_batch_size: int = 100
    contract_windows_ahead: int = 3
    clob_snapshot_interval_seconds: int = 5
    rtds_stale_after_ms: int = 5000
    coinbase_stale_after_ms: int = 2000


@dataclass(frozen=True)
class LiveCollectorResult:
    events_written: int
    files_written: int
    source_errors: dict[str, str] = field(default_factory=dict)


def _register_result(store: DuckDbIngestStore, result: RawWriteResult) -> None:
    partition_date = result.first_event_ts.date().isoformat()
    partition_hour = result.first_event_ts.hour
    parts = result.path.parts
    source_key = parts[-5]
    stream_key = parts[-4]
    store.register_ingest_file(
        file_id=result.file_id,
        source_key=source_key,
        stream_key=stream_key,
        partition_date=partition_date,
        partition_hour=partition_hour,
        path=str(result.path),
        sha256=result.sha256,
        row_count=result.row_count,
        first_event_ts=result.first_event_ts,
        last_event_ts=result.last_event_ts,
    )


async def run_fake_collection(
    config: LiveCollectorConfig,
    events: tuple[CollectorEvent, ...],
) -> LiveCollectorResult:
    store = DuckDbIngestStore(config.duckdb_path)
    store.apply_schema()
    writer = BufferedRawEventWriter(config.raw_root, max_batch_size=config.max_batch_size)
    events_written = 0
    files_written = 0

    for event in events:
        events_written += 1
        write_result = writer.add(event)
        if write_result is not None:
            _register_result(store, write_result)
            files_written += 1

    for write_result in writer.flush_all():
        _register_result(store, write_result)
        files_written += 1

    return LiveCollectorResult(events_written=events_written, files_written=files_written)
```

- [ ] **Step 4: Run test and verify GREEN**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_live_collector.py -v
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/live_collector.py tests/ingestion/test_live_collector.py
git commit -m "Add live collector orchestration shell"
```

---

## Task 8: CLI Command For Read-Only Collection

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/cli.py`
- Modify: `/Users/goon/polymarket/pyproject.toml`
- Test: `/Users/goon/polymarket/tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

```python
from pathlib import Path

from polymarket_engine.cli import parse_args


def test_parse_collect_args() -> None:
    args = parse_args(
        [
            "collect",
            "--assets",
            "BTC,ETH",
            "--duration",
            "60",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
        ]
    )

    assert args.command == "collect"
    assert args.assets == ("BTC", "ETH")
    assert args.duration == 60
    assert args.raw_root == Path("data/raw")
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
```

- [ ] **Step 2: Run test and verify RED**

```bash
cd /Users/goon/polymarket
uv run pytest tests/test_cli.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.cli'
```

- [ ] **Step 3: Implement CLI parser and entrypoint**

Create `/Users/goon/polymarket/src/polymarket_engine/cli.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path


def _asset_tuple(value: str) -> tuple[str, ...]:
    return tuple(asset.strip().upper() for asset in value.split(",") if asset.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="polymarket-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--assets", type=_asset_tuple, default=("BTC", "ETH"))
    collect.add_argument("--duration", type=int, required=True)
    collect.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    collect.add_argument("--duckdb-path", type=Path, default=Path("data/db/polymarket.duckdb"))
    collect.add_argument("--max-batch-size", type=int, default=100)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "collect":
        print(
            "collector command parsed",
            {
                "assets": args.assets,
                "duration": args.duration,
                "raw_root": str(args.raw_root),
                "duckdb_path": str(args.duckdb_path),
            },
        )
        return 0
    return 2
```

Modify `/Users/goon/polymarket/pyproject.toml`:

```toml
[project.scripts]
polymarket-engine = "polymarket_engine.cli:main"
```

- [ ] **Step 4: Run test and verify GREEN**

```bash
cd /Users/goon/polymarket
uv run pytest tests/test_cli.py -v
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Verify CLI script resolves**

```bash
cd /Users/goon/polymarket
uv run polymarket-engine collect --assets BTC,ETH --duration 1
```

Expected:

```text
collector command parsed
```

- [ ] **Step 6: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/cli.py pyproject.toml tests/test_cli.py
git commit -m "Add read-only collector CLI"
```

---

## Task 9: Wire Real Network Collection Behind The CLI

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`
- Modify: `/Users/goon/polymarket/src/polymarket_engine/cli.py`
- Test: `/Users/goon/polymarket/tests/ingestion/test_live_collector.py`
- Test: `/Users/goon/polymarket/tests/test_cli.py`

- [ ] **Step 1: Add tests for dependency-injected runner**

Append to `/Users/goon/polymarket/tests/test_cli.py`:

```python
import pytest

from polymarket_engine import cli
from polymarket_engine.ingestion.live_collector import LiveCollectorResult


@pytest.mark.anyio
async def test_run_collect_command_uses_injected_runner(tmp_path, monkeypatch) -> None:
    seen = {}

    async def fake_runner(config):
        seen["assets"] = config.assets
        seen["duration"] = config.duration_seconds
        return LiveCollectorResult(events_written=3, files_written=1)

    result = await cli.run_collect_command(
        [
            "collect",
            "--assets",
            "BTC,ETH",
            "--duration",
            "5",
            "--raw-root",
            str(tmp_path / "raw"),
            "--duckdb-path",
            str(tmp_path / "db.duckdb"),
        ],
        runner=fake_runner,
    )

    assert result == 0
    assert seen == {"assets": ("BTC", "ETH"), "duration": 5}
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd /Users/goon/polymarket
uv run pytest tests/test_cli.py -v
```

Expected:

```text
AttributeError: module 'polymarket_engine.cli' has no attribute 'run_collect_command'
```

- [ ] **Step 3: Implement dependency-injected runner**

Replace `/Users/goon/polymarket/src/polymarket_engine/cli.py` with:

```python
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from polymarket_engine.ingestion.live_collector import LiveCollectorConfig, LiveCollectorResult


CollectorRunner = Callable[[LiveCollectorConfig], Awaitable[LiveCollectorResult]]


def _asset_tuple(value: str) -> tuple[str, ...]:
    return tuple(asset.strip().upper() for asset in value.split(",") if asset.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="polymarket-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--assets", type=_asset_tuple, default=("BTC", "ETH"))
    collect.add_argument("--duration", type=int, required=True)
    collect.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    collect.add_argument("--duckdb-path", type=Path, default=Path("data/db/polymarket.duckdb"))
    collect.add_argument("--max-batch-size", type=int, default=100)

    return parser.parse_args(argv)


async def run_collect_command(
    argv: list[str] | None = None,
    runner: CollectorRunner | None = None,
) -> int:
    from polymarket_engine.ingestion.live_collector import run_live_collection

    args = parse_args(argv)
    if args.command != "collect":
        return 2
    selected_runner = run_live_collection if runner is None else runner
    config = LiveCollectorConfig(
        assets=args.assets,
        duration_seconds=args.duration,
        raw_root=args.raw_root,
        duckdb_path=args.duckdb_path,
        max_batch_size=args.max_batch_size,
    )
    result = await selected_runner(config)
    print(
        {
            "events_written": result.events_written,
            "files_written": result.files_written,
            "source_errors": result.source_errors,
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_collect_command(argv))
```

Add this function to `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`:

```python
async def run_live_collection(config: LiveCollectorConfig) -> LiveCollectorResult:
    return await run_fake_collection(config, ())
```

This keeps the CLI stable while the next task replaces the empty runner with real source loops.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
cd /Users/goon/polymarket
uv run pytest tests/test_cli.py tests/ingestion/test_live_collector.py -v
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/cli.py src/polymarket_engine/ingestion/live_collector.py tests/test_cli.py tests/ingestion/test_live_collector.py
git commit -m "Wire collector CLI to live runner seam"
```

---

## Task 9A: Reconnect Backoff And Outage Semantics

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/ingestion/reconnect.py`
- Test: `/Users/goon/polymarket/tests/ingestion/test_reconnect.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/goon/polymarket/tests/ingestion/test_reconnect.py`:

```python
from polymarket_engine.ingestion.reconnect import compute_reconnect_delay


def test_first_reconnect_delay_starts_at_base_without_jitter() -> None:
    assert compute_reconnect_delay(0, base=1.0, cap=30.0, jitter_pct=0.25, random_value=0.5) == 1.0


def test_reconnect_delay_grows_exponentially_without_jitter() -> None:
    assert compute_reconnect_delay(3, base=1.0, cap=30.0, jitter_pct=0.25, random_value=0.5) == 8.0


def test_reconnect_delay_is_capped_before_jitter() -> None:
    assert compute_reconnect_delay(99, base=1.0, cap=30.0, jitter_pct=0.0, random_value=0.5) == 30.0


def test_reconnect_delay_applies_symmetric_jitter() -> None:
    low = compute_reconnect_delay(2, base=1.0, cap=30.0, jitter_pct=0.25, random_value=0.0)
    high = compute_reconnect_delay(2, base=1.0, cap=30.0, jitter_pct=0.25, random_value=1.0)

    assert low == 3.0
    assert high == 5.0
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_reconnect.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.ingestion.reconnect'
```

- [ ] **Step 3: Implement reconnect delay helper**

Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/reconnect.py`:

```python
from __future__ import annotations

import random


def compute_reconnect_delay(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    jitter_pct: float = 0.25,
    random_value: float | None = None,
) -> float:
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    if base <= 0:
        raise ValueError("base must be > 0")
    if cap <= 0:
        raise ValueError("cap must be > 0")
    if jitter_pct < 0:
        raise ValueError("jitter_pct must be >= 0")

    unclipped = base * (2**attempt)
    delay = min(cap, unclipped)
    u = random.random() if random_value is None else random_value
    jitter = delay * jitter_pct * ((u * 2.0) - 1.0)
    return max(0.0, delay + jitter)
```

- [ ] **Step 4: Run tests and verify GREEN**

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_reconnect.py -v
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/reconnect.py tests/ingestion/test_reconnect.py
git commit -m "Add collector reconnect backoff"
```

---

## Task 10: Real Live Smoke Command And Documentation

**Files:**
- Create: `/Users/goon/polymarket/docs/PART_TWO_LIVE_COLLECTORS.md`
- Modify: `/Users/goon/polymarket/README.md`
- Modify: `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`

- [ ] **Step 1: Implement a conservative real network runner**

Modify `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py` so `run_live_collection`:

```python
async def run_live_collection(config: LiveCollectorConfig) -> LiveCollectorResult:
    import asyncio
    import json
    from datetime import datetime, timezone

    import httpx
    import websockets

    from polymarket_engine.ingestion.coinbase_ws import (
        build_coinbase_ticker_subscription,
        coinbase_ticker_events,
    )
    from polymarket_engine.ingestion.contract_discovery import (
        extract_market_tokens,
        fetch_crypto_5m_markets,
    )
    from polymarket_engine.ingestion.polymarket_clob import clob_book_event
    from polymarket_engine.ingestion.polymarket_rtds import (
        build_rtds_subscriptions,
        rtds_price_events,
    )
    from polymarket_engine.ingestion.reconnect import compute_reconnect_delay
    from polymarket_engine.storage.recovery import cleanup_orphaned_tmp, ensure_archive_sentinel

    store = DuckDbIngestStore(config.duckdb_path)
    store.apply_schema()
    ensure_archive_sentinel(config.raw_root)
    cleanup_orphaned_tmp(config.raw_root)
    writer = BufferedRawEventWriter(
        config.raw_root,
        max_batch_size=config.max_batch_size,
        require_archive_sentinel=True,
    )
    source_errors: dict[str, str] = {}
    events_written = 0
    files_written = 0

    def record_event(event: CollectorEvent) -> None:
        nonlocal events_written, files_written
        result = writer.add(event)
        events_written += 1
        if result is not None:
            _register_result(store, result)
            files_written += 1

    def flush_due() -> None:
        nonlocal files_written
        result = writer.maybe_flush()
        if result is not None:
            _register_result(store, result)
            files_written += 1

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            markets = await fetch_crypto_5m_markets(
                client=client,
                base_url="https://gamma-api.polymarket.com",
                now=datetime.now(timezone.utc),
                assets=config.assets,
                windows_ahead=config.contract_windows_ahead,
            )
            tokens = tuple(token for market in markets for token in extract_market_tokens(market))
            for market in markets:
                observed = datetime.now(timezone.utc)
                event = CollectorEvent(
                    source_key="polymarket_markets",
                    stream_key="crypto_5m_markets_snapshot",
                    symbol=str(market["slug"]),
                    event_ts=observed,
                    observed_ts=observed,
                    payload=dict(market),
                )
                record_event(event)

            for token in tokens:
                observed = datetime.now(timezone.utc)
                response = await client.get("https://clob.polymarket.com/book", params={"token_id": token.token_id})
                response.raise_for_status()
                event = clob_book_event(response.json(), token, observed)
                record_event(event)
        except Exception as exc:
            source_errors["polymarket"] = f"{type(exc).__name__}: {exc}"

    product_ids = tuple(f"{asset}-USD" for asset in config.assets)
    coinbase_deadline = datetime.now(timezone.utc).timestamp() + config.duration_seconds
    coinbase_attempt = 0
    while datetime.now(timezone.utc).timestamp() < coinbase_deadline:
        try:
            async with websockets.connect("wss://advanced-trade-ws.coinbase.com", open_timeout=10) as ws:
                await ws.send(json.dumps(build_coinbase_ticker_subscription(product_ids)))
                coinbase_attempt = 0
                while datetime.now(timezone.utc).timestamp() < coinbase_deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        flush_due()
                        continue
                    observed = datetime.now(timezone.utc)
                    for event in coinbase_ticker_events(json.loads(raw), observed):
                        record_event(event)
                break
        except Exception as exc:
            source_errors["coinbase_advanced_ws"] = f"{type(exc).__name__}: {exc}"
            remaining = coinbase_deadline - datetime.now(timezone.utc).timestamp()
            if remaining <= 0:
                break
            delay = min(compute_reconnect_delay(coinbase_attempt), remaining)
            coinbase_attempt += 1
            await asyncio.sleep(delay)

    rtds_deadline = datetime.now(timezone.utc).timestamp() + min(config.duration_seconds, 10)
    rtds_attempt = 0
    while datetime.now(timezone.utc).timestamp() < rtds_deadline:
        try:
            async with websockets.connect("wss://ws-live-data.polymarket.com", open_timeout=10) as ws:
                await ws.send(json.dumps(build_rtds_subscriptions(config.assets)))
                rtds_attempt = 0
                while datetime.now(timezone.utc).timestamp() < rtds_deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        flush_due()
                        continue
                    observed = datetime.now(timezone.utc)
                    for event in rtds_price_events(json.loads(raw), observed):
                        record_event(event)
                break
        except Exception as exc:
            source_errors["polymarket_rtds"] = f"{type(exc).__name__}: {exc}"
            remaining = rtds_deadline - datetime.now(timezone.utc).timestamp()
            if remaining <= 0:
                break
            delay = min(compute_reconnect_delay(rtds_attempt), remaining)
            rtds_attempt += 1
            await asyncio.sleep(delay)

    for result in writer.flush_all():
        _register_result(store, result)
        files_written += 1

    return LiveCollectorResult(
        events_written=events_written,
        files_written=files_written,
        source_errors=source_errors,
    )
```

This runner records Polymarket contract/order-book snapshots and Coinbase live ticker updates. RTDS is attempted in the same command, but an RTDS timeout records a source error instead of failing the whole run because the smoke test showed the RTDS subscription may be quiet or require more precise filters.

- [ ] **Step 2: Create Part Two docs**

Create `/Users/goon/polymarket/docs/PART_TWO_LIVE_COLLECTORS.md`:

```markdown
# Part Two Live Collectors

Part Two turns the Part One data foundation into read-only live collection.

## First Supported Command

```bash
mkdir -p data/raw
touch data/raw/.polymarket_archive_root
uv run polymarket-engine collect --assets BTC,ETH --duration 10
```

The first network runner records:

- Polymarket BTC/ETH 5-minute market snapshots discovered by deterministic slugs.
- Polymarket CLOB order book snapshots for Up and Down token ids.
- Coinbase BTC/ETH ticker updates for live proxy price movement.
- Polymarket RTDS/reference updates when the RTDS stream emits messages.
- DuckDB ingest-file rows under `ops.ingest_files`.
- Immutable raw Parquet files under `data/raw/`.

## Source Rules

- Polymarket website chart prices are not model truth.
- Coinbase is the first live proxy feed for BTC/ETH price movement.
- Polymarket RTDS is the first settlement/reference feed candidate.
- Binance.com is disabled by default on this machine because it returned `HTTP 451`.
- Every source event must preserve both source timestamp and local receive timestamp.
- Raw writes are crash-durable: `.parquet.tmp` files are atomically published and orphaned temporary files are cleaned at startup.
- WebSocket outages are handled with capped reconnect backoff; other sources continue running when one feed disconnects.

## Safety

Part Two does not trade, does not build model probabilities, and does not place orders.
```
```

Add this line to the README read-first list:

```markdown
- [Part Two Live Collectors](docs/PART_TWO_LIVE_COLLECTORS.md) - read-only collector command and live-source rules.
```

- [ ] **Step 3: Run live smoke command**

```bash
cd /Users/goon/polymarket
mkdir -p data/raw
touch data/raw/.polymarket_archive_root
uv run polymarket-engine collect --assets BTC,ETH --duration 10 --max-batch-size 10
```

Expected:

```text
'events_written': greater than 18
'files_written': greater than 2
```

The exact `events_written` count can be higher if the current window rolls while the command runs. It must be greater than zero. Polymarket and Coinbase must not appear in `source_errors`. RTDS may appear in `source_errors` during early testing and should be logged for follow-up filter work.

- [ ] **Step 4: Verify persisted files and DuckDB ledger**

```bash
cd /Users/goon/polymarket
uv run python - <<'PY'
from pathlib import Path
import duckdb

files = sorted(Path("data/raw").rglob("*.parquet"))
print("parquet_files", len(files))
with duckdb.connect("data/db/polymarket.duckdb") as conn:
    rows = conn.sql("select source_key, stream_key, sum(row_count) from ops.ingest_files group by 1,2 order by 1,2").fetchall()
print(rows)
PY
```

Expected:

```text
parquet_files
polymarket rows present in ops.ingest_files
```

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/live_collector.py docs/PART_TWO_LIVE_COLLECTORS.md README.md
git commit -m "Add Part Two live collector smoke command"
```

---

## Task 10A: Restartable Collector Service Draft

**Files:**
- Create: `/Users/goon/polymarket/ops/systemd/polymarket-live-collector.service`
- Modify: `/Users/goon/polymarket/docs/PART_TWO_LIVE_COLLECTORS.md`

- [ ] **Step 1: Create the systemd unit draft**

Create `/Users/goon/polymarket/ops/systemd/polymarket-live-collector.service`:

```ini
[Unit]
Description=Polymarket BTC/ETH binary live collector
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/home/spoon/polymarket/data/raw

[Service]
Type=exec
User=spoon
WorkingDirectory=/home/spoon/polymarket
Environment=PATH=/home/spoon/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=LANG=en_US.UTF-8
Environment=LC_ALL=en_US.UTF-8

ExecStartPre=/usr/bin/mkdir -p /home/spoon/polymarket/data/raw
ExecStartPre=/usr/bin/touch /home/spoon/polymarket/data/raw/.polymarket_archive_root
ExecStart=/home/spoon/.local/bin/uv run polymarket-engine collect --assets BTC,ETH --duration 86400 --raw-root /home/spoon/polymarket/data/raw --duckdb-path /home/spoon/polymarket/data/db/polymarket.duckdb --max-batch-size 100

Restart=on-failure
RestartSec=15
TimeoutStopSec=60
KillMode=process

StandardOutput=append:/home/spoon/polymarket/logs/live_collector.systemd.stdout.log
StandardError=append:/home/spoon/polymarket/logs/live_collector.systemd.stderr.log

[Install]
WantedBy=multi-user.target
```

This mirrors the GEX collector-service shape: wait for network, require the data path, restart on failure, and allow a long stop timeout so buffers can flush.

- [ ] **Step 2: Add service notes to docs**

Append to `/Users/goon/polymarket/docs/PART_TWO_LIVE_COLLECTORS.md`:

```markdown
## Restart Behavior

The draft systemd unit is `ops/systemd/polymarket-live-collector.service`.
It is read-only and exists to restart collection after process failure,
Wi-Fi recovery, reboot, or power loss. It should not be installed until
the 10-second live smoke command works locally.

The service uses:

- `After=network-online.target` and `Wants=network-online.target` so it waits for networking.
- `RequiresMountsFor=/home/spoon/polymarket/data/raw` so it does not write to the wrong path.
- `Restart=on-failure` and `RestartSec=15` so transient failures do not require manual restart.
- `TimeoutStopSec=60` so shutdown has time to flush buffered Parquet files.
```

- [ ] **Step 3: Validate the unit text locally**

Run:

```bash
cd /Users/goon/polymarket
python3 - <<'PY'
from pathlib import Path

unit = Path("ops/systemd/polymarket-live-collector.service").read_text()
required = [
    "After=network-online.target",
    "RequiresMountsFor=/home/spoon/polymarket/data/raw",
    "Restart=on-failure",
    "TimeoutStopSec=60",
    ".polymarket_archive_root",
]
missing = [item for item in required if item not in unit]
if missing:
    raise SystemExit(f"missing required service settings: {missing}")
print("service draft contains required durability settings")
PY
```

Expected:

```text
service draft contains required durability settings
```

- [ ] **Step 4: Commit**

```bash
cd /Users/goon/polymarket
git add ops/systemd/polymarket-live-collector.service docs/PART_TWO_LIVE_COLLECTORS.md
git commit -m "Add restartable live collector service draft"
```

---

## Task 11: Final Verification

**Files:**
- Verify all files created in Tasks 0-10A.

- [ ] **Step 1: Run unit tests**

```bash
cd /Users/goon/polymarket
uv run pytest
```

Expected:

```text
all tests pass
```

- [ ] **Step 2: Run lint**

```bash
cd /Users/goon/polymarket
uv run ruff check .
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Run type checking**

```bash
cd /Users/goon/polymarket
uv run mypy src
```

Expected:

```text
Success: no issues found
```

- [ ] **Step 4: Verify ignored data did not enter git**

```bash
cd /Users/goon/polymarket
git status --short
```

Expected:

```text
no data/raw files listed
```

- [ ] **Step 5: Commit verification note if docs changed during final checks**

```bash
cd /Users/goon/polymarket
git status --short
```

Expected:

```text
only intentional tracked docs or code changes remain
```

---

## Acceptance Criteria

Part Two is complete when:

- `polymarket-engine collect --assets BTC,ETH --duration 10` exists.
- Polymarket BTC/ETH 5m contracts are discovered with `btc-updown-5m-<epoch>` and `eth-updown-5m-<epoch>` slugs.
- Polymarket CLOB books are fetched by token id and written as raw Parquet.
- Coinbase BTC/ETH ticker updates are written by the live command.
- Coinbase ticker and Polymarket RTDS parsers are unit-tested.
- Polymarket RTDS is attempted and records either reference events or a source error without stopping Coinbase and Polymarket collection.
- Source lag and quality flags exist in every raw collector event payload.
- Raw Parquet publication uses `.parquet.tmp`, fsync, atomic publish, and orphan cleanup.
- The live command requires `data/raw/.polymarket_archive_root` before writing raw collector data.
- Buffered writes flush by row count and elapsed time.
- WebSocket reconnects use capped exponential backoff with jitter.
- DuckDB `ops.ingest_files` records files created by the collector.
- Binance.com is not required for the live command because this machine saw `HTTP 451`.
- A draft systemd service exists with `network-online.target`, `RequiresMountsFor`, `Restart=on-failure`, and `TimeoutStopSec=60`.
- `data/raw/` remains ignored by git.
- `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` pass.

---

## Explicit Non-Goals

- No real-money execution.
- No probability model.
- No Monte Carlo path generation.
- No XGBoost.
- No dashboard.
- No Postgres, Redis, ClickHouse, Timescale, or message queue.

---

## Source Documentation Checked

- Polymarket market WebSocket: `https://docs.polymarket.com/market-data/websocket/market-channel`
- Polymarket RTDS: `https://docs.polymarket.com/market-data/websocket/rtds`
- Polymarket CLOB order book: `https://docs.polymarket.com/trading/orderbook`
- Coinbase Advanced Trade WebSocket: `https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview`

---

## Self-Review Notes

Spec coverage:

- The plan references `/Users/goon/polymarket/docs/BINARY_CONTRACT_ENGINE_PLAN.md` by implementing its live shadow logger, raw immutable data, order-book snapshot, settlement/reference candidate, proxy feed, source-quality, and no-future-leakage requirements.
- The plan follows Part One by preserving DuckDB + Parquet and avoiding extra databases.
- The plan handles the smoke-test evidence that Coinbase worked, Polymarket Gamma/CLOB worked, and Binance.com returned `HTTP 451`.
- The plan borrows the GEX durability pattern without copying GEX-specific Schwab assumptions: atomic Parquet writes, sentinel checks, orphan cleanup, time-bounded flushes, restartable service behavior, and reconnect backoff.

Type consistency:

- All source events pass through `CollectorEvent`.
- All persisted raw events pass through Part One `RawEvent` and `write_raw_events`.
- All source timestamps and receive timestamps use `datetime`.

Execution order:

- Parser and event models come before live orchestration.
- Unit tests avoid network calls.
- The network smoke command comes only after parser, writer, and ledger tests exist.

---

## Execution Handoff

Recommended execution mode: use `superpowers:subagent-driven-development` and assign one task at a time. Each task is isolated enough for a fresh worker, and the reviewer should verify the tests and commit after each task.

Fallback execution mode: use `superpowers:executing-plans` inline in this session and work through the checklist sequentially.
