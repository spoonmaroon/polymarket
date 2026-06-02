import json
from pathlib import Path
from signal import SIGTERM, Signals
from typing import Any

import duckdb
import pytest

from polymarket_engine import cli
from polymarket_engine.cli import parse_args
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


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


def test_parse_normalize_rust_events_args() -> None:
    args = parse_args(
        [
            "normalize-rust-events",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
        ]
    )

    assert args.command == "normalize-rust-events"
    assert args.raw_root == Path("data/raw")
    assert args.file is None
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.skip_apply_schema is False
    assert args.include_state_snapshots is False


def test_parse_write_normalized_health_args() -> None:
    args = parse_args(
        [
            "write-normalized-health",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--out",
            "data/live/normalized_health.json",
        ]
    )

    assert args.command == "write-normalized-health"
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.out == Path("data/live/normalized_health.json")


def test_parse_build_current_decision_states_args() -> None:
    args = parse_args(
        [
            "build-current-decision-states",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--status-path",
            "data/live/status.json",
            "--include-next",
        ]
    )

    assert args.command == "build-current-decision-states"
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.status_path == Path("data/live/status.json")
    assert args.include_next is True


@pytest.mark.anyio
async def test_run_normalize_rust_events_command_writes_duckdb(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_path = (
        tmp_path
        / "raw"
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=05"
        / "events.jsonl"
    )
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(
        json.dumps(
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_type": "chainlink_price",
                "event_ts": "2026-06-02T05:33:54Z",
                "observed_ts": "2026-06-02T05:33:55.239695967Z",
                "payload": {
                    "topic": "crypto_prices_chainlink",
                    "payload": {
                        "symbol": "btc/usd",
                        "timestamp": 1_780_378_434_000,
                        "value": "70600.137545",
                    },
                },
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "state.duckdb"

    result = await cli.run_collect_command(
        [
            "normalize-rust-events",
            "--raw-root",
            str(tmp_path / "raw"),
            "--duckdb-path",
            str(db_path),
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "files": 1,
        "orderbooks_written": 0,
        "price_ticks_written": 1,
        "rows_read": 1,
    }
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (1,)


@pytest.mark.anyio
async def test_run_write_normalized_health_command_writes_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "state.duckdb"
    DuckDbIngestStore(db_path).apply_schema()
    out_path = tmp_path / "live" / "normalized_health.json"

    result = await cli.run_collect_command(
        [
            "write-normalized-health",
            "--duckdb-path",
            str(db_path),
            "--out",
            str(out_path),
        ]
    )

    assert result == 0
    stdout_payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert file_payload["schema_version"] == "polymarket-normalized-health-v1"
    assert {row["table"] for row in file_payload["tables"]} >= {
        "core.price_ticks",
        "core.orderbook_snapshots",
        "features.asof_state_inputs",
    }


@pytest.mark.anyio
async def test_run_build_current_decision_states_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "status.json"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "rust-live-probe-state-manager-v1",
                "mode": "state-manager",
                "generated_at": "2026-06-02T06:02:00+00:00",
                "current": [
                    {
                        "window": {
                            "asset": "BTC",
                            "interval": "5m",
                            "start_ts": "2026-06-02T06:00:00+00:00",
                            "end_ts": "2026-06-02T06:05:00+00:00",
                        },
                        "up": {"asset": "BTC", "side": "Up", "token_id": "up-token"},
                        "down": {"asset": "BTC", "side": "Down", "token_id": "down-token"},
                    }
                ],
                "orderbooks": [
                    {
                        "market_slug": "btc-updown-5m-1780380000",
                        "contract_id": "0xcondition",
                        "token_id": "up-token",
                    },
                    {
                        "market_slug": "btc-updown-5m-1780380000",
                        "contract_id": "0xcondition",
                        "token_id": "down-token",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = await cli.run_collect_command(
        [
            "build-current-decision-states",
            "--duckdb-path",
            str(db_path),
            "--status-path",
            str(status_path),
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["contracts_upserted"] == 2
    assert payload["states_written"] == 0
    assert len(payload["unavailable"]) == 2


@pytest.mark.anyio
async def test_run_collect_command_rejects_retired_python_collector(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="Python live collection is retired"):
        await cli.run_collect_command(
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
            ]
        )


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
async def test_run_collect_command_rejects_retired_collector_even_with_forever() -> None:
    with pytest.raises(SystemExit, match="Python live collection is retired"):
        await cli.run_collect_command(["collect", "--assets", "BTC,ETH", "--forever"])
