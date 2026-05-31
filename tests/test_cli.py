from pathlib import Path

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
    assert args.raw_root == Path("data/raw")
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")


@pytest.mark.anyio
async def test_run_collect_command_uses_injected_runner(tmp_path: Path) -> None:
    seen: dict[str, tuple[str, ...] | int] = {}

    async def fake_runner(config: LiveCollectorConfig) -> LiveCollectorResult:
        seen["assets"] = config.assets
        seen["duration"] = config.duration_seconds
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
        ],
        runner=fake_runner,
    )

    assert result == 0
    assert seen == {"assets": ("BTC", "ETH"), "duration": 5}
