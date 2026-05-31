from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.storage.buffered_writer import BufferedRawEventWriter
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore
from polymarket_engine.storage.raw_writer import RawWriteResult


@dataclass(frozen=True)
class LiveCollectorConfig:
    assets: tuple[str, ...]
    duration_seconds: int
    raw_root: Path
    duckdb_path: Path
    max_batch_size: int = 100
    flush_after_seconds: float = 5.0
    require_archive_sentinel: bool = False


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
