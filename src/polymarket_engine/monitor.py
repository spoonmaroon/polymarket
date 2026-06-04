from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import duckdb


CHAINLINK_SOURCE_KEY = "polymarket_rtds_chainlink"
TARGET_FIELDS = (
    "threshold_price",
    "threshold_event_ts",
    "threshold_observed_ts",
    "settlement_price",
    "settlement_event_ts",
)


@dataclass(frozen=True)
class MonitorSnapshot:
    generated_at: datetime
    prices: dict[tuple[str, str], float]
    price_rows: tuple[dict[str, Any], ...]
    orderbooks: tuple[dict[str, Any], ...]
    contracts: tuple[dict[str, Any], ...]
    ingest_counts: tuple[dict[str, Any], ...]
    normalized_health: tuple[dict[str, Any], ...] = ()
    source_freshness: tuple[dict[str, Any], ...] = ()
    source_disagreements: tuple[dict[str, Any], ...] = ()
    orderbook_freshness: tuple[dict[str, Any], ...] = ()
    source_errors: dict[str, str] = field(default_factory=dict)
    latency_marks: tuple[dict[str, Any], ...] = ()
    hot_decision_telemetry: dict[str, Any] | None = None
    health_flags: tuple[str, ...] = ()
    websocket_status: tuple[dict[str, Any], ...] = ()


def fetch_monitor_snapshot(
    duckdb_path: Path,
    limit: int = 8,
    lock_retry_seconds: float = 2.0,
    status_path: Path | None = None,
    target_cache_path: Path | None = None,
) -> MonitorSnapshot:
    if status_path is not None and status_path.exists():
        return _snapshot_from_status(
            status_path,
            limit=limit,
            target_cache_path=target_cache_path,
        )

    if not duckdb_path.exists():
        return MonitorSnapshot(
            generated_at=datetime.now(timezone.utc),
            prices={},
            price_rows=(),
            orderbooks=(),
            contracts=(),
            ingest_counts=(),
            normalized_health=(),
            source_freshness=(),
            source_disagreements=(),
            orderbook_freshness=(),
            source_errors={},
        )

    with _connect_read_only_with_retry(duckdb_path, lock_retry_seconds) as conn:
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


def _snapshot_from_status(
    status_path: Path,
    limit: int,
    now: datetime | None = None,
    target_cache_path: Path | None = None,
) -> MonitorSnapshot:
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    return snapshot_from_status_payload(
        payload,
        limit=limit,
        now=now,
        target_cache=_read_target_cache(target_cache_path),
    )


def snapshot_from_status_payload(
    payload: dict[str, Any],
    *,
    limit: int,
    now: datetime | None = None,
    target_cache: dict[str, Any] | None = None,
) -> MonitorSnapshot:
    generated_at = _parse_datetime(payload["generated_at"])
    wall_time = datetime.now(timezone.utc) if now is None else _to_utc(now)
    price_rows = _status_price_rows(payload)
    orderbooks = _status_orderbook_rows(payload, limit=limit)
    contracts = _status_contract_rows(payload, limit=limit)
    cache_targets = _target_cache_by_market_slug(target_cache, generated_at=generated_at)
    orderbooks = _merge_cached_targets(orderbooks, cache_targets)
    contracts = _merge_cached_targets(contracts, cache_targets)
    ingest_counts = tuple(dict(row) for row in payload.get("ingest_counts", ()))
    normalized_health = tuple(dict(row) for row in payload.get("normalized_health", ()))
    source_freshness, orderbook_freshness = _status_freshness_rows(
        payload,
        now=wall_time,
        fallback_ts=generated_at,
    )
    source_disagreements = _block_stale_disagreements(
        payload.get("source_disagreements", ()),
        source_freshness=source_freshness,
    )
    source_errors = {str(key): str(value) for key, value in payload.get("source_errors", {}).items()}
    hot_decision_telemetry = payload.get("hot_decision_telemetry")
    prices = {
        (str(row["source_key"]), str(row["symbol"])): float(row["price"]) for row in price_rows
    }
    return MonitorSnapshot(
        generated_at=generated_at,
        prices=prices,
        price_rows=price_rows,
        orderbooks=orderbooks,
        contracts=contracts,
        ingest_counts=ingest_counts,
        normalized_health=normalized_health,
        source_freshness=source_freshness,
        source_disagreements=source_disagreements,
        orderbook_freshness=orderbook_freshness,
        source_errors=source_errors,
        latency_marks=tuple(dict(row) for row in payload.get("latency_marks", ())),
        hot_decision_telemetry=dict(hot_decision_telemetry)
        if isinstance(hot_decision_telemetry, dict)
        else None,
        health_flags=tuple(str(flag) for flag in payload.get("health_flags", ())),
        websocket_status=tuple(dict(row) for row in payload.get("websocket_status", ())),
    )


def _status_price_rows(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if "prices" in payload:
        rows = payload.get("prices", ())
    else:
        rows = payload.get("chainlink_prices", ())
    return tuple(dict(row) for row in rows)


def _read_target_cache(target_cache_path: Path | None) -> dict[str, Any] | None:
    if target_cache_path is None or not target_cache_path.exists():
        return None
    try:
        payload = json.loads(target_cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _target_cache_by_market_slug(
    payload: dict[str, Any] | None,
    *,
    generated_at: datetime,
) -> dict[str, dict[str, Any]]:
    if payload is None or payload.get("schema_version") != "polymarket-target-cache-v1":
        return {}
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return {}
    targets: dict[str, dict[str, Any]] = {}
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        slug = str(row.get("market_slug", "")).casefold()
        if not slug or row.get("threshold_price") in (None, ""):
            continue
        try:
            observed_ts = _parse_optional_datetime(row.get("threshold_observed_ts"))
        except (TypeError, ValueError):
            continue
        if observed_ts is None or observed_ts > generated_at:
            continue
        targets[slug] = row
    return targets


def _merge_cached_targets(
    rows: tuple[dict[str, Any], ...],
    targets_by_slug: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    merged: list[dict[str, Any]] = []
    for raw_row in rows:
        row = dict(raw_row)
        target = targets_by_slug.get(str(row.get("market_slug", "")).casefold())
        if target is not None and row.get("threshold_price") in (None, ""):
            for field in (
                "threshold_price",
                "threshold_event_ts",
                "threshold_observed_ts",
            ):
                row[field] = target.get(field)
        merged.append(row)
    return tuple(merged)


def _status_contract_rows(payload: dict[str, Any], *, limit: int) -> tuple[dict[str, Any], ...]:
    if "contracts" in payload:
        return tuple(dict(row) for row in payload.get("contracts", ())[:limit])
    return _state_manager_contract_rows(payload, limit=limit)


def _status_orderbook_rows(payload: dict[str, Any], *, limit: int) -> tuple[dict[str, Any], ...]:
    if any(payload.get(group) for group in ("current", "next")):
        return _state_manager_orderbook_rows(payload, limit=limit)
    return tuple(_normalize_orderbook_row(dict(row)) for row in payload.get("orderbooks", ())[:limit])


def _state_manager_orderbook_rows(
    payload: dict[str, Any],
    *,
    limit: int,
) -> tuple[dict[str, Any], ...]:
    contracts = _state_manager_book_contract_rows(payload, limit=limit)
    targets_by_slug = _targets_by_market_slug(payload)
    raw_books_by_token = {
        str(row.get("token_id", "")): dict(row)
        for row in payload.get("orderbooks", ())
        if isinstance(row, dict) and row.get("token_id")
    }

    rows: list[dict[str, Any]] = []
    for contract in contracts:
        raw = raw_books_by_token.get(str(contract["token_id"]))
        row = _normalize_orderbook_row({} if raw is None else raw)
        for key, value in contract.items():
            row[key] = value
        _copy_matching_target_fields(row, targets_by_slug)
        if raw is None:
            row.update(
                {
                    "venue": "polymarket",
                    "source_key": "polymarket_rust_sdk",
                    "event_ts": None,
                    "observed_ts": None,
                    "best_bid": None,
                    "best_ask": None,
                    "spread": None,
                    "bid_size_top": None,
                    "ask_size_top": None,
                    "bids": [],
                    "asks": [],
                    "book_state": "missing",
                }
            )
        else:
            row["book_state"] = _book_state(row)
        rows.append(row)
        if len(rows) >= limit:
            break
    return tuple(rows)


def _state_manager_book_contract_rows(
    payload: dict[str, Any],
    *,
    limit: int,
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    group_order = {"current": 0, "next": 1}
    side_order = {"UP": 0, "DOWN": 1}
    asset_order = {"BTC": 0, "ETH": 1}
    for group in ("current", "next"):
        for raw_contract in payload.get(group, ()):
            contract = dict(raw_contract)
            window = dict(contract.get("window", {}))
            asset = str(window.get("asset", "")).upper()
            interval = str(window.get("interval", ""))
            start_ts = str(window.get("start_ts", ""))
            expiry_ts = str(window.get("end_ts", ""))
            contract_id = _state_manager_contract_id(
                asset=asset,
                interval=interval,
                start_ts=start_ts,
            )
            market_slug = contract_id
            for token_key in ("up", "down"):
                token = dict(contract.get(token_key, {}))
                side = str(token.get("side", token_key)).upper()
                rows.append(
                    {
                        "contract_id": contract_id,
                        "market_slug": market_slug,
                        "asset": asset,
                        "side": side,
                        "token_id": token.get("token_id", ""),
                        "start_ts": start_ts,
                        "expiry_ts": expiry_ts,
                        "window": group,
                        "_sort": (
                            asset_order.get(asset, len(asset_order)),
                            _sort_datetime(start_ts),
                            group_order[group],
                            side_order.get(side, len(side_order)),
                        ),
                    }
                )
    rows.sort(key=lambda row: row["_sort"])
    shaped = []
    for row in rows[:limit]:
        shaped.append({key: value for key, value in row.items() if key != "_sort"})
    return tuple(shaped)


def _normalize_orderbook_row(row: dict[str, Any]) -> dict[str, Any]:
    bid = _positive_number_or_none(row.get("best_bid"))
    ask = _positive_number_or_none(row.get("best_ask"))
    normalized = dict(row)
    normalized["best_bid"] = bid
    normalized["best_ask"] = ask
    normalized["bid_size_top"] = (
        _positive_number_or_none(row.get("bid_size_top")) if bid is not None else None
    )
    normalized["ask_size_top"] = (
        _positive_number_or_none(row.get("ask_size_top")) if ask is not None else None
    )
    normalized["spread"] = _positive_spread(row.get("spread"), bid=bid, ask=ask)
    normalized["bids"] = _positive_book_levels(row.get("bids", ()))
    normalized["asks"] = _positive_book_levels(row.get("asks", ()))
    return normalized


def _book_state(row: dict[str, Any]) -> str:
    has_bid = row.get("best_bid") is not None
    has_ask = row.get("best_ask") is not None
    if has_bid and has_ask:
        return "ok"
    if not has_bid and not has_ask:
        return "no_bid_ask"
    if not has_bid:
        return "no_bid"
    return "no_ask"


def _positive_book_levels(rows: Any) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    raw_rows = rows if isinstance(rows, (list, tuple)) else ()
    for raw_level in raw_rows:
        if not isinstance(raw_level, dict):
            continue
        price = _positive_number_or_none(raw_level.get("price"))
        size = _positive_number_or_none(raw_level.get("size"))
        if price is None or size is None:
            continue
        level = dict(raw_level)
        level["price"] = price
        level["size"] = size
        levels.append(level)
    return levels


def _positive_spread(value: object, *, bid: float | None, ask: float | None) -> float | None:
    spread = _positive_number_or_none(value)
    if spread is not None:
        return spread
    if bid is None or ask is None:
        return None
    computed = ask - bid
    return computed if computed > 0 else None


def _positive_number_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _sort_datetime(value: str) -> datetime:
    try:
        return _parse_datetime(value)
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)


def _state_manager_contract_rows(
    payload: dict[str, Any],
    *,
    limit: int,
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    targets_by_slug = _targets_by_market_slug(payload)
    for group in ("current", "next", "next_next"):
        for raw_contract in payload.get(group, ()):
            contract = dict(raw_contract)
            window = dict(contract.get("window", {}))
            asset = str(window.get("asset", ""))
            interval = str(window.get("interval", ""))
            start_ts = str(window.get("start_ts", ""))
            expiry_ts = str(window.get("end_ts", ""))
            contract_id = _state_manager_contract_id(
                asset=asset,
                interval=interval,
                start_ts=start_ts,
            )
            for token_key in ("up", "down"):
                token = dict(contract.get(token_key, {}))
                row = {
                    "contract_id": contract_id,
                    "asset": asset,
                    "side": str(token.get("side", token_key)).upper(),
                    "token_id": token.get("token_id", ""),
                    "threshold_type": "above" if token_key == "up" else "below",
                    "settlement_symbol": f"{asset}/USD" if asset else "",
                    "start_ts": start_ts,
                    "expiry_ts": expiry_ts,
                    "window": group,
                }
                _copy_matching_target_fields(row, targets_by_slug, market_slug=contract_id)
                rows.append(row)
                if len(rows) >= limit:
                    return tuple(rows)
    return tuple(rows)


def _targets_by_market_slug(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("market_slug", "")).casefold(): dict(row)
        for row in payload.get("targets", ())
        if isinstance(row, dict) and row.get("market_slug")
    }


def _copy_matching_target_fields(
    row: dict[str, Any],
    targets_by_slug: dict[str, dict[str, Any]],
    *,
    market_slug: str | None = None,
) -> None:
    slug = row.get("market_slug", "") if market_slug is None else market_slug
    target = targets_by_slug.get(str(slug).casefold())
    if target is None:
        return
    for key in TARGET_FIELDS:
        row[key] = target.get(key)


def _state_manager_contract_id(*, asset: str, interval: str, start_ts: str) -> str:
    if not asset or not interval or not start_ts:
        return ""
    try:
        start_key = str(int(_parse_datetime(start_ts).timestamp()))
    except ValueError:
        start_key = start_ts
    return f"{asset.lower()}-updown-{interval}-{start_key}"


def _status_freshness_rows(
    payload: dict[str, Any],
    *,
    now: datetime,
    fallback_ts: datetime,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    if "source_freshness" in payload or "orderbook_freshness" in payload:
        return (
            _refresh_freshness_rows(
                payload.get("source_freshness", ()),
                now=now,
                fallback_ts=fallback_ts,
            ),
            _refresh_freshness_rows(
                payload.get("orderbook_freshness", ()),
                now=now,
                fallback_ts=fallback_ts,
            ),
        )

    source_rows: list[dict[str, Any]] = []
    orderbook_rows: list[dict[str, Any]] = []
    for raw_row in payload.get("freshness", ()):
        row = dict(raw_row)
        if row.get("source_key") == CHAINLINK_SOURCE_KEY:
            source_rows.append(row)
        else:
            orderbook_rows.append(row)

    return (
        _refresh_freshness_rows(source_rows, now=now, fallback_ts=fallback_ts),
        _refresh_freshness_rows(orderbook_rows, now=now, fallback_ts=fallback_ts),
    )


def _refresh_freshness_rows(
    rows: Any,
    *,
    now: datetime,
    fallback_ts: datetime,
) -> tuple[dict[str, Any], ...]:
    refreshed: list[dict[str, Any]] = []
    for raw_row in rows:
        row = dict(raw_row)
        observed_ts = _parse_optional_datetime(row.get("observed_ts"))
        if observed_ts is None and "age_ms" in row and "stale" in row:
            row["age_ms"] = _optional_int(row.get("age_ms"))
            row["stale"] = _optional_bool(row.get("stale"))
            refreshed.append(row)
            continue
        observed_ts = observed_ts or fallback_ts
        age_ms = max(0, int((now - observed_ts).total_seconds() * 1000))
        row["age_ms"] = age_ms
        stale_after_ms = _optional_int(row.get("stale_after_ms"))
        if row.get("missing"):
            row["stale"] = True
        elif stale_after_ms is None:
            row["stale"] = True
        else:
            row["stale"] = age_ms > stale_after_ms
        refreshed.append(row)
    return tuple(refreshed)


def _block_stale_disagreements(
    rows: Any,
    *,
    source_freshness: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    freshness_by_source = {
        (str(row.get("source_key")), str(row.get("symbol"))): bool(row.get("stale"))
        for row in source_freshness
    }
    patched: list[dict[str, Any]] = []
    for raw_row in rows:
        row = dict(raw_row)
        primary_key = (str(row.get("primary_source_key")), str(row.get("primary_symbol")))
        proxy_key = (str(row.get("proxy_source_key")), str(row.get("proxy_symbol")))
        if freshness_by_source.get(primary_key):
            row["usable"] = False
            row["block_reason"] = "stale_reference_source"
        elif freshness_by_source.get(proxy_key):
            row["usable"] = False
            row["block_reason"] = "stale_proxy_source"
        patched.append(row)
    return tuple(patched)


def _parse_optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _parse_datetime(value)


def _parse_datetime(value: object) -> datetime:
    text = str(value).replace("Z", "+00:00")
    return _to_utc(datetime.fromisoformat(text))


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("status timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("boolean is not a valid integer value")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"unsupported integer value: {value!r}")


def _optional_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
    return bool(value)


def _connect_read_only_with_retry(
    duckdb_path: Path,
    lock_retry_seconds: float,
) -> duckdb.DuckDBPyConnection:
    deadline = time.monotonic() + lock_retry_seconds
    while True:
        try:
            return duckdb.connect(str(duckdb_path), read_only=True)
        except duckdb.IOException as exc:
            if "Could not set lock" not in str(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


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

    lines.extend(["", "Source Freshness"])
    if snapshot.source_freshness:
        for row in snapshot.source_freshness:
            state = "STALE" if row.get("stale") else "OK"
            lines.append(
                f"  {row['source_key']:<28} {row['symbol']:<8} {state:<5} "
                f"age_ms={_fmt_int(row.get('age_ms')):<8} obs={row.get('observed_ts')}"
            )
    else:
        lines.append("  no source freshness yet")

    lines.extend(["", "Source Disagreement"])
    if snapshot.source_disagreements:
        for row in snapshot.source_disagreements:
            if row.get("usable"):
                lines.append(
                    f"  {row['asset']:<3} {row['primary_source_key']}:{row['primary_symbol']} "
                    f"vs {row['proxy_source_key']}:{row['proxy_symbol']} "
                    f"diff={_fmt_float(row.get('diff'), 4)} "
                    f"diff_bps={_fmt_float(row.get('diff_bps'), 2)}"
                )
            else:
                lines.append(
                    f"  {row['asset']:<3} {row['primary_source_key']}:{row['primary_symbol']} "
                    f"vs {row['proxy_source_key']}:{row['proxy_symbol']} "
                    f"blocked={row.get('block_reason')}"
                )
    else:
        lines.append("  no source disagreement yet")

    lines.extend(["", "Source Errors"])
    if snapshot.source_errors:
        for source_key, error in sorted(snapshot.source_errors.items()):
            lines.append(f"  {source_key:<32} {error}")
    else:
        lines.append("  no source errors")

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

    lines.extend(["", "Orderbook Freshness"])
    if snapshot.orderbook_freshness:
        for row in snapshot.orderbook_freshness:
            state = "STALE" if row.get("stale") else "OK"
            lines.append(
                f"  {_orderbook_freshness_label(row):<36} {state:<5} "
                f"age_ms={_fmt_int(row.get('age_ms')):<8}"
            )
    else:
        lines.append("  no orderbook freshness yet")

    lines.extend(["", "Latency Marks"])
    if snapshot.latency_marks:
        for row in snapshot.latency_marks:
            lines.append(f"  {row.get('name', ''):<36} elapsed_ms={row.get('elapsed_ms')}")
    else:
        lines.append("  no latency marks yet")

    lines.extend(["", "Hot Decisions"])
    if snapshot.hot_decision_telemetry:
        telemetry = snapshot.hot_decision_telemetry
        lines.append(
            f"  states_built={telemetry.get('states_built')} "
            f"states_persist_queued={telemetry.get('states_persist_queued')} "
            f"dropped_events={telemetry.get('dropped_events')} "
            f"last_state_age_ms={telemetry.get('last_state_age_ms')} "
            f"last_observed_to_state_us={telemetry.get('last_observed_to_state_us')}"
        )
    else:
        lines.append("  no hot decision telemetry yet")

    lines.extend(["", "Health Flags"])
    if snapshot.health_flags:
        for flag in snapshot.health_flags:
            lines.append(f"  {flag}")
    else:
        lines.append("  no health flags")

    lines.extend(["", "Ingest"])
    if snapshot.ingest_counts:
        for row in snapshot.ingest_counts:
            lines.append(
                f"  {row['source_key']:<24} {row['stream_key']:<28} "
                f"files={row['files']:<4} rows={row['rows']:<6} last={row['last_event_ts']}"
            )
    else:
        lines.append("  no ingest files yet")

    lines.extend(["", "Normalized Health"])
    if snapshot.normalized_health:
        for row in snapshot.normalized_health:
            lines.append(
                f"  {row['table']:<32} rows={row['rows']:<8} latest={row.get('latest_ts')}"
            )
    else:
        lines.append("  no normalized health yet")

    return "\n".join(lines)


async def run_monitor(
    duckdb_path: Path,
    refresh_seconds: float,
    limit: int,
    status_path: Path | None = None,
) -> int:
    while True:
        snapshot = fetch_monitor_snapshot(duckdb_path, limit=limit, status_path=status_path)
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


def _fmt_int(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return str(int(value))
    if isinstance(value, int | float):
        return str(int(value))
    return "-"


def _short_token(value: object) -> str:
    text = str(value)
    if len(text) <= 14:
        return text
    return f"{text[:10]}..."


def _orderbook_freshness_label(row: dict[str, Any]) -> str:
    asset = str(row.get("asset") or "")
    side = str(row.get("side") or "")
    contract_id = str(row.get("contract_id") or "")
    if asset or side or contract_id:
        return f"{asset} {side} {contract_id}".strip()
    source_key = str(row.get("source_key") or "")
    symbol = str(row.get("symbol") or "")
    if source_key and symbol:
        return f"{source_key}:{symbol}"
    return symbol or source_key
