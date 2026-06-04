from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.features.rust_decision_snapshots import SETTLEMENT_SOURCE_KEY
from polymarket_engine.storage.atomic import durable_replace
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore, MarketOutcomeRecord

OUTCOME_HISTORY_SCHEMA_VERSION = "polymarket-outcome-runtime-v1"
OUTCOME_STATUS_MAX_AGE_SECONDS = 120.0
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
    "official_resolution_status",
    "mismatch",
)


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


def computed_winner(*, threshold_price: float, end_price: float) -> str:
    return "UP" if end_price >= threshold_price else "DOWN"


def upsert_computed_market_outcomes(
    *,
    store: DuckDbIngestStore,
    asof_ts: datetime,
) -> int:
    asof_ts = _to_utc(asof_ts)
    contract_rows = _expired_contract_rows(store=store, asof_ts=asof_ts)
    records: list[MarketOutcomeRecord] = []
    for market_rows in _group_by_market(contract_rows).values():
        up = market_rows.get("UP")
        down = market_rows.get("DOWN")
        if up is None or down is None:
            continue
        if up.expiry_ts > asof_ts:
            continue
        threshold = _threshold_tick(store=store, contract=up, asof_ts=asof_ts)
        end = store.latest_price_tick_before(
            source_key=SETTLEMENT_SOURCE_KEY,
            symbol=up.settlement_symbol,
            event_ts_lte=up.expiry_ts,
            observed_ts_lte=asof_ts,
        )
        if threshold is None or end is None:
            continue
        if end.event_ts <= threshold.event_ts:
            continue
        winner = computed_winner(threshold_price=threshold.price, end_price=end.price)
        official = _official_fields(store=store, market_id=up.market_id)
        official_winner = official["official_winner"]
        official_status = official["official_resolution_status"] or "pending"
        mismatch = (
            official_winner != winner
            if official_winner is not None and official_status != "pending"
            else None
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
                threshold_price=threshold.price,
                threshold_event_ts=threshold.event_ts,
                threshold_observed_ts=threshold.observed_ts,
                end_price=end.price,
                end_event_ts=end.event_ts,
                end_observed_ts=end.observed_ts,
                computed_winner=winner,
                computed_label_source=SETTLEMENT_SOURCE_KEY,
                computed_at=asof_ts,
                official_winner=official_winner,
                official_resolution_status=official_status,
                official_label_source=official["official_label_source"],
                official_resolved_at=official["official_resolved_at"],
                rule_hash=up.rule_hash,
                mismatch=mismatch,
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
    row_error = _outcome_status_row_error(rows)
    if row_error is not None:
        return {
            "ok": False,
            "state": "INVALID",
            "error": row_error,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [],
        }
    limited = dict(payload)
    limited["rows"] = rows[:limit]
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
) -> tuple[_ContractRow, ...]:
    with store._connection() as conn:
        rows = conn.execute(
            """
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
            where expiry_ts <= ?
            order by expiry_ts asc, asset, market_id, side
            """,
            [asof_ts],
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


def _group_by_market(
    rows: tuple[_ContractRow, ...],
) -> dict[str, dict[str, _ContractRow]]:
    grouped: dict[str, dict[str, _ContractRow]] = {}
    for row in rows:
        grouped.setdefault(row.market_id, {})[row.side] = row
    return grouped


def _threshold_tick(
    *,
    store: DuckDbIngestStore,
    contract: _ContractRow,
    asof_ts: datetime,
) -> PriceObservation | None:
    if contract.threshold_type == "fixed_price" and contract.threshold_price is not None:
        return PriceObservation(
            source_key=SETTLEMENT_SOURCE_KEY,
            symbol=contract.settlement_symbol,
            event_ts=contract.start_ts,
            observed_ts=contract.start_ts,
            price=contract.threshold_price,
        )
    return store.latest_price_tick_before(
        source_key=SETTLEMENT_SOURCE_KEY,
        symbol=contract.settlement_symbol,
        event_ts_lte=contract.start_ts,
        observed_ts_lte=asof_ts,
    )


def _official_fields(store: DuckDbIngestStore, *, market_id: str) -> dict[str, Any]:
    with store._connection() as conn:
        row = conn.execute(
            """
            select official_winner, official_resolution_status, official_label_source,
                   official_resolved_at::VARCHAR
            from validation.market_outcome_history
            where market_id = ?
            """,
            [market_id],
        ).fetchone()
    if row is None:
        return {
            "official_winner": None,
            "official_resolution_status": "pending",
            "official_label_source": None,
            "official_resolved_at": None,
        }
    return {
        "official_winner": row[0],
        "official_resolution_status": row[1],
        "official_label_source": row[2],
        "official_resolved_at": _parse_optional_duckdb_ts(row[3]),
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
        "computed_winner": row["computed_winner"],
        "official_winner": row["official_winner"],
        "official_resolution_status": row["official_resolution_status"],
        "mismatch": row["mismatch"],
    }


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
