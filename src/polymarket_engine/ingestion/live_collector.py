from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
import websockets

from polymarket_engine.domain.contracts import contract_specs_from_rule
from polymarket_engine.domain.contract_rules import (
    ContractRuleRejected,
    parse_polymarket_crypto_updown_rule,
)
from polymarket_engine.domain.market_state import OrderBookObservation, PriceObservation
from polymarket_engine.ingestion.coinbase_ws import (
    build_coinbase_ticker_subscription,
    coinbase_ticker_events,
)
from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.ingestion.contract_discovery import (
    MarketToken,
    fetch_crypto_updown_markets,
)
from polymarket_engine.ingestion.polymarket_clob import clob_book_event
from polymarket_engine.ingestion.polymarket_clob_ws import (
    CLOB_MARKET_WS_URL,
    build_market_ws_assets_update_message,
    build_market_ws_subscribe_message,
    clob_market_ws_events,
)
from polymarket_engine.ingestion.polymarket_rtds import (
    build_rtds_subscriptions,
    rtds_heartbeat_message,
    rtds_price_events,
)
from polymarket_engine.ingestion.reconnect import compute_reconnect_delay
from polymarket_engine.storage.buffered_writer import BufferedRawEventWriter
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore
from polymarket_engine.storage.raw_writer import RawWriteResult
from polymarket_engine.storage.recovery import cleanup_orphaned_tmp, ensure_archive_sentinel

RTDS_HEARTBEAT_SECONDS = 5.0


class _WebSocketSender(Protocol):
    async def send(self, message: str) -> None: ...


@dataclass(frozen=True)
class LiveCollectorConfig:
    assets: tuple[str, ...]
    duration_seconds: int | None
    raw_root: Path
    duckdb_path: Path
    status_path: Path = Path("data/live/status.json")
    max_batch_size: int = 100
    flush_after_seconds: float = 5.0
    require_archive_sentinel: bool = False
    windows_to_track: int = 2
    intervals: tuple[str, ...] = ("5m", "15m")
    enable_clob_websocket: bool = True
    clob_snapshot_interval_seconds: float = 1.0
    clob_rest_backup_interval_seconds: float = 15.0
    market_refresh_interval_seconds: float = 30.0
    display_timezone: str = "America/Chicago"
    orderbook_stale_after_ms: int = 10_000
    rtds_stale_after_ms: int = 5000
    rtds_idle_reconnect_seconds: float = 15.0
    coinbase_stale_after_ms: int = 2000

    def __post_init__(self) -> None:
        if self.duration_seconds is not None and self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive or None")
        if self.windows_to_track <= 0:
            raise ValueError("windows_to_track must be positive")
        unsupported_intervals = set(self.intervals) - {"5m", "15m"}
        if unsupported_intervals:
            raise ValueError(f"unsupported intervals: {sorted(unsupported_intervals)}")
        if self.clob_snapshot_interval_seconds <= 0:
            raise ValueError("clob_snapshot_interval_seconds must be positive")
        if self.clob_rest_backup_interval_seconds <= 0:
            raise ValueError("clob_rest_backup_interval_seconds must be positive")
        if self.market_refresh_interval_seconds <= 0:
            raise ValueError("market_refresh_interval_seconds must be positive")
        if self.rtds_idle_reconnect_seconds <= 0:
            raise ValueError("rtds_idle_reconnect_seconds must be positive")
        if self.display_timezone != "America/Chicago":
            raise ValueError("display_timezone must be America/Chicago for this project")
        if self.max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")


@dataclass(frozen=True)
class LiveCollectorResult:
    events_written: int
    files_written: int
    source_errors: dict[str, str] = field(default_factory=dict)


def collection_deadline(config: LiveCollectorConfig) -> float | None:
    if config.duration_seconds is None:
        return None
    return datetime.now(timezone.utc).timestamp() + config.duration_seconds


def _remaining_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - datetime.now(timezone.utc).timestamp())


def _should_continue(deadline: float | None) -> bool:
    remaining = _remaining_seconds(deadline)
    return remaining is None or remaining > 0


async def _sleep_for(seconds: float, deadline: float | None) -> None:
    remaining = _remaining_seconds(deadline)
    if remaining is None:
        await asyncio.sleep(seconds)
    elif remaining > 0:
        await asyncio.sleep(min(seconds, remaining))


def _freshness_row(
    *,
    generated_at: datetime,
    source_key: str,
    symbol: str,
    observed_ts: object | None,
    stale_after_ms: int,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    base: dict[str, object] = {
        "source_key": source_key,
        "symbol": symbol,
        "observed_ts": None,
        "age_ms": None,
        "stale_after_ms": stale_after_ms,
        "stale": True,
        "missing": True,
    }
    if extra:
        base.update(extra)
    if observed_ts is None:
        return base
    observed = datetime.fromisoformat(str(observed_ts))
    age_ms = max(0, int((generated_at - observed).total_seconds() * 1000))
    base.update(
        {
            "observed_ts": observed.isoformat(),
            "age_ms": age_ms,
            "stale": age_ms > stale_after_ms,
            "missing": False,
        }
    )
    return base


def _is_rtds_socket_idle(
    *,
    last_message_monotonic: float,
    now_monotonic: float,
    idle_reconnect_seconds: float,
) -> bool:
    return now_monotonic - last_message_monotonic >= idle_reconnect_seconds


def _price_freshness_rows(
    *,
    latest_prices: dict[str, dict[str, object]],
    assets: tuple[str, ...],
    generated_at: datetime,
    coinbase_stale_after_ms: int,
    rtds_stale_after_ms: int,
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for asset in assets:
        checks = (
            ("coinbase_advanced_ws", f"{asset}-USD", coinbase_stale_after_ms),
            ("polymarket_rtds_chainlink", f"{asset}/USD", rtds_stale_after_ms),
        )
        for source_key, symbol, stale_after_ms in checks:
            latest = latest_prices.get(f"{source_key}:{symbol}")
            rows.append(
                _freshness_row(
                    generated_at=generated_at,
                    source_key=source_key,
                    symbol=symbol,
                    observed_ts=None if latest is None else latest.get("observed_ts"),
                    stale_after_ms=stale_after_ms,
                )
            )
    return tuple(rows)


def _orderbook_freshness_rows(
    *,
    latest_contracts: dict[str, dict[str, object]],
    latest_orderbooks_by_source: dict[str, dict[str, object]],
    generated_at: datetime,
    stale_after_ms: int,
    acceptable_source_keys: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for contract in latest_contracts.values():
        token_id = str(contract["token_id"])
        candidates = tuple(
            latest
            for source_key in acceptable_source_keys
            if (latest := latest_orderbooks_by_source.get(f"{source_key}:{token_id}"))
            is not None
        )
        latest = (
            max(candidates, key=lambda row: str(row.get("observed_ts", "")))
            if candidates
            else None
        )
        rows.append(
            _freshness_row(
                generated_at=generated_at,
                source_key=(
                    "|".join(acceptable_source_keys)
                    if latest is None
                    else str(latest["source_key"])
                ),
                symbol=token_id,
                observed_ts=None if latest is None else latest.get("observed_ts"),
                stale_after_ms=stale_after_ms,
                extra={
                    "contract_id": str(contract["contract_id"]),
                    "token_id": token_id,
                    "asset": str(contract["asset"]),
                    "side": str(contract["side"]),
                },
            )
        )
    return tuple(rows)


def _prune_expired_contract_state(
    *,
    now: datetime,
    latest_contracts: dict[str, dict[str, object]],
    market_tokens: dict[str, object],
    latest_orderbooks: dict[str, dict[str, object]],
    latest_orderbooks_by_source: dict[str, dict[str, object]],
) -> None:
    expired_token_ids: set[str] = set()
    for contract_id, contract in tuple(latest_contracts.items()):
        expiry_ts = datetime.fromisoformat(str(contract["expiry_ts"]))
        if expiry_ts <= now:
            expired_token_ids.add(str(contract["token_id"]))
            latest_contracts.pop(contract_id, None)

    for token_id in expired_token_ids:
        market_tokens.pop(token_id, None)
        latest_orderbooks.pop(token_id, None)

    for source_token_id in tuple(latest_orderbooks_by_source):
        token_id = source_token_id.split(":", 1)[1]
        if token_id in expired_token_ids:
            latest_orderbooks_by_source.pop(source_token_id, None)


def _source_disagreement_rows(
    *,
    latest_prices: dict[str, dict[str, object]],
    source_freshness: tuple[dict[str, object], ...],
    assets: tuple[str, ...],
) -> tuple[dict[str, object], ...]:
    freshness = {
        (str(row["source_key"]), str(row["symbol"])): row
        for row in source_freshness
    }
    rows: list[dict[str, object]] = []
    for asset in assets:
        normalized_asset = asset.upper()
        primary_key = ("polymarket_rtds_chainlink", f"{normalized_asset}/USD")
        proxy_key = ("coinbase_advanced_ws", f"{normalized_asset}-USD")
        primary = latest_prices.get(f"{primary_key[0]}:{primary_key[1]}")
        proxy = latest_prices.get(f"{proxy_key[0]}:{proxy_key[1]}")
        primary_price = None if primary is None else float(str(primary["price"]))
        proxy_price = None if proxy is None else float(str(proxy["price"]))
        block_reason = _source_disagreement_block_reason(
            primary_key=primary_key,
            proxy_key=proxy_key,
            primary_price=primary_price,
            freshness=freshness,
        )
        diff: float | None = None
        diff_bps: float | None = None
        if block_reason is None and primary_price is not None and proxy_price is not None:
            diff = proxy_price - primary_price
            diff_bps = abs(diff) / primary_price * 10_000
        rows.append(
            {
                "asset": normalized_asset,
                "primary_source_key": primary_key[0],
                "primary_symbol": primary_key[1],
                "primary_price": primary_price,
                "proxy_source_key": proxy_key[0],
                "proxy_symbol": proxy_key[1],
                "proxy_price": proxy_price,
                "diff": diff,
                "diff_bps": diff_bps,
                "usable": block_reason is None,
                "block_reason": block_reason,
            }
        )
    return tuple(rows)


def _source_disagreement_block_reason(
    *,
    primary_key: tuple[str, str],
    proxy_key: tuple[str, str],
    primary_price: float | None,
    freshness: dict[tuple[str, str], dict[str, object]],
) -> str | None:
    primary_freshness = freshness.get(primary_key)
    proxy_freshness = freshness.get(proxy_key)
    if primary_price is None or primary_freshness is None or primary_freshness.get("missing"):
        return "missing_reference_source"
    if primary_price <= 0:
        return "invalid_reference_price"
    if primary_freshness.get("stale"):
        return "stale_reference_source"
    if proxy_freshness is None or proxy_freshness.get("missing"):
        return "missing_proxy_source"
    if proxy_freshness.get("stale"):
        return "stale_proxy_source"
    return None


def _register_file(store: DuckDbIngestStore, raw_root: Path, result: RawWriteResult) -> None:
    relative_parts = result.path.relative_to(raw_root).parts
    source_key = relative_parts[0]
    stream_key = relative_parts[1]
    partition_ts = result.first_event_ts.astimezone(timezone.utc)
    store.register_ingest_file(
        file_id=result.file_id,
        source_key=source_key,
        stream_key=stream_key,
        partition_date=partition_ts.date().isoformat(),
        partition_hour=partition_ts.hour,
        path=str(result.path),
        sha256=result.sha256,
        row_count=result.row_count,
        first_event_ts=result.first_event_ts,
        last_event_ts=result.last_event_ts,
    )


def register_market_rules(
    duckdb_path: Path,
    markets: tuple[dict[str, Any], ...],
) -> dict[str, str]:
    store = DuckDbIngestStore(duckdb_path)
    store.apply_schema()
    source_errors: dict[str, str] = {}
    for market in markets:
        slug = str(market.get("slug", "unknown"))
        try:
            rule = parse_polymarket_crypto_updown_rule(market)
            store.upsert_contract_rule(rule)
            for contract in contract_specs_from_rule(rule):
                store.upsert_contract_spec(contract)
        except ContractRuleRejected as exc:
            source_errors[f"contract_rule:{slug}"] = str(exc)
    return source_errors


async def run_live_collection(config: LiveCollectorConfig) -> LiveCollectorResult:
    store = DuckDbIngestStore(config.duckdb_path)
    store.apply_schema()
    ensure_archive_sentinel(config.raw_root)
    cleanup_orphaned_tmp(config.raw_root)
    writer = BufferedRawEventWriter(
        raw_root=config.raw_root,
        max_batch_size=config.max_batch_size,
        flush_after_seconds=config.flush_after_seconds,
        require_archive_sentinel=True,
    )
    source_errors: dict[str, str] = {}
    events_written = 0
    files_written = 0
    deadline = collection_deadline(config)
    market_tokens: dict[str, Any] = {}
    token_update_queue: asyncio.Queue[tuple[MarketToken, ...]] = asyncio.Queue(maxsize=1)
    rtds_events_written = 0
    write_lock = asyncio.Lock()
    latest_prices: dict[str, dict[str, object]] = {}
    latest_orderbooks: dict[str, dict[str, object]] = {}
    latest_orderbooks_by_source: dict[str, dict[str, object]] = {}
    latest_contracts: dict[str, dict[str, object]] = {}
    last_status_write = 0.0

    def register_result(result: RawWriteResult) -> None:
        _register_file(store, config.raw_root, result)

    def update_status_from_markets(markets: tuple[dict[str, Any], ...]) -> tuple[MarketToken, ...]:
        accepted_tokens: list[MarketToken] = []
        active_token_ids: set[str] = set()
        latest_contracts.clear()
        for market in markets:
            try:
                rule = parse_polymarket_crypto_updown_rule(market)
                contracts = contract_specs_from_rule(rule)
            except ContractRuleRejected:
                continue
            for contract in contracts:
                latest_contracts[contract.contract_id] = {
                    "contract_id": contract.contract_id,
                    "asset": contract.asset,
                    "side": contract.side,
                    "token_id": contract.token_id,
                    "threshold_type": contract.threshold_type,
                    "settlement_symbol": contract.settlement_symbol,
                    "start_ts": contract.start_ts.isoformat(),
                    "expiry_ts": contract.expiry_ts.isoformat(),
                }
                if contract.token_id not in active_token_ids:
                    accepted_tokens.append(
                        MarketToken(
                            slug=contract.slug,
                            outcome=contract.side,
                            token_id=contract.token_id,
                        )
                    )
                    active_token_ids.add(contract.token_id)
        for token_id in tuple(latest_orderbooks):
            if token_id not in active_token_ids:
                latest_orderbooks.pop(token_id, None)
        for source_token_id in tuple(latest_orderbooks_by_source):
            token_id = source_token_id.split(":", 1)[1]
            if token_id not in active_token_ids:
                latest_orderbooks_by_source.pop(source_token_id, None)
        return tuple(accepted_tokens)

    def publish_token_update(tokens: tuple[MarketToken, ...]) -> None:
        while not token_update_queue.empty():
            token_update_queue.get_nowait()
            token_update_queue.task_done()
        token_update_queue.put_nowait(tokens)

    def update_status_from_event(event: CollectorEvent) -> None:
        price_tick = _price_observation_from_event(event)
        if price_tick is not None:
            latest_prices[f"{price_tick.source_key}:{price_tick.symbol}"] = {
                "source_key": price_tick.source_key,
                "symbol": price_tick.symbol,
                "observed_ts": price_tick.observed_ts.isoformat(),
                "price": price_tick.price,
            }
            return
        orderbook = _orderbook_observation_from_event(event)
        if orderbook is not None:
            source_row: dict[str, object] = {
                "source_key": event.source_key,
                "venue": orderbook.venue,
                "contract_id": orderbook.contract_id,
                "token_id": orderbook.token_id,
                "observed_ts": orderbook.observed_ts.isoformat(),
                "best_bid": orderbook.best_bid,
                "best_ask": orderbook.best_ask,
                "spread": orderbook.spread,
                "bid_size_top": orderbook.bid_size_top,
                "ask_size_top": orderbook.ask_size_top,
            }
            latest_orderbooks_by_source[f"{event.source_key}:{orderbook.token_id}"] = (
                source_row
            )
            latest_orderbooks[orderbook.token_id] = {
                "source_key": event.source_key,
                "venue": orderbook.venue,
                "contract_id": orderbook.contract_id,
                "token_id": orderbook.token_id,
                "observed_ts": orderbook.observed_ts.isoformat(),
                "best_bid": orderbook.best_bid,
                "best_ask": orderbook.best_ask,
                "spread": orderbook.spread,
                "bid_size_top": orderbook.bid_size_top,
                "ask_size_top": orderbook.ask_size_top,
            }

    def write_status(force: bool = False) -> None:
        nonlocal last_status_write
        now = time.monotonic()
        if not force and now - last_status_write < 1.0:
            return
        last_status_write = now
        config.status_path.parent.mkdir(parents=True, exist_ok=True)
        generated_now = datetime.now(timezone.utc)
        _prune_expired_contract_state(
            now=generated_now,
            latest_contracts=latest_contracts,
            market_tokens=market_tokens,
            latest_orderbooks=latest_orderbooks,
            latest_orderbooks_by_source=latest_orderbooks_by_source,
        )
        generated_at = generated_now.isoformat()
        try:
            normalized_health = store.normalized_table_health()
        except Exception as exc:
            source_errors["normalized_health"] = f"{type(exc).__name__}: {exc}"
            normalized_health = ()
        source_freshness = _price_freshness_rows(
            latest_prices=latest_prices,
            assets=config.assets,
            generated_at=generated_now,
            coinbase_stale_after_ms=config.coinbase_stale_after_ms,
            rtds_stale_after_ms=config.rtds_stale_after_ms,
        )
        status = {
            "generated_at": generated_at,
            "prices": sorted(latest_prices.values(), key=lambda row: str(row["source_key"])),
            "source_freshness": list(source_freshness),
            "source_disagreements": list(
                _source_disagreement_rows(
                    latest_prices=latest_prices,
                    source_freshness=source_freshness,
                    assets=config.assets,
                )
            ),
            "orderbooks": sorted(
                latest_orderbooks.values(),
                key=lambda row: str(row["observed_ts"]),
                reverse=True,
            ),
            "contracts": sorted(
                latest_contracts.values(),
                key=lambda row: (str(row["expiry_ts"]), str(row["asset"]), str(row["side"])),
                reverse=True,
            ),
            "ingest_counts": [
                {
                    "source_key": "collector",
                    "stream_key": "events_total",
                    "files": files_written,
                    "rows": events_written,
                    "last_event_ts": generated_at,
                }
            ],
            "normalized_health": list(normalized_health),
            "orderbook_freshness": list(
                _orderbook_freshness_rows(
                    latest_contracts=latest_contracts,
                    latest_orderbooks_by_source=latest_orderbooks_by_source,
                    generated_at=generated_now,
                    stale_after_ms=config.orderbook_stale_after_ms,
                    acceptable_source_keys=(
                        ("polymarket_market_ws", "polymarket_clob")
                        if config.enable_clob_websocket
                        else ("polymarket_clob",)
                    ),
                )
            ),
            "source_errors": dict(source_errors),
        }
        tmp_path = config.status_path.with_suffix(f"{config.status_path.suffix}.tmp")
        tmp_path.write_text(json.dumps(status, sort_keys=True), encoding="utf-8")
        tmp_path.replace(config.status_path)

    async def record_event(event: CollectorEvent) -> None:
        nonlocal events_written, files_written
        async with write_lock:
            events_written += 1
            update_status_from_event(event)
            source_errors.pop(event.source_key, None)
            source_errors.pop(f"{event.source_key}:{event.stream_key}", None)
            try:
                _write_normalized_event(store, event)
                source_errors.pop(f"normalized:{event.source_key}:{event.stream_key}", None)
            except Exception as exc:
                source_errors[f"normalized:{event.source_key}:{event.stream_key}"] = (
                    f"{type(exc).__name__}: {exc}"
                )
            result = writer.add(event)
            if result is not None:
                register_result(result)
                files_written += 1
            write_status()

    async def flush_due() -> None:
        nonlocal files_written
        async with write_lock:
            result = writer.maybe_flush()
            if result is not None:
                register_result(result)
                files_written += 1

    async def market_loop() -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            while _should_continue(deadline):
                try:
                    markets = await fetch_crypto_updown_markets(
                        client=client,
                        base_url="https://gamma-api.polymarket.com",
                        now=datetime.now(timezone.utc),
                        assets=config.assets,
                        intervals=config.intervals,
                        windows_ahead=config.windows_to_track,
                    )
                    async with write_lock:
                        source_errors.update(register_market_rules(config.duckdb_path, markets))
                    tokens = update_status_from_markets(markets)
                    market_tokens.clear()
                    market_tokens.update({token.token_id: token for token in tokens})
                    publish_token_update(tokens)
                    write_status(force=True)
                    for market in markets:
                        observed = datetime.now(timezone.utc)
                        await record_event(
                            CollectorEvent(
                                source_key="polymarket_markets",
                                stream_key="crypto_updown_markets_snapshot",
                                symbol=str(market["slug"]),
                                event_ts=observed,
                                observed_ts=observed,
                                payload=dict(market),
                            )
                        )
                    await flush_due()
                except Exception as exc:
                    source_errors["polymarket_markets"] = f"{type(exc).__name__}: {exc}"
                await _sleep_for(config.market_refresh_interval_seconds, deadline)

    async def clob_loop() -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            while _should_continue(deadline):
                _prune_expired_contract_state(
                    now=datetime.now(timezone.utc),
                    latest_contracts=latest_contracts,
                    market_tokens=market_tokens,
                    latest_orderbooks=latest_orderbooks,
                    latest_orderbooks_by_source=latest_orderbooks_by_source,
                )
                for token in tuple(market_tokens.values()):
                    if not _should_continue(deadline):
                        break
                    try:
                        observed = datetime.now(timezone.utc)
                        response = await client.get(
                            "https://clob.polymarket.com/book",
                            params={"token_id": token.token_id},
                        )
                        response.raise_for_status()
                        await record_event(clob_book_event(response.json(), token, observed))
                        source_errors.pop(f"polymarket_clob:{token.token_id}", None)
                    except Exception as exc:
                        source_errors[f"polymarket_clob:{token.token_id}"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                await flush_due()
                sleep_seconds = (
                    config.clob_rest_backup_interval_seconds
                    if config.enable_clob_websocket
                    else config.clob_snapshot_interval_seconds
                )
                await _sleep_for(sleep_seconds, deadline)

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
                async with websockets.connect(
                    CLOB_MARKET_WS_URL,
                    open_timeout=10,
                    ping_interval=None,
                ) as ws:
                    source_errors.pop("polymarket_market_ws", None)
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
                        for task in pending:
                            with suppress(asyncio.CancelledError):
                                await task
                        if update_task in done:
                            tokens = update_task.result()
                            token_update_queue.task_done()
                            next_tokens = {token.token_id: token for token in tokens}
                            added = tuple(
                                token_id
                                for token_id in next_tokens
                                if token_id not in active_tokens
                            )
                            removed = tuple(
                                token_id
                                for token_id in active_tokens
                                if token_id not in next_tokens
                            )
                            active_tokens = next_tokens
                            if removed:
                                await ws.send(
                                    json.dumps(
                                        build_market_ws_assets_update_message(
                                            removed,
                                            operation="unsubscribe",
                                        )
                                    )
                                )
                            if added:
                                await ws.send(
                                    json.dumps(
                                        build_market_ws_assets_update_message(
                                            added,
                                            operation="subscribe",
                                        )
                                    )
                                )
                        if recv_task in done:
                            raw = recv_task.result()
                            if raw:
                                observed = datetime.now(timezone.utc)
                                for event in clob_market_ws_events(
                                    raw,
                                    active_tokens,
                                    observed,
                                ):
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

    async def coinbase_loop() -> None:
        product_ids = tuple(f"{asset}-USD" for asset in config.assets)
        coinbase_attempt = 0
        while _should_continue(deadline):
            try:
                async with websockets.connect(
                    "wss://advanced-trade-ws.coinbase.com",
                    open_timeout=10,
                ) as ws:
                    await ws.send(json.dumps(build_coinbase_ticker_subscription(product_ids)))
                    coinbase_attempt = 0
                    while _should_continue(deadline):
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            await flush_due()
                            continue
                        observed = datetime.now(timezone.utc)
                        for event in coinbase_ticker_events(json.loads(raw), observed):
                            await record_event(event)
            except Exception as exc:
                source_errors["coinbase_advanced_ws"] = f"{type(exc).__name__}: {exc}"
                if not _should_continue(deadline):
                    break
                delay = compute_reconnect_delay(coinbase_attempt)
                coinbase_attempt += 1
                await _sleep_for(delay, deadline)

    async def rtds_loop() -> None:
        nonlocal rtds_events_written
        rtds_attempt = 0
        while _should_continue(deadline):
            try:
                async with websockets.connect(
                    "wss://ws-live-data.polymarket.com",
                    open_timeout=10,
                    ping_interval=None,
                ) as ws:
                    source_errors.pop("polymarket_rtds", None)
                    await ws.send(json.dumps(build_rtds_subscriptions(config.assets)))
                    heartbeat_task = asyncio.create_task(_send_rtds_heartbeats(ws))
                    rtds_attempt = 0
                    last_message_monotonic = time.monotonic()
                    try:
                        while _should_continue(deadline):
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                            except asyncio.TimeoutError:
                                if _is_rtds_socket_idle(
                                    last_message_monotonic=last_message_monotonic,
                                    now_monotonic=time.monotonic(),
                                    idle_reconnect_seconds=config.rtds_idle_reconnect_seconds,
                                ):
                                    raise TimeoutError(
                                        "RTDS socket idle; reconnecting to avoid stale Chainlink"
                                    )
                                await flush_due()
                                continue
                            if not raw:
                                continue
                            last_message_monotonic = time.monotonic()
                            observed = datetime.now(timezone.utc)
                            for event in rtds_price_events(
                                json.loads(raw),
                                observed,
                                assets=config.assets,
                            ):
                                await record_event(event)
                                rtds_events_written += 1
                    finally:
                        heartbeat_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await heartbeat_task
            except Exception as exc:
                source_errors["polymarket_rtds"] = f"{type(exc).__name__}: {exc}"
                if not _should_continue(deadline):
                    break
                delay = compute_reconnect_delay(rtds_attempt)
                rtds_attempt += 1
                await _sleep_for(delay, deadline)

    try:
        loops = [market_loop(), clob_loop(), coinbase_loop(), rtds_loop()]
        if config.enable_clob_websocket:
            loops.append(clob_market_ws_loop())
        await asyncio.gather(*loops)
    finally:
        for result in writer.flush_all():
            register_result(result)
            files_written += 1
        write_status(force=True)

    if rtds_events_written == 0 and "polymarket_rtds" not in source_errors:
        source_errors["polymarket_rtds"] = "NoMessages: RTDS emitted no price updates"

    return LiveCollectorResult(
        events_written=events_written,
        files_written=files_written,
        source_errors=source_errors,
    )


def _write_normalized_event(store: DuckDbIngestStore, event: CollectorEvent) -> None:
    price_tick = _price_observation_from_event(event)
    if price_tick is not None:
        store.insert_price_tick(price_tick)
        return
    orderbook = _orderbook_observation_from_event(event)
    if orderbook is not None:
        store.insert_orderbook_snapshot(orderbook)


def _price_observation_from_event(event: CollectorEvent) -> PriceObservation | None:
    if event.stream_key not in {"price_update", "ticker"}:
        return None
    raw_price = event.payload.get("value", event.payload.get("price"))
    if raw_price is None:
        return None
    return PriceObservation(
        source_key=event.source_key,
        symbol=event.symbol,
        event_ts=event.event_ts,
        observed_ts=event.observed_ts,
        price=float(str(raw_price)),
        bid=_optional_float(event.payload.get("best_bid")),
        ask=_optional_float(event.payload.get("best_ask")),
        sequence=_optional_sequence(event.payload.get("sequence")),
    )


def _orderbook_observation_from_event(event: CollectorEvent) -> OrderBookObservation | None:
    if event.source_key not in {"polymarket_clob", "polymarket_market_ws"}:
        return None
    if event.stream_key not in {"orderbook_snapshot", "top_of_book"}:
        return None
    return OrderBookObservation(
        venue="polymarket",
        contract_id=str(event.payload["contract_id"]),
        token_id=str(event.payload["token_id"]),
        event_ts=event.event_ts,
        observed_ts=event.observed_ts,
        best_bid=_optional_float(event.payload.get("best_bid")),
        best_ask=_optional_float(event.payload.get("best_ask")),
        bid_size_top=_optional_float(event.payload.get("bid_size_top")),
        ask_size_top=_optional_float(event.payload.get("ask_size_top")),
        spread=_optional_float(event.payload.get("spread")),
        depth_json=str(event.payload["depth_json"]),
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(str(value))


def _optional_sequence(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


async def _send_rtds_heartbeats(ws: _WebSocketSender) -> None:
    while True:
        await asyncio.sleep(RTDS_HEARTBEAT_SECONDS)
        await ws.send(rtds_heartbeat_message())
