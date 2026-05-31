from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
import websockets

from polymarket_engine.domain.contract_rules import (
    ContractRuleRejected,
    parse_polymarket_crypto_updown_rule,
)
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
    duration_seconds: int
    raw_root: Path
    duckdb_path: Path
    max_batch_size: int = 100
    flush_after_seconds: float = 5.0
    require_archive_sentinel: bool = False
    contract_windows_ahead: int = 3
    clob_snapshot_interval_seconds: int = 5
    rtds_stale_after_ms: int = 5000
    coinbase_stale_after_ms: int = 2000


@dataclass(frozen=True)
class LiveCollectorResult:
    events_written: int
    files_written: int
    source_errors: dict[str, str] = field(default_factory=dict)


async def run_fake_collection(
    config: LiveCollectorConfig,
    events: tuple[CollectorEvent, ...],
) -> LiveCollectorResult:
    store = DuckDbIngestStore(config.duckdb_path)
    store.apply_schema()
    writer = BufferedRawEventWriter(
        raw_root=config.raw_root,
        max_batch_size=config.max_batch_size,
        flush_after_seconds=config.flush_after_seconds,
        require_archive_sentinel=config.require_archive_sentinel,
    )

    results: list[RawWriteResult] = []
    for event in events:
        result = writer.add(event)
        if result is not None:
            results.append(result)
    results.extend(writer.flush_all())

    for result in results:
        _register_file(store, config.raw_root, result)

    return LiveCollectorResult(
        events_written=sum(result.row_count for result in results),
        files_written=len(results),
    )


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
            store.upsert_contract_rule(parse_polymarket_crypto_updown_rule(market))
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

    def register_result(result: RawWriteResult) -> None:
        _register_file(store, config.raw_root, result)

    def record_event(event: CollectorEvent) -> None:
        nonlocal events_written, files_written
        events_written += 1
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

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            markets = await fetch_crypto_5m_markets(
                client=client,
                base_url="https://gamma-api.polymarket.com",
                now=datetime.now(timezone.utc),
                assets=config.assets,
                windows_ahead=config.contract_windows_ahead,
            )
            source_errors.update(register_market_rules(config.duckdb_path, markets))
            tokens = tuple(token for market in markets for token in extract_market_tokens(market))
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

            for token in tokens:
                observed = datetime.now(timezone.utc)
                response = await client.get(
                    "https://clob.polymarket.com/book",
                    params={"token_id": token.token_id},
                )
                response.raise_for_status()
                record_event(clob_book_event(response.json(), token, observed))
        except Exception as exc:
            source_errors["polymarket"] = f"{type(exc).__name__}: {exc}"

    product_ids = tuple(f"{asset}-USD" for asset in config.assets)
    coinbase_deadline = datetime.now(timezone.utc).timestamp() + config.duration_seconds
    coinbase_attempt = 0
    while datetime.now(timezone.utc).timestamp() < coinbase_deadline:
        try:
            async with websockets.connect(
                "wss://advanced-trade-ws.coinbase.com",
                open_timeout=10,
            ) as ws:
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
    rtds_events_written = 0
    while datetime.now(timezone.utc).timestamp() < rtds_deadline:
        try:
            async with websockets.connect(
                "wss://ws-live-data.polymarket.com",
                open_timeout=10,
            ) as ws:
                await ws.send(json.dumps(build_rtds_subscriptions(config.assets)))
                heartbeat_task = asyncio.create_task(_send_rtds_heartbeats(ws))
                rtds_attempt = 0
                try:
                    while datetime.now(timezone.utc).timestamp() < rtds_deadline:
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
                break
        except Exception as exc:
            source_errors["polymarket_rtds"] = f"{type(exc).__name__}: {exc}"
            remaining = rtds_deadline - datetime.now(timezone.utc).timestamp()
            if remaining <= 0:
                break
            delay = min(compute_reconnect_delay(rtds_attempt), remaining)
            rtds_attempt += 1
            await asyncio.sleep(delay)
    if rtds_events_written == 0 and "polymarket_rtds" not in source_errors:
        source_errors["polymarket_rtds"] = "NoMessages: RTDS emitted no price updates"

    for result in writer.flush_all():
        register_result(result)
        files_written += 1

    return LiveCollectorResult(
        events_written=events_written,
        files_written=files_written,
        source_errors=source_errors,
    )


async def _send_rtds_heartbeats(ws: _WebSocketSender) -> None:
    while True:
        await asyncio.sleep(RTDS_HEARTBEAT_SECONDS)
        await ws.send(rtds_heartbeat_message())
