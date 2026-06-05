from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import signal
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta, timezone
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

    probability_grid = subparsers.add_parser("build-probability-grid")
    probability_grid.add_argument("--duckdb-path", type=Path, required=True)
    probability_grid.add_argument("--assets", type=_asset_tuple, default=("BTC", "ETH"))
    probability_grid.add_argument("--limit", type=int, default=8)
    probability_grid.add_argument("--path-count", type=int, default=10_000)
    probability_grid.add_argument("--seed", type=int, default=None)
    probability_grid.add_argument("--valid-seconds", type=int, default=30)
    probability_grid.add_argument(
        "--generator-version",
        default="offline-lognormal-chainlink-sigma-v1",
    )
    probability_grid.add_argument("--model-version", default="cached-grid-v1")

    cuda_probability_worker = subparsers.add_parser("run-cuda-probability-worker")
    cuda_probability_worker.add_argument("--duckdb-path", type=Path, required=True)
    cuda_probability_worker.add_argument(
        "--probability-status-path",
        type=Path,
        default=Path("data/live/probabilities.json"),
    )
    cuda_probability_worker.add_argument("--probability-inputs-path", type=Path, default=None)
    cuda_probability_worker.add_argument("--interval-seconds", type=float, default=1.0)
    cuda_probability_worker.add_argument("--limit", type=int, default=24)
    cuda_probability_worker.add_argument("--valid-seconds", type=int, default=30)
    cuda_probability_worker.add_argument("--max-state-age-seconds", type=float, default=600.0)
    cuda_probability_worker.add_argument("--max-input-snapshot-age-seconds", type=float, default=10.0)
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
        "--reprocess-all",
        action="store_true",
        help="Ignore raw-file byte checkpoints and reprocess complete JSONL files.",
    )
    sidecar.add_argument(
        "--once",
        action="store_true",
        help="Run one sidecar cycle and exit.",
    )

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
    if args.command == "build-probability-grid":
        return _run_build_probability_grid(args)
    if args.command == "run-cuda-probability-worker":
        return _run_cuda_probability_worker(args)
    if args.command == "run-rust-normalizer-sidecar":
        return _run_rust_normalizer_sidecar(args)
    if args.command == "verify-hot-decision-replay":
        return _run_verify_hot_decision_replay(args)
    if args.command == "backfill-outcomes":
        return _run_backfill_outcomes(args)
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


def _run_build_probability_grid(args: argparse.Namespace) -> int:
    from polymarket_engine.probability.grid_cache import grid_entry_from_probability_input
    from polymarket_engine.probability.grid_cache import upsert_probability_grid_entry
    from polymarket_engine.probability.monte_carlo import run_seeded_monte_carlo
    from polymarket_engine.probability.runtime import DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS
    from polymarket_engine.probability.runtime import latest_probability_inputs
    from polymarket_engine.storage.duckdb_store import DuckDbIngestStore

    if args.limit <= 0:
        raise ValueError("limit must be positive")
    if args.path_count <= 0:
        raise ValueError("path_count must be positive")
    if args.valid_seconds <= 0:
        raise ValueError("valid_seconds must be positive")

    store = DuckDbIngestStore(args.duckdb_path)
    store.apply_schema()
    inputs, quality_skipped = latest_probability_inputs(
        duckdb_path=args.duckdb_path,
        limit=args.limit,
        max_state_age_seconds=DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS,
        active_only=True,
    )
    asset_filter = set(args.assets)
    generated_at = datetime.now(timezone.utc)
    rows_written = 0
    skipped_assets = 0
    errors: list[str] = []
    cache_rows: list[dict[str, object]] = []

    for index, runtime_input in enumerate(inputs):
        probability_input = runtime_input.probability_input
        if probability_input.asset not in asset_filter:
            skipped_assets += 1
            continue
        seed = (
            int(args.seed) + index
            if args.seed is not None
            else _probability_grid_seed(probability_input.state_id, probability_input.asof_ts)
        )
        try:
            output = run_seeded_monte_carlo(
                probability_input,
                path_count=args.path_count,
                steps=_probability_grid_steps(probability_input.seconds_left),
                seed=seed,
            )
            diagnostics = dict(output.diagnostics)
            diagnostics["cache"] = {
                "source": "build-probability-grid",
                "market_slug": runtime_input.market_slug,
                "start_ts": runtime_input.start_ts.isoformat(),
                "expiry_ts": runtime_input.expiry_ts.isoformat(),
                "asof_ts": probability_input.asof_ts.isoformat(),
            }
            entry = grid_entry_from_probability_input(
                probability_input,
                market_slug=runtime_input.market_slug,
                start_ts=runtime_input.start_ts,
                expiry_ts=runtime_input.expiry_ts,
                p_finish=output.p_finish,
                p_no_touch=output.p_no_touch,
                u_gen=0.0,
                path_count=args.path_count,
                seed=seed,
                volatility_regime=runtime_input.volatility_regime,
                generator_version=args.generator_version,
                model_version=args.model_version,
                training_cutoff_ts=probability_input.asof_ts,
                max_event_ts=probability_input.asof_ts,
                max_observed_ts=probability_input.asof_ts,
                generated_at=generated_at,
                valid_from=generated_at,
                valid_until=generated_at + timedelta(seconds=args.valid_seconds),
                diagnostics=diagnostics,
            )
            upsert_probability_grid_entry(store, entry)
        except ValueError as exc:
            errors.append(f"{probability_input.state_id}: {type(exc).__name__}: {exc}")
            continue
        rows_written += 1
        cache_rows.append(
            {
                "cache_key": entry.cache_key,
                "market_slug": runtime_input.market_slug,
                "start_ts": runtime_input.start_ts.isoformat(),
                "expiry_ts": runtime_input.expiry_ts.isoformat(),
                "asset": entry.asset,
                "side": entry.side,
                "asof_ts": probability_input.asof_ts.isoformat(),
                "generated_at": entry.generated_at.isoformat(),
                "valid_from": entry.valid_from.isoformat(),
                "valid_until": entry.valid_until.isoformat(),
                "path_count": entry.path_count,
            }
        )

    payload = {
        "ok": not errors,
        "generated_at": generated_at.isoformat(),
        "rows_seen": len(inputs),
        "rows_written": rows_written,
        "skipped_quality": quality_skipped,
        "skipped_assets": skipped_assets,
        "errors": errors,
        "cache_rows": cache_rows,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if not errors else 1


def _run_cuda_probability_worker(args: argparse.Namespace) -> int:
    from polymarket_engine.probability.gpu_worker import run_cuda_probability_worker_cycle
    from polymarket_engine.probability.gpu_worker import run_cuda_probability_worker_loop

    if args.once:
        payload = run_cuda_probability_worker_cycle(
            duckdb_path=args.duckdb_path,
            probability_status_path=args.probability_status_path,
            probability_inputs_path=args.probability_inputs_path,
            limit=args.limit,
            valid_seconds=args.valid_seconds,
            max_state_age_seconds=args.max_state_age_seconds,
            max_input_snapshot_age_seconds=args.max_input_snapshot_age_seconds,
        )
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0 if payload.get("ok") else 1
    run_cuda_probability_worker_loop(
        duckdb_path=args.duckdb_path,
        probability_status_path=args.probability_status_path,
        probability_inputs_path=args.probability_inputs_path,
        interval_seconds=args.interval_seconds,
        limit=args.limit,
        valid_seconds=args.valid_seconds,
        max_state_age_seconds=args.max_state_age_seconds,
        max_input_snapshot_age_seconds=args.max_input_snapshot_age_seconds,
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
            outcome_status_path=args.outcome_status_path,
            volatility_status_path=args.volatility_status_path,
            include_next=args.include_next,
            compute_probabilities=args.enable_probabilities,
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
        outcome_status_path=args.outcome_status_path,
        volatility_status_path=args.volatility_status_path,
        interval_seconds=args.interval_seconds,
        include_next=args.include_next,
        compute_probabilities=args.enable_probabilities,
        reprocess_all=args.reprocess_all,
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


def _isoformat_optional(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _probability_grid_seed(state_id: str, asof_ts: datetime) -> int:
    digest = hashlib.sha256(f"{state_id}|{asof_ts.isoformat()}".encode()).hexdigest()
    return int(digest[:8], 16)


def _probability_grid_steps(seconds_left: float) -> int:
    if seconds_left < 0 or not math.isfinite(seconds_left):
        raise ValueError("seconds_left must be nonnegative and finite")
    return max(1, min(300, int(math.ceil(seconds_left))))


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
