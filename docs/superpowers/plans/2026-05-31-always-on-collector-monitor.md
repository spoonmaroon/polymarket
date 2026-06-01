# Always-On Collector And Terminal Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only always-on BTC/ETH collector that tracks only the current and next 5-minute Polymarket contracts, snapshots active order books every second, and exposes a terminal monitor for a second tab.

**Architecture:** Keep collection and display separate. The collector keeps persistent WebSocket loops for Coinbase and Polymarket RTDS, refreshes Gamma market discovery on a slower cadence, snapshots the active CLOB books every second, and writes raw Parquet plus normalized DuckDB rows. The monitor only reads DuckDB and renders the latest normalized state; it must not trade, mutate collector state, or invent synthetic data.

**Tech Stack:** Python 3.11+, asyncio, httpx, websockets, DuckDB, pytest, ruff, mypy, existing `polymarket_engine` CLI.

---

## File Structure

- Modify `/Users/goon/polymarket/src/polymarket_engine/cli.py`
  - Add `collect --forever`, optional `--duration`, `--windows-to-track`, `--snapshot-interval`, and `--market-refresh-interval`.
  - Add `monitor` subcommand.
- Modify `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`
  - Add continuous collector loops.
  - Keep finite `--duration` mode for smoke tests.
  - Default to two tracked windows: current 5m and next 5m.
- Create `/Users/goon/polymarket/src/polymarket_engine/monitor.py`
  - Read latest normalized contracts, prices, books, and ingest status from DuckDB.
  - Render a compact ANSI terminal view.
- Modify `/Users/goon/polymarket/tests/test_cli.py`
  - Cover new CLI flags and subcommands.
- Create `/Users/goon/polymarket/tests/ingestion/test_live_collector_config.py`
  - Cover config defaults and finite/forever timing behavior.
- Create `/Users/goon/polymarket/tests/test_monitor.py`
  - Cover monitor snapshot and render output from a temp DuckDB database.
- Modify `/Users/goon/polymarket/docs/BINARY_CONTRACT_ENGINE_PLAN.md`
  - Note that v1 collection watches current and next 5m BTC/ETH contracts only.

---

### Task 1: CLI Contract For Always-On Collection

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/cli.py`
- Test: `/Users/goon/polymarket/tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Add tests that assert:

```python
def test_parse_collect_forever_args() -> None:
    args = parse_args(
        [
            "collect",
            "--assets",
            "BTC,ETH",
            "--forever",
            "--windows-to-track",
            "2",
            "--snapshot-interval",
            "1",
            "--market-refresh-interval",
            "30",
        ]
    )

    assert args.command == "collect"
    assert args.forever is True
    assert args.duration is None
    assert args.windows_to_track == 2
    assert args.snapshot_interval == 1.0
    assert args.market_refresh_interval == 30.0


def test_parse_monitor_args() -> None:
    args = parse_args(["monitor", "--duckdb-path", "data/db/polymarket.duckdb", "--refresh", "1"])

    assert args.command == "monitor"
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.refresh == 1.0
```

Update the injected runner test so it expects:

```python
assert seen == {
    "assets": ("BTC", "ETH"),
    "duration": 5,
    "windows_to_track": 2,
    "snapshot_interval": 1.0,
}
```

- [ ] **Step 2: Run CLI tests to verify RED**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/test_cli.py -v
```

Expected: FAIL because `--forever`, `--windows-to-track`, `--snapshot-interval`, `--market-refresh-interval`, and `monitor` do not exist yet.

- [ ] **Step 3: Implement CLI flags**

Update `parse_args()`:

```python
collect.add_argument("--duration", type=int, default=None)
collect.add_argument("--forever", action="store_true")
collect.add_argument("--windows-to-track", type=int, default=2)
collect.add_argument("--snapshot-interval", type=float, default=1.0)
collect.add_argument("--market-refresh-interval", type=float, default=30.0)

monitor = subparsers.add_parser("monitor")
monitor.add_argument("--duckdb-path", type=Path, default=Path("data/db/polymarket.duckdb"))
monitor.add_argument("--refresh", type=float, default=1.0)
monitor.add_argument("--limit", type=int, default=8)
```

In `run_collect_command()`, reject missing duration unless `--forever` is present:

```python
if args.duration is None and not args.forever:
    raise SystemExit("collect requires --duration or --forever")
duration_seconds = None if args.forever else args.duration
```

Pass new values into `LiveCollectorConfig`.

- [ ] **Step 4: Run CLI tests to verify GREEN**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/cli.py tests/test_cli.py
git commit -m "feat: add always-on collector cli flags"
```

---

### Task 2: Continuous Current-And-Next Collector

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`
- Test: `/Users/goon/polymarket/tests/ingestion/test_live_collector_config.py`

- [ ] **Step 1: Write failing config tests**

Create tests:

```python
from pathlib import Path

from polymarket_engine.ingestion.live_collector import LiveCollectorConfig


def test_live_collector_defaults_to_current_and_next_windows() -> None:
    config = LiveCollectorConfig(
        assets=("BTC", "ETH"),
        duration_seconds=10,
        raw_root=Path("data/raw"),
        duckdb_path=Path("data/db/polymarket.duckdb"),
    )

    assert config.windows_to_track == 2
    assert config.clob_snapshot_interval_seconds == 1.0
    assert config.market_refresh_interval_seconds == 30.0


def test_live_collector_allows_forever_duration() -> None:
    config = LiveCollectorConfig(
        assets=("BTC", "ETH"),
        duration_seconds=None,
        raw_root=Path("data/raw"),
        duckdb_path=Path("data/db/polymarket.duckdb"),
    )

    assert config.duration_seconds is None
```

- [ ] **Step 2: Run config tests to verify RED**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_live_collector_config.py -v
```

Expected: FAIL because `duration_seconds=None`, `windows_to_track`, and `market_refresh_interval_seconds` are not supported yet.

- [ ] **Step 3: Implement config and concurrent loops**

Change `LiveCollectorConfig`:

```python
duration_seconds: int | None
windows_to_track: int = 2
clob_snapshot_interval_seconds: float = 1.0
market_refresh_interval_seconds: float = 30.0
```

Replace the sequential collector body with four concurrent loops:

```python
market_tokens: dict[str, MarketToken] = {}

async def market_loop() -> None:
    while should_continue():
        markets = await fetch_crypto_5m_markets(..., windows_ahead=config.windows_to_track)
        source_errors.update(register_market_rules(config.duckdb_path, markets))
        market_tokens.clear()
        market_tokens.update({token.token_id: token for market in markets for token in extract_market_tokens(market)})
        record each market snapshot
        await sleep_until_next_refresh()

async def clob_loop() -> None:
    while should_continue():
        for token in tuple(market_tokens.values()):
            fetch book and record `clob_book_event`
        await asyncio.sleep(config.clob_snapshot_interval_seconds)

async def coinbase_loop() -> None:
    keep the Coinbase WebSocket open until deadline or forever, reconnecting with backoff.

async def rtds_loop() -> None:
    keep the Polymarket RTDS WebSocket open until deadline or forever, reconnecting with backoff.
```

Rules:
- `--duration` exits after the finite deadline.
- `--forever` runs until interrupted.
- No source loop should stop the whole collector after one source error.
- Final `flush_all()` must still run when finite mode exits.

- [ ] **Step 4: Run collector unit tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_live_collector.py tests/ingestion/test_live_collector_config.py tests/ingestion/test_contract_discovery.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/ingestion/live_collector.py tests/ingestion/test_live_collector_config.py
git commit -m "feat: collect current and next contracts continuously"
```

---

### Task 3: Terminal Monitor

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/monitor.py`
- Modify: `/Users/goon/polymarket/src/polymarket_engine/cli.py`
- Test: `/Users/goon/polymarket/tests/test_monitor.py`

- [ ] **Step 1: Write failing monitor tests**

Create tests:

```python
from datetime import datetime, timezone
from pathlib import Path

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.monitor import fetch_monitor_snapshot, render_monitor
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_monitor_snapshot_reads_latest_prices(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    ts = datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)
    store.insert_price_tick(PriceObservation("coinbase_advanced_ws", "BTC-USD", ts, ts, 73500.0))
    store.insert_price_tick(PriceObservation("polymarket_rtds_chainlink", "BTC/USD", ts, ts, 73501.0))

    snapshot = fetch_monitor_snapshot(db_path, limit=4)

    assert ("coinbase_advanced_ws", "BTC-USD") in snapshot.prices
    assert ("polymarket_rtds_chainlink", "BTC/USD") in snapshot.prices


def test_render_monitor_outputs_read_only_status(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    output = render_monitor(fetch_monitor_snapshot(db_path, limit=4))

    assert "Polymarket Engine Monitor" in output
    assert "READ ONLY" in output
```

- [ ] **Step 2: Run monitor tests to verify RED**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/test_monitor.py -v
```

Expected: FAIL because `polymarket_engine.monitor` does not exist.

- [ ] **Step 3: Implement monitor module**

Create:

```python
@dataclass(frozen=True)
class MonitorSnapshot:
    generated_at: datetime
    prices: dict[tuple[str, str], float]
    orderbooks: tuple[dict[str, object], ...]
    contracts: tuple[dict[str, object], ...]
    ingest_counts: tuple[dict[str, object], ...]


def fetch_monitor_snapshot(duckdb_path: Path, limit: int = 8) -> MonitorSnapshot:
    # query latest rows from core.price_ticks, core.orderbook_snapshots, core.contracts, ops.ingest_files


def render_monitor(snapshot: MonitorSnapshot) -> str:
    # return compact ANSI-safe text table with READ ONLY header


async def run_monitor(duckdb_path: Path, refresh_seconds: float, limit: int) -> int:
    while True:
        print("\\033[2J\\033[H" + render_monitor(fetch_monitor_snapshot(duckdb_path, limit)))
        await asyncio.sleep(refresh_seconds)
```

- [ ] **Step 4: Wire monitor command**

In `cli.py`:

```python
if args.command == "monitor":
    from polymarket_engine.monitor import run_monitor

    return await run_monitor(args.duckdb_path, args.refresh, args.limit)
```

- [ ] **Step 5: Run monitor tests to verify GREEN**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/test_monitor.py tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/monitor.py src/polymarket_engine/cli.py tests/test_monitor.py tests/test_cli.py
git commit -m "feat: add read-only terminal monitor"
```

---

### Task 4: Docs And Live Smoke Verification

**Files:**
- Modify: `/Users/goon/polymarket/docs/BINARY_CONTRACT_ENGINE_PLAN.md`

- [ ] **Step 1: Update plan wording**

Add a short note to the collection section:

```markdown
The first live collector should track only the current and next BTC/ETH 5-minute contracts. This keeps the order-book set small: BTC current, BTC next, ETH current, and ETH next, each with UP and DOWN sides. Broader contract discovery can be added later after the first live replay path is stable.
```

- [ ] **Step 2: Run full verification**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest
uv run ruff check .
uv run mypy src
```

Expected:
- pytest passes
- ruff passes
- mypy passes

- [ ] **Step 3: Run finite live smoke**

Run:

```bash
cd /Users/goon/polymarket
uv run polymarket-engine collect --assets BTC,ETH --duration 12 --windows-to-track 2 --snapshot-interval 1 --raw-root data/raw --duckdb-path data/db/polymarket.duckdb --max-batch-size 50
```

Expected:
- exits after about 12 seconds
- `source_errors` is `{}` or only a clearly non-fatal source-specific transient error
- normalized rows are written to DuckDB

- [ ] **Step 4: Run monitor smoke**

Run:

```bash
cd /Users/goon/polymarket
timeout 3 uv run polymarket-engine monitor --duckdb-path data/db/polymarket.duckdb --refresh 1
```

Expected:
- monitor prints `Polymarket Engine Monitor`
- monitor prints `READ ONLY`
- monitor exits because `timeout` kills it

- [ ] **Step 5: Commit docs and verification fixes**

```bash
git add docs/BINARY_CONTRACT_ENGINE_PLAN.md
git commit -m "docs: document current and next collection scope"
```

