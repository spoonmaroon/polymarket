from datetime import datetime, timedelta, timezone
from pathlib import Path

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.storage.buffered_writer import BufferedRawEventWriter


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


def test_buffered_writer_flushes_by_source_and_stream(tmp_path: Path) -> None:
    writer = BufferedRawEventWriter(raw_root=tmp_path, max_batch_size=2)
    event_ts = datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc)

    result_none = writer.add(
        CollectorEvent("coinbase_advanced_ws", "ticker", "BTC-USD", event_ts, event_ts, {"price": "1"})
    )
    result = writer.add(
        CollectorEvent("coinbase_advanced_ws", "ticker", "ETH-USD", event_ts, event_ts, {"price": "2"})
    )

    assert result_none is None
    assert result is not None
    assert result.row_count == 2
    assert result.path.exists()


def test_buffered_writer_flushes_by_time_when_stream_is_quiet(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc))
    writer = BufferedRawEventWriter(
        raw_root=tmp_path,
        max_batch_size=100,
        flush_after_seconds=5.0,
        clock=clock,
    )
    event_ts = datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc)

    assert writer.add(
        CollectorEvent("coinbase_advanced_ws", "ticker", "BTC-USD", event_ts, event_ts, {"price": "1"})
    ) is None
    clock.advance(6.0)
    result = writer.maybe_flush()

    assert result is not None
    assert result.row_count == 1
    assert writer.buffered_count == 0
