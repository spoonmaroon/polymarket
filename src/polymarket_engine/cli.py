from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import signal
import subprocess as subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Protocol


RETIRED_COLLECTOR_MESSAGE = (
    "Python live collection is retired and cannot be started from this CLI. "
    "Use the Rust live probe instead: cd rust && cargo run -p polymarket-live-probe -- "
    "--assets BTC,ETH --interval 5m --windows 1"
)


class _SignalLoop(Protocol):
    def add_signal_handler(self, sig: signal.Signals, callback: Callable[[], object]) -> None: ...

    def remove_signal_handler(self, sig: signal.Signals) -> bool: ...


class _CancellableTask(Protocol):
    def cancel(self) -> bool | None: ...


def _asset_tuple(value: str) -> tuple[str, ...]:
    return tuple(asset.strip().upper() for asset in value.split(",") if asset.strip())


def _interval_tuple(value: str) -> tuple[str, ...]:
    return tuple(interval.strip().lower() for interval in value.split(",") if interval.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="polymarket-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--assets", type=_asset_tuple, default=("BTC", "ETH"))
    collect.add_argument("--duration", type=int, default=None)
    collect.add_argument("--forever", action="store_true")
    collect.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    collect.add_argument("--duckdb-path", type=Path, default=Path("data/db/polymarket.duckdb"))
    collect.add_argument("--status-path", type=Path, default=Path("data/live/status.json"))
    collect.add_argument("--max-batch-size", type=int, default=100)
    collect.add_argument("--windows-to-track", type=int, default=2)
    collect.add_argument("--intervals", type=_interval_tuple, default=("5m", "15m"))
    collect.add_argument(
        "--disable-clob-websocket",
        dest="enable_clob_websocket",
        action="store_false",
    )
    collect.set_defaults(enable_clob_websocket=True)
    collect.add_argument("--snapshot-interval", type=float, default=1.0)
    collect.add_argument("--clob-rest-backup-interval", type=float, default=5.0)
    collect.add_argument("--clob-request-timeout", type=float, default=2.0)
    collect.add_argument("--market-refresh-interval", type=float, default=30.0)
    collect.add_argument("--market-fetch-timeout", type=float, default=10.0)
    collect.add_argument("--coinbase-min-record-interval", type=float, default=1.0)
    collect.add_argument("--display-timezone", default="America/Chicago")

    monitor = subparsers.add_parser("monitor")
    monitor.add_argument("--duckdb-path", type=Path, default=Path("data/db/polymarket.duckdb"))
    monitor.add_argument("--status-path", type=Path, default=Path("data/live/status.json"))
    monitor.add_argument("--refresh", type=float, default=1.0)
    monitor.add_argument("--limit", type=int, default=8)

    normalize = subparsers.add_parser("normalize-rust-events")
    normalize_source = normalize.add_mutually_exclusive_group(required=True)
    normalize_source.add_argument("--raw-root", type=Path)
    normalize_source.add_argument("--file", type=Path)
    normalize.add_argument("--duckdb-path", type=Path, required=True)
    normalize.add_argument(
        "--skip-apply-schema",
        action="store_true",
        help="Do not apply the DuckDB schema before normalizing.",
    )
    normalize.add_argument(
        "--include-state-snapshots",
        action="store_true",
        help="Also normalize repeated state-manager snapshot rows.",
    )
    normalize.add_argument(
        "--reprocess-all",
        action="store_true",
        help="Ignore raw-file byte checkpoints and reprocess complete JSONL files.",
    )

    normalized_health = subparsers.add_parser("write-normalized-health")
    normalized_health.add_argument("--duckdb-path", type=Path, required=True)
    normalized_health.add_argument("--out", type=Path, required=True)

    current_states = subparsers.add_parser("build-current-decision-states")
    current_states.add_argument("--duckdb-path", type=Path, required=True)
    current_states.add_argument("--status-path", type=Path, required=True)
    current_states.add_argument(
        "--include-next",
        action="store_true",
        help="Also build states for the next warmed contract window.",
    )

    cuda_probability_worker = subparsers.add_parser("run-cuda-probability-worker")
    cuda_probability_worker.add_argument("--duckdb-path", type=Path, required=True)
    cuda_probability_worker.add_argument(
        "--probability-status-path",
        type=Path,
        default=Path("data/live/probabilities.json"),
    )
    cuda_probability_worker.add_argument(
        "--recovery-status-path",
        type=Path,
        default=Path("data/live/recovery_status.json"),
    )
    cuda_probability_worker.add_argument(
        "--offload-status-path",
        type=Path,
        default=Path("data/live/offload_status.json"),
    )
    cuda_probability_worker.add_argument(
        "--probability-inputs-path",
        type=Path,
        default=Path("data/live/probability_inputs.json"),
    )
    cuda_probability_worker.add_argument(
        "--probability-fragments-path",
        type=Path,
        default=Path("data/live/probability_fragments.json"),
    )
    cuda_probability_worker.add_argument("--interval-seconds", type=float, default=1.0)
    cuda_probability_worker.add_argument("--limit", type=int, default=24)
    cuda_probability_worker.add_argument("--valid-seconds", type=int, default=30)
    cuda_probability_worker.add_argument("--max-state-age-seconds", type=float, default=600.0)
    cuda_probability_worker.add_argument(
        "--max-input-snapshot-age-seconds",
        type=float,
        default=30.0,
    )
    cuda_probability_worker.add_argument("--worker-mode", default="ensemble")
    cuda_probability_worker.add_argument(
        "--generator-policy",
        default="all_four_every_cycle",
    )
    cuda_probability_worker.add_argument("--cpu-target-percent", type=float, default=15.0)
    cuda_probability_worker.add_argument("--cpu-soft-max-percent", type=float, default=20.0)
    cuda_probability_worker.add_argument("--max-rss-mb", type=int, default=512)
    cuda_probability_worker.add_argument("--max-cycle-runtime-ms", type=int, default=10_000)
    cuda_probability_worker.add_argument("--max-total-paths", type=int, default=80_000)
    cuda_probability_worker.add_argument("--min-total-paths", type=int, default=20_000)
    cuda_probability_worker.add_argument("--sustained-breach-cycles", type=int, default=3)
    cuda_probability_worker.add_argument("--fragment-max-rows", type=int, default=250_000)
    cuda_probability_worker.add_argument(
        "--use-prior-fragments",
        action="store_true",
        help="Opt into uncalibrated live probability fragment priors for research runs.",
    )
    cuda_probability_worker.add_argument("--cpu-threads", type=int, default=1)
    cuda_probability_worker.add_argument(
        "--once",
        action="store_true",
        help="Run one CUDA probability worker cycle and exit.",
    )

    sidecar = subparsers.add_parser("run-rust-normalizer-sidecar")
    sidecar.add_argument("--raw-root", type=Path, required=True)
    sidecar.add_argument("--duckdb-path", type=Path, required=True)
    sidecar.add_argument("--status-path", type=Path, required=True)
    sidecar.add_argument("--normalized-health-path", type=Path, required=True)
    sidecar.add_argument(
        "--probability-status-path",
        type=Path,
        default=Path("data/live/probabilities.json"),
    )
    sidecar.add_argument(
        "--probability-inputs-path",
        type=Path,
        default=Path("data/live/probability_inputs.json"),
    )
    sidecar.add_argument(
        "--probability-fragments-path",
        type=Path,
        default=Path("data/live/probability_fragments.json"),
    )
    sidecar.add_argument("--fragment-max-rows", type=int, default=250_000)
    sidecar.add_argument(
        "--outcome-status-path",
        type=Path,
        default=Path("data/live/outcomes.json"),
    )
    sidecar.add_argument(
        "--volatility-status-path",
        type=Path,
        default=Path("data/live/volatility.json"),
    )
    sidecar.add_argument("--interval-seconds", type=float, default=0.25)
    sidecar.add_argument(
        "--include-next",
        action="store_true",
        help="Also build states for the next warmed contract window.",
    )
    sidecar.add_argument(
        "--enable-probabilities",
        action="store_true",
        help="Opt in to runtime probability computation; disabled by default.",
    )
    sidecar.add_argument(
        "--enable-outcome-refresh",
        action="store_true",
        help="Opt in to official outcome refresh from the normalizer loop.",
    )
    sidecar.add_argument(
        "--reprocess-all",
        action="store_true",
        help="Ignore raw-file byte checkpoints and reprocess complete JSONL files.",
    )
    sidecar.add_argument(
        "--once",
        action="store_true",
        help="Run one sidecar cycle and exit.",
    )

    outcome_sidecar = subparsers.add_parser("run-outcome-refresh-sidecar")
    outcome_sidecar.add_argument("--duckdb-path", type=Path, required=True)
    outcome_sidecar.add_argument("--outcome-status-path", type=Path, required=True)
    outcome_sidecar.add_argument("--interval-seconds", type=float, default=30.0)
    outcome_sidecar.add_argument("--max-cycles", type=int, default=None)

    verify_hot_replay = subparsers.add_parser("verify-hot-decision-replay")
    verify_hot_replay.add_argument("--raw-root", type=Path, required=True)
    verify_hot_replay.add_argument("--duckdb-path", type=Path, required=True)
    verify_hot_replay.add_argument("--limit", type=int, default=40)
    verify_hot_replay.add_argument("--scan-limit", type=int, default=1000)
    verify_hot_replay.add_argument("--report-out", type=Path, default=None)

    backfill_outcomes = subparsers.add_parser("backfill-outcomes")
    backfill_outcomes.add_argument("--duckdb-path", type=Path, required=True)
    backfill_outcomes.add_argument("--outcomes-path", type=Path, required=True)
    backfill_outcomes.add_argument("--start-date", default=None)
    backfill_outcomes.add_argument("--end-date", default=None)
    backfill_outcomes.add_argument("--limit", type=int, default=500)
    backfill_outcomes.add_argument("--write", action="store_true")
    backfill_outcomes.add_argument("--official-outcome-source", default="clob")
    backfill_outcomes.add_argument("--official-timeout-seconds", type=float, default=2.0)

    calibration_report = subparsers.add_parser("calibration-report")
    calibration_report.add_argument(
        "--input",
        type=Path,
        default=Path("data/research/calibration/asof_decision_states.jsonl"),
    )
    calibration_report.add_argument("--out", type=Path, required=True)

    run_backtest = subparsers.add_parser("run-backtest")
    run_backtest.add_argument("--input", type=Path, required=True)
    run_backtest.add_argument("--out", type=Path, required=True)
    run_backtest.add_argument("--probability-field", default="p_finish_mc")
    run_backtest.add_argument("--stake-usd", type=float, default=100.0)
    run_backtest.add_argument("--min-edge", type=float, default=0.02)
    run_backtest.add_argument("--max-quote-age-ms", type=int, default=1000)
    run_backtest.add_argument("--fee-rate", type=float, default=0.0)

    export_calibration = subparsers.add_parser("export-calibration-dataset")
    export_calibration.add_argument("--duckdb-path", type=Path, required=True)
    export_calibration.add_argument(
        "--out",
        type=Path,
        default=Path("data/research/calibration/asof_decision_states.jsonl"),
    )
    export_calibration.add_argument("--start-ts", default=None)
    export_calibration.add_argument("--end-ts", default=None)
    export_calibration.add_argument("--include-unlabeled", action="store_true")
    export_calibration.add_argument("--limit", type=int, default=10_000)

    runtime_keeper = subparsers.add_parser("runtime-keeper")
    runtime_keeper.add_argument("--repo", type=Path, default=Path("/home/ender/polymarket"))
    runtime_keeper.add_argument("--data-dir", type=Path, default=Path("/home/ender/polymarket-data"))
    runtime_keeper.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    runtime_keeper.add_argument(
        "--compose-file",
        type=Path,
        action="append",
        default=None,
        help="Docker Compose file to use. Repeatable, preserving order.",
    )
    runtime_keeper.add_argument(
        "--required-service",
        action="append",
        default=None,
        help="Compose service required to be running. Repeatable.",
    )
    runtime_keeper.add_argument(
        "--optional-container",
        action="append",
        default=None,
        help="Existing Docker container to start and check when present. Repeatable.",
    )
    runtime_keeper.add_argument("--loop", action="store_true")
    runtime_keeper.add_argument("--loop-interval-seconds", type=float, default=30.0)
    runtime_keeper.add_argument("--recovery-warmup-min-seconds", type=int, default=60)
    runtime_keeper.add_argument("--recovery-required-healthy-cycles", type=int, default=3)

    cluster_sync = subparsers.add_parser("sync-cluster-artifacts")
    cluster_sync.add_argument(
        "--manifest-path",
        type=Path,
        default=Path("deploy/cluster/cluster.local.example.json"),
    )
    cluster_sync.add_argument(
        "--execute",
        action="store_true",
        help="Run rsync commands. Omit for dry-run output only.",
    )

    return parser.parse_args(argv)


async def run_collect_command(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "monitor":
        from polymarket_engine.monitor import run_monitor

        return await run_monitor(args.duckdb_path, args.refresh, args.limit, args.status_path)
    if args.command == "normalize-rust-events":
        return _run_normalize_rust_events(args)
    if args.command == "write-normalized-health":
        return _run_write_normalized_health(args)
    if args.command == "build-current-decision-states":
        return _run_build_current_decision_states(args)
    if args.command == "run-cuda-probability-worker":
        return _run_cuda_probability_worker(args)
    if args.command == "run-rust-normalizer-sidecar":
        return _run_rust_normalizer_sidecar(args)
    if args.command == "run-outcome-refresh-sidecar":
        return _run_outcome_refresh_sidecar(args)
    if args.command == "verify-hot-decision-replay":
        return _run_verify_hot_decision_replay(args)
    if args.command == "backfill-outcomes":
        return _run_backfill_outcomes(args)
    if args.command == "calibration-report":
        return _run_calibration_report(args)
    if args.command == "run-backtest":
        return _run_backtest(args)
    if args.command == "export-calibration-dataset":
        return _run_export_calibration_dataset(args)
    if args.command == "runtime-keeper":
        return _run_runtime_keeper(args)
    if args.command == "sync-cluster-artifacts":
        return _run_sync_cluster_artifacts(args)
    if args.command != "collect":
        return 2
    raise SystemExit(RETIRED_COLLECTOR_MESSAGE)


def _run_normalize_rust_events(args: argparse.Namespace) -> int:
    from polymarket_engine.ingestion.rust_event_normalizer import (
        RustEventNormalizeResult,
        normalize_rust_event_file,
        normalize_rust_event_tree,
    )
    from polymarket_engine.storage.duckdb_store import DuckDbIngestStore

    store = DuckDbIngestStore(args.duckdb_path)
    if not args.skip_apply_schema:
        store.apply_schema()
    results: tuple[RustEventNormalizeResult, ...]
    if args.file is not None:
        results = (
            normalize_rust_event_file(
                path=args.file,
                store=store,
                reprocess_all=args.reprocess_all,
            ),
        )
    else:
        results = normalize_rust_event_tree(
            raw_root=args.raw_root,
            store=store,
            include_state_snapshots=args.include_state_snapshots,
            reprocess_all=args.reprocess_all,
        )
    summary = {
        "files": len(results),
        "files_with_rows": sum(1 for result in results if result.rows_read > 0),
        "files_skipped": sum(1 for result in results if result.rows_read == 0),
        "bytes_read": sum(result.end_byte_offset - result.start_byte_offset for result in results),
        "file_size_bytes": sum(result.file_size_bytes for result in results),
        "rows_read": sum(result.rows_read for result in results),
        "price_ticks_written": sum(result.price_ticks_written for result in results),
        "orderbooks_written": sum(result.orderbooks_written for result in results),
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


def _run_write_normalized_health(args: argparse.Namespace) -> int:
    from polymarket_engine.health.normalized_status import write_normalized_health_status
    from polymarket_engine.storage.duckdb_store import DuckDbIngestStore

    store = DuckDbIngestStore(args.duckdb_path)
    status = write_normalized_health_status(store=store, out_path=args.out)
    print(json.dumps(status, sort_keys=True))
    return 0


def _run_build_current_decision_states(args: argparse.Namespace) -> int:
    from polymarket_engine.features.rust_decision_snapshots import (
        build_current_decision_state_snapshots,
    )
    from polymarket_engine.storage.duckdb_store import DuckDbIngestStore

    store = DuckDbIngestStore(args.duckdb_path)
    result = build_current_decision_state_snapshots(
        status_path=args.status_path,
        store=store,
        include_next=args.include_next,
    )
    print(
        json.dumps(
            {
                "asof_ts": result.asof_ts.isoformat(),
                "contracts_upserted": result.contracts_upserted,
                "states_written": result.states_written,
                "unavailable": [
                    {
                        "contract_id": row.contract_id,
                        "token_id": row.token_id,
                        "reason": row.reason,
                    }
                    for row in result.unavailable
                ],
            },
            sort_keys=True,
        )
    )
    return 0


def _run_cuda_probability_worker(args: argparse.Namespace) -> int:
    from polymarket_engine.probability.gpu_worker import ProbabilityWorkerBudget
    from polymarket_engine.probability.gpu_worker import run_cuda_probability_worker_cycle
    from polymarket_engine.probability.gpu_worker import run_cuda_probability_worker_loop

    budget = ProbabilityWorkerBudget(
        worker_mode=args.worker_mode,
        generator_policy=args.generator_policy,
        cpu_target_percent=args.cpu_target_percent,
        cpu_soft_max_percent=args.cpu_soft_max_percent,
        max_rss_mb=args.max_rss_mb,
        max_cycle_runtime_ms=args.max_cycle_runtime_ms,
        max_total_paths=args.max_total_paths,
        min_total_paths=args.min_total_paths,
        sustained_breach_cycles=args.sustained_breach_cycles,
        fragment_max_rows=args.fragment_max_rows,
        use_prior_fragments=args.use_prior_fragments,
        cpu_threads=args.cpu_threads,
    )
    if args.once:
        payload = run_cuda_probability_worker_cycle(
            duckdb_path=args.duckdb_path,
            probability_status_path=args.probability_status_path,
            recovery_status_path=args.recovery_status_path,
            offload_status_path=args.offload_status_path,
            probability_inputs_path=args.probability_inputs_path,
            probability_fragments_path=args.probability_fragments_path,
            limit=args.limit,
            valid_seconds=args.valid_seconds,
            max_state_age_seconds=args.max_state_age_seconds,
            max_input_snapshot_age_seconds=args.max_input_snapshot_age_seconds,
            budget=budget,
        )
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0 if payload.get("ok") else 1
    run_cuda_probability_worker_loop(
        duckdb_path=args.duckdb_path,
        probability_status_path=args.probability_status_path,
        recovery_status_path=args.recovery_status_path,
        offload_status_path=args.offload_status_path,
        probability_inputs_path=args.probability_inputs_path,
        probability_fragments_path=args.probability_fragments_path,
        interval_seconds=args.interval_seconds,
        limit=args.limit,
        valid_seconds=args.valid_seconds,
        max_state_age_seconds=args.max_state_age_seconds,
        max_input_snapshot_age_seconds=args.max_input_snapshot_age_seconds,
        budget=budget,
    )
    return 0


def _run_rust_normalizer_sidecar(args: argparse.Namespace) -> int:
    from polymarket_engine.ingestion.rust_normalizer_sidecar import (
        run_rust_normalizer_cycle,
        run_rust_normalizer_loop,
    )

    if args.once:
        result = run_rust_normalizer_cycle(
            raw_root=args.raw_root,
            db_path=args.duckdb_path,
            status_path=args.status_path,
            normalized_health_path=args.normalized_health_path,
            probability_status_path=args.probability_status_path,
            probability_inputs_path=args.probability_inputs_path,
            probability_fragments_path=args.probability_fragments_path,
            fragment_max_rows=args.fragment_max_rows,
            outcome_status_path=args.outcome_status_path,
            volatility_status_path=args.volatility_status_path,
            include_next=args.include_next,
            compute_probabilities=args.enable_probabilities,
            refresh_outcomes=args.enable_outcome_refresh,
            reprocess_all=args.reprocess_all,
            apply_schema=True,
        )
        print(json.dumps(result.to_json_dict(), sort_keys=True, separators=(",", ":")))
        return 0
    run_rust_normalizer_loop(
        raw_root=args.raw_root,
        db_path=args.duckdb_path,
        status_path=args.status_path,
        normalized_health_path=args.normalized_health_path,
        probability_status_path=args.probability_status_path,
        probability_inputs_path=args.probability_inputs_path,
        probability_fragments_path=args.probability_fragments_path,
        fragment_max_rows=args.fragment_max_rows,
        outcome_status_path=args.outcome_status_path,
        volatility_status_path=args.volatility_status_path,
        interval_seconds=args.interval_seconds,
        include_next=args.include_next,
        compute_probabilities=args.enable_probabilities,
        enable_outcome_refresh=args.enable_outcome_refresh,
        reprocess_all=args.reprocess_all,
    )
    return 0


def _run_outcome_refresh_sidecar(args: argparse.Namespace) -> int:
    from polymarket_engine.validation.outcome_sidecar import run_outcome_refresh_loop

    run_outcome_refresh_loop(
        duckdb_path=args.duckdb_path,
        outcome_status_path=args.outcome_status_path,
        interval_seconds=args.interval_seconds,
        max_cycles=args.max_cycles,
    )
    return 0


def _run_verify_hot_decision_replay(args: argparse.Namespace) -> int:
    from polymarket_engine.features.hot_decision_replay import (
        recent_hot_decision_rows,
        replay_ready_hot_decision_rows,
        verify_hot_decision_rows,
    )
    from polymarket_engine.storage.duckdb_store import DuckDbIngestStore

    store = DuckDbIngestStore(args.duckdb_path)
    scanned_rows = recent_hot_decision_rows(args.raw_root, limit=args.scan_limit)
    selection = replay_ready_hot_decision_rows(
        rows=scanned_rows,
        store=store,
        limit=args.limit,
    )
    result = verify_hot_decision_rows(rows=selection.rows, store=store)
    payload = {
        "ok": result.ok and result.rows_checked > 0,
        "rows_scanned": selection.rows_scanned,
        "rows_checked": result.rows_checked,
        "rows_skipped_not_replay_ready": selection.rows_skipped_not_replay_ready,
        "rows_skipped_quality_blocked": selection.rows_skipped_quality_blocked,
        "rows_skipped_not_replay_ready_by_reason": selection.rows_skipped_not_replay_ready_by_reason,
        "rows_skipped_quality_blocked_by_reason": selection.rows_skipped_quality_blocked_by_reason,
        "mismatch_count": len(result.mismatches),
        "price_observed_watermark": _isoformat_optional(selection.price_observed_watermark),
        "orderbook_observed_watermark": _isoformat_optional(selection.orderbook_observed_watermark),
        "mismatches": [
            {
                "state_id": mismatch.state_id,
                "field": mismatch.field,
                "hot_value": mismatch.hot_value,
                "replay_value": mismatch.replay_value,
            }
            for mismatch in result.mismatches
        ],
    }
    if args.report_out is not None:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["ok"] else 1


def _run_backfill_outcomes(args: argparse.Namespace) -> int:
    from polymarket_engine.validation.outcomes import (
        PolymarketClobMarketPayloadSource,
        backfill_outcome_history,
    )

    market_payload_source = None
    if args.official_outcome_source == "clob":
        market_payload_source = PolymarketClobMarketPayloadSource(
            timeout_seconds=args.official_timeout_seconds,
        )
    report = backfill_outcome_history(
        duckdb_path=args.duckdb_path,
        outcomes_path=args.outcomes_path,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
        write=args.write,
        market_payload_source=market_payload_source,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report.get("ok") is True else 1


def _run_calibration_report(args: argparse.Namespace) -> int:
    from polymarket_engine.calibration.reports import build_calibration_report
    from polymarket_engine.calibration.reports import load_calibration_jsonl

    try:
        rows = load_calibration_jsonl(args.input)
    except ValueError as exc:
        payload: dict[str, object] = {"ok": False, "error": str(exc)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")),
            file=sys.stderr,
        )
        return 1

    report = build_calibration_report(rows)
    payload = report.to_json_dict()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")))
    return 0


def _run_backtest(args: argparse.Namespace) -> int:
    from polymarket_engine.backtest.runner import BacktestRunConfig
    from polymarket_engine.backtest.runner import run_backtest

    try:
        report = run_backtest(
            BacktestRunConfig(
                input_path=args.input,
                out_path=args.out,
                probability_field=args.probability_field,
                stake_usd=args.stake_usd,
                min_edge=args.min_edge,
                max_quote_age_ms=args.max_quote_age_ms,
                fee_rate=args.fee_rate,
            )
        )
    except ValueError as exc:
        payload: dict[str, object] = {"ok": False, "error": str(exc)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(report.to_json_dict(), sort_keys=True, separators=(",", ":")))
    return 0


def _run_export_calibration_dataset(args: argparse.Namespace) -> int:
    from polymarket_engine.calibration.export import CalibrationExportConfig
    from polymarket_engine.calibration.export import export_calibration_dataset

    result = export_calibration_dataset(
        CalibrationExportConfig(
            duckdb_path=args.duckdb_path,
            out_path=args.out,
            start_ts=_parse_optional_cli_datetime(args.start_ts),
            end_ts=_parse_optional_cli_datetime(args.end_ts),
            include_unlabeled=args.include_unlabeled,
            limit=args.limit,
        )
    )
    print(json.dumps(result.to_json_dict(), sort_keys=True, separators=(",", ":")))
    return 0


def _run_runtime_keeper(args: argparse.Namespace) -> int:
    from polymarket_engine.ops.recovery_manager import RecoveryConfig
    from polymarket_engine.ops.runtime_keeper import DEFAULT_OPTIONAL_CONTAINERS
    from polymarket_engine.ops.runtime_keeper import DEFAULT_REQUIRED_SERVICES
    from polymarket_engine.ops.runtime_keeper import RuntimeKeeper
    from polymarket_engine.ops.runtime_keeper import RuntimeKeeperConfig

    config = RuntimeKeeperConfig(
        repo=args.repo,
        data_dir=args.data_dir,
        api_base_url=args.api_base_url,
        compose_files=tuple(args.compose_file or ()),
        required_services=tuple(args.required_service or DEFAULT_REQUIRED_SERVICES),
        optional_containers=tuple(args.optional_container or DEFAULT_OPTIONAL_CONTAINERS),
        loop_interval_seconds=args.loop_interval_seconds,
        recovery_config=RecoveryConfig(
            warmup_min_seconds=args.recovery_warmup_min_seconds,
            required_healthy_cycles=args.recovery_required_healthy_cycles,
        ),
    )
    keeper = RuntimeKeeper(config=config)
    if args.loop:
        keeper.run_loop()
        return 0
    payload = keeper.run_once()
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload.get("ok") is True else 1


def _run_sync_cluster_artifacts(args: argparse.Namespace) -> int:
    from polymarket_engine.cluster.artifact_sync import MirrorPlan
    from polymarket_engine.cluster.artifact_sync import build_rsync_command
    from polymarket_engine.cluster.manifest import load_cluster_manifest

    manifest = load_cluster_manifest(args.manifest_path)
    source_host = manifest.nodes[manifest.mirror.source_node].host

    commands: list[list[str]] = []
    for artifact in manifest.artifacts.values():
        if artifact.owner != manifest.mirror.source_node:
            continue
        target_path = artifact.mirrors.get(manifest.mirror.target_node)
        if target_path is None:
            continue
        command = build_rsync_command(
            MirrorPlan(
                source_host=source_host,
                source_path=artifact.canonical_path,
                target_path=target_path,
                timeout_seconds=int(manifest.mirror.max_age_seconds),
            )
        )
        print(" ".join(shlex.quote(token) for token in command))
        commands.append(command)

    if not args.execute:
        return 0

    last_code = 0
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            print(
                " ".join(shlex.quote(token) for token in command),
                file=sys.stderr,
            )
            if result.stdout:
                print(result.stdout, file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            last_code = result.returncode
            break
    return last_code


def _isoformat_optional(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _parse_optional_cli_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _install_shutdown_signal_handlers(
    loop: _SignalLoop,
    task: _CancellableTask,
) -> tuple[signal.Signals, ...]:
    installed: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(sig)
    return tuple(installed)


def _remove_shutdown_signal_handlers(
    loop: _SignalLoop,
    installed: tuple[signal.Signals, ...],
) -> None:
    for sig in installed:
        with suppress(NotImplementedError, RuntimeError):
            loop.remove_signal_handler(sig)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_collect_command(argv))
