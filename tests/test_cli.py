import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from signal import SIGTERM, Signals
from typing import Any

import duckdb
import pytest

from polymarket_engine import cli
from polymarket_engine.cli import parse_args
from polymarket_engine.ingestion.rust_event_normalizer import normalize_rust_event_tree
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
    assert args.reprocess_all is False


def test_parse_normalize_rust_events_reprocess_all_arg() -> None:
    args = parse_args(
        [
            "normalize-rust-events",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--reprocess-all",
        ]
    )

    assert args.command == "normalize-rust-events"
    assert args.reprocess_all is True


def test_parse_run_rust_normalizer_sidecar_args() -> None:
    args = parse_args(
        [
            "run-rust-normalizer-sidecar",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--status-path",
            "data/live/status.json",
            "--normalized-health-path",
            "data/live/normalized_health.json",
            "--interval-seconds",
            "1",
            "--once",
        ]
    )

    assert args.command == "run-rust-normalizer-sidecar"
    assert args.raw_root == Path("data/raw")
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.status_path == Path("data/live/status.json")
    assert args.normalized_health_path == Path("data/live/normalized_health.json")
    assert args.probability_status_path == Path("data/live/probabilities.json")
    assert args.probability_inputs_path == Path("data/live/probability_inputs.json")
    assert args.probability_fragments_path == Path("data/live/probability_fragments.json")
    assert args.fragment_max_rows == 250_000
    assert args.outcome_status_path == Path("data/live/outcomes.json")
    assert args.interval_seconds == 1.0
    assert args.enable_probabilities is False
    assert args.enable_outcome_refresh is False
    assert args.once is True


def test_parse_run_rust_normalizer_sidecar_enable_probabilities_arg() -> None:
    args = parse_args(
        [
            "run-rust-normalizer-sidecar",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--status-path",
            "data/live/status.json",
            "--normalized-health-path",
            "data/live/normalized_health.json",
            "--enable-probabilities",
        ]
    )

    assert args.enable_probabilities is True


def test_parse_run_rust_normalizer_sidecar_enable_outcome_refresh_arg() -> None:
    args = parse_args(
        [
            "run-rust-normalizer-sidecar",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--status-path",
            "data/live/status.json",
            "--normalized-health-path",
            "data/live/normalized_health.json",
            "--enable-outcome-refresh",
        ]
    )

    assert args.enable_outcome_refresh is True


def test_parse_run_outcome_refresh_sidecar_args() -> None:
    args = parse_args(
        [
            "run-outcome-refresh-sidecar",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--outcome-status-path",
            "data/live/outcomes.json",
            "--interval-seconds",
            "12.5",
            "--max-cycles",
            "2",
        ]
    )

    assert args.command == "run-outcome-refresh-sidecar"
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.outcome_status_path == Path("data/live/outcomes.json")
    assert args.interval_seconds == 12.5
    assert args.max_cycles == 2


def test_parse_run_rust_normalizer_sidecar_probability_inputs_path_arg() -> None:
    args = parse_args(
        [
            "run-rust-normalizer-sidecar",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--status-path",
            "data/live/status.json",
            "--normalized-health-path",
            "data/live/normalized_health.json",
            "--probability-inputs-path",
            "tmp/probability_inputs.json",
        ]
    )

    assert args.probability_inputs_path == Path("tmp/probability_inputs.json")


def test_parse_run_rust_normalizer_sidecar_probability_fragments_path_arg() -> None:
    args = parse_args(
        [
            "run-rust-normalizer-sidecar",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--status-path",
            "data/live/status.json",
            "--normalized-health-path",
            "data/live/normalized_health.json",
            "--probability-fragments-path",
            "tmp/probability_fragments.json",
            "--fragment-max-rows",
            "123",
        ]
    )

    assert args.probability_fragments_path == Path("tmp/probability_fragments.json")
    assert args.fragment_max_rows == 123


def test_parse_run_cuda_probability_worker_defaults() -> None:
    args = parse_args(
        [
            "run-cuda-probability-worker",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
        ]
    )

    assert args.command == "run-cuda-probability-worker"
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.probability_status_path == Path("data/live/probabilities.json")
    assert args.probability_inputs_path == Path("data/live/probability_inputs.json")
    assert args.probability_fragments_path == Path("data/live/probability_fragments.json")
    assert args.interval_seconds == 1.0
    assert args.limit == 24
    assert args.valid_seconds == 30
    assert args.max_input_snapshot_age_seconds == 30.0
    assert args.worker_mode == "ensemble"
    assert args.generator_policy == "all_four_every_cycle"
    assert args.cpu_target_percent == 15.0
    assert args.cpu_soft_max_percent == 20.0
    assert args.max_rss_mb == 512
    assert args.max_cycle_runtime_ms == 10_000
    assert args.max_total_paths == 320_000
    assert args.min_total_paths == 80_000
    assert args.sustained_breach_cycles == 3
    assert args.fragment_max_rows == 250_000
    assert args.cpu_threads == 1
    assert args.once is False


def test_parse_run_cuda_probability_worker_probability_fragments_path_arg() -> None:
    args = parse_args(
        [
            "run-cuda-probability-worker",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--probability-fragments-path",
            "tmp/probability_fragments.json",
        ]
    )

    assert args.probability_fragments_path == Path("tmp/probability_fragments.json")


def test_run_rust_normalizer_sidecar_defaults_to_quarter_second_cadence() -> None:
    args = parse_args(
        [
            "run-rust-normalizer-sidecar",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--status-path",
            "data/live/status.json",
            "--normalized-health-path",
            "data/live/normalized_health.json",
        ]
    )

    assert args.interval_seconds == 0.25


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


def test_parse_verify_hot_decision_replay_args() -> None:
    args = parse_args(
        [
            "verify-hot-decision-replay",
            "--raw-root",
            "data/raw",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--limit",
            "10",
            "--scan-limit",
            "200",
            "--report-out",
            "reports/hot-replay.json",
        ]
    )

    assert args.command == "verify-hot-decision-replay"
    assert args.raw_root == Path("data/raw")
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.limit == 10
    assert args.scan_limit == 200
    assert args.report_out == Path("reports/hot-replay.json")


def test_parse_calibration_report_args() -> None:
    args = parse_args(
        [
            "calibration-report",
            "--input",
            "data/research/calibration/asof_decision_states.jsonl",
            "--out",
            "reports/calibration.json",
        ]
    )

    assert args.command == "calibration-report"
    assert args.input == Path("data/research/calibration/asof_decision_states.jsonl")
    assert args.out == Path("reports/calibration.json")


def test_parse_runtime_keeper_args() -> None:
    args = parse_args(
        [
            "runtime-keeper",
            "--repo",
            "/home/ender/polymarket",
            "--data-dir",
            "/home/ender/polymarket-data",
            "--api-base-url",
            "http://127.0.0.1:8000",
            "--optional-container",
            "polymarket-rust-collector-gpu-probability-worker-1",
            "--loop",
            "--loop-interval-seconds",
            "15",
        ]
    )

    assert args.command == "runtime-keeper"
    assert args.repo == Path("/home/ender/polymarket")
    assert args.data_dir == Path("/home/ender/polymarket-data")
    assert args.api_base_url == "http://127.0.0.1:8000"
    assert args.optional_container == [
        "polymarket-rust-collector-gpu-probability-worker-1",
    ]
    assert args.loop is True
    assert args.loop_interval_seconds == 15.0


def test_parse_sync_cluster_artifacts_args() -> None:
    args = parse_args(
        [
            "sync-cluster-artifacts",
            "--manifest-path",
            "/tmp/cluster.local.example.json",
            "--execute",
        ]
    )

    assert args.command == "sync-cluster-artifacts"
    assert args.manifest_path == Path("/tmp/cluster.local.example.json")
    assert args.execute is True


@pytest.mark.anyio
async def test_backfill_outcomes_dry_run_prints_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []
    expected_report = {
        "ok": True,
        "dry_run": True,
        "asof_ts": "2026-06-04T12:00:00+00:00",
        "start_date": "2026-06-01",
        "end_date": "2026-06-02",
        "limit": 500,
        "markets_scanned": 3,
        "rows_written": 0,
        "missing_k_before": 2,
        "missing_k_after": 2,
        "pending_official_before": 1,
        "pending_official_after": 1,
    }

    def fake_backfill_outcome_history(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return expected_report

    monkeypatch.setattr(
        "polymarket_engine.validation.outcomes.backfill_outcome_history",
        fake_backfill_outcome_history,
    )

    result = await cli.run_collect_command(
        [
            "backfill-outcomes",
            "--duckdb-path",
            str(tmp_path / "state.duckdb"),
            "--outcomes-path",
            str(tmp_path / "live" / "outcomes.json"),
            "--start-date",
            "2026-06-01",
            "--end-date",
            "2026-06-02",
        ]
    )

    assert result == 0
    assert calls == [
        {
            "duckdb_path": tmp_path / "state.duckdb",
            "outcomes_path": tmp_path / "live" / "outcomes.json",
            "start_date": "2026-06-01",
            "end_date": "2026-06-02",
            "limit": 500,
            "write": False,
            "market_payload_source": calls[0]["market_payload_source"],
        }
    ]
    assert calls[0]["market_payload_source"] is not None
    assert json.loads(capsys.readouterr().out) == expected_report


@pytest.mark.anyio
async def test_backfill_outcomes_write_mode_passes_write_true(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    def fake_backfill_outcome_history(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "ok": True,
            "dry_run": False,
            "asof_ts": "2026-06-04T12:00:00+00:00",
            "start_date": None,
            "end_date": None,
            "limit": 25,
            "markets_scanned": 1,
            "rows_written": 1,
            "missing_k_before": 1,
            "missing_k_after": 0,
            "pending_official_before": 1,
            "pending_official_after": 0,
        }

    monkeypatch.setattr(
        "polymarket_engine.validation.outcomes.backfill_outcome_history",
        fake_backfill_outcome_history,
    )

    result = await cli.run_collect_command(
        [
            "backfill-outcomes",
            "--duckdb-path",
            str(tmp_path / "state.duckdb"),
            "--outcomes-path",
            str(tmp_path / "live" / "outcomes.json"),
            "--limit",
            "25",
            "--official-outcome-source",
            "none",
            "--write",
        ]
    )

    assert result == 0
    assert calls[0]["write"] is True
    assert calls[0]["limit"] == 25
    assert calls[0]["market_payload_source"] is None
    assert json.loads(capsys.readouterr().out)["dry_run"] is False


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
        "bytes_read": raw_path.stat().st_size,
        "files": 1,
        "file_size_bytes": raw_path.stat().st_size,
        "files_skipped": 0,
        "files_with_rows": 1,
        "orderbooks_written": 0,
        "price_ticks_written": 1,
        "rows_read": 1,
    }
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (1,)


@pytest.mark.anyio
async def test_run_normalize_rust_events_command_reports_byte_delta_and_skips(
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
    _write_jsonl(
        raw_path,
        [
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_type": "chainlink_price",
                "event_ts": "2026-06-02T05:33:54Z",
                "observed_ts": "2026-06-02T05:33:55Z",
                "payload": {"value": "70600.0"},
            }
        ],
    )
    db_path = tmp_path / "state.duckdb"
    argv = [
        "normalize-rust-events",
        "--raw-root",
        str(tmp_path / "raw"),
        "--duckdb-path",
        str(db_path),
    ]

    assert await cli.run_collect_command(argv) == 0
    first_payload = json.loads(capsys.readouterr().out)
    assert first_payload["bytes_read"] == raw_path.stat().st_size
    assert first_payload["files_with_rows"] == 1
    assert first_payload["files_skipped"] == 0

    assert await cli.run_collect_command(argv) == 0
    second_payload = json.loads(capsys.readouterr().out)
    assert second_payload["bytes_read"] == 0
    assert second_payload["files_with_rows"] == 0
    assert second_payload["files_skipped"] == 1


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
async def test_run_rust_normalizer_sidecar_once_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_root = tmp_path / "raw"
    raw_path = (
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl"
    )
    _write_jsonl(
        raw_path,
        [
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_type": "chainlink_price",
                "event_ts": "2026-06-02T06:00:00+00:00",
                "observed_ts": "2026-06-02T06:00:00+00:00",
                "payload": {"value": "70000.0"},
            }
        ],
    )
    (raw_root / ".polymarket_archive_root").write_text("", encoding="utf-8")
    status_path = tmp_path / "live" / "status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
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
                "orderbooks": [],
            }
        ),
        encoding="utf-8",
    )
    health_path = tmp_path / "live" / "normalized_health.json"
    probability_inputs_path = tmp_path / "live" / "probability_inputs.json"
    outcome_path = tmp_path / "live" / "outcomes.json"

    result = await cli.run_collect_command(
        [
            "run-rust-normalizer-sidecar",
            "--raw-root",
            str(raw_root),
            "--duckdb-path",
            str(tmp_path / "state.duckdb"),
            "--status-path",
            str(status_path),
            "--normalized-health-path",
            str(health_path),
            "--probability-inputs-path",
            str(probability_inputs_path),
            "--probability-fragments-path",
            str(tmp_path / "live" / "probability_fragments.json"),
            "--outcome-status-path",
            str(outcome_path),
            "--once",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows_read"] == 1
    assert payload["bytes_read"] > 0
    assert payload["elapsed_ms"] >= 0
    assert payload["contracts_upserted"] == 2
    assert payload["market_outcomes_written"] == 0
    assert health_path.exists()
    assert probability_inputs_path.exists()
    assert not outcome_path.exists()


@pytest.mark.anyio
async def test_run_rust_normalizer_sidecar_loop_command_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_loop(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.run_rust_normalizer_loop",
        fake_loop,
    )

    result = await cli.run_collect_command(
        [
            "run-rust-normalizer-sidecar",
            "--raw-root",
            str(tmp_path / "raw"),
            "--duckdb-path",
            str(tmp_path / "state.duckdb"),
            "--status-path",
            str(tmp_path / "live" / "status.json"),
            "--normalized-health-path",
            str(tmp_path / "live" / "normalized_health.json"),
            "--interval-seconds",
            "1.5",
            "--include-next",
            "--reprocess-all",
        ]
    )

    assert result == 0
    assert calls == [
        {
            "raw_root": tmp_path / "raw",
            "db_path": tmp_path / "state.duckdb",
            "status_path": tmp_path / "live" / "status.json",
            "normalized_health_path": tmp_path / "live" / "normalized_health.json",
            "probability_status_path": Path("data/live/probabilities.json"),
            "probability_inputs_path": Path("data/live/probability_inputs.json"),
            "probability_fragments_path": Path("data/live/probability_fragments.json"),
            "fragment_max_rows": 250_000,
            "outcome_status_path": Path("data/live/outcomes.json"),
            "volatility_status_path": Path("data/live/volatility.json"),
            "interval_seconds": 1.5,
            "include_next": True,
            "compute_probabilities": False,
            "enable_outcome_refresh": False,
            "reprocess_all": True,
        }
    ]


@pytest.mark.anyio
async def test_run_outcome_refresh_sidecar_command_dispatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_loop(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        "polymarket_engine.validation.outcome_sidecar.run_outcome_refresh_loop",
        fake_loop,
    )

    result = await cli.run_collect_command(
        [
            "run-outcome-refresh-sidecar",
            "--duckdb-path",
            str(tmp_path / "state.duckdb"),
            "--outcome-status-path",
            str(tmp_path / "live" / "outcomes.json"),
            "--interval-seconds",
            "15",
            "--max-cycles",
            "2",
        ]
    )

    assert result == 0
    assert calls == [
        {
            "duckdb_path": tmp_path / "state.duckdb",
            "outcome_status_path": tmp_path / "live" / "outcomes.json",
            "interval_seconds": 15.0,
            "max_cycles": 2,
        }
    ]


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
async def test_run_verify_hot_decision_replay_command_writes_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "state.duckdb"
    raw_root = tmp_path / "raw"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    start_ts = datetime(2026, 6, 2, 8, 10, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(seconds=72, milliseconds=500)
    token_id = "up-token"
    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=08"
        / "events.jsonl",
        [
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_ts": start_ts.isoformat(),
                "observed_ts": (start_ts + timedelta(milliseconds=100)).isoformat(),
                "payload": {"value": "70000.0"},
            },
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_ts": (asof_ts - timedelta(seconds=2)).isoformat(),
                "observed_ts": (asof_ts - timedelta(seconds=1)).isoformat(),
                "payload": {"value": "70050.0"},
            },
        ],
    )
    _write_jsonl(
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=08"
        / "events.jsonl",
        [
            {
                "source_key": "polymarket_clob_market_ws",
                "stream_key": "best_bid_ask",
                "symbol": token_id,
                "event_ts": (asof_ts - timedelta(seconds=3)).isoformat(),
                "observed_ts": (asof_ts - timedelta(milliseconds=500)).isoformat(),
                "payload": {
                    "contract_id": "0xcondition",
                    "token_id": token_id,
                    "best_bid": "0.61",
                    "best_ask": "0.64",
                    "spread": "0.03",
                },
            }
        ],
    )
    _write_jsonl(
        raw_root
        / "polymarket_decision_state"
        / "hot_state"
        / "date=2026-06-02"
        / "hour=08"
        / "decision-state.jsonl",
        [
            _hot_decision_row(start_ts, asof_ts),
            {
                **_hot_decision_row(start_ts, asof_ts + timedelta(seconds=1)),
                "threshold_price": None,
                "threshold_event_ts": None,
                "data_quality_flags": ["MissingThreshold"],
            },
            _hot_decision_row(start_ts, asof_ts + timedelta(minutes=2)),
        ],
    )
    normalize_rust_event_tree(raw_root=raw_root, store=store)
    report_path = tmp_path / "reports" / "hot-replay.json"

    result = await cli.run_collect_command(
        [
            "verify-hot-decision-replay",
            "--raw-root",
            str(raw_root),
            "--duckdb-path",
            str(db_path),
            "--limit",
            "1",
            "--scan-limit",
            "5",
            "--report-out",
            str(report_path),
        ]
    )

    assert result == 0
    stdout_payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert file_payload["ok"] is True
    assert file_payload["rows_scanned"] == 3
    assert file_payload["rows_checked"] == 1
    assert file_payload["rows_skipped_not_replay_ready"] == 1
    assert file_payload["rows_skipped_quality_blocked"] == 1
    assert file_payload["rows_skipped_not_replay_ready_by_reason"] == {
        "price_observed_after_watermark": 1,
        "orderbook_observed_after_watermark": 1,
    }
    assert file_payload["rows_skipped_quality_blocked_by_reason"] == {
        "MissingThreshold": 1,
    }
    assert file_payload["mismatch_count"] == 0
    assert file_payload["mismatches"] == []


@pytest.mark.anyio
async def test_run_calibration_report_command_writes_output_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "data" / "research" / "calibration" / "asof_decision_states.jsonl"
    out_path = tmp_path / "reports" / "nested" / "calibration.json"
    _write_jsonl(
        input_path,
        [
            _calibration_row(state_id="state-1", p_finish_mc=0.8, final_label=1),
            _calibration_row(state_id="state-2", p_finish_mc=0.2, final_label=0),
        ],
    )
    original_input = input_path.read_text(encoding="utf-8")

    result = await cli.run_collect_command(
        [
            "calibration-report",
            "--input",
            str(input_path),
            "--out",
            str(out_path),
        ]
    )

    assert result == 0
    assert input_path.read_text(encoding="utf-8") == original_input
    stdout_payload = json.loads(capsys.readouterr().out)
    file_payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert file_payload["schema_version"] == "polymarket-calibration-report-v1"
    assert file_payload["report_ready"] is True
    assert file_payload["input_row_count"] == 2
    assert file_payload["evaluated_row_count"] == 2
    assert file_payload["skipped_row_count"] == 0
    assert round(file_payload["brier_score"], 4) == 0.04
    assert file_payload["bucket_counts"]["tte_0_60"] == 2


@pytest.mark.anyio
async def test_run_calibration_report_command_handles_malformed_jsonl(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "data" / "research" / "calibration" / "bad.jsonl"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text("[1, 2, 3]\n", encoding="utf-8")
    out_path = tmp_path / "reports" / "calibration-error.json"

    result = await cli.run_collect_command(
        [
            "calibration-report",
            "--input",
            str(input_path),
            "--out",
            str(out_path),
        ]
    )

    captured = capsys.readouterr()
    file_payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert result == 1
    assert captured.out == ""
    assert "Traceback" not in captured.err
    stderr_payload = json.loads(captured.err)
    assert stderr_payload == file_payload
    assert file_payload == {
        "error": "invalid calibration JSONL at line 1: row must be an object",
        "ok": False,
    }


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


@pytest.mark.anyio
async def test_sync_cluster_artifacts_dry_run_prints_rsync_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "cluster.local.example.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-cluster-manifest-v1",
                "nodes": {
                    "spoon": {"host": "spoon", "role": "cpu_authority"},
                    "thepc": {"host": "thepc-lan", "role": "gpu_api"},
                },
                "artifacts": {
                    "probability_inputs.json": {
                        "owner": "spoon",
                        "canonical_path": "/home/spoon/polymarket-data/live/probability_inputs.json",
                        "mirrors": {
                            "thepc": "/home/ender/polymarket-data/live/probability_inputs.json"
                        },
                    },
                    "probabilities.json": {
                        "owner": "thepc",
                        "canonical_path": "/home/ender/polymarket-data/live/probabilities.json",
                        "mirrors": {
                            "spoon": "/home/spoon/polymarket-data/live/probabilities.thepc.json"
                        },
                    },
                },
                "mirror": {
                    "source_node": "spoon",
                    "target_node": "thepc",
                    "max_age_seconds": 5.0,
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("did not expect execution in dry-run")
        ),
    )

    result = await cli.run_collect_command(
        ["sync-cluster-artifacts", "--manifest-path", str(manifest_path)]
    )

    assert result == 0
    assert (
        capsys.readouterr().out.strip()
        == "rsync -az --delay-updates --partial --timeout=5 spoon:/home/spoon/polymarket-data/live/probability_inputs.json /home/ender/polymarket-data/live/probability_inputs.json"
    )


@pytest.mark.anyio
async def test_sync_cluster_artifacts_execute_runs_rsync_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "cluster.local.example.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-cluster-manifest-v1",
                "nodes": {
                    "spoon": {"host": "spoon", "role": "cpu_authority"},
                    "thepc": {"host": "thepc-lan", "role": "gpu_api"},
                },
                "artifacts": {
                    "probability_inputs.json": {
                        "owner": "spoon",
                        "canonical_path": "/home/spoon/polymarket-data/live/probability_inputs.json",
                        "mirrors": {
                            "thepc": "/home/ender/polymarket-data/live/probability_inputs.json"
                        },
                    }
                },
                "mirror": {
                    "source_node": "spoon",
                    "target_node": "thepc",
                    "max_age_seconds": 5.0,
                },
            }
        ),
        encoding="utf-8",
    )

    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = await cli.run_collect_command(
        ["sync-cluster-artifacts", "--manifest-path", str(manifest_path), "--execute"]
    )

    assert result == 0
    assert commands == [
        [
            "rsync",
            "-az",
            "--delay-updates",
            "--partial",
            "--timeout=5",
            "spoon:/home/spoon/polymarket-data/live/probability_inputs.json",
            "/home/ender/polymarket-data/live/probability_inputs.json",
        ]
    ]


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


def _hot_decision_row(start_ts: datetime, asof_ts: datetime) -> dict[str, object]:
    return {
        "schema_version": "rust-hot-decision-state-v1",
        "state_id": f"btc-updown-5m-{int(start_ts.timestamp())}:UP:{asof_ts.isoformat()}",
        "trigger_kind": "OrderBookTopOfBook",
        "trigger_symbol": None,
        "trigger_token_id": "up-token",
        "asof_ts": asof_ts.isoformat(),
        "contract": {
            "window": {
                "asset": "BTC",
                "interval": "5m",
                "start_ts": start_ts.isoformat(),
                "end_ts": (start_ts + timedelta(minutes=5)).isoformat(),
            },
            "up": {"asset": "BTC", "side": "Up", "token_id": "up-token"},
            "down": {"asset": "BTC", "side": "Down", "token_id": "down-token"},
        },
        "side": "Up",
        "token_id": "up-token",
        "threshold_price": "70000.0",
        "threshold_event_ts": start_ts.isoformat(),
        "settlement_price": "70050.0",
        "settlement_event_ts": (asof_ts - timedelta(seconds=2)).isoformat(),
        "best_bid": "0.61",
        "best_ask": "0.64",
        "executable_price": "0.64",
        "spread": "0.03",
        "source_age_ms": 1000,
        "book_age_ms": 500,
        "data_quality_flags": [],
        "latency": {
            "trigger_event_to_observed_ms": 2500,
            "observed_to_state_us": 100,
            "state_to_persist_us": None,
            "total_event_to_persist_ms": None,
        },
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(row, sort_keys=True)}\n" for row in rows),
        encoding="utf-8",
    )


def _calibration_row(
    *,
    state_id: str,
    p_finish_mc: float,
    final_label: int,
) -> dict[str, object]:
    return {
        "state_id": state_id,
        "contract_id": "condition-1",
        "market_slug": "btc-updown-5m-1781102700",
        "asset": "BTC",
        "side": "UP",
        "asof_ts": "2026-06-10T12:00:00+00:00",
        "expiry_ts": "2026-06-10T12:05:00+00:00",
        "tte_seconds": 45,
        "k": 65000.0,
        "current_price": 65020.0,
        "distance_to_threshold": 20.0,
        "z_path": 0.2,
        "sigma_tau": 0.015,
        "p_finish_mc": p_finish_mc,
        "p_no_touch_mc": 0.64,
        "spread": 0.02,
        "best_bid": 0.79,
        "best_ask": 0.81,
        "midpoint": 0.8,
        "visible_depth": 1800.0,
        "orderbook_imbalance": 0.35,
        "quote_age_ms": 250.0,
        "source_age_ms": 1000.0,
        "volatility_regime": "normal",
        "probability_model_version": "mc-v1",
        "skip_or_block_reason": None,
        "final_label": final_label,
        "resolved_outcome": "UP",
        "settlement_price_at_expiry": 65080.0,
    }
