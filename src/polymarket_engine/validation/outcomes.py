from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import httpx

from polymarket_engine.storage.atomic import durable_replace
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore, MarketOutcomeRecord

OUTCOME_HISTORY_SCHEMA_VERSION = "polymarket-outcome-runtime-v1"
OUTCOME_STATUS_MAX_AGE_SECONDS = 120.0
POLYMARKET_CLOB_MARKET_LABEL_SOURCE = "polymarket_clob_market"
OUTCOME_STATUS_REQUIRED_ROW_FIELDS = (
    "market",
    "market_id",
    "market_slug",
    "asset",
    "interval",
    "start_ts",
    "expiry_ts",
    "computed_winner",
    "official_winner",
    "winning_token_id",
    "official_resolution_status",
    "mismatch",
)
MarketPayloadSource = Callable[[str], Mapping[str, Any] | None]


@dataclass(frozen=True)
class _ContractRow:
    market_id: str
    condition_id: str
    market_slug: str
    asset: str
    interval: str
    side: str
    token_id: str
    threshold_type: str
    threshold_price: float | None
    start_ts: datetime
    expiry_ts: datetime
    settlement_symbol: str
    rule_hash: str


@dataclass(frozen=True)
class OfficialOutcomeResolution:
    official_winner: str | None
    winning_token_id: str | None
    official_resolution_status: str
    official_label_source: str | None
    official_resolved_at: datetime | None


class PolymarketClobMarketPayloadSource:
    def __init__(
        self,
        *,
        base_url: str = "https://clob.polymarket.com",
        timeout_seconds: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(
            timeout_seconds,
            connect=min(timeout_seconds, 2.0),
        )

    def __call__(self, condition_id: str) -> Mapping[str, Any] | None:
        try:
            response = httpx.get(
                f"{self.base_url}/markets/{condition_id}",
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None


def official_resolution_from_polymarket_market(
    payload: Mapping[str, Any],
    *,
    up_token_id: str,
    down_token_id: str,
    observed_at: datetime,
) -> OfficialOutcomeResolution:
    tokens = payload.get("tokens")
    if not isinstance(tokens, list):
        return _pending_official_resolution()
    winning_token_ids = tuple(
        token_id
        for token in tokens
        if isinstance(token, Mapping)
        if token.get("winner") is True
        for token_id in (_token_id_from_payload(token),)
        if token_id is not None
    )
    if len(winning_token_ids) != 1:
        return _pending_official_resolution()
    winning_token_id = winning_token_ids[0]
    if winning_token_id == up_token_id:
        winner = "UP"
    elif winning_token_id == down_token_id:
        winner = "DOWN"
    else:
        return _pending_official_resolution()
    return OfficialOutcomeResolution(
        official_winner=winner,
        winning_token_id=winning_token_id,
        official_resolution_status="resolved",
        official_label_source=POLYMARKET_CLOB_MARKET_LABEL_SOURCE,
        official_resolved_at=_to_utc(observed_at),
    )


def upsert_official_market_outcomes(
    *,
    store: DuckDbIngestStore,
    asof_ts: datetime,
    market_payload_source: MarketPayloadSource | None = None,
    max_markets: int | None = None,
    pending_sweep_limit: int | None = None,
    expiry_start_ts: datetime | None = None,
    expiry_end_ts: datetime | None = None,
) -> int:
    if max_markets is not None and max_markets <= 0:
        return 0
    asof_ts = _to_utc(asof_ts)
    expiry_start_ts = _to_utc(expiry_start_ts) if expiry_start_ts is not None else None
    expiry_end_ts = _to_utc(expiry_end_ts) if expiry_end_ts is not None else None
    contract_rows = _expired_contract_rows(
        store=store,
        asof_ts=asof_ts,
        expiry_start_ts=expiry_start_ts,
        expiry_end_ts=expiry_end_ts,
    )
    records: list[MarketOutcomeRecord] = []
    payload_cache: dict[str, Mapping[str, Any] | None] = {}
    market_groups = sorted(
        _group_by_market(contract_rows).values(),
        key=_market_group_expiry_ts,
        reverse=True,
    )
    market_groups = _selected_market_groups(
        market_groups=market_groups,
        store=store,
        asof_ts=asof_ts,
        max_markets=max_markets,
        pending_sweep_limit=pending_sweep_limit,
    )
    for market_rows in market_groups:
        up = market_rows.get("UP")
        down = market_rows.get("DOWN")
        if up is None or down is None:
            continue
        if up.expiry_ts > asof_ts:
            continue
        existing = _existing_outcome_fields(store=store, market_id=up.market_id)
        payload = _market_payload_for_condition(
            condition_id=up.condition_id,
            market_payload_source=market_payload_source,
            payload_cache=payload_cache,
        )
        resolution = (
            official_resolution_from_polymarket_market(
                payload,
                up_token_id=up.token_id,
                down_token_id=down.token_id,
                observed_at=asof_ts,
            )
            if payload is not None
            else _pending_official_resolution()
        )
        resolution = _preserve_existing_resolved_official_fields(
            existing=existing,
            resolution=resolution,
        )
        threshold = _preserved_threshold(
            existing,
            start_ts=up.start_ts,
            asof_ts=asof_ts,
        ) or _chainlink_tick_at_or_before(
            store=store,
            symbol=up.settlement_symbol,
            event_ts_lte=up.start_ts,
            observed_ts_lte=asof_ts,
        )
        end = _chainlink_tick_at_or_before(
            store=store,
            symbol=up.settlement_symbol,
            event_ts_lte=up.expiry_ts,
            observed_ts_lte=asof_ts,
        )
        records.append(
            MarketOutcomeRecord(
                market_id=up.market_id,
                condition_id=up.condition_id,
                market_slug=up.market_slug,
                asset=up.asset,
                interval=up.interval,
                start_ts=up.start_ts,
                expiry_ts=up.expiry_ts,
                up_token_id=up.token_id,
                down_token_id=down.token_id,
                threshold_price=threshold["price"] if threshold is not None else None,
                threshold_event_ts=threshold["event_ts"] if threshold is not None else None,
                threshold_observed_ts=(
                    threshold["observed_ts"] if threshold is not None else None
                ),
                end_price=end["price"] if end is not None else None,
                end_event_ts=end["event_ts"] if end is not None else None,
                end_observed_ts=end["observed_ts"] if end is not None else None,
                computed_winner=None,
                computed_label_source=None,
                computed_at=None,
                official_winner=resolution.official_winner,
                winning_token_id=resolution.winning_token_id,
                official_resolution_status=resolution.official_resolution_status,
                official_label_source=resolution.official_label_source,
                official_resolved_at=resolution.official_resolved_at,
                rule_hash=up.rule_hash,
                mismatch=None,
            )
        )
    return store.upsert_market_outcome_records(tuple(records))


def build_outcome_history_payload(
    *,
    duckdb_path: Path,
    limit: int = 20,
    outcome_status_path: Path | None = None,
) -> dict[str, Any]:
    if outcome_status_path is not None and outcome_status_path.exists():
        return _outcome_status_payload_from_file(
            outcome_status_path=outcome_status_path,
            limit=limit,
        )
    if not duckdb_path.exists():
        return {
            "ok": False,
            "state": "MISSING",
            "error": f"{duckdb_path} missing",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [],
        }
    try:
        rows = latest_market_outcome_rows(duckdb_path=duckdb_path, limit=limit)
    except (duckdb.Error, OSError, ValueError) as exc:
        return {
            "ok": False,
            "state": "INVALID",
            "error": f"{type(exc).__name__}: {exc}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [],
        }
    return {
        "ok": True,
        "state": "OK",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": [_runtime_row(row) for row in rows],
    }


def backfill_outcome_history(
    *,
    duckdb_path: Path,
    outcomes_path: Path,
    asof_ts: datetime | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int | None = None,
    write: bool = False,
    market_payload_source: MarketPayloadSource | None = None,
) -> dict[str, Any]:
    asof_ts = _to_utc(asof_ts or datetime.now(timezone.utc))
    expiry_start_ts = _parse_utc_date_start(start_date) if start_date is not None else None
    expiry_end_ts = _parse_utc_next_day_start(end_date) if end_date is not None else None
    if not write and not duckdb_path.exists():
        return _outcome_backfill_error_report(
            state="MISSING",
            error=f"{duckdb_path} missing",
            asof_ts=asof_ts,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            dry_run=True,
        )
    store = DuckDbIngestStore(duckdb_path)
    if write:
        store.apply_schema()
        before = _outcome_backfill_counts(
            store=store,
            asof_ts=asof_ts,
            expiry_start_ts=expiry_start_ts,
            expiry_end_ts=expiry_end_ts,
        )
    else:
        try:
            with _connect_read_only_with_retry(duckdb_path, lock_retry_seconds=2.0) as conn:
                before = _outcome_backfill_counts_from_connection(
                    conn=conn,
                    asof_ts=asof_ts,
                    expiry_start_ts=expiry_start_ts,
                    expiry_end_ts=expiry_end_ts,
                )
        except (duckdb.Error, OSError, ValueError) as exc:
            return _outcome_backfill_error_report(
                state="INVALID",
                error=f"{type(exc).__name__}: {exc}",
                asof_ts=asof_ts,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                dry_run=True,
            )
    rows_written = 0
    if write:
        rows_written = upsert_official_market_outcomes(
            store=store,
            asof_ts=asof_ts,
            market_payload_source=market_payload_source,
            max_markets=limit,
            expiry_start_ts=expiry_start_ts,
            expiry_end_ts=expiry_end_ts,
        )
        rows = latest_market_outcome_rows(duckdb_path=duckdb_path, limit=5000)
        write_outcome_history_status(out_path=outcomes_path, rows=rows)
        after = _outcome_backfill_counts(
            store=store,
            asof_ts=asof_ts,
            expiry_start_ts=expiry_start_ts,
            expiry_end_ts=expiry_end_ts,
        )
    else:
        after = before
    return {
        "ok": True,
        "dry_run": not write,
        "asof_ts": asof_ts.isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "markets_scanned": before["markets_scanned"],
        "rows_written": rows_written,
        "missing_k_before": before["missing_k"],
        "missing_k_after": after["missing_k"],
        "pending_official_before": before["pending_official"],
        "pending_official_after": after["pending_official"],
    }


def _outcome_backfill_error_report(
    *,
    state: str,
    error: str,
    asof_ts: datetime,
    start_date: str | None,
    end_date: str | None,
    limit: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "ok": False,
        "state": state,
        "error": error,
        "dry_run": dry_run,
        "asof_ts": asof_ts.isoformat(),
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "markets_scanned": 0,
        "rows_written": 0,
        "missing_k_before": 0,
        "missing_k_after": 0,
        "pending_official_before": 0,
        "pending_official_after": 0,
    }


def write_outcome_history_status(
    *,
    out_path: Path,
    rows: list[dict[str, Any]],
) -> None:
    payload = {
        "schema_version": OUTCOME_HISTORY_SCHEMA_VERSION,
        "ok": True,
        "state": "OK",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": [_runtime_row(row) for row in rows],
    }
    _write_outcome_status_payload(out_path=out_path, payload=payload)


def write_locked_outcome_status(
    *,
    out_path: Path,
    rows: list[object],
    error: str,
) -> None:
    generated_at = datetime.now(timezone.utc).isoformat()
    if rows:
        payload = {
            "schema_version": OUTCOME_HISTORY_SCHEMA_VERSION,
            "ok": True,
            "state": "OK",
            "generated_at": generated_at,
            "rows": rows,
            "refresh_ok": False,
            "refresh_state": "LOCKED",
            "refresh_error": error,
            "served_from": "last_good_rows",
        }
    else:
        payload = {
            "schema_version": OUTCOME_HISTORY_SCHEMA_VERSION,
            "ok": False,
            "state": "LOCKED",
            "error": error,
            "generated_at": generated_at,
            "rows": [],
        }
    _write_outcome_status_payload(out_path=out_path, payload=payload)


def _write_outcome_status_payload(*, out_path: Path, payload: dict[str, Any]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(f"{out_path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    durable_replace(tmp_path, out_path)


def latest_market_outcome_rows(*, duckdb_path: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    with _connect_read_only_with_retry(duckdb_path, lock_retry_seconds=2.0) as conn:
        return latest_market_outcome_rows_from_connection(conn=conn, limit=limit)


def latest_market_outcome_rows_from_connection(
    *,
    conn: duckdb.DuckDBPyConnection,
    limit: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select
            market_id,
            condition_id,
            market_slug,
            asset,
            interval,
            start_ts::VARCHAR,
            expiry_ts::VARCHAR,
            up_token_id,
            down_token_id,
            threshold_price,
            threshold_event_ts::VARCHAR,
            threshold_observed_ts::VARCHAR,
            end_price,
            end_event_ts::VARCHAR,
            end_observed_ts::VARCHAR,
            computed_winner,
            computed_label_source,
            computed_at::VARCHAR,
            official_winner,
            winning_token_id,
            official_resolution_status,
            official_label_source,
            official_resolved_at::VARCHAR,
            rule_hash,
            mismatch,
            updated_at::VARCHAR
        from validation.market_outcome_history
        order by expiry_ts desc, asset, interval, market_id
        limit ?
        """,
        [limit],
    ).fetchall()
    keys = (
        "market_id",
        "condition_id",
        "market_slug",
        "asset",
        "interval",
        "start_ts",
        "expiry_ts",
        "up_token_id",
        "down_token_id",
        "threshold_price",
        "threshold_event_ts",
        "threshold_observed_ts",
        "end_price",
        "end_event_ts",
        "end_observed_ts",
        "computed_winner",
        "computed_label_source",
        "computed_at",
        "official_winner",
        "winning_token_id",
        "official_resolution_status",
        "official_label_source",
        "official_resolved_at",
        "rule_hash",
        "mismatch",
        "updated_at",
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def _outcome_status_payload_from_file(
    *,
    outcome_status_path: Path,
    limit: int,
) -> dict[str, Any]:
    try:
        payload = json.loads(outcome_status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "ok": False,
            "state": "INVALID",
            "error": f"{type(exc).__name__}: {exc}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [],
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "state": "INVALID",
            "error": "outcome status shape invalid: payload must be an object",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [],
        }
    schema_version = payload.get("schema_version")
    if schema_version != OUTCOME_HISTORY_SCHEMA_VERSION:
        return {
            "ok": False,
            "state": "INVALID",
            "error": (
                "outcome status schema invalid: expected "
                f"{OUTCOME_HISTORY_SCHEMA_VERSION}, got {schema_version!r}"
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [],
        }
    generated_at = _parse_outcome_status_generated_at(payload.get("generated_at"))
    if generated_at is None:
        return {
            "ok": False,
            "state": "INVALID",
            "error": "outcome status shape invalid: generated_at must be an ISO timestamp",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [],
        }
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return {
            "ok": False,
            "state": "INVALID",
            "error": "outcome status shape invalid: rows must be a list",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [],
        }
    normalized_rows = _normalize_outcome_status_rows(rows)
    row_error = _outcome_status_row_error(normalized_rows)
    if row_error is not None:
        return {
            "ok": False,
            "state": "INVALID",
            "error": row_error,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [],
        }
    limited = dict(payload)
    limited["rows"] = normalized_rows[:limit]
    age_seconds = (datetime.now(timezone.utc) - generated_at).total_seconds()
    if age_seconds > OUTCOME_STATUS_MAX_AGE_SECONDS:
        limited["ok"] = False
        limited["state"] = "STALE"
        limited["error"] = (
            f"outcome status stale: age_seconds={age_seconds:.3f} "
            f"max={OUTCOME_STATUS_MAX_AGE_SECONDS:.3f}"
        )
    return limited


def _parse_outcome_status_generated_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _to_utc(parsed)


def _outcome_status_row_error(rows: list[Any]) -> str | None:
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return f"outcome status shape invalid: row {index} must be an object"
        for field in OUTCOME_STATUS_REQUIRED_ROW_FIELDS:
            if field not in row:
                return (
                    "outcome status shape invalid: "
                    f"row {index} missing row field {field}"
                )
    return None


def _normalize_outcome_status_rows(rows: list[Any]) -> list[Any]:
    optional_legacy_fields = (
        "winning_token_id",
        "threshold_price",
        "threshold_event_ts",
        "threshold_observed_ts",
        "end_price",
        "end_event_ts",
        "end_observed_ts",
    )
    normalized: list[Any] = []
    for row in rows:
        if isinstance(row, dict):
            row = dict(row)
            for field in optional_legacy_fields:
                row.setdefault(field, None)
        normalized.append(row)
    return normalized


@contextmanager
def _connect_read_only_with_retry(
    duckdb_path: Path,
    *,
    lock_retry_seconds: float,
) -> Iterator[duckdb.DuckDBPyConnection]:
    deadline = time.monotonic() + lock_retry_seconds
    while True:
        try:
            with duckdb.connect(str(duckdb_path), read_only=True) as conn:
                yield conn
            return
        except duckdb.IOException:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _expired_contract_rows(
    *,
    store: DuckDbIngestStore,
    asof_ts: datetime,
    expiry_start_ts: datetime | None = None,
    expiry_end_ts: datetime | None = None,
) -> tuple[_ContractRow, ...]:
    filters = ["expiry_ts <= ?"]
    params: list[object] = [asof_ts]
    if expiry_start_ts is not None:
        filters.append("expiry_ts >= ?")
        params.append(expiry_start_ts)
    if expiry_end_ts is not None:
        filters.append("expiry_ts < ?")
        params.append(expiry_end_ts)
    with store._connection() as conn:
        rows = conn.execute(
            f"""
            select
                market_id,
                condition_id,
                slug,
                asset,
                side,
                token_id,
                threshold_type,
                threshold_price,
                start_ts::VARCHAR,
                expiry_ts::VARCHAR,
                settlement_symbol,
                rule_hash
            from core.contracts
            where {" and ".join(filters)}
            order by expiry_ts asc, asset, market_id, side
            """,
            params,
        ).fetchall()
    return tuple(
        _ContractRow(
            market_id=str(row[0]),
            condition_id=str(row[1]),
            market_slug=str(row[2]),
            asset=str(row[3]),
            interval=_interval_from_slug(str(row[2])),
            side=str(row[4]).upper(),
            token_id=str(row[5]),
            threshold_type=str(row[6]),
            threshold_price=row[7],
            start_ts=_parse_duckdb_ts(row[8]),
            expiry_ts=_parse_duckdb_ts(row[9]),
            settlement_symbol=str(row[10]),
            rule_hash=str(row[11]),
        )
        for row in rows
    )


def _outcome_backfill_counts(
    *,
    store: DuckDbIngestStore,
    asof_ts: datetime,
    expiry_start_ts: datetime | None,
    expiry_end_ts: datetime | None,
) -> dict[str, int]:
    filters = ["expiry_ts <= ?"]
    params: list[object] = [asof_ts]
    if expiry_start_ts is not None:
        filters.append("expiry_ts >= ?")
        params.append(expiry_start_ts)
    if expiry_end_ts is not None:
        filters.append("expiry_ts < ?")
        params.append(expiry_end_ts)
    with store._connection() as conn:
        row = _fetch_outcome_backfill_counts(conn=conn, filters=filters, params=params)
    if row is None:
        return {"markets_scanned": 0, "missing_k": 0, "pending_official": 0}
    return {
        "markets_scanned": int(row[0] or 0),
        "missing_k": int(row[1] or 0),
        "pending_official": int(row[2] or 0),
    }


def _outcome_backfill_counts_from_connection(
    *,
    conn: duckdb.DuckDBPyConnection,
    asof_ts: datetime,
    expiry_start_ts: datetime | None,
    expiry_end_ts: datetime | None,
) -> dict[str, int]:
    filters = ["expiry_ts <= ?"]
    params: list[object] = [asof_ts]
    if expiry_start_ts is not None:
        filters.append("expiry_ts >= ?")
        params.append(expiry_start_ts)
    if expiry_end_ts is not None:
        filters.append("expiry_ts < ?")
        params.append(expiry_end_ts)
    row = _fetch_outcome_backfill_counts(conn=conn, filters=filters, params=params)
    if row is None:
        return {"markets_scanned": 0, "missing_k": 0, "pending_official": 0}
    return {
        "markets_scanned": int(row[0] or 0),
        "missing_k": int(row[1] or 0),
        "pending_official": int(row[2] or 0),
    }


def _fetch_outcome_backfill_counts(
    *,
    conn: duckdb.DuckDBPyConnection,
    filters: list[str],
    params: list[object],
) -> tuple[Any, ...] | None:
    return conn.execute(
        f"""
        with markets as (
            select market_id, max(expiry_ts) as expiry_ts
            from core.contracts
            where {" and ".join(filters)}
            group by market_id
        )
        select
            count(*) as markets_scanned,
            sum(
                case
                    when history.market_id is null
                      or history.threshold_price is null
                    then 1
                    else 0
                end
            ) as missing_k,
            sum(
                case
                    when history.market_id is null
                      or history.official_resolution_status = 'pending'
                      or history.official_winner is null
                    then 1
                    else 0
                end
            ) as pending_official
        from markets
        left join validation.market_outcome_history as history
          on markets.market_id = history.market_id
        """,
        params,
    ).fetchone()


def _group_by_market(
    rows: tuple[_ContractRow, ...],
) -> dict[str, dict[str, _ContractRow]]:
    grouped: dict[str, dict[str, _ContractRow]] = {}
    for row in rows:
        grouped.setdefault(row.market_id, {})[row.side] = row
    return grouped


def _market_group_expiry_ts(rows: dict[str, _ContractRow]) -> datetime:
    expiries = tuple(row.expiry_ts for row in rows.values())
    if not expiries:
        return datetime.min.replace(tzinfo=timezone.utc)
    return max(expiries)


def _selected_market_groups(
    *,
    market_groups: list[dict[str, _ContractRow]],
    store: DuckDbIngestStore,
    asof_ts: datetime,
    max_markets: int | None,
    pending_sweep_limit: int | None,
) -> list[dict[str, _ContractRow]]:
    if max_markets is None:
        return market_groups
    selected_by_market_id: dict[str, dict[str, _ContractRow]] = {}
    for rows in market_groups[:max_markets]:
        market_id = _market_group_id(rows)
        if market_id is not None:
            selected_by_market_id[market_id] = rows

    pending_ids = _pending_official_market_ids(
        store=store,
        asof_ts=asof_ts,
        limit=pending_sweep_limit,
    )
    if not pending_ids:
        return list(selected_by_market_id.values())

    for rows in reversed(market_groups):
        market_id = _market_group_id(rows)
        if market_id in pending_ids:
            selected_by_market_id[market_id] = rows
    return list(selected_by_market_id.values())


def _market_group_id(rows: dict[str, _ContractRow]) -> str | None:
    first = next(iter(rows.values()), None)
    return first.market_id if first is not None else None


def _parse_utc_date_start(value: str) -> datetime:
    parsed = datetime.fromisoformat(value).date()
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


def _parse_utc_next_day_start(value: str) -> datetime:
    return _parse_utc_date_start(value) + timedelta(days=1)


def _pending_official_market_ids(
    *,
    store: DuckDbIngestStore,
    asof_ts: datetime,
    limit: int | None,
) -> set[str]:
    if limit is not None and limit <= 0:
        return set()
    limit_clause = "" if limit is None else " limit ?"
    params: list[object] = [asof_ts]
    if limit is not None:
        params.append(limit)
    with store._connection() as conn:
        rows = conn.execute(
            """
            select market_id
            from validation.market_outcome_history
            where expiry_ts <= ?
              and official_winner is null
              and official_resolution_status = 'pending'
            order by expiry_ts asc, asset, interval, market_id
            """
            + limit_clause,
            params,
        ).fetchall()
    return {str(row[0]) for row in rows}


def _existing_outcome_fields(store: DuckDbIngestStore, *, market_id: str) -> dict[str, Any]:
    with store._connection() as conn:
        row = conn.execute(
            """
            select official_winner, winning_token_id, official_resolution_status,
                   official_label_source, official_resolved_at::VARCHAR,
                   threshold_price, threshold_event_ts::VARCHAR,
                   threshold_observed_ts::VARCHAR
            from validation.market_outcome_history
            where market_id = ?
            """,
            [market_id],
        ).fetchone()
    if row is None:
        return {
            "official_winner": None,
            "winning_token_id": None,
            "official_resolution_status": "pending",
            "official_label_source": None,
            "official_resolved_at": None,
            "threshold_price": None,
            "threshold_event_ts": None,
            "threshold_observed_ts": None,
        }
    return {
        "official_winner": row[0],
        "winning_token_id": row[1],
        "official_resolution_status": row[2],
        "official_label_source": row[3],
        "official_resolved_at": _parse_optional_duckdb_ts(row[4]),
        "threshold_price": row[5],
        "threshold_event_ts": _parse_optional_duckdb_ts(row[6]),
        "threshold_observed_ts": _parse_optional_duckdb_ts(row[7]),
    }


def _preserved_threshold(
    existing: dict[str, Any],
    *,
    start_ts: datetime,
    asof_ts: datetime,
) -> dict[str, Any] | None:
    threshold_price = existing["threshold_price"]
    threshold_event_ts = existing["threshold_event_ts"]
    threshold_observed_ts = existing["threshold_observed_ts"]
    if threshold_price is None or threshold_event_ts is None or threshold_observed_ts is None:
        return None
    if threshold_event_ts > start_ts or threshold_observed_ts > asof_ts:
        return None
    return {
        "price": threshold_price,
        "event_ts": threshold_event_ts,
        "observed_ts": threshold_observed_ts,
    }


def _runtime_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "market": f"{row['asset']} {row['interval']}",
        "market_id": row["market_id"],
        "market_slug": row["market_slug"],
        "asset": row["asset"],
        "interval": row["interval"],
        "start_ts": _iso_string(row["start_ts"]),
        "expiry_ts": _iso_string(row["expiry_ts"]),
        "threshold_price": row.get("threshold_price"),
        "threshold_event_ts": _iso_string(row.get("threshold_event_ts")),
        "threshold_observed_ts": _iso_string(row.get("threshold_observed_ts")),
        "end_price": row.get("end_price"),
        "end_event_ts": _iso_string(row.get("end_event_ts")),
        "end_observed_ts": _iso_string(row.get("end_observed_ts")),
        "computed_winner": row["computed_winner"],
        "official_winner": row["official_winner"],
        "winning_token_id": row["winning_token_id"],
        "official_resolution_status": row["official_resolution_status"],
        "mismatch": row["mismatch"],
    }


def _pending_official_resolution() -> OfficialOutcomeResolution:
    return OfficialOutcomeResolution(
        official_winner=None,
        winning_token_id=None,
        official_resolution_status="pending",
        official_label_source=None,
        official_resolved_at=None,
    )


def _market_payload_for_condition(
    *,
    condition_id: str,
    market_payload_source: MarketPayloadSource | None,
    payload_cache: dict[str, Mapping[str, Any] | None],
) -> Mapping[str, Any] | None:
    if market_payload_source is None:
        return None
    if condition_id not in payload_cache:
        payload_cache[condition_id] = market_payload_source(condition_id)
    return payload_cache[condition_id]


def _chainlink_tick_at_or_before(
    *,
    store: DuckDbIngestStore,
    symbol: str,
    event_ts_lte: datetime,
    observed_ts_lte: datetime,
) -> dict[str, Any] | None:
    with store._connection() as conn:
        row = conn.execute(
            """
            select price, event_ts::VARCHAR, observed_ts::VARCHAR
            from core.price_ticks
            where source_key = 'polymarket_rtds_chainlink'
              and symbol = ?
              and event_ts <= ?
              and observed_ts <= ?
            order by event_ts desc, observed_ts desc
            limit 1
            """,
            [symbol, event_ts_lte, observed_ts_lte],
        ).fetchone()
    if row is None:
        return None
    return {
        "price": row[0],
        "event_ts": _parse_duckdb_ts(row[1]),
        "observed_ts": _parse_duckdb_ts(row[2]),
    }


def _preserve_existing_resolved_official_fields(
    *,
    existing: dict[str, Any],
    resolution: OfficialOutcomeResolution,
) -> OfficialOutcomeResolution:
    if resolution.official_winner is not None:
        return resolution
    if existing["official_winner"] is None:
        return resolution
    return OfficialOutcomeResolution(
        official_winner=existing["official_winner"],
        winning_token_id=existing["winning_token_id"],
        official_resolution_status=existing["official_resolution_status"] or "resolved",
        official_label_source=existing["official_label_source"],
        official_resolved_at=existing["official_resolved_at"],
    )


def _token_id_from_payload(token: Mapping[str, Any]) -> str | None:
    for field in ("token_id", "tokenId", "asset_id", "assetId", "id"):
        value = token.get(field)
        if value is not None:
            return str(value)
    return None


def _interval_from_slug(slug: str) -> str:
    match = re.search(r"-(\d+m)-", slug)
    return match.group(1) if match is not None else "unknown"


def _parse_duckdb_ts(value: object) -> datetime:
    if isinstance(value, datetime):
        return _to_utc(value)
    return _to_utc(datetime.fromisoformat(str(value).replace(" ", "T")))


def _parse_optional_duckdb_ts(value: object) -> datetime | None:
    if value is None:
        return None
    return _parse_duckdb_ts(value)


def _iso_string(value: object) -> str | None:
    if value is None:
        return None
    return _parse_duckdb_ts(value).isoformat()


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
