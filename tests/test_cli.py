from pathlib import Path
from typing import Any

import pytest

from polymarket_engine import cli
from polymarket_engine.cli import parse_args
from polymarket_engine.ingestion.live_collector import LiveCollectorConfig, LiveCollectorResult


def test_parse_collect_args() -> None:
    args = parse_args(
        [
            "collect",
            "--assets",
            "BTC,ETH",
            "--duration",
            "60",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
        ]
    )

    assert args.command == "collect"
    assert args.assets == ("BTC", "ETH")
    assert args.duration == 60
    assert args.forever is False
    assert args.windows_to_track == 2
    assert args.intervals == ("5m", "15m")
    assert args.snapshot_interval == 1.0
    assert args.market_refresh_interval == 30.0
    assert args.raw_root == Path("data/raw")
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.status_path == Path("data/live/status.json")


def test_parse_collect_forever_args() -> None:
    args = parse_args(
        [
            "collect",
            "--assets",
            "BTC,ETH",
            "--forever",
            "--windows-to-track",
            "2",
            "--intervals",
            "5m,15m",
            "--snapshot-interval",
            "1",
            "--market-refresh-interval",
            "30",
        ]
    )

    assert args.command == "collect"
    assert args.forever is True
    assert args.duration is None
    assert args.windows_to_track == 2
    assert args.intervals == ("5m", "15m")
    assert args.snapshot_interval == 1.0
    assert args.market_refresh_interval == 30.0


def test_parse_monitor_args() -> None:
    args = parse_args(
        [
            "monitor",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--status-path",
            "data/live/status.json",
            "--refresh",
            "1",
        ]
    )

    assert args.command == "monitor"
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.status_path == Path("data/live/status.json")
    assert args.refresh == 1.0


@pytest.mark.anyio
async def test_run_collect_command_uses_injected_runner(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def fake_runner(config: LiveCollectorConfig) -> LiveCollectorResult:
        seen["assets"] = config.assets
        seen["duration"] = config.duration_seconds
        seen["windows_to_track"] = config.windows_to_track
        seen["intervals"] = config.intervals
        seen["snapshot_interval"] = config.clob_snapshot_interval_seconds
        seen["status_path"] = config.status_path
        return LiveCollectorResult(events_written=3, files_written=1)

    result = await cli.run_collect_command(
        [
            "collect",
            "--assets",
            "BTC,ETH",
            "--duration",
            "5",
            "--raw-root",
            str(tmp_path / "raw"),
            "--duckdb-path",
            str(tmp_path / "db.duckdb"),
            "--status-path",
            str(tmp_path / "status.json"),
        ],
        runner=fake_runner,
    )

    assert result == 0
    assert seen == {
        "assets": ("BTC", "ETH"),
        "duration": 5,
        "windows_to_track": 2,
        "intervals": ("5m", "15m"),
        "snapshot_interval": 1.0,
        "status_path": tmp_path / "status.json",
    }
