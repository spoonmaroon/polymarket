from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
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
    rows_read: int
    price_ticks_written: int
    orderbooks_written: int


def normalize_rust_event_tree(
    *,
    raw_root: Path,
    store: DuckDbIngestStore,
    include_state_snapshots: bool = False,
) -> tuple[RustEventNormalizeResult, ...]:
    results: list[RustEventNormalizeResult] = []
    streams = RUST_JSONL_STREAMS + (STATE_SNAPSHOT_STREAMS if include_state_snapshots else ())
    for source_key, stream_key in streams:
        stream_root = raw_root / source_key / stream_key
        if not stream_root.exists():
            continue
        for path in sorted(stream_root.rglob("*.jsonl")):
            results.append(normalize_rust_event_file(path=path, store=store))
    return tuple(results)


def normalize_rust_event_file(
    *,
    path: Path,
    store: DuckDbIngestStore,
) -> RustEventNormalizeResult:
    read_limit = path.stat().st_size
    file_id = _file_id(path, byte_limit=read_limit)
    rows_read = 0
    price_ticks_written = 0
    orderbooks_written = 0
    event_times: list[datetime] = []
    price_ticks: list[PriceObservation] = []
    orderbooks: list[OrderBookObservation] = []
    source_key, stream_key = _source_stream_from_path(path)

    for row in _iter_jsonl(path, byte_limit=read_limit):
        rows_read += 1
        for tick in _price_ticks_from_row(row):
            price_ticks.append(tick)
            event_times.append(tick.event_ts)
            price_ticks_written += 1
        for book in _orderbooks_from_row(row):
            orderbooks.append(book)
            event_times.append(book.event_ts)
            orderbooks_written += 1

    store.insert_price_ticks(price_ticks, raw_file_id=file_id)
    store.insert_orderbook_snapshots(orderbooks, raw_file_id=file_id)

    if rows_read > 0:
        first_event_ts = min(event_times) if event_times else _fallback_file_timestamp(path)
        last_event_ts = max(event_times) if event_times else first_event_ts
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
        rows_read=rows_read,
        price_ticks_written=price_ticks_written,
        orderbooks_written=orderbooks_written,
    )


def _iter_jsonl(path: Path, byte_limit: int | None = None) -> Iterator[dict[str, Any]]:
    with path.open("rb") as handle:
        bytes_read = 0
        for line_number, raw_line in enumerate(handle, start=1):
            if byte_limit is not None:
                if bytes_read >= byte_limit:
                    break
                if bytes_read + len(raw_line) > byte_limit:
                    break
            bytes_read += len(raw_line)
            line = raw_line.decode("utf-8")
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} JSONL row must be an object")
            yield value


def _price_ticks_from_row(row: dict[str, Any]) -> tuple[PriceObservation, ...]:
    if row.get("schema_version") == STATE_MANAGER_SCHEMA_VERSION:
        return tuple(_price_tick_from_state_row(price) for price in row.get("chainlink_prices", []))
    if row.get("source_key") != "polymarket_rtds_chainlink":
        return ()
    if row.get("stream_key") != "price_update":
        return ()
    price = _chainlink_price_from_payload(row)
    return (
        PriceObservation(
            source_key="polymarket_rtds_chainlink",
            symbol=str(row["symbol"]).upper(),
            event_ts=_parse_ts(row["event_ts"]),
            observed_ts=_parse_ts(row["observed_ts"]),
            price=price,
        ),
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
    if row.get("source_key") != "polymarket_clob_market_ws":
        return ()
    if row.get("stream_key") != "best_bid_ask":
        return ()
    payload = _payload(row)
    best_bid = _optional_probability_float(payload.get("best_bid"), "best_bid")
    best_ask = _optional_probability_float(payload.get("best_ask"), "best_ask")
    spread = _canonical_spread(
        best_bid,
        best_ask,
        _optional_probability_float(payload.get("spread"), "spread"),
    )
    token_id = str(payload.get("token_id") or row.get("symbol"))
    return (
        OrderBookObservation(
            venue="polymarket",
            contract_id=str(payload["contract_id"]),
            token_id=token_id,
            event_ts=_parse_ts(row["event_ts"]),
            observed_ts=_parse_ts(row["observed_ts"]),
            best_bid=best_bid,
            best_ask=best_ask,
            bid_size_top=None,
            ask_size_top=None,
            spread=spread,
            depth_json=json.dumps(
                {
                    "source_key": "polymarket_clob_market_ws",
                    "stream_key": "best_bid_ask",
                    "top_of_book": {
                        "best_bid": _json_scalar(payload.get("best_bid")),
                        "best_ask": _json_scalar(payload.get("best_ask")),
                        "spread": _json_scalar(payload.get("spread")),
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
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


def _file_id(path: Path, byte_limit: int | None = None) -> str:
    hasher = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as handle:
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
    digest = hasher.hexdigest()
    return f"sha256:{digest}"


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
