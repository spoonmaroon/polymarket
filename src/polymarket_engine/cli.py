from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from polymarket_engine.ingestion.live_collector import LiveCollectorConfig, LiveCollectorResult


CollectorRunner = Callable[[LiveCollectorConfig], Awaitable[LiveCollectorResult]]


def _asset_tuple(value: str) -> tuple[str, ...]:
    return tuple(asset.strip().upper() for asset in value.split(",") if asset.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="polymarket-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--assets", type=_asset_tuple, default=("BTC", "ETH"))
    collect.add_argument("--duration", type=int, default=None)
    collect.add_argument("--forever", action="store_true")
    collect.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    collect.add_argument("--duckdb-path", type=Path, default=Path("data/db/polymarket.duckdb"))
    collect.add_argument("--max-batch-size", type=int, default=100)
    collect.add_argument("--windows-to-track", type=int, default=2)
    collect.add_argument("--snapshot-interval", type=float, default=1.0)
    collect.add_argument("--market-refresh-interval", type=float, default=30.0)

    monitor = subparsers.add_parser("monitor")
    monitor.add_argument("--duckdb-path", type=Path, default=Path("data/db/polymarket.duckdb"))
    monitor.add_argument("--refresh", type=float, default=1.0)
    monitor.add_argument("--limit", type=int, default=8)

    return parser.parse_args(argv)


async def run_collect_command(
    argv: list[str] | None = None,
    runner: CollectorRunner | None = None,
) -> int:
    from polymarket_engine.ingestion.live_collector import run_live_collection

    args = parse_args(argv)
    if args.command == "monitor":
        from polymarket_engine.monitor import run_monitor

        return await run_monitor(args.duckdb_path, args.refresh, args.limit)
    if args.command != "collect":
        return 2
    if args.duration is None and not args.forever:
        raise SystemExit("collect requires --duration or --forever")
    selected_runner = run_live_collection if runner is None else runner
    config = LiveCollectorConfig(
        assets=args.assets,
        duration_seconds=None if args.forever else args.duration,
        raw_root=args.raw_root,
        duckdb_path=args.duckdb_path,
        max_batch_size=args.max_batch_size,
        windows_to_track=args.windows_to_track,
        clob_snapshot_interval_seconds=args.snapshot_interval,
        market_refresh_interval_seconds=args.market_refresh_interval,
    )
    result = await selected_runner(config)
    print(
        {
            "events_written": result.events_written,
            "files_written": result.files_written,
            "source_errors": result.source_errors,
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_collect_command(argv))
