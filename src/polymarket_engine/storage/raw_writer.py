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
        raise ValueError("one source_key and one stream_key required per raw file")

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
