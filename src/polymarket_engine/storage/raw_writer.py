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

    for event in events:
        _require_aware(event.event_ts, "event_ts")
        _require_aware(event.observed_ts, "observed_ts")

    first = min(event.event_ts for event in events)
    last = max(event.event_ts for event in events)
    source_keys = {event.source_key for event in events}
    stream_keys = {event.stream_key for event in events}
    if len(source_keys) != 1 or len(stream_keys) != 1:
        raise ValueError("one source_key and one stream_key required per raw file")
    partition_keys = {_partition_key(event.event_ts) for event in events}
    if len(partition_keys) != 1:
        raise ValueError("one UTC date/hour partition required per raw file")

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


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _partition_key(value: datetime) -> tuple[str, str]:
    ts = value.astimezone(timezone.utc)
    return (ts.strftime("%Y-%m-%d"), ts.strftime("%H"))
