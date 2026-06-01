from __future__ import annotations

import argparse
import asyncio
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

    return parser.parse_args(argv)


async def run_collect_command(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "monitor":
        from polymarket_engine.monitor import run_monitor

        return await run_monitor(args.duckdb_path, args.refresh, args.limit, args.status_path)
    if args.command != "collect":
        return 2
    raise SystemExit(RETIRED_COLLECTOR_MESSAGE)


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
