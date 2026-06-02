from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polymarket_engine.domain.market_state import OrderBookObservation, PriceObservation
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


STATE_MANAGER_SCHEMA_VERSION = "rust-live-probe-state-manager-v1"
RUST_JSONL_STREAMS = (
    ("polymarket_rtds_chainlink", "price_update"),
    ("polymarket_clob_market_ws", "best_bid_ask"),
)
STATE_SNAPSHOT_STREAMS = (
    ("polymarket_state_manager", "state_snapshot"),
)


@dataclass(frozen=True)
class RustEventNormalizeResult:
    path: Path
    file_id: str
    start_byte_offset: int
    end_byte_offset: int
    file_size_bytes: int
    rows_read: int
    price_ticks_written: int
    orderbooks_written: int


@dataclass(frozen=True)
class _TopOfBookRow:
    token_key: tuple[str, str]
    state_key: tuple[object, ...]
    contract_id: str
    token_id: str
    event_ts: datetime
    observed_ts: object
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class _PriceTickRow:
    symbol_key: tuple[str, str]
    state_key: tuple[object, ...]
    source_key: str
    symbol: str
    event_ts: datetime
    observed_ts: object
    price: float


@dataclass
class _EventTimeBounds:
    first: datetime | None = None
    last: datetime | None = None

    def record(self, event_ts: datetime) -> None:
        if self.first is None or event_ts < self.first:
            self.first = event_ts
        if self.last is None or event_ts > self.last:
            self.last = event_ts


def normalize_rust_event_tree(
    *,
    raw_root: Path,
    store: DuckDbIngestStore,
    include_state_snapshots: bool = False,
    reprocess_all: bool = False,
) -> tuple[RustEventNormalizeResult, ...]:
    paths: list[Path] = []
    streams = RUST_JSONL_STREAMS + (STATE_SNAPSHOT_STREAMS if include_state_snapshots else ())
    for source_key, stream_key in streams:
        stream_root = raw_root / source_key / stream_key
        if not stream_root.exists():
            continue
        for path in sorted(stream_root.rglob("*.jsonl")):
            paths.append(path)
    checkpoints = {} if reprocess_all else store.raw_file_checkpoints(paths)
    results = [
        normalize_rust_event_file(
            path=path,
            store=store,
            reprocess_all=reprocess_all,
            checkpoint=checkpoints.get(path),
            checkpoint_loaded=not reprocess_all,
        )
        for path in paths
    ]
    return tuple(results)


def normalize_rust_event_file(
    *,
    path: Path,
    store: DuckDbIngestStore,
    reprocess_all: bool = False,
    checkpoint: int | None = None,
    checkpoint_loaded: bool = False,
    last_price_state_by_symbol: dict[tuple[str, str], tuple[object, ...]] | None = None,
    last_orderbook_state_by_token: dict[tuple[str, str], tuple[object, ...]] | None = None,
) -> RustEventNormalizeResult:
    file_size = path.stat().st_size
    if reprocess_all:
        checkpoint = None
    elif not checkpoint_loaded:
        checkpoint = store.raw_file_checkpoint(path)
    start_byte_offset = 0 if checkpoint is None else checkpoint
    if start_byte_offset > file_size:
        start_byte_offset = 0
    end_byte_offset = _complete_jsonl_byte_limit(path, start_byte_offset, file_size)
    read_limit = end_byte_offset - start_byte_offset
    if read_limit <= 0:
        return RustEventNormalizeResult(
            path=path,
            file_id=_file_id_from_hasher(
                _file_id_hasher(
                    path,
                    start_byte_offset=start_byte_offset,
                    byte_limit=read_limit,
                )
            ),
            start_byte_offset=start_byte_offset,
            end_byte_offset=end_byte_offset,
            file_size_bytes=file_size,
            rows_read=0,
            price_ticks_written=0,
            orderbooks_written=0,
        )
    file_id_hasher = _file_id_hasher(
        path,
        start_byte_offset=start_byte_offset,
        byte_limit=read_limit,
    )
    rows_read = 0
    price_ticks_written = 0
    orderbooks_written = 0
    event_time_bounds = _EventTimeBounds()
    price_ticks: list[PriceObservation] = []
    orderbooks: list[OrderBookObservation] = []
    price_state_cache: dict[tuple[str, str], tuple[object, ...]]
    orderbook_state_cache: dict[tuple[str, str], tuple[object, ...]]
    price_state_cache = (
        {} if last_price_state_by_symbol is None else last_price_state_by_symbol
    )
    orderbook_state_cache = (
        {} if last_orderbook_state_by_token is None else last_orderbook_state_by_token
    )
    source_key, stream_key = _source_stream_from_path(path)

    for row in _iter_jsonl(
        path,
        start_byte_offset=start_byte_offset,
        byte_limit=read_limit,
        raw_line_handler=file_id_hasher.update,
    ):
        rows_read += 1
        if (
            row.get("source_key") == "polymarket_rtds_chainlink"
            and row.get("stream_key") == "price_update"
        ):
            price_tick = _price_tick_row_from_raw(row)
            assert price_tick is not None
            if price_state_cache.get(price_tick.symbol_key) == price_tick.state_key:
                continue
            price_state_cache[price_tick.symbol_key] = price_tick.state_key
            tick = _price_observation_from_price_tick_row(price_tick)
            price_ticks.append(tick)
            event_time_bounds.record(tick.event_ts)
            price_ticks_written += 1
            continue
        if row.get("schema_version") == STATE_MANAGER_SCHEMA_VERSION:
            for tick in _price_ticks_from_row(row):
                symbol_key = (tick.source_key, tick.symbol)
                state_key = _price_state_key(tick)
                if price_state_cache.get(symbol_key) != state_key:
                    price_state_cache[symbol_key] = state_key
                    price_ticks.append(tick)
                    event_time_bounds.record(tick.event_ts)
                    price_ticks_written += 1
        _append_orderbooks_from_row(
            row=row,
            orderbook_state_cache=orderbook_state_cache,
            orderbooks=orderbooks,
            event_time_bounds=event_time_bounds,
        )
        orderbooks_written = len(orderbooks)

    file_id = _file_id_from_hasher(file_id_hasher)
    if price_ticks:
        store.insert_price_ticks(price_ticks, raw_file_id=file_id)
    if orderbooks:
        store.insert_orderbook_snapshots(orderbooks, raw_file_id=file_id)

    if end_byte_offset > start_byte_offset:
        store.upsert_raw_file_checkpoint(
            path=path,
            source_key=source_key,
            stream_key=stream_key,
            byte_offset=end_byte_offset,
            file_size_bytes=file_size,
            rows_read=rows_read,
            first_event_ts=event_time_bounds.first,
            last_event_ts=event_time_bounds.last,
        )

    if rows_read > 0 and (price_ticks_written > 0 or orderbooks_written > 0):
        first_event_ts = event_time_bounds.first or _fallback_file_timestamp(path)
        last_event_ts = event_time_bounds.last or first_event_ts
        store.register_ingest_file(
            file_id=file_id,
            source_key=source_key,
            stream_key=stream_key,
            partition_date=first_event_ts.date().isoformat(),
            partition_hour=first_event_ts.hour,
            path=str(path),
            sha256=file_id.removeprefix("sha256:"),
            row_count=rows_read,
            first_event_ts=first_event_ts,
            last_event_ts=last_event_ts,
        )

    return RustEventNormalizeResult(
        path=path,
        file_id=file_id,
        start_byte_offset=start_byte_offset,
        end_byte_offset=end_byte_offset,
        file_size_bytes=file_size,
        rows_read=rows_read,
        price_ticks_written=price_ticks_written,
        orderbooks_written=orderbooks_written,
    )


def _price_state_key(tick: PriceObservation) -> tuple[object, ...]:
    return (
        tick.source_key,
        tick.symbol,
        tick.event_ts,
        tick.price,
        tick.bid,
        tick.ask,
        tick.sequence,
    )


def _orderbook_state_key(book: OrderBookObservation) -> tuple[object, ...]:
    return (
        book.venue,
        book.contract_id,
        book.token_id,
        book.event_ts,
        book.best_bid,
        book.best_ask,
        book.bid_size_top,
        book.ask_size_top,
        book.spread,
        book.depth_json,
    )


def _iter_jsonl(
    path: Path,
    byte_limit: int | None = None,
    start_byte_offset: int = 0,
    raw_line_handler: Callable[[bytes], None] | None = None,
) -> Iterator[dict[str, Any]]:
    if byte_limit is not None and byte_limit <= 0:
        return
    with path.open("rb") as handle:
        if start_byte_offset:
            handle.seek(start_byte_offset)
        bytes_read = 0
        for line_number, raw_line in enumerate(handle, start=1):
            if byte_limit is not None:
                if bytes_read >= byte_limit:
                    break
                if bytes_read + len(raw_line) > byte_limit:
                    break
            bytes_read += len(raw_line)
            if raw_line_handler is not None:
                raw_line_handler(raw_line)
            if not raw_line.endswith(b"\n"):
                break
            line = raw_line.decode("utf-8")
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} JSONL row must be an object")
            yield value


def _complete_jsonl_byte_limit(path: Path, start_byte_offset: int, file_size: int) -> int:
    if file_size <= start_byte_offset:
        return start_byte_offset
    with path.open("rb") as handle:
        handle.seek(file_size - 1)
        if handle.read(1) == b"\n":
            return file_size

        search_end = file_size
        chunk_size = 64 * 1024
        while search_end > start_byte_offset:
            read_start = max(start_byte_offset, search_end - chunk_size)
            handle.seek(read_start)
            chunk = handle.read(search_end - read_start)
            last_newline = chunk.rfind(b"\n")
            if last_newline >= 0:
                return read_start + last_newline + 1
            search_end = read_start

    return start_byte_offset


def _price_ticks_from_row(row: dict[str, Any]) -> tuple[PriceObservation, ...]:
    if row.get("schema_version") == STATE_MANAGER_SCHEMA_VERSION:
        return tuple(_price_tick_from_state_row(price) for price in row.get("chainlink_prices", []))
    price_tick = _price_tick_row_from_raw(row)
    if price_tick is None:
        return ()
    return (_price_observation_from_price_tick_row(price_tick),)


def _price_tick_row_from_raw(row: dict[str, Any]) -> _PriceTickRow | None:
    if row.get("source_key") != "polymarket_rtds_chainlink":
        return None
    if row.get("stream_key") != "price_update":
        return None
    price = _chainlink_price_from_payload(row)
    source_key = "polymarket_rtds_chainlink"
    symbol = str(row["symbol"]).upper()
    event_ts = _parse_ts(row["event_ts"])
    return _PriceTickRow(
        symbol_key=(source_key, symbol),
        state_key=(source_key, symbol, event_ts, price, None, None, None),
        source_key=source_key,
        symbol=symbol,
        event_ts=event_ts,
        observed_ts=row["observed_ts"],
        price=price,
    )


def _price_observation_from_price_tick_row(row: _PriceTickRow) -> PriceObservation:
    return PriceObservation(
        source_key=row.source_key,
        symbol=row.symbol,
        event_ts=row.event_ts,
        observed_ts=_parse_ts(row.observed_ts),
        price=row.price,
    )


def _price_tick_from_state_row(row: object) -> PriceObservation:
    if not isinstance(row, dict):
        raise ValueError("state-manager chainlink price row must be an object")
    return PriceObservation(
        source_key=str(row["source_key"]),
        symbol=str(row["symbol"]).upper(),
        event_ts=_parse_ts(row["event_ts"]),
        observed_ts=_parse_ts(row["observed_ts"]),
        price=_positive_float(row["price"], "price"),
    )


def _orderbooks_from_row(row: dict[str, Any]) -> tuple[OrderBookObservation, ...]:
    if row.get("schema_version") == STATE_MANAGER_SCHEMA_VERSION:
        return tuple(_orderbook_from_state_row(book) for book in row.get("orderbooks", []))
    top_of_book = _top_of_book_row_from_raw(row)
    if top_of_book is None:
        return ()
    return (_orderbook_from_top_of_book_row(top_of_book),)


def _append_orderbooks_from_row(
    *,
    row: dict[str, Any],
    orderbook_state_cache: dict[tuple[str, str], tuple[object, ...]],
    orderbooks: list[OrderBookObservation],
    event_time_bounds: _EventTimeBounds,
) -> None:
    top_of_book = _top_of_book_row_from_raw(row)
    if top_of_book is not None:
        if orderbook_state_cache.get(top_of_book.token_key) != top_of_book.state_key:
            orderbook_state_cache[top_of_book.token_key] = top_of_book.state_key
            orderbook = _orderbook_from_top_of_book_row(top_of_book)
            orderbooks.append(orderbook)
            event_time_bounds.record(orderbook.event_ts)
        return
    for book in _orderbooks_from_row(row):
        token_key = (book.venue, book.token_id)
        state_key = _orderbook_state_key(book)
        if orderbook_state_cache.get(token_key) != state_key:
            orderbook_state_cache[token_key] = state_key
            orderbooks.append(book)
            event_time_bounds.record(book.event_ts)


def _top_of_book_row_from_raw(row: dict[str, Any]) -> _TopOfBookRow | None:
    if row.get("source_key") != "polymarket_clob_market_ws":
        return None
    if row.get("stream_key") != "best_bid_ask":
        return None
    payload = _payload(row)
    best_bid = _optional_probability_float(payload.get("best_bid"), "best_bid")
    best_ask = _optional_probability_float(payload.get("best_ask"), "best_ask")
    spread_fallback = (
        None
        if best_bid is not None and best_ask is not None
        else _optional_probability_float(payload.get("spread"), "spread")
    )
    spread = _canonical_spread(best_bid, best_ask, spread_fallback)
    token_id = str(payload.get("token_id") or row.get("symbol"))
    contract_id = str(payload["contract_id"])
    event_ts = _parse_ts(row["event_ts"])
    return _TopOfBookRow(
        token_key=("polymarket", token_id),
        state_key=(
            "top_of_book",
            "polymarket",
            contract_id,
            token_id,
            event_ts,
            best_bid,
            best_ask,
            None,
            None,
            spread,
        ),
        contract_id=contract_id,
        token_id=token_id,
        event_ts=event_ts,
        observed_ts=row["observed_ts"],
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        payload=payload,
    )


def _orderbook_from_top_of_book_row(row: _TopOfBookRow) -> OrderBookObservation:
    return OrderBookObservation(
        venue="polymarket",
        contract_id=row.contract_id,
        token_id=row.token_id,
        event_ts=row.event_ts,
        observed_ts=_parse_ts(row.observed_ts),
        best_bid=row.best_bid,
        best_ask=row.best_ask,
        bid_size_top=None,
        ask_size_top=None,
        spread=row.spread,
        depth_json=json.dumps(
            {
                "source_key": "polymarket_clob_market_ws",
                "stream_key": "best_bid_ask",
                "top_of_book": {
                    "best_bid": _json_scalar(row.payload.get("best_bid")),
                    "best_ask": _json_scalar(row.payload.get("best_ask")),
                    "spread": _json_scalar(row.payload.get("spread")),
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _orderbook_from_state_row(row: object) -> OrderBookObservation:
    if not isinstance(row, dict):
        raise ValueError("state-manager orderbook row must be an object")
    best_bid = _optional_probability_float(row.get("best_bid"), "best_bid")
    best_ask = _optional_probability_float(row.get("best_ask"), "best_ask")
    spread = _canonical_spread(
        best_bid,
        best_ask,
        _optional_probability_float(row.get("spread"), "spread"),
    )
    return OrderBookObservation(
        venue=str(row.get("venue") or "polymarket"),
        contract_id=str(row["contract_id"]),
        token_id=str(row["token_id"]),
        event_ts=_parse_ts(row["event_ts"]),
        observed_ts=_parse_ts(row["observed_ts"]),
        best_bid=best_bid,
        best_ask=best_ask,
        bid_size_top=_optional_nonnegative_float(row.get("bid_size_top"), "bid_size_top"),
        ask_size_top=_optional_nonnegative_float(row.get("ask_size_top"), "ask_size_top"),
        spread=spread,
        depth_json=json.dumps(
            {
                "source_key": row.get("source_key"),
                "market_slug": row.get("market_slug"),
                "asset": row.get("asset"),
                "side": row.get("side"),
                "bids": row.get("bids") or [],
                "asks": row.get("asks") or [],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _chainlink_price_from_payload(row: dict[str, Any]) -> float:
    payload = _payload(row)
    message_payload = payload.get("payload", payload)
    if isinstance(message_payload, dict) and "value" in message_payload:
        return _positive_float(message_payload["value"], "payload.value")
    if isinstance(message_payload, dict) and isinstance(message_payload.get("data"), list):
        event_ms = int(_parse_ts(row["event_ts"]).timestamp() * 1000)
        points = [
            point
            for point in message_payload["data"]
            if isinstance(point, dict) and "timestamp" in point and "value" in point
        ]
        if not points:
            raise ValueError("Chainlink snapshot payload has no price points")
        matching = [
            point
            for point in points
            if int(point["timestamp"]) == event_ms
        ]
        selected = matching[-1] if matching else max(points, key=lambda point: int(point["timestamp"]))
        return _positive_float(selected["value"], "payload.data.value")
    raise ValueError("Chainlink payload missing price value")


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("raw event payload must be an object")
    return payload


def _parse_ts(value: object) -> datetime:
    raw = str(value)
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    if "." in raw:
        head, tail = raw.split(".", 1)
        offset_start = max(tail.rfind("+"), tail.rfind("-"))
        if offset_start > 0:
            fraction = tail[:offset_start]
            offset = tail[offset_start:]
        else:
            fraction = tail
            offset = ""
        if len(fraction) > 6:
            raw = f"{head}.{fraction[:6]}{offset}"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{field_name} must be numeric")
    return float(value)


def _positive_float(value: object, field_name: str) -> float:
    parsed = _number(value, field_name)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _nonnegative_float(value: object, field_name: str) -> float:
    parsed = _number(value, field_name)
    if parsed < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return parsed


def _probability_float(value: object, field_name: str) -> float:
    parsed = _nonnegative_float(value, field_name)
    if parsed > 1:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return parsed


def _optional_probability_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value:
        return None
    return _probability_float(value, field_name)


def _canonical_spread(
    best_bid: float | None,
    best_ask: float | None,
    fallback: float | None,
) -> float | None:
    if best_bid is not None and best_ask is not None:
        return best_ask - best_bid
    return fallback


def _optional_nonnegative_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value:
        return None
    return _nonnegative_float(value, field_name)


def _json_scalar(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return value
    return str(value)


def _file_id(
    path: Path,
    byte_limit: int | None = None,
    start_byte_offset: int = 0,
) -> str:
    hasher = _file_id_hasher(path, byte_limit=byte_limit, start_byte_offset=start_byte_offset)
    bytes_read = 0
    with path.open("rb") as handle:
        if start_byte_offset:
            handle.seek(start_byte_offset)
        while True:
            chunk_size = 1024 * 1024
            if byte_limit is not None:
                remaining = byte_limit - bytes_read
                if remaining <= 0:
                    break
                chunk_size = min(chunk_size, remaining)
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            bytes_read += len(chunk)
            hasher.update(chunk)
    return _file_id_from_hasher(hasher)


def _file_id_hasher(
    path: Path,
    byte_limit: int | None = None,
    start_byte_offset: int = 0,
) -> Any:
    hasher = hashlib.sha256()
    hasher.update(str(path).encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(str(start_byte_offset).encode("ascii"))
    hasher.update(b"\0")
    hasher.update(str(byte_limit).encode("ascii"))
    hasher.update(b"\0")
    return hasher


def _file_id_from_hasher(hasher: Any) -> str:
    return f"sha256:{hasher.hexdigest()}"


def _source_stream_from_path(path: Path) -> tuple[str, str]:
    parts = path.parts
    for source_key, stream_key in RUST_JSONL_STREAMS + STATE_SNAPSHOT_STREAMS:
        needle = (source_key, stream_key)
        for index in range(len(parts) - 1):
            if parts[index : index + 2] == needle:
                return source_key, stream_key
    return "unknown", "unknown"


def _fallback_file_timestamp(path: Path) -> datetime:
    for part in path.parts:
        if part.startswith("date="):
            date_text = part.removeprefix("date=")
            hour = 0
            for other in path.parts:
                if other.startswith("hour="):
                    hour = int(other.removeprefix("hour="))
                    break
            return datetime.fromisoformat(f"{date_text}T{hour:02d}:00:00+00:00")
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
