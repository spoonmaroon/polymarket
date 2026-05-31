from datetime import datetime, timezone
from pathlib import Path

import pytest

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.ingestion.live_collector import LiveCollectorConfig, run_fake_collection


@pytest.mark.anyio
async def test_run_fake_collection_writes_events_and_registers_files(tmp_path: Path) -> None:
    event_ts = datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc)
    events = (
        CollectorEvent("coinbase_advanced_ws", "ticker", "BTC-USD", event_ts, event_ts, {"price": "1"}),
        CollectorEvent("coinbase_advanced_ws", "ticker", "ETH-USD", event_ts, event_ts, {"price": "2"}),
    )
    config = LiveCollectorConfig(
        assets=("BTC", "ETH"),
        duration_seconds=1,
        raw_root=tmp_path / "raw",
        duckdb_path=tmp_path / "collector.duckdb",
        max_batch_size=2,
    )

    result = await run_fake_collection(config, events)

    assert result.events_written == 2
    assert result.files_written == 1
    assert result.source_errors == {}
