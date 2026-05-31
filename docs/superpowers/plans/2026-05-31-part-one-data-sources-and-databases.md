# Part One Data Sources and Databases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Lock the first production-shaped data architecture for the BTC/ETH binary-contract engine. Part One must collect, normalize, and replay the exact as-of data needed by the remaining-path probability engine without adding fragile extra predictors too early.

**Architecture:** Use a local event lake plus analytical database. Raw source messages are written immutably to partitioned Parquet. DuckDB owns normalized tables, replay views, and as-of feature snapshots. Runtime code reads from in-memory state for speed and periodically persists checkpoints. The first build is read-only and paper-trading only.

**Tech Stack:** Python 3.11, Polars, PyArrow, DuckDB, httpx, websockets, FastAPI health surface, pytest, ruff, mypy.

---

## Locked Part One Decisions

### Sources Included In Part One

1. **Polymarket market metadata**
   - Job: identify BTC/ETH binary contracts, side, threshold `K`, expiry, token ids, rules, and settlement wording.
   - Storage: raw metadata response plus normalized `core.contracts`.
   - Reason: the model does not invent contracts. The venue defines the contract, threshold, expiry, and settlement condition.

2. **Polymarket CLOB order book**
   - Job: capture executable bid/ask, depth, quote age, spread, and market-implied price.
   - Storage: order book snapshots and order book updates.
   - Reason: fair probability is only useful after comparison against executable price, not midpoint or chart price.

3. **Polymarket market WebSocket**
   - Job: maintain live order book state and record price/depth changes quickly.
   - Storage: raw WebSocket events and normalized book updates.
   - Reason: many short-dated binaries converge quickly, so snapshots alone will miss the tradable path.

4. **Polymarket RTDS or venue-supported Chainlink-style crypto price stream**
   - Job: observe the BTC/ETH price feed closest to the venue's settlement reference.
   - Storage: `core.price_ticks` with `source="polymarket_rtds_chainlink"`.
   - Reason: the probability engine must price the contract using the settlement-source price, or the best validated proxy when direct access is not available.

5. **Binance Spot WebSocket**
   - Job: high-frequency BTCUSDT and ETHUSDT proxy ticks, trades, book ticker, and short-horizon movement.
   - Storage: `core.price_ticks` and optional best bid/ask rows.
   - Reason: free, liquid, fast, and useful for volatility/path reconstruction, while clearly labeled as a proxy.

6. **Coinbase Advanced Trade WebSocket**
   - Job: independent BTC-USD and ETH-USD proxy ticks.
   - Storage: `core.price_ticks`.
   - Reason: gives source disagreement checks against Binance and the venue-supported stream.

### Sources Excluded From Part One

1. **ETF options / GEX context**
   - Status: reserved but excluded from first historical replay.
   - Reason: this data is useful as volatility, skew, and risk-appetite context, but it can easily create backtest leakage if historical as-of chain data is incomplete.
   - First use: ablation after the core BTC/ETH engine works: core engine versus core engine plus ETF options context.

2. **Jupiter prediction markets**
   - Status: deferred.
   - Reason: multi-venue support should not be added before one venue's contract parsing, order book replay, settlement-source mapping, and labeling are reliable.

3. **Direct Chainlink Data Streams**
   - Status: upgrade path.
   - Reason: use direct Chainlink only when access, symbols, latency, and historical retention are verified. Until then, store the venue-supported stream and proxy sources separately.

4. **News/headline NLP**
   - Status: excluded.
   - Reason: speed and replay integrity matter more in Part One. Event flags can be added as manual calendar rows before any text model is used.

### Database Choice

Use **DuckDB plus Parquet** for Part One.

Reason:

- The project already depends on DuckDB, Polars, PyArrow, and Parquet.
- The workload is local research, replay, feature construction, and backtesting.
- DuckDB is strong for analytical scans over Parquet and does not require a server.
- The first system only needs one writer process.
- Avoiding Postgres, Redis, ClickHouse, or Timescale keeps the first architecture easy to inspect and hard to overbuild.

Database rule:

- **Parquet is the immutable raw event lake.**
- **DuckDB is the query, normalization, replay, and feature database.**
- **In-memory state is the live speed layer.**

Upgrade rules:

- Add **Postgres** only if the app needs multi-user state, account state, or concurrent transactional writes.
- Add **ClickHouse** only if event volume or concurrent historical analytics outgrow DuckDB.
- Add **Redis** only if a live dashboard or execution daemon needs pub/sub or cross-process low-latency cache.
- Do not add a second database before Part One replay tests pass.

---

## File Structure

```text
/Users/goon/polymarket/
├── config/
│   └── local.example.toml
├── data/
│   ├── raw/
│   │   ├── polymarket/
│   │   ├── binance/
│   │   └── coinbase/
│   └── db/
│       └── polymarket.duckdb
├── docs/
│   └── superpowers/
│       └── plans/
│           └── 2026-05-31-part-one-data-sources-and-databases.md
├── src/
│   └── polymarket_engine/
│       ├── domain/
│       │   ├── __init__.py
│       │   └── sources.py
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── paths.py
│       │   ├── raw_writer.py
│       │   └── schema.sql
│       ├── venues/
│       │   ├── __init__.py
│       │   ├── binance.py
│       │   ├── coinbase.py
│       │   └── polymarket.py
│       ├── features/
│       │   ├── __init__.py
│       │   └── asof_inputs.py
│       └── ingestion/
│           ├── __init__.py
│           └── runner.py
└── tests/
    ├── domain/
    │   └── test_sources.py
    ├── storage/
    │   ├── test_raw_writer.py
    │   └── test_schema.py
    ├── venues/
    │   ├── test_binance.py
    │   ├── test_coinbase.py
    │   └── test_polymarket.py
    └── features/
        └── test_asof_inputs.py
```

---

## Implementation Tasks

### Task 1: Lock Source Registry

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/domain/__init__.py`.

```python
"""Domain objects for the binary-contract engine."""
```

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/domain/sources.py`.

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceRole(str, Enum):
    CONTRACT_METADATA = "contract_metadata"
    EXECUTABLE_MARKET = "executable_market"
    SETTLEMENT_REFERENCE = "settlement_reference"
    PRICE_PROXY = "price_proxy"
    CONTEXT_LAYER = "context_layer"


class SourceStatus(str, Enum):
    PART_ONE = "part_one"
    DEFERRED = "deferred"
    UPGRADE_PATH = "upgrade_path"


@dataclass(frozen=True)
class DataSource:
    key: str
    role: SourceRole
    status: SourceStatus
    symbols: tuple[str, ...]
    reason: str


LOCKED_SOURCES: dict[str, DataSource] = {
    "polymarket_markets": DataSource(
        key="polymarket_markets",
        role=SourceRole.CONTRACT_METADATA,
        status=SourceStatus.PART_ONE,
        symbols=("BTC", "ETH"),
        reason="Venue-defined contract object, threshold, expiry, token ids, rules, and resolution text.",
    ),
    "polymarket_clob": DataSource(
        key="polymarket_clob",
        role=SourceRole.EXECUTABLE_MARKET,
        status=SourceStatus.PART_ONE,
        symbols=("BTC", "ETH"),
        reason="Executable bid, ask, spread, and depth used for edge after costs.",
    ),
    "polymarket_market_ws": DataSource(
        key="polymarket_market_ws",
        role=SourceRole.EXECUTABLE_MARKET,
        status=SourceStatus.PART_ONE,
        symbols=("BTC", "ETH"),
        reason="Fast order book updates for short-dated binary convergence.",
    ),
    "polymarket_rtds_chainlink": DataSource(
        key="polymarket_rtds_chainlink",
        role=SourceRole.SETTLEMENT_REFERENCE,
        status=SourceStatus.PART_ONE,
        symbols=("BTC/USD", "ETH/USD"),
        reason="Venue-supported crypto price stream closest to settlement-source behavior.",
    ),
    "binance_spot_ws": DataSource(
        key="binance_spot_ws",
        role=SourceRole.PRICE_PROXY,
        status=SourceStatus.PART_ONE,
        symbols=("BTCUSDT", "ETHUSDT"),
        reason="Liquid free proxy for high-frequency price path and volatility reconstruction.",
    ),
    "coinbase_advanced_ws": DataSource(
        key="coinbase_advanced_ws",
        role=SourceRole.PRICE_PROXY,
        status=SourceStatus.PART_ONE,
        symbols=("BTC-USD", "ETH-USD"),
        reason="Independent USD proxy for source disagreement checks.",
    ),
    "etf_gex_context": DataSource(
        key="etf_gex_context",
        role=SourceRole.CONTEXT_LAYER,
        status=SourceStatus.DEFERRED,
        symbols=("IBIT", "FBTC"),
        reason="Useful as volatility/skew/risk-appetite context after core replay integrity is proven.",
    ),
    "jupiter_prediction_markets": DataSource(
        key="jupiter_prediction_markets",
        role=SourceRole.EXECUTABLE_MARKET,
        status=SourceStatus.DEFERRED,
        symbols=("BTC", "ETH", "SOL"),
        reason="Multi-venue expansion after one venue's replay and labeling are reliable.",
    ),
    "chainlink_data_streams_direct": DataSource(
        key="chainlink_data_streams_direct",
        role=SourceRole.SETTLEMENT_REFERENCE,
        status=SourceStatus.UPGRADE_PATH,
        symbols=("BTC/USD", "ETH/USD"),
        reason="Direct settlement-reference upgrade once access, latency, and history are verified.",
    ),
}


def part_one_sources() -> tuple[DataSource, ...]:
    return tuple(source for source in LOCKED_SOURCES.values() if source.status == SourceStatus.PART_ONE)
```

- [x] Create `/Users/goon/polymarket/tests/domain/test_sources.py`.

```python
from polymarket_engine.domain.sources import LOCKED_SOURCES, SourceRole, SourceStatus, part_one_sources


def test_part_one_sources_are_locked() -> None:
    keys = {source.key for source in part_one_sources()}

    assert keys == {
        "polymarket_markets",
        "polymarket_clob",
        "polymarket_market_ws",
        "polymarket_rtds_chainlink",
        "binance_spot_ws",
        "coinbase_advanced_ws",
    }


def test_etf_and_jupiter_are_not_part_one() -> None:
    assert LOCKED_SOURCES["etf_gex_context"].status == SourceStatus.DEFERRED
    assert LOCKED_SOURCES["jupiter_prediction_markets"].status == SourceStatus.DEFERRED


def test_settlement_source_is_separate_from_proxy_sources() -> None:
    assert LOCKED_SOURCES["polymarket_rtds_chainlink"].role == SourceRole.SETTLEMENT_REFERENCE
    assert LOCKED_SOURCES["binance_spot_ws"].role == SourceRole.PRICE_PROXY
    assert LOCKED_SOURCES["coinbase_advanced_ws"].role == SourceRole.PRICE_PROXY
```

- [x] Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/domain/test_sources.py
```

Expected result:

```text
3 passed
```

---

### Task 2: Expand Configuration For Locked Data Sources

- [x] Update `/Users/goon/polymarket/config/local.example.toml`.

Add these sections without storing keys:

```toml
[storage]
raw_root = "data/raw"
duckdb_path = "data/db/polymarket.duckdb"
parquet_compression = "zstd"

[data_sources.polymarket]
enabled = true
mode = "read_only"
markets_base_url = "https://gamma-api.polymarket.com"
clob_base_url = "https://clob.polymarket.com"
market_ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
rtds_url = "wss://ws-live-data.polymarket.com"
symbols = ["BTC", "ETH"]

[data_sources.binance]
enabled = true
mode = "read_only"
spot_ws_url = "wss://stream.binance.com:9443/ws"
streams = ["btcusdt@trade", "ethusdt@trade", "btcusdt@bookTicker", "ethusdt@bookTicker"]

[data_sources.coinbase]
enabled = true
mode = "read_only"
advanced_trade_ws_url = "wss://advanced-trade-ws.coinbase.com"
product_ids = ["BTC-USD", "ETH-USD"]

[data_sources.etf_gex_context]
enabled = false
mode = "reserved"
reason = "Excluded from Part One replay until historical as-of chain data is verified."

[data_sources.jupiter]
enabled = false
mode = "reserved"
reason = "Deferred until Polymarket BTC/ETH replay works."
```

- [x] Verify no secrets were added:

```bash
cd /Users/goon/polymarket
rg -n "api_key|secret|token|private|password" config/local.example.toml
```

Expected result:

```text
no matches
```

---

### Task 3: Create DuckDB Schema

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/storage/__init__.py`.

```python
"""Storage utilities for raw events, DuckDB schemas, and replay state."""
```

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/storage/schema.sql`.

```sql
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS features;
CREATE SCHEMA IF NOT EXISTS validation;

CREATE TABLE IF NOT EXISTS ops.ingest_files (
    file_id VARCHAR PRIMARY KEY,
    source_key VARCHAR NOT NULL,
    stream_key VARCHAR NOT NULL,
    partition_date DATE NOT NULL,
    partition_hour UTINYINT NOT NULL,
    path VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    row_count UBIGINT NOT NULL,
    first_event_ts TIMESTAMPTZ,
    last_event_ts TIMESTAMPTZ,
    written_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ops.ingest_checkpoints (
    source_key VARCHAR NOT NULL,
    stream_key VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    last_event_ts TIMESTAMPTZ,
    last_sequence VARCHAR,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source_key, stream_key, symbol)
);

CREATE TABLE IF NOT EXISTS core.contracts (
    contract_id VARCHAR PRIMARY KEY,
    venue VARCHAR NOT NULL,
    asset VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    threshold DOUBLE NOT NULL,
    expiry_ts TIMESTAMPTZ NOT NULL,
    settlement_source VARCHAR NOT NULL,
    token_id VARCHAR NOT NULL,
    rule_text VARCHAR NOT NULL,
    rule_hash VARCHAR NOT NULL,
    first_seen_ts TIMESTAMPTZ NOT NULL,
    last_seen_ts TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS core.price_ticks (
    source_key VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    observed_ts TIMESTAMPTZ NOT NULL,
    price DOUBLE NOT NULL,
    bid DOUBLE,
    ask DOUBLE,
    sequence VARCHAR,
    raw_file_id VARCHAR,
    PRIMARY KEY (source_key, symbol, event_ts, observed_ts)
);

CREATE TABLE IF NOT EXISTS core.orderbook_snapshots (
    venue VARCHAR NOT NULL,
    contract_id VARCHAR NOT NULL,
    token_id VARCHAR NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    observed_ts TIMESTAMPTZ NOT NULL,
    best_bid DOUBLE,
    best_ask DOUBLE,
    bid_size_top DOUBLE,
    ask_size_top DOUBLE,
    spread DOUBLE,
    depth_json VARCHAR NOT NULL,
    raw_file_id VARCHAR,
    PRIMARY KEY (venue, token_id, event_ts, observed_ts)
);

CREATE TABLE IF NOT EXISTS features.asof_state_inputs (
    state_id VARCHAR PRIMARY KEY,
    contract_id VARCHAR NOT NULL,
    asof_ts TIMESTAMPTZ NOT NULL,
    asset VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    threshold DOUBLE NOT NULL,
    seconds_left DOUBLE NOT NULL,
    settlement_price DOUBLE NOT NULL,
    settlement_source_key VARCHAR NOT NULL,
    binance_price DOUBLE,
    coinbase_price DOUBLE,
    source_disagreement_bps DOUBLE,
    best_bid DOUBLE,
    best_ask DOUBLE,
    executable_price DOUBLE,
    spread DOUBLE,
    quote_age_ms DOUBLE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS validation.contract_labels (
    contract_id VARCHAR PRIMARY KEY,
    resolved_side VARCHAR NOT NULL,
    settlement_price DOUBLE NOT NULL,
    settlement_ts TIMESTAMPTZ NOT NULL,
    label_source VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

- [x] Create `/Users/goon/polymarket/tests/storage/test_schema.py`.

```python
from pathlib import Path

import duckdb


def test_schema_applies_to_empty_database(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    schema_path = Path("src/polymarket_engine/storage/schema.sql")

    with duckdb.connect(str(db_path)) as conn:
        conn.sql(schema_path.read_text())
        tables = {
            row[0]
            for row in conn.sql(
                """
                SELECT table_schema || '.' || table_name
                FROM information_schema.tables
                WHERE table_schema IN ('ops', 'core', 'features', 'validation')
                """
            ).fetchall()
        }

    assert {
        "ops.ingest_files",
        "ops.ingest_checkpoints",
        "core.contracts",
        "core.price_ticks",
        "core.orderbook_snapshots",
        "features.asof_state_inputs",
        "validation.contract_labels",
    }.issubset(tables)
```

- [x] Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_schema.py
```

Expected result:

```text
1 passed
```

---

### Task 4: Add Partitioned Raw Event Paths

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/storage/paths.py`.

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RawPartition:
    root: Path
    source_key: str
    stream_key: str
    event_ts: datetime

    @property
    def date_part(self) -> str:
        ts = self.event_ts.astimezone(timezone.utc)
        return ts.strftime("date=%Y-%m-%d")

    @property
    def hour_part(self) -> str:
        ts = self.event_ts.astimezone(timezone.utc)
        return ts.strftime("hour=%H")

    @property
    def directory(self) -> Path:
        return self.root / self.source_key / self.stream_key / self.date_part / self.hour_part
```

- [x] Add path expectations to `/Users/goon/polymarket/tests/storage/test_raw_writer.py`.

```python
from datetime import datetime, timezone
from pathlib import Path

from polymarket_engine.storage.paths import RawPartition


def test_raw_partition_path_uses_source_stream_date_and_hour() -> None:
    partition = RawPartition(
        root=Path("data/raw"),
        source_key="binance_spot_ws",
        stream_key="trade",
        event_ts=datetime(2026, 5, 31, 20, 4, 1, tzinfo=timezone.utc),
    )

    assert partition.directory == Path("data/raw/binance_spot_ws/trade/date=2026-05-31/hour=20")
```

- [x] Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_raw_writer.py
```

Expected result:

```text
1 passed
```

---

### Task 5: Create Raw Event Writer

- [x] Extend `/Users/goon/polymarket/src/polymarket_engine/storage/raw_writer.py`.

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import polars as pl

from polymarket_engine.storage.paths import RawPartition


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


def write_raw_events(raw_root: Path, events: list[RawEvent]) -> RawWriteResult:
    if not events:
        raise ValueError("events must not be empty")

    first = min(event.event_ts for event in events)
    last = max(event.event_ts for event in events)
    source_keys = {event.source_key for event in events}
    stream_keys = {event.stream_key for event in events}
    if len(source_keys) != 1 or len(stream_keys) != 1:
        raise ValueError("one raw file must contain exactly one source_key and one stream_key")

    source_key = next(iter(source_keys))
    stream_key = next(iter(stream_keys))
    partition = RawPartition(raw_root, source_key, stream_key, first)
    partition.directory.mkdir(parents=True, exist_ok=True)

    file_id = uuid4().hex
    output_path = partition.directory / f"{file_id}.parquet"
    rows = [
        {
            **asdict(event),
            "event_ts": event.event_ts.astimezone(timezone.utc),
            "observed_ts": event.observed_ts.astimezone(timezone.utc),
            "payload": json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
        }
        for event in events
    ]

    pl.DataFrame(rows).write_parquet(output_path, compression="zstd")
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

- [x] Extend `/Users/goon/polymarket/tests/storage/test_raw_writer.py`.

```python
from datetime import datetime, timezone

import polars as pl
import pytest

from polymarket_engine.storage.raw_writer import RawEvent, write_raw_events


def test_write_raw_events_creates_parquet_file(tmp_path) -> None:
    event = RawEvent(
        source_key="binance_spot_ws",
        stream_key="trade",
        symbol="BTCUSDT",
        event_ts=datetime(2026, 5, 31, 20, 4, 1, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 4, 2, tzinfo=timezone.utc),
        payload={"p": "104000.1", "q": "0.02"},
    )

    result = write_raw_events(tmp_path, [event])

    assert result.path.exists()
    assert result.row_count == 1
    frame = pl.read_parquet(result.path)
    assert frame["source_key"].to_list() == ["binance_spot_ws"]
    assert frame["symbol"].to_list() == ["BTCUSDT"]


def test_write_raw_events_rejects_mixed_sources(tmp_path) -> None:
    events = [
        RawEvent(
            source_key="binance_spot_ws",
            stream_key="trade",
            symbol="BTCUSDT",
            event_ts=datetime(2026, 5, 31, 20, 4, 1, tzinfo=timezone.utc),
            observed_ts=datetime(2026, 5, 31, 20, 4, 2, tzinfo=timezone.utc),
            payload={"p": "104000.1"},
        ),
        RawEvent(
            source_key="coinbase_advanced_ws",
            stream_key="ticker",
            symbol="BTC-USD",
            event_ts=datetime(2026, 5, 31, 20, 4, 1, tzinfo=timezone.utc),
            observed_ts=datetime(2026, 5, 31, 20, 4, 2, tzinfo=timezone.utc),
            payload={"price": "104001.0"},
        ),
    ]

    with pytest.raises(ValueError, match="one source_key"):
        write_raw_events(tmp_path, events)
```

- [x] Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_raw_writer.py
```

Expected result:

```text
3 passed
```

---

### Task 6: Normalize Venue Messages Without Network Calls

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/venues/__init__.py`.

```python
"""Read-only venue adapters for market metadata, prices, and order books."""
```

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/venues/binance.py`.

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class NormalizedPriceTick:
    source_key: str
    symbol: str
    event_ts: datetime
    price: float
    bid: float | None = None
    ask: float | None = None
    sequence: str | None = None


def parse_binance_trade(message: dict[str, object]) -> NormalizedPriceTick:
    return NormalizedPriceTick(
        source_key="binance_spot_ws",
        symbol=str(message["s"]),
        event_ts=datetime.fromtimestamp(int(message["T"]) / 1000, tz=timezone.utc),
        price=float(message["p"]),
        sequence=str(message.get("t", "")),
    )


def parse_binance_book_ticker(message: dict[str, object]) -> NormalizedPriceTick:
    bid = float(message["b"])
    ask = float(message["a"])
    return NormalizedPriceTick(
        source_key="binance_spot_ws",
        symbol=str(message["s"]),
        event_ts=datetime.fromtimestamp(int(message["E"]) / 1000, tz=timezone.utc),
        price=(bid + ask) / 2,
        bid=bid,
        ask=ask,
        sequence=str(message.get("u", "")),
    )
```

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/venues/coinbase.py`.

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CoinbaseTick:
    source_key: str
    symbol: str
    event_ts: datetime
    price: float


def parse_coinbase_ticker(event: dict[str, object]) -> CoinbaseTick:
    return CoinbaseTick(
        source_key="coinbase_advanced_ws",
        symbol=str(event["product_id"]),
        event_ts=datetime.fromisoformat(str(event["time"]).replace("Z", "+00:00")),
        price=float(event["price"]),
    )
```

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/venues/polymarket.py`.

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PolymarketContract:
    contract_id: str
    asset: str
    side: str
    threshold: float
    expiry_ts: datetime
    settlement_source: str
    token_id: str
    rule_text: str
    rule_hash: str


def rule_hash(rule_text: str) -> str:
    return hashlib.sha256(rule_text.strip().encode("utf-8")).hexdigest()


def normalize_contract(raw: dict[str, object]) -> PolymarketContract:
    text = str(raw["rule_text"])
    return PolymarketContract(
        contract_id=str(raw["contract_id"]),
        asset=str(raw["asset"]).upper(),
        side=str(raw["side"]).upper(),
        threshold=float(raw["threshold"]),
        expiry_ts=datetime.fromisoformat(str(raw["expiry_ts"]).replace("Z", "+00:00")),
        settlement_source=str(raw["settlement_source"]),
        token_id=str(raw["token_id"]),
        rule_text=text,
        rule_hash=rule_hash(text),
    )
```

- [x] Create `/Users/goon/polymarket/tests/venues/test_binance.py`.

```python
from polymarket_engine.venues.binance import parse_binance_book_ticker, parse_binance_trade


def test_parse_binance_trade() -> None:
    tick = parse_binance_trade({"s": "BTCUSDT", "T": 1780257601000, "p": "104000.1", "t": 123})

    assert tick.source_key == "binance_spot_ws"
    assert tick.symbol == "BTCUSDT"
    assert tick.price == 104000.1
    assert tick.sequence == "123"


def test_parse_binance_book_ticker() -> None:
    tick = parse_binance_book_ticker({"s": "ETHUSDT", "E": 1780257601000, "b": "3500.0", "a": "3501.0", "u": 9})

    assert tick.price == 3500.5
    assert tick.bid == 3500.0
    assert tick.ask == 3501.0
```

- [x] Create `/Users/goon/polymarket/tests/venues/test_coinbase.py`.

```python
from polymarket_engine.venues.coinbase import parse_coinbase_ticker


def test_parse_coinbase_ticker() -> None:
    tick = parse_coinbase_ticker(
        {"product_id": "BTC-USD", "time": "2026-05-31T20:00:01Z", "price": "104001.2"}
    )

    assert tick.source_key == "coinbase_advanced_ws"
    assert tick.symbol == "BTC-USD"
    assert tick.price == 104001.2
```

- [x] Create `/Users/goon/polymarket/tests/venues/test_polymarket.py`.

```python
from polymarket_engine.venues.polymarket import normalize_contract, rule_hash


def test_normalize_contract_hashes_rule_text() -> None:
    contract = normalize_contract(
        {
            "contract_id": "btc-up-5m-1",
            "asset": "btc",
            "side": "up",
            "threshold": 104000,
            "expiry_ts": "2026-05-31T20:05:00Z",
            "settlement_source": "chainlink_btc_usd",
            "token_id": "123",
            "rule_text": "BTC resolves up if final settlement price is above 104000.",
        }
    )

    assert contract.asset == "BTC"
    assert contract.side == "UP"
    assert contract.rule_hash == rule_hash(contract.rule_text)
```

- [x] Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/venues
```

Expected result:

```text
4 passed
```

---

### Task 7: Build As-Of State Inputs With Leakage Guard

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/features/__init__.py`.

```python
"""Feature construction for as-of contract states."""
```

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/features/asof_inputs.py`.

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AsOfStateInput:
    contract_id: str
    asof_ts: datetime
    asset: str
    side: str
    threshold: float
    seconds_left: float
    settlement_price: float
    settlement_source_key: str
    binance_price: float | None
    coinbase_price: float | None
    source_disagreement_bps: float | None
    best_bid: float | None
    best_ask: float | None
    executable_price: float | None
    spread: float | None
    quote_age_ms: float | None


def calculate_source_disagreement_bps(primary_price: float, proxy_prices: list[float]) -> float | None:
    if not proxy_prices:
        return None
    worst_bps = max(abs(proxy - primary_price) / primary_price * 10_000 for proxy in proxy_prices)
    return worst_bps


def ensure_asof(timestamp: datetime, asof_ts: datetime, field_name: str) -> None:
    if timestamp > asof_ts:
        raise ValueError(f"{field_name} timestamp is after asof_ts")
```

- [x] Create `/Users/goon/polymarket/tests/features/test_asof_inputs.py`.

```python
from datetime import datetime, timezone

import pytest

from polymarket_engine.features.asof_inputs import calculate_source_disagreement_bps, ensure_asof


def test_source_disagreement_bps_uses_worst_proxy_gap() -> None:
    assert calculate_source_disagreement_bps(100_000, [100_010, 99_950]) == 5.0


def test_ensure_asof_rejects_future_data() -> None:
    asof_ts = datetime(2026, 5, 31, 20, 0, 0, tzinfo=timezone.utc)
    future_ts = datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="after asof_ts"):
        ensure_asof(future_ts, asof_ts, "price_tick")
```

- [x] Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/features/test_asof_inputs.py
```

Expected result:

```text
2 passed
```

---

### Task 8: Add Read-Only Ingestion Runner Skeleton

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/__init__.py`.

```python
"""Read-only ingestion orchestration for Part One data collection."""
```

- [x] Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/runner.py`.

```python
from __future__ import annotations

from dataclasses import dataclass

from polymarket_engine.domain.sources import DataSource, part_one_sources


@dataclass(frozen=True)
class IngestionPlan:
    sources: tuple[DataSource, ...]
    paper_only: bool


def build_part_one_ingestion_plan() -> IngestionPlan:
    return IngestionPlan(sources=part_one_sources(), paper_only=True)
```

- [x] Create `/Users/goon/polymarket/tests/test_part_one_ingestion_plan.py`.

```python
from polymarket_engine.ingestion.runner import build_part_one_ingestion_plan


def test_part_one_ingestion_plan_is_paper_only() -> None:
    plan = build_part_one_ingestion_plan()

    assert plan.paper_only is True
    assert {source.key for source in plan.sources} == {
        "polymarket_markets",
        "polymarket_clob",
        "polymarket_market_ws",
        "polymarket_rtds_chainlink",
        "binance_spot_ws",
        "coinbase_advanced_ws",
    }
```

- [x] Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/test_part_one_ingestion_plan.py
```

Expected result:

```text
1 passed
```

---

### Task 9: Document The Part One Data Contract

- [x] Create `/Users/goon/polymarket/docs/PART_ONE_DATA_CONTRACT.md`.

```markdown
# Part One Data Contract

Part One collects only the data required to reconstruct BTC/ETH binary-contract states as they were observable at time `t`.

## Included

- Polymarket market metadata for BTC and ETH binary contracts.
- Polymarket executable order book data.
- Polymarket market WebSocket updates.
- Venue-supported Chainlink-style BTC/ETH price stream when available.
- Binance BTCUSDT and ETHUSDT proxy ticks.
- Coinbase BTC-USD and ETH-USD proxy ticks.

## Excluded

- ETF options / GEX context.
- Jupiter prediction markets.
- Direct Chainlink Data Streams.
- News/headline NLP.

## Database

Part One uses DuckDB plus Parquet.

- Parquet stores immutable raw source events.
- DuckDB stores normalized contracts, prices, order books, feature snapshots, labels, and replay metadata.
- Live runtime state stays in memory and is periodically persisted.

## Leakage Rule

When replaying a contract at time `t`, the engine may only use data timestamped at or before `t`.
Future BTC movement, final settlement, future Polymarket prices, and future order book changes are labels only.
```

- [x] Update `/Users/goon/polymarket/README.md` with a short pointer:

```markdown
## Part One Data Contract

The locked Part One data-source and database plan lives in `docs/PART_ONE_DATA_CONTRACT.md`.
```

- [x] Run:

```bash
cd /Users/goon/polymarket
rg -n "Part One Data Contract|DuckDB plus Parquet|Leakage Rule" README.md docs/PART_ONE_DATA_CONTRACT.md
```

Expected result:

```text
README.md:...
docs/PART_ONE_DATA_CONTRACT.md:...
```

---

### Task 10: Run Full Verification

- [x] Run focused tests:

```bash
cd /Users/goon/polymarket
uv run pytest tests/domain tests/storage tests/venues tests/features tests/test_part_one_ingestion_plan.py
```

Expected result:

```text
all selected tests pass
```

- [x] Run project formatting/linting:

```bash
cd /Users/goon/polymarket
uv run ruff check .
```

Expected result:

```text
All checks passed!
```

- [x] Run type checking:

```bash
cd /Users/goon/polymarket
uv run mypy src
```

Expected result:

```text
Success: no issues found
```

---

## Acceptance Criteria

Part One is complete when:

- Locked source registry exists in code and tests prove ETF/GEX and Jupiter are excluded from Part One.
- `config/local.example.toml` includes all first-pass source endpoints without secrets.
- DuckDB schema creates `ops`, `core`, `features`, and `validation` schemas.
- Raw event writer writes partitioned Parquet and rejects mixed-source files.
- Binance, Coinbase, and Polymarket normalizers parse sample messages without network calls.
- As-of helpers reject future timestamps.
- Documentation explains the included sources, excluded sources, database choice, and leakage rule.
- Focused tests, ruff, and mypy pass.

---

## First Build Sequence

1. Implement Tasks 1-3 first so the source and database contract is real.
2. Implement Tasks 4-5 so raw capture has a stable file format.
3. Implement Tasks 6-7 so source messages can become as-of state inputs.
4. Implement Tasks 8-9 so the build has a paper-only runner and human-readable contract.
5. Run Task 10 before connecting any live WebSocket.

---

## Notes For Later Parts

Part Two should build live collectors and replay jobs on top of this storage contract.

Part Three should build the Monte Carlo probability engine using `features.asof_state_inputs`.

Part Four should add XGBoost as a challenger probability model after clean labels exist.

Part Five should test ETF/GEX context through ablation only after the core engine is stable.

---

## Source Documentation Checked

- Polymarket market discovery: `https://docs.polymarket.com/market-data/fetching-markets`
- Polymarket order book REST and executable prices: `https://docs.polymarket.com/trading/orderbook`
- Polymarket market WebSocket: `https://docs.polymarket.com/market-data/websocket/market-channel`
- Polymarket real-time crypto price streams: `https://docs.polymarket.com/market-data/websocket/rtds`
- Binance Spot WebSocket streams: `https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams`
- Coinbase Advanced Trade WebSocket: `https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview`
- DuckDB Parquet support: `https://duckdb.org/docs/stable/data/parquet/overview`
- DuckDB concurrency model: `https://duckdb.org/docs/current/connect/concurrency.html`
