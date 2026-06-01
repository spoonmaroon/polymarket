import asyncio
from pathlib import Path
from signal import SIGTERM, Signals
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


def test_parse_collect_market_ws_flags() -> None:
    args = parse_args(
        [
            "collect",
            "--assets",
            "BTC,ETH",
            "--duration",
            "60",
            "--disable-clob-websocket",
            "--clob-rest-backup-interval",
            "20",
            "--clob-request-timeout",
            "4",
            "--display-timezone",
            "America/Chicago",
        ]
    )

    assert args.enable_clob_websocket is False
    assert args.clob_rest_backup_interval == 20.0
    assert args.clob_request_timeout == 4.0
    assert args.display_timezone == "America/Chicago"


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
        seen["enable_clob_websocket"] = config.enable_clob_websocket
        seen["clob_rest_backup_interval"] = config.clob_rest_backup_interval_seconds
        seen["clob_request_timeout"] = config.clob_request_timeout_seconds
        seen["display_timezone"] = config.display_timezone
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
        "enable_clob_websocket": True,
        "clob_rest_backup_interval": 15.0,
        "clob_request_timeout": 5.0,
        "display_timezone": "America/Chicago",
        "status_path": tmp_path / "status.json",
    }


def test_shutdown_signal_handler_cancels_collector_task() -> None:
    class FakeLoop:
        def __init__(self) -> None:
            self.handlers: dict[Signals, Any] = {}

        def add_signal_handler(self, signal_number: Signals, callback: Any) -> None:
            self.handlers[signal_number] = callback

        def remove_signal_handler(self, signal_number: Signals) -> bool:
            self.handlers.pop(signal_number, None)
            return True

    class FakeTask:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    loop = FakeLoop()
    task = FakeTask()

    installed = cli._install_shutdown_signal_handlers(loop, task)  # noqa: SLF001
    loop.handlers[SIGTERM]()

    assert SIGTERM in installed
    assert task.cancelled is True


@pytest.mark.anyio
async def test_run_collect_command_returns_zero_after_signal_cancel(tmp_path: Path) -> None:
    async def fake_runner(config: LiveCollectorConfig) -> LiveCollectorResult:
        raise asyncio.CancelledError

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
