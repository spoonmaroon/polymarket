from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.ingestion.live_collector import (
    LiveCollectorConfig,
    register_market_rules,
    run_fake_collection,
)


BTC_DESCRIPTION = """This market will resolve to "Up" if the Bitcoin price at the end of the time range specified in the title is greater than or equal to the price at the beginning of that range. Otherwise, it will resolve to "Down".
The resolution source for this market is information from Chainlink, specifically the BTC/USD data stream available at https://data.chain.link/streams/btc-usd.
Please note that this market is about the price according to Chainlink data stream BTC/USD, not according to other sources or spot exchanges."""


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


def test_register_market_rules_stores_accepted_contract_rule(tmp_path: Path) -> None:
    config = LiveCollectorConfig(
        assets=("BTC",),
        duration_seconds=1,
        raw_root=tmp_path / "raw",
        duckdb_path=tmp_path / "collector.duckdb",
        max_batch_size=2,
    )
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    source_errors = register_market_rules(config.duckdb_path, (market,))

    assert source_errors == {}
    with duckdb.connect(str(config.duckdb_path), read_only=True) as conn:
        row = conn.execute(
            "select asset, threshold_type, comparison_operator_up, comparison_operator_down, "
            "settlement_symbol, accepted from core.contract_rules where slug = ?",
            ["btc-updown-5m-1780264500"],
        ).fetchone()

    assert row == ("BTC", "start_price", ">=", "<", "BTC/USD", True)
