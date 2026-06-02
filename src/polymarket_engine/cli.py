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

    normalized_health = subparsers.add_parser("write-normalized-health")
    normalized_health.add_argument("--duckdb-path", type=Path, required=True)
    normalized_health.add_argument("--out", type=Path, required=True)

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
        results = (normalize_rust_event_file(path=args.file, store=store),)
    else:
        results = normalize_rust_event_tree(
            raw_root=args.raw_root,
            store=store,
            include_state_snapshots=args.include_state_snapshots,
        )
    summary = {
        "files": len(results),
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
