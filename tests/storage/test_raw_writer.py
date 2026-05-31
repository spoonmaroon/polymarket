from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from polymarket_engine.storage.paths import RawPartition
from polymarket_engine.storage.raw_writer import RawEvent, write_raw_events


def test_raw_partition_path_uses_source_stream_date_and_hour() -> None:
    partition = RawPartition(
        root=Path("data/raw"),
        source_key="binance_spot_ws",
        stream_key="trade",
        event_ts=datetime(2026, 5, 31, 20, 4, 1, tzinfo=timezone.utc),
    )

    assert partition.directory == Path("data/raw/binance_spot_ws/trade/date=2026-05-31/hour=20")


def test_write_raw_events_creates_parquet_file(tmp_path: Path) -> None:
    event = RawEvent(
        source_key="binance_spot_ws",
        stream_key="trade",
        symbol="BTCUSDT",
        event_ts=datetime(2026, 5, 31, 20, 4, 1, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 4, 2, tzinfo=timezone.utc),
        payload={"p": "104000.1", "q": "0.02"},
    )

    result = write_raw_events(tmp_path, [event])

    assert result.path.exists()
    assert result.row_count == 1
    frame = pl.read_parquet(result.path)
    assert frame["source_key"].to_list() == ["binance_spot_ws"]
    assert frame["symbol"].to_list() == ["BTCUSDT"]


def test_write_raw_events_rejects_mixed_sources(tmp_path: Path) -> None:
    events = [
        RawEvent(
            source_key="binance_spot_ws",
            stream_key="trade",
            symbol="BTCUSDT",
            event_ts=datetime(2026, 5, 31, 20, 4, 1, tzinfo=timezone.utc),
            observed_ts=datetime(2026, 5, 31, 20, 4, 2, tzinfo=timezone.utc),
            payload={"p": "104000.1"},
        ),
        RawEvent(
            source_key="coinbase_advanced_ws",
            stream_key="ticker",
            symbol="BTC-USD",
            event_ts=datetime(2026, 5, 31, 20, 4, 1, tzinfo=timezone.utc),
            observed_ts=datetime(2026, 5, 31, 20, 4, 2, tzinfo=timezone.utc),
            payload={"price": "104001.0"},
        ),
    ]

    with pytest.raises(ValueError, match="one source_key"):
        write_raw_events(tmp_path, events)


def test_write_raw_events_leaves_no_tmp_files(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    (raw_root / ".polymarket_archive_root").touch()
    event = RawEvent(
        source_key="coinbase_advanced_ws",
        stream_key="ticker",
        symbol="BTC-USD",
        event_ts=datetime(2026, 5, 31, 21, 0, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc),
        payload={"price": "104000"},
    )

    result = write_raw_events(raw_root, [event], require_archive_sentinel=True)

    assert result.path.exists()
    assert list(raw_root.rglob("*.parquet.tmp")) == []
