from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from polymarket_engine.storage.atomic import durable_replace
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore
from polymarket_engine.validation.outcomes import OUTCOME_HISTORY_SCHEMA_VERSION
from polymarket_engine.validation.outcomes import PolymarketClobMarketPayloadSource
from polymarket_engine.validation.outcomes import latest_market_outcome_rows_from_connection
from polymarket_engine.validation.outcomes import upsert_official_market_outcomes
from polymarket_engine.validation.outcomes import write_outcome_history_status


OUTCOME_OUTPUT_LIMIT = 5000
OUTCOME_REFRESH_MARKET_LIMIT = 4
OUTCOME_PENDING_SWEEP_LIMIT = 20
OUTCOME_OUTPUT_LIMIT_ENV = "POLYMARKET_OUTCOME_OUTPUT_LIMIT"
OFFICIAL_OUTCOME_SOURCE_ENV = "POLYMARKET_OFFICIAL_OUTCOME_SOURCE"
OFFICIAL_OUTCOME_REFRESH_LIMIT_ENV = "POLYMARKET_OFFICIAL_OUTCOME_REFRESH_LIMIT"
OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT_ENV = (
    "POLYMARKET_OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT"
)


def run_outcome_refresh_loop(
    *,
    duckdb_path: Path,
    outcome_status_path: Path,
    interval_seconds: float,
    max_cycles: int | None = None,
) -> None:
    _validate_loop_cadence(interval_seconds=interval_seconds, max_cycles=max_cycles)
    with DuckDbIngestStore(duckdb_path, persistent_connection=False) as store:
        store.apply_schema()
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            try:
                refresh_market_outcomes(store=store, out_path=outcome_status_path)
            except duckdb.Error as exc:
                if not _is_transient_duckdb_lock_error(exc):
                    raise
                print(
                    f"outcome refresh skipped: DuckDB lock unavailable: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                _write_locked_outcome_status(out_path=outcome_status_path, exc=exc)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
            time.sleep(interval_seconds)


def refresh_market_outcomes(*, store: DuckDbIngestStore, out_path: Path) -> int:
    return _upsert_market_outcomes(store=store, out_path=out_path)


def has_expired_pending_official_outcomes(*, store: DuckDbIngestStore) -> bool:
    return _has_expired_pending_official_outcomes(store=store)


def _validate_loop_cadence(
    *,
    interval_seconds: float,
    max_cycles: int | None,
) -> None:
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, (int, float))
        or not math.isfinite(interval_seconds)
        or interval_seconds <= 0
    ):
        raise ValueError("interval_seconds must be positive")
    if max_cycles is not None and (
        isinstance(max_cycles, bool)
        or not isinstance(max_cycles, int)
        or max_cycles <= 0
    ):
        raise ValueError("max_cycles must be positive when provided")


def _is_transient_duckdb_lock_error(exc: duckdb.Error) -> bool:
    message = str(exc).lower()
    return "conflicting lock" in message or "could not set lock" in message


def _write_locked_outcome_status(*, out_path: Path, exc: duckdb.Error) -> None:
    rows = _existing_outcome_status_rows(out_path)
    generated_at = datetime.now(timezone.utc).isoformat()
    error = f"DuckDB lock unavailable: {exc}"
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(f"{out_path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    durable_replace(tmp_path, out_path)


def _existing_outcome_status_rows(out_path: Path) -> list[object]:
    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("schema_version") != OUTCOME_HISTORY_SCHEMA_VERSION:
        return []
    rows = payload.get("rows")
    return rows if isinstance(rows, list) else []


def _upsert_market_outcomes(*, store: DuckDbIngestStore, out_path: Path) -> int:
    written = upsert_official_market_outcomes(
        store=store,
        asof_ts=datetime.now(timezone.utc),
        market_payload_source=_official_outcome_payload_source_from_env(),
        max_markets=_official_outcome_refresh_limit_from_env(),
        pending_sweep_limit=_official_outcome_pending_sweep_limit_from_env(),
    )
    with store._connection() as conn:
        rows = latest_market_outcome_rows_from_connection(
            conn=conn,
            limit=_outcome_output_limit_from_env(),
        )
    write_outcome_history_status(out_path=out_path, rows=rows)
    return written


def _has_expired_pending_official_outcomes(*, store: DuckDbIngestStore) -> bool:
    with store._connection() as conn:
        row = conn.execute(
            """
            select count(*)
            from validation.market_outcome_history
            where expiry_ts <= ?
              and official_winner is null
              and official_resolution_status = 'pending'
            """,
            [datetime.now(timezone.utc)],
        ).fetchone()
    return bool(row is not None and int(row[0]) > 0)


def _official_outcome_payload_source_from_env() -> PolymarketClobMarketPayloadSource | None:
    source = os.environ.get(OFFICIAL_OUTCOME_SOURCE_ENV, "").strip().lower()
    if source not in {"clob", "polymarket_clob", "polymarket_clob_market"}:
        return None
    return PolymarketClobMarketPayloadSource(
        base_url=os.environ.get(
            "POLYMARKET_CLOB_HTTP_URL",
            "https://clob.polymarket.com",
        ),
        timeout_seconds=float(
            os.environ.get("POLYMARKET_OFFICIAL_OUTCOME_TIMEOUT_SECONDS", "2.0")
        ),
    )


def _official_outcome_refresh_limit_from_env() -> int | None:
    raw_limit = os.environ.get(OFFICIAL_OUTCOME_REFRESH_LIMIT_ENV)
    if raw_limit is None or raw_limit.strip() == "":
        return OUTCOME_REFRESH_MARKET_LIMIT
    limit = int(raw_limit)
    return limit if limit > 0 else None


def _official_outcome_pending_sweep_limit_from_env() -> int | None:
    raw_limit = os.environ.get(OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT_ENV)
    if raw_limit is None or raw_limit.strip() == "":
        return OUTCOME_PENDING_SWEEP_LIMIT
    limit = int(raw_limit)
    return limit if limit > 0 else None


def _outcome_output_limit_from_env() -> int:
    raw_limit = os.environ.get(OUTCOME_OUTPUT_LIMIT_ENV)
    if raw_limit is None or raw_limit.strip() == "":
        return OUTCOME_OUTPUT_LIMIT
    return max(20, int(raw_limit))
