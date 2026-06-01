from pathlib import Path

import pytest

from polymarket_engine.ingestion.live_collector import LiveCollectorConfig, collection_deadline


def test_live_collector_defaults_to_current_and_next_windows() -> None:
    config = LiveCollectorConfig(
        assets=("BTC", "ETH"),
        duration_seconds=10,
        raw_root=Path("data/raw"),
        duckdb_path=Path("data/db/polymarket.duckdb"),
    )

    assert config.windows_to_track == 2
    assert config.intervals == ("5m", "15m")
    assert config.enable_clob_websocket is True
    assert config.clob_rest_backup_interval_seconds == 5.0
    assert config.clob_request_timeout_seconds == 2.0
    assert config.display_timezone == "America/Chicago"
    assert config.clob_snapshot_interval_seconds == 1.0
    assert config.market_refresh_interval_seconds == 30.0


def test_live_collector_allows_forever_duration() -> None:
    config = LiveCollectorConfig(
        assets=("BTC", "ETH"),
        duration_seconds=None,
        raw_root=Path("data/raw"),
        duckdb_path=Path("data/db/polymarket.duckdb"),
    )

    assert config.duration_seconds is None
    assert collection_deadline(config) is None


def test_live_collector_rejects_invalid_loop_intervals() -> None:
    with pytest.raises(ValueError, match="windows_to_track must be positive"):
        LiveCollectorConfig(
            assets=("BTC",),
            duration_seconds=10,
            raw_root=Path("data/raw"),
            duckdb_path=Path("data/db/polymarket.duckdb"),
            windows_to_track=0,
        )

    with pytest.raises(ValueError, match="clob_snapshot_interval_seconds must be positive"):
        LiveCollectorConfig(
            assets=("BTC",),
            duration_seconds=10,
            raw_root=Path("data/raw"),
            duckdb_path=Path("data/db/polymarket.duckdb"),
            clob_snapshot_interval_seconds=0,
        )


def test_live_collector_rejects_unsupported_contract_interval() -> None:
    with pytest.raises(ValueError, match="unsupported intervals"):
        LiveCollectorConfig(
            assets=("BTC",),
            duration_seconds=10,
            raw_root=Path("data/raw"),
            duckdb_path=Path("data/db/polymarket.duckdb"),
            intervals=("1h",),
        )


def test_live_collector_rejects_invalid_rest_backup_and_timezone() -> None:
    with pytest.raises(ValueError, match="clob_rest_backup_interval_seconds"):
        LiveCollectorConfig(
            assets=("BTC",),
            duration_seconds=10,
            raw_root=Path("data/raw"),
            duckdb_path=Path("data/db/polymarket.duckdb"),
            clob_rest_backup_interval_seconds=0,
        )

    with pytest.raises(ValueError, match="display_timezone"):
        LiveCollectorConfig(
            assets=("BTC",),
            duration_seconds=10,
            raw_root=Path("data/raw"),
            duckdb_path=Path("data/db/polymarket.duckdb"),
            display_timezone="UTC",
        )

    with pytest.raises(ValueError, match="clob_request_timeout_seconds"):
        LiveCollectorConfig(
            assets=("BTC",),
            duration_seconds=10,
            raw_root=Path("data/raw"),
            duckdb_path=Path("data/db/polymarket.duckdb"),
            clob_request_timeout_seconds=0,
        )
