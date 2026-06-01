from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


@dataclass(frozen=True)
class MonitorSnapshot:
    generated_at: datetime
    prices: dict[tuple[str, str], float]
    price_rows: tuple[dict[str, Any], ...]
    orderbooks: tuple[dict[str, Any], ...]
    contracts: tuple[dict[str, Any], ...]
    ingest_counts: tuple[dict[str, Any], ...]


def fetch_monitor_snapshot(duckdb_path: Path, limit: int = 8) -> MonitorSnapshot:
    if not duckdb_path.exists():
        return MonitorSnapshot(
            generated_at=datetime.now(timezone.utc),
            prices={},
            price_rows=(),
            orderbooks=(),
            contracts=(),
            ingest_counts=(),
        )

    with duckdb.connect(str(duckdb_path), read_only=True) as conn:
        price_rows = tuple(
            _dict_rows(
                conn.sql(
                    """
                    select source_key, symbol, cast(observed_ts as varchar) as observed_ts, price
                    from (
                        select
                            source_key,
                            symbol,
                            observed_ts,
                            event_ts,
                            price,
                            row_number() over (
                                partition by source_key, symbol
                                order by observed_ts desc, event_ts desc
                            ) as rn
                        from core.price_ticks
                    )
                    where rn = 1
                    order by source_key, symbol
                    """
                ).fetchall(),
                ("source_key", "symbol", "observed_ts", "price"),
            )
        )
        orderbooks = tuple(
            _dict_rows(
                conn.sql(
                    """
                    select
                        venue,
                        contract_id,
                        token_id,
                        cast(observed_ts as varchar) as observed_ts,
                        best_bid,
                        best_ask,
                        spread,
                        bid_size_top,
                        ask_size_top
                    from core.orderbook_snapshots
                    order by observed_ts desc
                    limit ?
                    """,
                    params=[limit],
                ).fetchall(),
                (
                    "venue",
                    "contract_id",
                    "token_id",
                    "observed_ts",
                    "best_bid",
                    "best_ask",
                    "spread",
                    "bid_size_top",
                    "ask_size_top",
                ),
            )
        )
        contracts = tuple(
            _dict_rows(
                conn.sql(
                    """
                    select
                        contract_id,
                        asset,
                        side,
                        token_id,
                        threshold_type,
                        settlement_symbol,
                        cast(start_ts as varchar) as start_ts,
                        cast(expiry_ts as varchar) as expiry_ts
                    from core.contracts
                    order by expiry_ts desc, asset, side
                    limit ?
                    """,
                    params=[limit],
                ).fetchall(),
                (
                    "contract_id",
                    "asset",
                    "side",
                    "token_id",
                    "threshold_type",
                    "settlement_symbol",
                    "start_ts",
                    "expiry_ts",
                ),
            )
        )
        ingest_counts = tuple(
            _dict_rows(
                conn.sql(
                    """
                    select
                        source_key,
                        stream_key,
                        count(*) as files,
                        coalesce(sum(row_count), 0) as rows,
                        cast(max(last_event_ts) as varchar) as last_event_ts
                    from ops.ingest_files
                    group by source_key, stream_key
                    order by source_key, stream_key
                    """
                ).fetchall(),
                ("source_key", "stream_key", "files", "rows", "last_event_ts"),
            )
        )

    prices = {
        (str(row["source_key"]), str(row["symbol"])): float(row["price"]) for row in price_rows
    }
    return MonitorSnapshot(
        generated_at=datetime.now(timezone.utc),
        prices=prices,
        price_rows=price_rows,
        orderbooks=orderbooks,
        contracts=contracts,
        ingest_counts=ingest_counts,
    )


def render_monitor(snapshot: MonitorSnapshot) -> str:
    lines = [
        "Polymarket Engine Monitor | READ ONLY",
        f"generated_at={snapshot.generated_at.isoformat()}",
        "",
        "Prices",
    ]
    if snapshot.price_rows:
        for row in snapshot.price_rows:
            lines.append(
                f"  {row['source_key']:<28} {row['symbol']:<8} "
                f"{_fmt_float(row['price'], 4):>12}  obs={row['observed_ts']}"
            )
    else:
        lines.append("  no price ticks yet")

    lines.extend(["", "Active Contracts"])
    if snapshot.contracts:
        for row in snapshot.contracts:
            lines.append(
                f"  {row['asset']:<3} {row['side']:<4} {row['contract_id']:<14} "
                f"{_short_token(row['token_id'])} {row['settlement_symbol']:<7} "
                f"{row['start_ts']} -> {row['expiry_ts']}"
            )
    else:
        lines.append("  no contracts yet")

    lines.extend(["", "Order Books"])
    if snapshot.orderbooks:
        for row in snapshot.orderbooks:
            lines.append(
                f"  {row['contract_id'][:18]:<18} {_short_token(row['token_id'])} "
                f"bid={_fmt_float(row['best_bid'], 2):>5} "
                f"ask={_fmt_float(row['best_ask'], 2):>5} "
                f"spr={_fmt_float(row['spread'], 2):>5} obs={row['observed_ts']}"
            )
    else:
        lines.append("  no order books yet")

    lines.extend(["", "Ingest"])
    if snapshot.ingest_counts:
        for row in snapshot.ingest_counts:
            lines.append(
                f"  {row['source_key']:<24} {row['stream_key']:<28} "
                f"files={row['files']:<4} rows={row['rows']:<6} last={row['last_event_ts']}"
            )
    else:
        lines.append("  no ingest files yet")

    return "\n".join(lines)


async def run_monitor(duckdb_path: Path, refresh_seconds: float, limit: int) -> int:
    while True:
        snapshot = fetch_monitor_snapshot(duckdb_path, limit=limit)
        print("\033[2J\033[H" + render_monitor(snapshot), flush=True)
        await asyncio.sleep(refresh_seconds)


def _dict_rows(rows: list[tuple[Any, ...]], columns: tuple[str, ...]) -> list[dict[str, Any]]:
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _fmt_float(value: object, digits: int) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return f"{float(value):.{digits}f}"
    if isinstance(value, int | float):
        return f"{float(value):.{digits}f}"
    return "-"


def _short_token(value: object) -> str:
    text = str(value)
    if len(text) <= 14:
        return text
    return f"{text[:10]}..."
