from __future__ import annotations

import argparse
import asyncio
import json
import signal
from collections.abc import Callable
from contextlib import suppress
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

    sidecar = subparsers.add_parser("run-rust-normalizer-sidecar")
    sidecar.add_argument("--raw-root", type=Path, required=True)
    sidecar.add_argument("--duckdb-path", type=Path, required=True)
    sidecar.add_argument("--status-path", type=Path, required=True)
    sidecar.add_argument("--normalized-health-path", type=Path, required=True)
    sidecar.add_argument("--interval-seconds", type=float, default=1.0)
    sidecar.add_argument(
        "--include-next",
        action="store_true",
        help="Also build states for the next warmed contract window.",
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
    if args.command == "run-rust-normalizer-sidecar":
        return _run_rust_normalizer_sidecar(args)
    if args.command == "verify-hot-decision-replay":
        return _run_verify_hot_decision_replay(args)
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
            include_next=args.include_next,
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
        interval_seconds=args.interval_seconds,
        include_next=args.include_next,
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


def _isoformat_optional(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


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
