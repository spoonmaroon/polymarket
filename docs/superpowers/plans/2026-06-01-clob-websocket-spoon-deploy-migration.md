# CLOB WebSocket And Spoon Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace slow serial CLOB REST polling with live Polymarket market WebSocket order-book updates, add a GEX-style GitHub/CI/deploy-to-spoon workflow, and migrate 24/7 read-only collection from the Mac to spoon without losing data.

**Architecture:** Keep the collector read-only. The market WebSocket becomes the primary executable-order-book feed, while REST `/book` polling stays as a backup snapshot path. Spoon runs the collector through Docker Compose from a checked-out repo and persistent host-mounted data directories; GitHub CI proves code quality before `main` is deployed. Raw data remains hot for 90 days, then future compaction keeps replay-safe research tables permanently.

**Tech Stack:** Python 3.11+, `uv`, `websockets`, `httpx`, DuckDB, Parquet/Zstandard, Docker Compose, GitHub Actions, system shell scripts.

---

## Current Context

- Repo root: `/Users/goon/polymarket`.
- Current collector command tracks BTC/ETH, 5m/15m, current and next windows.
- Current CLOB path fetches `https://clob.polymarket.com/book` serially for each token. With BTC/ETH x 5m/15m x current/next x UP/DOWN, this can be 16 HTTP requests per loop, so `--snapshot-interval 1` does not mean each token updates once per second.
- Polymarket's market WebSocket endpoint is `wss://ws-subscriptions-clob.polymarket.com/ws/market`. It emits `book`, `price_change`, `last_trade_price`, `tick_size_change`, and, with `custom_feature_enabled: true`, `best_bid_ask`, `new_market`, and `market_resolved`.
- Project time policy:
  - Store and compare all timestamps in UTC.
  - Display operator time in `America/Chicago`.
  - Preserve venue rule text exactly.
  - If a contract page states ET, parse it from the rule and store UTC plus the raw text.
- Retention policy:
  - Raw event data stays hot for 90 days.
  - Contract rules, rule hashes, decision states, labels, incident logs, and retention manifests are kept forever.
  - After 90 days, raw events are compacted into replay-safe second-level research tables before any deletion is enabled.

## File Structure

Create:
- `/Users/goon/polymarket/src/polymarket_engine/ingestion/polymarket_clob_ws.py`  
  WebSocket subscription builder, message parser, and event conversion for Polymarket market channel.
- `/Users/goon/polymarket/tests/ingestion/test_polymarket_clob_ws.py`  
  Unit tests for subscription shape, ping/pong handling, `book`, `price_change`, and `best_bid_ask`.
- `/Users/goon/polymarket/src/polymarket_engine/storage/retention.py`  
  Named retention policy constants so raw-hot-90-day behavior is not hardcoded magic.
- `/Users/goon/polymarket/tests/storage/test_retention.py`  
  Unit tests for retention policy values.
- `/Users/goon/polymarket/.github/workflows/tests.yml`  
  GEX-style CI: install `uv`, sync dependencies, run `ruff`, `mypy`, and `pytest`.
- `/Users/goon/polymarket/deploy/collector/Dockerfile`  
  Runtime image for the read-only collector.
- `/Users/goon/polymarket/deploy/collector/docker-compose.yml`  
  Spoon collector service with persistent host mounts and restart policy.
- `/Users/goon/polymarket/deploy/collector/.env.example`  
  Non-secret deployment defaults.
- `/Users/goon/polymarket/scripts/check_collector_status.py`  
  Smoke check for status freshness after deploy.
- `/Users/goon/polymarket/scripts/deploy.sh`  
  Spoon-side GEX-style auto-deploy script.
- `/Users/goon/polymarket/scripts/migrate_mac_data_to_spoon.sh`  
  One-time migration script that stops the Mac collector and rsyncs raw/DB/live/log data to spoon.
- `/Users/goon/polymarket/docs/SPOON_DEPLOYMENT.md`  
  Operator runbook.

Modify:
- `/Users/goon/polymarket/src/polymarket_engine/venues/polymarket.py`  
  Normalize `best_bid_ask` and compact top-of-book messages.
- `/Users/goon/polymarket/src/polymarket_engine/ingestion/polymarket_clob.py`  
  Keep REST book snapshots as backup; do not remove existing functions.
- `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`  
  Add CLOB market WebSocket loop, token subscription update queue, source error clearing, and prioritized REST backup polling.
- `/Users/goon/polymarket/src/polymarket_engine/cli.py`  
  Add flags for enabling/disabling CLOB WebSocket, REST backup interval, and display timezone.
- `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`  
  Use retention constants when registering raw ingest files.
- `/Users/goon/polymarket/docs/PART_TWO_LIVE_COLLECTORS.md`  
  Document market WebSocket as primary CLOB path, REST as backup, and 90-day hot retention.
- `/Users/goon/polymarket/ops/systemd/polymarket-live-collector.service`  
  Mark as legacy/manual fallback once Docker Compose is the spoon runtime path.

---

### Task 1: Add Polymarket CLOB Market WebSocket Parser

**Files:**
- Create: `/Users/goon/polymarket/tests/ingestion/test_polymarket_clob_ws.py`
- Create: `/Users/goon/polymarket/src/polymarket_engine/ingestion/polymarket_clob_ws.py`
- Modify: `/Users/goon/polymarket/src/polymarket_engine/venues/polymarket.py`

- [ ] **Step 1: Write failing parser tests**

Add `/Users/goon/polymarket/tests/ingestion/test_polymarket_clob_ws.py`:

```python
from datetime import datetime, timezone

from polymarket_engine.ingestion.contract_discovery import MarketToken
from polymarket_engine.ingestion.polymarket_clob_ws import (
    CLOB_MARKET_WS_URL,
    build_market_ws_subscribe_message,
    clob_market_ws_events,
)


def test_build_market_ws_subscribe_message_requests_best_bid_ask() -> None:
    message = build_market_ws_subscribe_message(("111", "222"))

    assert CLOB_MARKET_WS_URL == "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    assert message == {
        "assets_ids": ["111", "222"],
        "type": "market",
        "custom_feature_enabled": True,
    }


def test_market_ws_book_becomes_orderbook_snapshot_event() -> None:
    observed = datetime(2026, 6, 1, 10, 55, 1, tzinfo=timezone.utc)
    token = MarketToken(slug="btc-updown-5m-1780301700", outcome="UP", token_id="111")
    events = clob_market_ws_events(
        {
            "event_type": "book",
            "asset_id": "111",
            "market": "0xabc",
            "timestamp": "1780301701000",
            "bids": [{"price": "0.48", "size": "20"}, {"price": "0.50", "size": "8"}],
            "asks": [{"price": "0.52", "size": "9"}, {"price": "0.54", "size": "30"}],
            "hash": "0xbookhash",
        },
        {"111": token},
        observed,
    )

    assert len(events) == 1
    event = events[0]
    assert event.source_key == "polymarket_market_ws"
    assert event.stream_key == "orderbook_snapshot"
    assert event.symbol == "btc-updown-5m-1780301700:UP"
    assert event.event_ts.isoformat() == "2026-06-01T10:55:01+00:00"
    assert event.observed_ts == observed
    assert event.payload["token_id"] == "111"
    assert event.payload["contract_id"] == "0xabc"
    assert event.payload["best_bid"] == 0.50
    assert event.payload["best_ask"] == 0.52
    assert round(float(event.payload["spread"]), 2) == 0.02
    assert event.payload["event_type"] == "book"
    assert '"bids"' in str(event.payload["depth_json"])


def test_market_ws_best_bid_ask_becomes_top_of_book_event() -> None:
    observed = datetime(2026, 6, 1, 10, 55, 2, tzinfo=timezone.utc)
    token = MarketToken(slug="eth-updown-5m-1780301700", outcome="DOWN", token_id="222")
    events = clob_market_ws_events(
        {
            "event_type": "best_bid_ask",
            "asset_id": "222",
            "market": "0xdef",
            "timestamp": "1780301702000",
            "best_bid": "0.41",
            "best_ask": "0.42",
        },
        {"222": token},
        observed,
    )

    assert len(events) == 1
    event = events[0]
    assert event.source_key == "polymarket_market_ws"
    assert event.stream_key == "top_of_book"
    assert event.payload["best_bid"] == 0.41
    assert event.payload["best_ask"] == 0.42
    assert round(float(event.payload["spread"]), 2) == 0.01
    assert event.payload["depth_json"] == '{"source":"best_bid_ask"}'


def test_market_ws_price_change_with_best_prices_updates_top_of_book() -> None:
    observed = datetime(2026, 6, 1, 10, 55, 3, tzinfo=timezone.utc)
    token = MarketToken(slug="btc-updown-5m-1780301700", outcome="DOWN", token_id="333")
    events = clob_market_ws_events(
        {
            "event_type": "price_change",
            "market": "0xghi",
            "timestamp": "1780301703000",
            "price_changes": [
                {
                    "asset_id": "333",
                    "price": "0.39",
                    "size": "200",
                    "side": "BUY",
                    "best_bid": "0.39",
                    "best_ask": "0.40",
                }
            ],
        },
        {"333": token},
        observed,
    )

    assert len(events) == 1
    event = events[0]
    assert event.source_key == "polymarket_market_ws"
    assert event.stream_key == "top_of_book"
    assert event.payload["price"] == 0.39
    assert event.payload["size"] == 200.0
    assert event.payload["side"] == "BUY"
    assert event.payload["best_bid"] == 0.39
    assert event.payload["best_ask"] == 0.40


def test_market_ws_ignores_unknown_asset_and_pong() -> None:
    observed = datetime(2026, 6, 1, 10, 55, 4, tzinfo=timezone.utc)

    assert clob_market_ws_events("PONG", {}, observed) == ()
    assert (
        clob_market_ws_events(
            {
                "event_type": "best_bid_ask",
                "asset_id": "missing",
                "market": "0x0",
                "timestamp": "1780301704000",
                "best_bid": "0.50",
                "best_ask": "0.51",
            },
            {},
            observed,
        )
        == ()
    )
```

- [ ] **Step 2: Run parser tests to verify they fail**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_polymarket_clob_ws.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'polymarket_engine.ingestion.polymarket_clob_ws'`.

- [ ] **Step 3: Add parser implementation**

Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/polymarket_clob_ws.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.ingestion.contract_discovery import MarketToken
from polymarket_engine.venues.polymarket import (
    normalize_orderbook_snapshot,
    normalize_price_changes,
)

CLOB_MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def build_market_ws_subscribe_message(asset_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "assets_ids": list(asset_ids),
        "type": "market",
        "custom_feature_enabled": True,
    }


def clob_market_ws_events(
    message: object,
    tokens_by_id: dict[str, MarketToken],
    observed_ts: datetime,
) -> tuple[CollectorEvent, ...]:
    if isinstance(message, str):
        if message.upper() in {"PONG", "PING"}:
            return ()
        parsed = json.loads(message)
    else:
        parsed = message
    if not isinstance(parsed, dict):
        return ()
    event_type = str(parsed.get("event_type", ""))
    if event_type == "book":
        return _book_events(parsed, tokens_by_id, observed_ts)
    if event_type == "best_bid_ask":
        return _best_bid_ask_events(parsed, tokens_by_id, observed_ts)
    if event_type == "price_change":
        return _price_change_events(parsed, tokens_by_id, observed_ts)
    return ()


def _book_events(
    message: dict[str, Any],
    tokens_by_id: dict[str, MarketToken],
    observed_ts: datetime,
) -> tuple[CollectorEvent, ...]:
    token_id = str(message.get("asset_id", ""))
    token = tokens_by_id.get(token_id)
    if token is None:
        return ()
    snapshot = normalize_orderbook_snapshot(message)
    return (
        CollectorEvent(
            source_key="polymarket_market_ws",
            stream_key="orderbook_snapshot",
            symbol=f"{token.slug}:{token.outcome}",
            event_ts=snapshot.event_ts,
            observed_ts=observed_ts,
            payload={
                **message,
                "event_type": "book",
                "contract_slug": token.slug,
                "outcome": token.outcome,
                "token_id": token.token_id,
                "contract_id": snapshot.contract_id,
                "best_bid": snapshot.best_bid,
                "best_ask": snapshot.best_ask,
                "bid_size_top": snapshot.bid_size_top,
                "ask_size_top": snapshot.ask_size_top,
                "spread": snapshot.spread,
                "depth_json": snapshot.depth_json,
            },
        ),
    )


def _best_bid_ask_events(
    message: dict[str, Any],
    tokens_by_id: dict[str, MarketToken],
    observed_ts: datetime,
) -> tuple[CollectorEvent, ...]:
    token_id = str(message.get("asset_id", ""))
    token = tokens_by_id.get(token_id)
    if token is None:
        return ()
    event_ts = _timestamp_ms(message["timestamp"])
    best_bid = _optional_float(message.get("best_bid"))
    best_ask = _optional_float(message.get("best_ask"))
    return (
        CollectorEvent(
            source_key="polymarket_market_ws",
            stream_key="top_of_book",
            symbol=f"{token.slug}:{token.outcome}",
            event_ts=event_ts,
            observed_ts=observed_ts,
            payload={
                **message,
                "event_type": "best_bid_ask",
                "contract_slug": token.slug,
                "outcome": token.outcome,
                "token_id": token.token_id,
                "contract_id": str(message["market"]),
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_size_top": None,
                "ask_size_top": None,
                "spread": _spread(best_bid, best_ask),
                "depth_json": '{"source":"best_bid_ask"}',
            },
        ),
    )


def _price_change_events(
    message: dict[str, Any],
    tokens_by_id: dict[str, MarketToken],
    observed_ts: datetime,
) -> tuple[CollectorEvent, ...]:
    events: list[CollectorEvent] = []
    for change in normalize_price_changes(message):
        token = tokens_by_id.get(change.token_id)
        if token is None:
            continue
        events.append(
            CollectorEvent(
                source_key="polymarket_market_ws",
                stream_key="top_of_book",
                symbol=f"{token.slug}:{token.outcome}",
                event_ts=change.event_ts,
                observed_ts=observed_ts,
                payload={
                    "event_type": "price_change",
                    "contract_slug": token.slug,
                    "outcome": token.outcome,
                    "contract_id": change.contract_id,
                    "token_id": change.token_id,
                    "side": change.side,
                    "price": change.price,
                    "size": change.size,
                    "best_bid": change.best_bid,
                    "best_ask": change.best_ask,
                    "bid_size_top": None,
                    "ask_size_top": None,
                    "spread": change.spread,
                    "depth_json": '{"source":"price_change"}',
                },
            )
        )
    return tuple(events)


def _timestamp_ms(value: object) -> datetime:
    return datetime.fromtimestamp(int(str(value)) / 1000, tz=timezone.utc)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(str(value))


def _spread(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return ask - bid
```

- [ ] **Step 4: Run parser tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_polymarket_clob_ws.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/polymarket_clob_ws.py tests/ingestion/test_polymarket_clob_ws.py
git commit -m "feat: parse polymarket clob market websocket"
```

---

### Task 2: Wire CLOB Market WebSocket Into The Live Collector

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`
- Modify: `/Users/goon/polymarket/src/polymarket_engine/cli.py`
- Modify: `/Users/goon/polymarket/tests/ingestion/test_live_collector_config.py`
- Modify: `/Users/goon/polymarket/tests/test_cli.py`

- [ ] **Step 1: Write failing config and CLI tests**

Append to `/Users/goon/polymarket/tests/ingestion/test_live_collector_config.py`:

```python
def test_live_collector_enables_market_ws_and_rest_backup_by_default() -> None:
    config = LiveCollectorConfig(
        assets=("BTC", "ETH"),
        duration_seconds=10,
        raw_root=Path("data/raw"),
        duckdb_path=Path("data/db/polymarket.duckdb"),
    )

    assert config.enable_clob_websocket is True
    assert config.clob_rest_backup_interval_seconds == 15.0
    assert config.display_timezone == "America/Chicago"
```

Append to `/Users/goon/polymarket/tests/test_cli.py`:

```python
def test_parse_collect_market_ws_flags() -> None:
    args = parse_args(
        [
            "collect",
            "--assets",
            "BTC,ETH",
            "--duration",
            "60",
            "--disable-clob-websocket",
            "--clob-rest-backup-interval",
            "20",
            "--display-timezone",
            "America/Chicago",
        ]
    )

    assert args.enable_clob_websocket is False
    assert args.clob_rest_backup_interval == 20.0
    assert args.display_timezone == "America/Chicago"
```

- [ ] **Step 2: Run targeted tests to verify they fail**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_live_collector_config.py::test_live_collector_enables_market_ws_and_rest_backup_by_default tests/test_cli.py::test_parse_collect_market_ws_flags -q
```

Expected: FAIL because `LiveCollectorConfig.enable_clob_websocket`, `clob_rest_backup_interval_seconds`, and CLI flags do not exist.

- [ ] **Step 3: Add config and CLI fields**

In `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`, extend `LiveCollectorConfig`:

```python
    enable_clob_websocket: bool = True
    clob_rest_backup_interval_seconds: float = 15.0
    display_timezone: str = "America/Chicago"
```

In `__post_init__`, add:

```python
        if self.clob_rest_backup_interval_seconds <= 0:
            raise ValueError("clob_rest_backup_interval_seconds must be positive")
        if self.display_timezone != "America/Chicago":
            raise ValueError("display_timezone must be America/Chicago for this project")
```

In `/Users/goon/polymarket/src/polymarket_engine/cli.py`, add collect flags:

```python
    collect.add_argument("--disable-clob-websocket", dest="enable_clob_websocket", action="store_false")
    collect.set_defaults(enable_clob_websocket=True)
    collect.add_argument("--clob-rest-backup-interval", type=float, default=15.0)
    collect.add_argument("--display-timezone", default="America/Chicago")
```

Pass them into `LiveCollectorConfig`:

```python
        enable_clob_websocket=args.enable_clob_websocket,
        clob_rest_backup_interval_seconds=args.clob_rest_backup_interval,
        display_timezone=args.display_timezone,
```

- [ ] **Step 4: Run config and CLI tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_live_collector_config.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 5: Add WebSocket token update queue and primary market WebSocket loop**

In `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`, add imports:

```python
from polymarket_engine.ingestion.polymarket_clob_ws import (
    CLOB_MARKET_WS_URL,
    build_market_ws_subscribe_message,
    clob_market_ws_events,
)
```

Inside `run_live_collection`, after `market_tokens: dict[str, Any] = {}`, add:

```python
    token_update_queue: asyncio.Queue[tuple[MarketToken, ...]] = asyncio.Queue(maxsize=1)
```

Add helper inside `run_live_collection`:

```python
    def publish_token_update(tokens: tuple[MarketToken, ...]) -> None:
        while not token_update_queue.empty():
            token_update_queue.get_nowait()
            token_update_queue.task_done()
        token_update_queue.put_nowait(tokens)
```

After `market_tokens.update({token.token_id: token for token in tokens})` in `market_loop`, add:

```python
                    publish_token_update(tokens)
```

Add source-error clearing to `record_event` after `update_status_from_event(event)`:

```python
            source_errors.pop(event.source_key, None)
            source_errors.pop(f"{event.source_key}:{event.stream_key}", None)
```

Add this new loop inside `run_live_collection`:

```python
    async def clob_market_ws_loop() -> None:
        clob_ws_attempt = 0
        active_tokens: dict[str, MarketToken] = {}
        while _should_continue(deadline):
            if not active_tokens:
                try:
                    tokens = await asyncio.wait_for(token_update_queue.get(), timeout=5)
                    active_tokens = {token.token_id: token for token in tokens}
                    token_update_queue.task_done()
                except asyncio.TimeoutError:
                    await flush_due()
                    continue
            try:
                async with websockets.connect(CLOB_MARKET_WS_URL, open_timeout=10) as ws:
                    await ws.send(
                        json.dumps(build_market_ws_subscribe_message(tuple(active_tokens)))
                    )
                    clob_ws_attempt = 0
                    while _should_continue(deadline):
                        recv_task = asyncio.create_task(ws.recv())
                        update_task = asyncio.create_task(token_update_queue.get())
                        done, pending = await asyncio.wait(
                            {recv_task, update_task},
                            timeout=5,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in pending:
                            task.cancel()
                        if update_task in done:
                            tokens = update_task.result()
                            token_update_queue.task_done()
                            active_tokens = {token.token_id: token for token in tokens}
                            await ws.send(
                                json.dumps(build_market_ws_subscribe_message(tuple(active_tokens)))
                            )
                        if recv_task in done:
                            raw = recv_task.result()
                            if not raw:
                                continue
                            observed = datetime.now(timezone.utc)
                            for event in clob_market_ws_events(raw, active_tokens, observed):
                                await record_event(event)
                        if not done:
                            await ws.send("PING")
                            await flush_due()
            except Exception as exc:
                source_errors["polymarket_market_ws"] = f"{type(exc).__name__}: {exc}"
                if not _should_continue(deadline):
                    break
                delay = compute_reconnect_delay(clob_ws_attempt)
                clob_ws_attempt += 1
                await _sleep_for(delay, deadline)
```

Replace the final gather call:

```python
        loops = [market_loop(), clob_loop(), coinbase_loop(), rtds_loop()]
        if config.enable_clob_websocket:
            loops.append(clob_market_ws_loop())
        await asyncio.gather(*loops)
```

- [ ] **Step 6: Convert WebSocket top-of-book events into normalized order-book rows**

In `_orderbook_observation_from_event`, replace the current guard:

```python
    if event.source_key != "polymarket_clob" or event.stream_key != "orderbook_snapshot":
        return None
```

with:

```python
    if event.source_key not in {"polymarket_clob", "polymarket_market_ws"}:
        return None
    if event.stream_key not in {"orderbook_snapshot", "top_of_book"}:
        return None
```

This lets WebSocket `book`, `price_change`, and `best_bid_ask` refresh the same normalized `core.orderbook_snapshots` table and the same monitor display.

- [ ] **Step 7: Make REST polling a backup loop, not the main freshness path**

In `clob_loop`, change the sleep interval to use the backup interval when the WebSocket is enabled:

```python
                sleep_seconds = (
                    config.clob_rest_backup_interval_seconds
                    if config.enable_clob_websocket
                    else config.clob_snapshot_interval_seconds
                )
                await _sleep_for(sleep_seconds, deadline)
```

Keep REST polling enabled because it gives full depth snapshots and protects against WebSocket gaps.

- [ ] **Step 8: Run targeted tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_live_collector_config.py tests/test_cli.py tests/ingestion/test_polymarket_clob_ws.py tests/ingestion/test_polymarket_clob.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/live_collector.py src/polymarket_engine/cli.py tests/ingestion/test_live_collector_config.py tests/test_cli.py
git commit -m "feat: stream clob order books over websocket"
```

---

### Task 3: Add Freshness Proof And Retention Policy Constants

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/storage/retention.py`
- Create: `/Users/goon/polymarket/tests/storage/test_retention.py`
- Modify: `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`
- Modify: `/Users/goon/polymarket/docs/PART_TWO_LIVE_COLLECTORS.md`

- [ ] **Step 1: Write failing retention tests**

Create `/Users/goon/polymarket/tests/storage/test_retention.py`:

```python
from polymarket_engine.storage.retention import RAW_HOT_RETENTION_DAYS, retention_manifest_class


def test_raw_hot_retention_is_90_days() -> None:
    assert RAW_HOT_RETENTION_DAYS == 90
    assert retention_manifest_class("raw") == "raw_hot_90d"
```

- [ ] **Step 2: Run retention test to verify it fails**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_retention.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Add retention constants**

Create `/Users/goon/polymarket/src/polymarket_engine/storage/retention.py`:

```python
from __future__ import annotations

RAW_HOT_RETENTION_DAYS = 90
RAW_HOT_RETENTION_CLASS = "raw_hot_90d"
COMPACT_RESEARCH_RETENTION_CLASS = "compact_research_forever"


def retention_manifest_class(kind: str) -> str:
    if kind == "raw":
        return RAW_HOT_RETENTION_CLASS
    if kind == "compact":
        return COMPACT_RESEARCH_RETENTION_CLASS
    raise ValueError(f"unsupported retention kind: {kind}")
```

- [ ] **Step 4: Use retention constants in DuckDB ingest registration**

In `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`, add:

```python
from polymarket_engine.storage.retention import RAW_HOT_RETENTION_DAYS, retention_manifest_class
```

Replace:

```python
                    f"{file_id}:raw_hot_90d",
```

with:

```python
                    f"{file_id}:{retention_manifest_class('raw')}",
```

Replace:

```python
                    "raw_hot_90d",
                    90,
```

with:

```python
                    retention_manifest_class("raw"),
                    RAW_HOT_RETENTION_DAYS,
```

- [ ] **Step 5: Update Part Two docs**

In `/Users/goon/polymarket/docs/PART_TWO_LIVE_COLLECTORS.md`, add this section after `## Safety`:

```markdown
## Retention Policy

Raw event data is retained hot for 90 days. Hot raw data includes Polymarket market snapshots, CLOB market WebSocket events, REST order-book backup snapshots, RTDS price updates, Coinbase price ticks, source errors, and raw collector payloads.

After 90 days, raw events should be compacted into replay-safe research tables before deletion is enabled. The compact layer should preserve 1-second price bars, 1-second top-of-book rows, source freshness, contract windows, rule hashes, decision states, and final labels. Automatic deletion remains disabled until replay tests prove compacted tables reproduce the same as-of state for sampled contracts.
```

- [ ] **Step 6: Run retention and normalized write tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_retention.py tests/storage/test_normalized_writes.py tests/storage/test_raw_writer.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/storage/retention.py src/polymarket_engine/storage/duckdb_store.py tests/storage/test_retention.py docs/PART_TWO_LIVE_COLLECTORS.md
git commit -m "docs: lock ninety day raw retention policy"
```

---

### Task 4: Add GEX-Style GitHub CI

**Files:**
- Create: `/Users/goon/polymarket/.github/workflows/tests.yml`

- [ ] **Step 1: Create workflow file**

Create `/Users/goon/polymarket/.github/workflows/tests.yml`:

```yaml
name: tests

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  UV_PYTHON: "3.12"
  UV_PYTHON_PREFERENCE: only-managed

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up uv
        uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync

      - name: Ruff
        run: uv run ruff check .

      - name: Mypy
        run: uv run mypy src tests

      - name: Pytest
        run: uv run pytest -q
```

- [ ] **Step 2: Verify workflow syntax locally by running equivalent commands**

Run:

```bash
cd /Users/goon/polymarket
uv sync
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```

Expected: all commands pass.

- [ ] **Step 3: Commit**

```bash
cd /Users/goon/polymarket
git add .github/workflows/tests.yml
git commit -m "ci: add test workflow"
```

---

### Task 5: Add Docker Collector Runtime For Spoon

**Files:**
- Create: `/Users/goon/polymarket/deploy/collector/Dockerfile`
- Create: `/Users/goon/polymarket/deploy/collector/docker-compose.yml`
- Create: `/Users/goon/polymarket/deploy/collector/.env.example`
- Create: `/Users/goon/polymarket/scripts/check_collector_status.py`
- Modify: `/Users/goon/polymarket/ops/systemd/polymarket-live-collector.service`

- [ ] **Step 1: Create Dockerfile**

Create `/Users/goon/polymarket/deploy/collector/Dockerfile`:

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

ENTRYPOINT ["polymarket-engine"]
CMD ["collect", "--assets", "BTC,ETH", "--intervals", "5m,15m", "--forever", "--windows-to-track", "2", "--raw-root", "/var/lib/polymarket/raw", "--duckdb-path", "/var/lib/polymarket/db/polymarket.duckdb", "--status-path", "/var/lib/polymarket/live/status.json", "--max-batch-size", "100", "--snapshot-interval", "1", "--clob-rest-backup-interval", "15", "--market-refresh-interval", "30", "--display-timezone", "America/Chicago"]
```

- [ ] **Step 2: Create Docker Compose file**

Create `/Users/goon/polymarket/deploy/collector/docker-compose.yml`:

```yaml
name: polymarket-collector

services:
  collector:
    build:
      context: ../..
      dockerfile: deploy/collector/Dockerfile
    restart: unless-stopped
    init: true
    environment:
      TZ: ${POLYMARKET_DISPLAY_TZ:-America/Chicago}
      POLYMARKET_MODE: ${POLYMARKET_MODE:-collect}
    command:
      - collect
      - --assets
      - ${POLYMARKET_ASSETS:-BTC,ETH}
      - --intervals
      - ${POLYMARKET_INTERVALS:-5m,15m}
      - --forever
      - --windows-to-track
      - ${POLYMARKET_WINDOWS_TO_TRACK:-2}
      - --raw-root
      - /var/lib/polymarket/raw
      - --duckdb-path
      - /var/lib/polymarket/db/polymarket.duckdb
      - --status-path
      - /var/lib/polymarket/live/status.json
      - --max-batch-size
      - ${POLYMARKET_MAX_BATCH_SIZE:-100}
      - --snapshot-interval
      - ${POLYMARKET_REST_SNAPSHOT_INTERVAL:-1}
      - --clob-rest-backup-interval
      - ${POLYMARKET_CLOB_REST_BACKUP_INTERVAL:-15}
      - --market-refresh-interval
      - ${POLYMARKET_MARKET_REFRESH_INTERVAL:-30}
      - --display-timezone
      - ${POLYMARKET_DISPLAY_TZ:-America/Chicago}
    volumes:
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/raw:/var/lib/polymarket/raw
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/db:/var/lib/polymarket/db
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/live:/var/lib/polymarket/live
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/logs:/var/lib/polymarket/logs
    tmpfs:
      - /tmp
```

- [ ] **Step 3: Create deployment env example**

Create `/Users/goon/polymarket/deploy/collector/.env.example`:

```dotenv
POLYMARKET_MODE=collect
POLYMARKET_ASSETS=BTC,ETH
POLYMARKET_INTERVALS=5m,15m
POLYMARKET_WINDOWS_TO_TRACK=2
POLYMARKET_DATA_DIR=/home/spoon/polymarket-data
POLYMARKET_DISPLAY_TZ=America/Chicago
POLYMARKET_MAX_BATCH_SIZE=100
POLYMARKET_REST_SNAPSHOT_INTERVAL=1
POLYMARKET_CLOB_REST_BACKUP_INTERVAL=15
POLYMARKET_MARKET_REFRESH_INTERVAL=30
```

- [ ] **Step 4: Create status smoke checker**

Create `/Users/goon/polymarket/scripts/check_collector_status.py`:

```python
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("--max-status-age-seconds", type=float, default=20.0)
    parser.add_argument("--max-price-age-ms", type=int, default=10_000)
    parser.add_argument("--max-orderbook-age-ms", type=int, default=10_000)
    args = parser.parse_args()

    payload = json.loads(args.status_path.read_text(encoding="utf-8"))
    generated_at = datetime.fromisoformat(str(payload["generated_at"]))
    now = datetime.now(timezone.utc)
    status_age = (now - generated_at.astimezone(timezone.utc)).total_seconds()
    if status_age > args.max_status_age_seconds:
        raise SystemExit(f"status file stale: age_seconds={status_age:.2f}")

    prices = payload.get("prices", [])
    if not prices:
        raise SystemExit("status has no price rows")
    orderbooks = payload.get("orderbooks", [])
    if not orderbooks:
        raise SystemExit("status has no orderbook rows")

    newest_price = max(datetime.fromisoformat(str(row["observed_ts"])) for row in prices)
    newest_book = max(datetime.fromisoformat(str(row["observed_ts"])) for row in orderbooks)
    price_age_ms = int((now - newest_price.astimezone(timezone.utc)).total_seconds() * 1000)
    book_age_ms = int((now - newest_book.astimezone(timezone.utc)).total_seconds() * 1000)
    if price_age_ms > args.max_price_age_ms:
        raise SystemExit(f"price rows stale: age_ms={price_age_ms}")
    if book_age_ms > args.max_orderbook_age_ms:
        raise SystemExit(f"orderbook rows stale: age_ms={book_age_ms}")

    print(
        {
            "ok": True,
            "status_age_seconds": round(status_age, 3),
            "price_age_ms": price_age_ms,
            "orderbook_age_ms": book_age_ms,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Mark systemd unit as fallback**

At the top of `/Users/goon/polymarket/ops/systemd/polymarket-live-collector.service`, under `[Unit]`, add:

```ini
# Fallback/manual runner. The preferred spoon runtime is deploy/collector/docker-compose.yml.
```

- [ ] **Step 6: Run Docker syntax and script smoke checks**

Run:

```bash
cd /Users/goon/polymarket
docker compose -f deploy/collector/docker-compose.yml config >/tmp/polymarket-compose-config.yml
python scripts/check_collector_status.py --help >/tmp/polymarket-status-help.txt
```

Expected: both commands exit 0.

- [ ] **Step 7: Commit**

```bash
cd /Users/goon/polymarket
git add deploy/collector/Dockerfile deploy/collector/docker-compose.yml deploy/collector/.env.example scripts/check_collector_status.py ops/systemd/polymarket-live-collector.service
git commit -m "ops: add docker collector runtime"
```

---

### Task 6: Add GEX-Style Spoon Deploy Script

**Files:**
- Create: `/Users/goon/polymarket/scripts/deploy.sh`
- Create: `/Users/goon/polymarket/docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Create spoon deploy script**

Create `/Users/goon/polymarket/scripts/deploy.sh`:

```bash
#!/usr/bin/env bash
set -u
set -o pipefail

export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO="${REPO:-$HOME/polymarket}"
DATA_DIR="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}"
COMPOSE_FILE="$REPO/deploy/collector/docker-compose.yml"
STATUS_PATH="$DATA_DIR/live/status.json"
LOCK_DIR="/tmp/polymarket-deploy.lock.d"
LOG_FILE="$REPO/logs/deploy.log"
DEPLOYED_MARKER="$HOME/.polymarket/last-deployed-sha"
STAMP="$(date -Iseconds)"
LOG() { echo "[$STAMP] $*" | tee -a "$LOG_FILE"; }

mkdir -p "$REPO/logs" "$DATA_DIR/raw" "$DATA_DIR/db" "$DATA_DIR/live" "$DATA_DIR/logs" "$(dirname "$DEPLOYED_MARKER")"
touch "$DATA_DIR/raw/.polymarket_archive_root"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  LOG "deploy already running"
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

cd "$REPO" || exit 1

git fetch --quiet origin main || { LOG "git fetch failed"; exit 1; }
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
if [ "$LOCAL" = "$REMOTE" ] && [ "${DEPLOY_FORCE:-0}" != "1" ]; then
  exit 0
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  LOG "working tree is dirty; refusing deploy"
  git status --porcelain | while read -r line; do LOG "  $line"; done
  exit 1
fi

LOG "deploying $REMOTE from $LOCAL"

if ! git pull --ff-only --quiet origin main; then
  LOG "git pull failed"
  exit 1
fi

if ! docker compose -f "$COMPOSE_FILE" up -d --build collector >> "$LOG_FILE" 2>&1; then
  LOG "docker compose failed"
  exit 1
fi

for _ in $(seq 1 30); do
  if python3 "$REPO/scripts/check_collector_status.py" \
    --status-path "$STATUS_PATH" \
    --max-status-age-seconds 30 \
    --max-price-age-ms 30000 \
    --max-orderbook-age-ms 30000 >> "$LOG_FILE" 2>&1; then
    echo "$REMOTE" > "$DEPLOYED_MARKER"
    LOG "deploy OK $REMOTE"
    exit 0
  fi
  sleep 2
done

LOG "collector smoke failed; leaving container logs in docker compose"
docker compose -f "$COMPOSE_FILE" logs --tail=80 collector >> "$LOG_FILE" 2>&1 || true
exit 1
```

- [ ] **Step 2: Make deploy script executable**

Run:

```bash
cd /Users/goon/polymarket
chmod +x scripts/deploy.sh
```

- [ ] **Step 3: Create spoon deployment runbook**

Create `/Users/goon/polymarket/docs/SPOON_DEPLOYMENT.md`:

```markdown
# Spoon Deployment Runbook

The collector runs read-only on spoon from `/home/spoon/polymarket`. Persistent data lives outside the repo at `/home/spoon/polymarket-data`.

## Time Policy

All stored timestamps are UTC. Operator displays use `America/Chicago`. Venue rule text is stored raw so ET wording from Polymarket can be audited later.

## One-Time Setup On Spoon

```bash
cd /home/spoon
git clone git@github.com:AnimeWeeb9000/polymarket.git polymarket
mkdir -p /home/spoon/polymarket-data/{raw,db,live,logs}
touch /home/spoon/polymarket-data/raw/.polymarket_archive_root
cd /home/spoon/polymarket
cp deploy/collector/.env.example deploy/collector/.env
docker compose -f deploy/collector/docker-compose.yml --env-file deploy/collector/.env up -d --build collector
python3 scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json
```

## Auto Deploy

Install a cron entry on spoon:

```cron
*/5 * * * * /home/spoon/polymarket/scripts/deploy.sh >> /home/spoon/polymarket/logs/deploy.cron.log 2>&1
```

The deploy script fetches `origin/main`, refuses dirty server worktrees, pulls fast-forward only, rebuilds the collector image, restarts the collector, and smoke-checks the status file.

## Retention

Raw data remains hot for 90 days. Do not enable deletion until compact replay tests prove 1-second compacted research tables reproduce the same as-of state for sampled contracts.

## Manual Health Checks

```bash
docker compose -f /home/spoon/polymarket/deploy/collector/docker-compose.yml ps
docker compose -f /home/spoon/polymarket/deploy/collector/docker-compose.yml logs --tail=100 collector
python3 /home/spoon/polymarket/scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json
du -sh /home/spoon/polymarket-data/*
```
```

- [ ] **Step 4: Verify shell syntax**

Run:

```bash
cd /Users/goon/polymarket
bash -n scripts/deploy.sh
```

Expected: exits 0.

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add scripts/deploy.sh docs/SPOON_DEPLOYMENT.md
git commit -m "ops: add spoon deploy workflow"
```

---

### Task 7: Add One-Time Mac-To-Spoon Data Migration Script

**Files:**
- Create: `/Users/goon/polymarket/scripts/migrate_mac_data_to_spoon.sh`
- Modify: `/Users/goon/polymarket/docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Create migration script**

Create `/Users/goon/polymarket/scripts/migrate_mac_data_to_spoon.sh`:

```bash
#!/usr/bin/env bash
set -u
set -o pipefail

LOCAL_REPO="${LOCAL_REPO:-/Users/goon/polymarket}"
REMOTE_HOST="${REMOTE_HOST:-spoon}"
REMOTE_DATA_DIR="${REMOTE_DATA_DIR:-/home/spoon/polymarket-data}"
PID_FILE="$LOCAL_REPO/logs/live-collector.pid"

if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE")"
  if ps -p "$pid" >/dev/null 2>&1; then
    echo "Stopping local collector pid=$pid"
    kill "$pid"
    for _ in $(seq 1 30); do
      if ! ps -p "$pid" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    if ps -p "$pid" >/dev/null 2>&1; then
      echo "Local collector did not stop within 30 seconds" >&2
      exit 1
    fi
  fi
fi

ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_DATA_DIR/raw' '$REMOTE_DATA_DIR/db' '$REMOTE_DATA_DIR/live' '$REMOTE_DATA_DIR/logs' && touch '$REMOTE_DATA_DIR/raw/.polymarket_archive_root'"

rsync -a --info=progress2 "$LOCAL_REPO/data/raw/" "$REMOTE_HOST:$REMOTE_DATA_DIR/raw/"
rsync -a --info=progress2 "$LOCAL_REPO/data/db/" "$REMOTE_HOST:$REMOTE_DATA_DIR/db/"
rsync -a --info=progress2 "$LOCAL_REPO/data/live/" "$REMOTE_HOST:$REMOTE_DATA_DIR/live/"
rsync -a --info=progress2 "$LOCAL_REPO/logs/" "$REMOTE_HOST:$REMOTE_DATA_DIR/logs/"

echo "Migration copied data to $REMOTE_HOST:$REMOTE_DATA_DIR"
echo "Next: run /home/spoon/polymarket/scripts/deploy.sh on spoon and verify status freshness."
```

- [ ] **Step 2: Make migration script executable**

Run:

```bash
cd /Users/goon/polymarket
chmod +x scripts/migrate_mac_data_to_spoon.sh
```

- [ ] **Step 3: Add migration section to runbook**

Append to `/Users/goon/polymarket/docs/SPOON_DEPLOYMENT.md`:

```markdown
## Mac-To-Spoon Migration

Run this only after the CLOB WebSocket collector passes local smoke checks and the spoon deploy workflow exists.

```bash
cd /Users/goon/polymarket
REMOTE_HOST=spoon REMOTE_DATA_DIR=/home/spoon/polymarket-data ./scripts/migrate_mac_data_to_spoon.sh
ssh spoon 'cd /home/spoon/polymarket && ./scripts/deploy.sh'
ssh spoon 'python3 /home/spoon/polymarket/scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json'
```

After spoon is fresh, keep the Mac collector stopped. The Mac can still run the read-only monitor against copied files, but it should not write to the same logical data stream while spoon is the active collector.
```

- [ ] **Step 4: Verify shell syntax**

Run:

```bash
cd /Users/goon/polymarket
bash -n scripts/migrate_mac_data_to_spoon.sh
```

Expected: exits 0.

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add scripts/migrate_mac_data_to_spoon.sh docs/SPOON_DEPLOYMENT.md
git commit -m "ops: add mac to spoon data migration script"
```

---

### Task 8: Local Smoke Test The WebSocket Collector Before Spoon Migration

**Files:**
- No code files required if previous tasks pass.
- Uses local temp directories under `/Users/goon/polymarket/data/tmp/clob_ws_smoke`.

- [ ] **Step 1: Run full local verification**

Run:

```bash
cd /Users/goon/polymarket
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```

Expected: all pass.

- [ ] **Step 2: Run a finite local live smoke with CLOB WebSocket enabled**

Run:

```bash
cd /Users/goon/polymarket
rm -rf data/tmp/clob_ws_smoke
mkdir -p data/tmp/clob_ws_smoke/raw
touch data/tmp/clob_ws_smoke/raw/.polymarket_archive_root
uv run polymarket-engine collect \
  --assets BTC,ETH \
  --intervals 5m,15m \
  --duration 45 \
  --windows-to-track 2 \
  --snapshot-interval 1 \
  --clob-rest-backup-interval 15 \
  --market-refresh-interval 15 \
  --raw-root data/tmp/clob_ws_smoke/raw \
  --duckdb-path data/tmp/clob_ws_smoke/db/polymarket.duckdb \
  --status-path data/tmp/clob_ws_smoke/live/status.json
```

Expected: command exits 0 and prints a result with `events_written > 0`, `files_written > 0`, and no fatal CLOB WebSocket error.

- [ ] **Step 3: Smoke-check status freshness**

Run:

```bash
cd /Users/goon/polymarket
python scripts/check_collector_status.py \
  --status-path data/tmp/clob_ws_smoke/live/status.json \
  --max-status-age-seconds 60 \
  --max-price-age-ms 60000 \
  --max-orderbook-age-ms 60000
```

Expected: exits 0 and prints `{"ok": true, ...}`.

- [ ] **Step 4: Verify market WebSocket rows reached raw and normalized storage**

Run:

```bash
cd /Users/goon/polymarket
uv run python - <<'PY'
import duckdb
from pathlib import Path

db = Path("data/tmp/clob_ws_smoke/db/polymarket.duckdb")
with duckdb.connect(str(db), read_only=True) as conn:
    files = conn.sql("""
        select source_key, stream_key, count(*) files, sum(row_count) rows
        from ops.ingest_files
        group by source_key, stream_key
        order by source_key, stream_key
    """).fetchall()
    books = conn.sql("select count(*) from core.orderbook_snapshots").fetchone()[0]
print({"files": files, "orderbook_snapshots": books})
assert any(row[0] == "polymarket_market_ws" for row in files)
assert books > 0
PY
```

Expected: exits 0 and printed `files` include `polymarket_market_ws`.

- [ ] **Step 5: Commit smoke docs if any command required correction**

If no files changed, skip this commit. If runbook commands changed:

```bash
cd /Users/goon/polymarket
git add docs/SPOON_DEPLOYMENT.md docs/PART_TWO_LIVE_COLLECTORS.md
git commit -m "docs: update clob websocket smoke steps"
```

---

### Task 9: Spoon Deploy And Migration Execution Gate

**Files:**
- No repository files should change during this task unless verification reveals a runbook bug.

- [ ] **Step 1: Push the branch and open/merge through GitHub**

Run:

```bash
cd /Users/goon/polymarket
git status --short
git push origin HEAD
```

Expected: branch pushes. Open a PR, wait for GitHub Actions to pass, then merge to `main`.

- [ ] **Step 2: Prepare spoon checkout**

Run:

```bash
ssh spoon 'test -d /home/spoon/polymarket || git clone git@github.com:AnimeWeeb9000/polymarket.git /home/spoon/polymarket'
ssh spoon 'mkdir -p /home/spoon/polymarket-data/{raw,db,live,logs} && touch /home/spoon/polymarket-data/raw/.polymarket_archive_root'
```

Expected: both commands exit 0.

- [ ] **Step 3: Migrate Mac data to spoon**

Run:

```bash
cd /Users/goon/polymarket
REMOTE_HOST=spoon REMOTE_DATA_DIR=/home/spoon/polymarket-data ./scripts/migrate_mac_data_to_spoon.sh
```

Expected: local collector is stopped if running, raw/db/live/log files are copied to spoon, and the script prints the next deploy instruction.

- [ ] **Step 4: Deploy on spoon**

Run:

```bash
ssh spoon 'cd /home/spoon/polymarket && ./scripts/deploy.sh'
```

Expected: deploy exits 0 and writes `/home/spoon/.polymarket/last-deployed-sha`.

- [ ] **Step 5: Verify spoon collector freshness**

Run:

```bash
ssh spoon 'python3 /home/spoon/polymarket/scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000'
ssh spoon 'docker compose -f /home/spoon/polymarket/deploy/collector/docker-compose.yml ps'
ssh spoon 'du -sh /home/spoon/polymarket-data/*'
```

Expected: status check exits 0, Docker shows collector running, and data directories have nonzero size.

- [ ] **Step 6: Confirm Mac collector is stopped**

Run:

```bash
cd /Users/goon/polymarket
test ! -f logs/live-collector.pid || ! ps -p "$(cat logs/live-collector.pid)" >/dev/null
```

Expected: exits 0.

---

## Self-Review Checklist

- Spec coverage:
  - CLOB WebSocket primary path: Tasks 1, 2, and 8.
  - REST order-book backup: Task 2.
  - GEX-style GitHub workflow: Task 4.
  - Spoon Docker deployment: Tasks 5 and 6.
  - Mac-to-spoon migration: Task 7 and Task 9.
  - 90-day hot raw policy: Task 3 and runbook sections.
  - UTC storage and `America/Chicago` display policy: Task 2 and runbook sections.
- No probability modeling is included in this slice.
- No live trading or order placement is included in this slice.
- No raw deletion is enabled in this slice.
