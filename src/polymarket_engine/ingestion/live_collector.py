from __future__ import annotations

import asyncio
import json
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
    extract_market_tokens,
    fetch_crypto_5m_markets,
)
from polymarket_engine.ingestion.polymarket_clob import clob_book_event
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
    max_batch_size: int = 100
    flush_after_seconds: float = 5.0
    require_archive_sentinel: bool = False
    windows_to_track: int = 2
    clob_snapshot_interval_seconds: float = 1.0
    market_refresh_interval_seconds: float = 30.0
    rtds_stale_after_ms: int = 5000
    coinbase_stale_after_ms: int = 2000

    def __post_init__(self) -> None:
        if self.duration_seconds is not None and self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive or None")
        if self.windows_to_track <= 0:
            raise ValueError("windows_to_track must be positive")
        if self.clob_snapshot_interval_seconds <= 0:
            raise ValueError("clob_snapshot_interval_seconds must be positive")
        if self.market_refresh_interval_seconds <= 0:
            raise ValueError("market_refresh_interval_seconds must be positive")
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
    rtds_events_written = 0

    def register_result(result: RawWriteResult) -> None:
        _register_file(store, config.raw_root, result)

    def record_event(event: CollectorEvent) -> None:
        nonlocal events_written, files_written
        events_written += 1
        try:
            _write_normalized_event(store, event)
        except Exception as exc:
            source_errors[f"normalized:{event.source_key}:{event.stream_key}"] = (
                f"{type(exc).__name__}: {exc}"
            )
        result = writer.add(event)
        if result is not None:
            register_result(result)
            files_written += 1

    def flush_due() -> None:
        nonlocal files_written
        result = writer.maybe_flush()
        if result is not None:
            register_result(result)
            files_written += 1

    async def market_loop() -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            while _should_continue(deadline):
                try:
                    markets = await fetch_crypto_5m_markets(
                        client=client,
                        base_url="https://gamma-api.polymarket.com",
                        now=datetime.now(timezone.utc),
                        assets=config.assets,
                        windows_ahead=config.windows_to_track,
                    )
                    source_errors.update(register_market_rules(config.duckdb_path, markets))
                    tokens = tuple(
                        token for market in markets for token in extract_market_tokens(market)
                    )
                    market_tokens.clear()
                    market_tokens.update({token.token_id: token for token in tokens})
                    for market in markets:
                        observed = datetime.now(timezone.utc)
                        record_event(
                            CollectorEvent(
                                source_key="polymarket_markets",
                                stream_key="crypto_5m_markets_snapshot",
                                symbol=str(market["slug"]),
                                event_ts=observed,
                                observed_ts=observed,
                                payload=dict(market),
                            )
                        )
                    flush_due()
                except Exception as exc:
                    source_errors["polymarket_markets"] = f"{type(exc).__name__}: {exc}"
                await _sleep_for(config.market_refresh_interval_seconds, deadline)

    async def clob_loop() -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            while _should_continue(deadline):
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
                        record_event(clob_book_event(response.json(), token, observed))
                    except Exception as exc:
                        source_errors[f"polymarket_clob:{token.token_id}"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                flush_due()
                await _sleep_for(config.clob_snapshot_interval_seconds, deadline)

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
                            flush_due()
                            continue
                        observed = datetime.now(timezone.utc)
                        for event in coinbase_ticker_events(json.loads(raw), observed):
                            record_event(event)
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
                ) as ws:
                    await ws.send(json.dumps(build_rtds_subscriptions(config.assets)))
                    heartbeat_task = asyncio.create_task(_send_rtds_heartbeats(ws))
                    rtds_attempt = 0
                    try:
                        while _should_continue(deadline):
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                            except asyncio.TimeoutError:
                                flush_due()
                                continue
                            if not raw:
                                continue
                            observed = datetime.now(timezone.utc)
                            for event in rtds_price_events(json.loads(raw), observed):
                                record_event(event)
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
        await asyncio.gather(market_loop(), clob_loop(), coinbase_loop(), rtds_loop())
    finally:
        for result in writer.flush_all():
            register_result(result)
            files_written += 1

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
    if event.source_key != "polymarket_clob" or event.stream_key != "orderbook_snapshot":
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
