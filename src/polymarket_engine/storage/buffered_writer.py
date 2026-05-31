from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.storage.raw_writer import RawWriteResult, write_raw_events


class BufferedRawEventWriter:
    def __init__(
        self,
        raw_root: Path,
        max_batch_size: int = 100,
        flush_after_seconds: float = 5.0,
        require_archive_sentinel: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.raw_root = raw_root
        self.max_batch_size = max_batch_size
        self.flush_after_seconds = flush_after_seconds
        self.require_archive_sentinel = require_archive_sentinel
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._buffers: defaultdict[tuple[str, str], list[CollectorEvent]] = defaultdict(list)
        self._first_buffered_at: dict[tuple[str, str], datetime] = {}

    @property
    def buffered_count(self) -> int:
        return sum(len(events) for events in self._buffers.values())

    def add(self, event: CollectorEvent) -> RawWriteResult | None:
        key = (event.source_key, event.stream_key)
        if key not in self._first_buffered_at:
            self._first_buffered_at[key] = self._clock()
        self._buffers[key].append(event)
        if len(self._buffers[key]) >= self.max_batch_size:
            return self.flush_key(key)
        return None

    def maybe_flush(self) -> RawWriteResult | None:
        now = self._clock()
        for key, first_seen in list(self._first_buffered_at.items()):
            if (now - first_seen).total_seconds() >= self.flush_after_seconds:
                return self.flush_key(key)
        return None

    def flush_key(self, key: tuple[str, str]) -> RawWriteResult | None:
        events = self._buffers.pop(key, [])
        self._first_buffered_at.pop(key, None)
        if not events:
            return None
        return write_raw_events(
            self.raw_root,
            [event.to_raw_event() for event in events],
            require_archive_sentinel=self.require_archive_sentinel,
        )

    def flush_all(self) -> tuple[RawWriteResult, ...]:
        results: list[RawWriteResult] = []
        for key in list(self._buffers):
            result = self.flush_key(key)
            if result is not None:
                results.append(result)
        return tuple(results)
