from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence, cast
import json
import os
import subprocess
import time

import duckdb
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from polymarket_engine.monitor import (
    MonitorSnapshot,
    fetch_monitor_snapshot,
    snapshot_from_status_payload,
)
from polymarket_engine.probability.runtime import ProbabilityRuntimeCache
from polymarket_engine.runtime_gates import evaluate_runtime_gates
from polymarket_engine.validation.outcomes import build_outcome_history_payload


NORMALIZED_HEALTH_SCHEMA_VERSION = "polymarket-normalized-health-v1"
VOLATILITY_STATUS_SCHEMA_VERSION = "polymarket-volatility-runtime-v1"


def build_runtime_router(
    *,
    status_path: Path = Path("data/live/status.json"),
    duckdb_path: Path = Path("data/db/polymarket.duckdb"),
    normalized_health_path: Path = Path("data/live/normalized_health.json"),
    probability_status_path: Path = Path("data/live/probabilities.json"),
    probability_inputs_path: Path | None = Path("data/live/probability_inputs.json"),
    outcome_status_path: Path = Path("data/live/outcomes.json"),
    target_cache_path: Path = Path("data/live/targets.json"),
    volatility_status_path: Path = Path("data/live/volatility.json"),
    data_dir: Path = Path("data"),
    enable_container_status: bool = False,
    enable_runtime_probabilities: bool = False,
    allow_probability_compute_fallback: bool = False,
) -> APIRouter:
    router = APIRouter(prefix="/api/runtime")
    probability_cache = ProbabilityRuntimeCache()
    probability_event_path = probability_status_path.with_name("probability-events.jsonl")

    @router.get("/status")
    def runtime_status() -> dict[str, Any]:
        payload, read_error = _read_json_or_error(status_path)
        if payload is None:
            return _status_error_payload(
                path=status_path,
                state=read_error["state"],
                error=read_error["error"],
            )

        shape_error = _status_shape_error(payload)
        if shape_error is not None:
            return _status_error_payload(
                path=status_path,
                state="INVALID",
                error=shape_error,
                payload=payload,
            )

        return _status_payload_from_valid_status(status_path, payload)

    @router.get("/monitor")
    def runtime_monitor(limit: int = 12) -> dict[str, Any]:
        if status_path.exists():
            payload, read_error = _read_json_or_error(status_path)
            if payload is None:
                return _empty_monitor_payload(
                    state=read_error["state"],
                    error=read_error["error"],
                )
            shape_error = _status_shape_error(payload, require_generated_at=True)
            if shape_error is not None:
                return _empty_monitor_payload(
                    state="INVALID",
                    error=shape_error,
                )
        elif not duckdb_path.exists():
            return _empty_monitor_payload(
                state="MISSING",
                error=(
                    f"runtime source missing: status_path {status_path} missing "
                    f"and duckdb_path {duckdb_path} missing"
                ),
            )

        try:
            snapshot = fetch_monitor_snapshot(
                duckdb_path=duckdb_path,
                limit=limit,
                status_path=status_path if status_path.exists() else None,
                target_cache_path=target_cache_path if target_cache_path.exists() else None,
            )
        except duckdb.Error as exc:
            return _empty_monitor_payload(
                state="INVALID",
                error=f"DuckDB monitor unavailable: {_format_error(exc)}",
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            return _empty_monitor_payload(
                state="INVALID",
                error=f"runtime monitor unavailable: {_format_error(exc)}",
            )
        return _snapshot_to_json(snapshot)

    @router.get("/normalized-health")
    def normalized_health() -> dict[str, Any]:
        payload, read_error = _read_json_or_error(normalized_health_path)
        if payload is None:
            return {
                "ok": False,
                "path": str(normalized_health_path),
                "state": read_error["state"],
                "error": read_error["error"],
                "tables": [],
            }

        schema_version = payload.get("schema_version")
        ok = schema_version == NORMALIZED_HEALTH_SCHEMA_VERSION
        return {
            "ok": ok,
            "path": str(normalized_health_path),
            "state": "OK" if ok else "SCHEMA STALE",
            "schema_version": schema_version,
            "generated_at": payload.get("generated_at"),
            "tables": payload.get("tables", []),
        }

    @router.get("/gates")
    def runtime_gates() -> dict[str, Any]:
        return evaluate_runtime_gates(
            status_path=status_path,
            normalized_health_path=normalized_health_path,
        )

    @router.get("/live")
    def runtime_live(limit: int = 8) -> dict[str, Any]:
        return _runtime_live_payload(
            status_path=status_path,
            duckdb_path=duckdb_path,
            normalized_health_path=normalized_health_path,
            target_cache_path=target_cache_path,
            volatility_status_path=volatility_status_path,
            limit=limit,
        )

    @router.get("/live/stream")
    def runtime_live_stream(
        limit: int = 8,
        interval_ms: int = 250,
        max_events: int | None = None,
    ) -> StreamingResponse:
        interval_seconds = max(interval_ms, 50) / 1000

        async def events() -> Any:
            emitted = 0
            while max_events is None or emitted < max_events:
                payload = _runtime_live_payload(
                    status_path=status_path,
                    duckdb_path=duckdb_path,
                    normalized_health_path=normalized_health_path,
                    target_cache_path=target_cache_path,
                    volatility_status_path=volatility_status_path,
                    limit=limit,
                )
                yield f"event: live\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                emitted += 1
                if max_events is not None and emitted >= max_events:
                    break
                await asyncio.sleep(interval_seconds)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/probabilities")
    def runtime_probabilities(limit: int = 8) -> dict[str, Any]:
        if not enable_runtime_probabilities:
            if probability_status_path.exists():
                return _probability_status_payload(
                    probability_status_path=probability_status_path,
                    limit=limit,
                )
            return _probabilities_disabled_payload()
        hot_or_fallback_payload: dict[str, Any] | None = None
        if probability_status_path.exists():
            status_payload = _probability_status_payload(
                probability_status_path=probability_status_path,
                limit=limit,
            )
            rows = status_payload.get("rows")
            if isinstance(rows, list) and rows:
                return status_payload
        try:
            if probability_inputs_path is not None and probability_inputs_path.exists():
                hot_or_fallback_payload = probability_cache.payload(
                    duckdb_path=duckdb_path,
                    limit=limit,
                    allow_compute=allow_probability_compute_fallback,
                    probability_inputs_path=probability_inputs_path,
                )
                hot_rows = hot_or_fallback_payload.get("rows")
                if (
                    hot_or_fallback_payload.get("source") == "hot_inputs"
                    and isinstance(hot_rows, list)
                    and hot_rows
                ):
                    return hot_or_fallback_payload
        except ValueError as exc:
            return {
                "ok": False,
                "state": "INVALID",
                "error": str(exc),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "cached": False,
                "model_version": None,
                "rows": [],
                "skipped": 0,
                "errors": [str(exc)],
            }
        if probability_status_path.exists():
            return _probability_status_payload(
                probability_status_path=probability_status_path,
                limit=limit,
            )
        if hot_or_fallback_payload is not None:
            return hot_or_fallback_payload
        try:
            return probability_cache.payload(
                duckdb_path=duckdb_path,
                limit=limit,
                allow_compute=allow_probability_compute_fallback,
                probability_inputs_path=probability_inputs_path,
            )
        except ValueError as exc:
            return {
                "ok": False,
                "state": "INVALID",
                "error": str(exc),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "cached": False,
                "model_version": None,
                "rows": [],
                "skipped": 0,
                "errors": [str(exc)],
            }

    @router.get("/probability-events/stream")
    def runtime_probability_events_stream(
        limit: int = 24,
        interval_ms: int = 250,
        max_events: int | None = None,
        after_event_id: str | None = None,
    ) -> StreamingResponse:
        interval_seconds = max(interval_ms, 50) / 1000

        async def events() -> Any:
            emitted = 0
            last_event_id = after_event_id
            while max_events is None or emitted < max_events:
                payload = _probability_events_payload(
                    probability_event_path=probability_event_path,
                    limit=limit,
                    after_event_id=last_event_id,
                )
                rows = payload.get("events", [])
                if isinstance(rows, list) and rows:
                    last_event_id = _last_probability_event_id(rows) or last_event_id
                    yield (
                        "event: probability\n"
                        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                    )
                    emitted += 1
                elif max_events is not None:
                    yield (
                        "event: probability\n"
                        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                    )
                    emitted += 1
                if max_events is not None and emitted >= max_events:
                    break
                await asyncio.sleep(interval_seconds)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/outcomes")
    def runtime_outcomes(limit: int = 20) -> dict[str, Any]:
        return build_outcome_history_payload(
            duckdb_path=duckdb_path,
            limit=limit,
            outcome_status_path=outcome_status_path,
        )

    @router.get("/storage")
    def storage() -> dict[str, Any]:
        return _storage_payload(data_dir)

    @router.get("/containers")
    def containers() -> dict[str, Any]:
        if not enable_container_status:
            raise HTTPException(status_code=403, detail="container status disabled")

        try:
            result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return _container_error_payload(exc)
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    return router


def container_status_enabled_from_env() -> bool:
    return os.getenv("POLYMARKET_ENABLE_CONTAINER_STATUS") == "1"


def runtime_probabilities_enabled_from_env() -> bool:
    return os.getenv("POLYMARKET_ENABLE_RUNTIME_PROBABILITIES") == "1"


def runtime_probability_compute_fallback_enabled_from_env() -> bool:
    return os.getenv("POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE") == "1"


def _probabilities_disabled_payload() -> dict[str, Any]:
    return {
        "schema_version": "polymarket-probability-runtime-v1",
        "ok": True,
        "state": "DISABLED",
        "error": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
        "model_version": None,
        "rows": [],
        "skipped": 0,
        "errors": [],
    }


def _probability_status_payload(
    *,
    probability_status_path: Path,
    limit: int,
) -> dict[str, Any]:
    status_payload, read_error = _read_json_or_error(probability_status_path)
    if status_payload is None:
        return {
            "ok": False,
            "state": read_error["state"],
            "error": read_error["error"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "model_version": None,
            "rows": [],
            "skipped": 0,
            "errors": [read_error["error"]],
        }
    rows = status_payload.get("rows")
    if not isinstance(rows, list):
        return {
            "ok": False,
            "state": "INVALID",
            "error": "probability status shape invalid: rows must be a list",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "model_version": None,
            "rows": [],
            "skipped": 0,
            "errors": ["probability status shape invalid: rows must be a list"],
        }
    limited = dict(status_payload)
    limited["rows"] = rows[:limit]
    limited["cached"] = False
    return limited


def _probability_events_payload(
    *,
    probability_event_path: Path,
    limit: int,
    after_event_id: str | None,
) -> dict[str, Any]:
    rows, errors = _read_probability_event_rows(
        probability_event_path,
        limit=max(limit, 1),
    )
    if after_event_id:
        rows = _probability_events_after(rows, after_event_id)
    return {
        "schema_version": "polymarket-probability-events-v1",
        "ok": not errors,
        "state": "OK" if not errors else "PARTIAL",
        "error": None if not errors else errors[0],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "path": str(probability_event_path),
        "events": rows[-limit:] if limit > 0 else [],
        "errors": errors,
    }


def _read_probability_event_rows(
    path: Path,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    read_path = path if path.exists() else _newest_probability_event_drain(path)
    if read_path is None:
        return [], [f"{path} missing"]
    try:
        lines = read_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], [f"file read failed: {_format_error(exc)}"]

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for line in lines[-max(limit * 2, limit) :]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"JSONL parse failed: {_format_error(exc)}")
            continue
        if not isinstance(payload, dict):
            errors.append("probability event line must be an object")
            continue
        rows.append(payload)
    return rows[-limit:], errors


def _newest_probability_event_drain(path: Path) -> Path | None:
    newest: tuple[int, Path] | None = None
    for candidate in path.parent.glob(f"{path.name}.*.drain"):
        try:
            mtime_ns = candidate.stat().st_mtime_ns
        except OSError:
            continue
        if newest is None or mtime_ns > newest[0]:
            newest = (mtime_ns, candidate)
    return None if newest is None else newest[1]


def _probability_events_after(
    rows: Sequence[dict[str, Any]],
    after_event_id: str,
) -> list[dict[str, Any]]:
    for index, row in enumerate(rows):
        if row.get("event_id") == after_event_id:
            return list(rows[index + 1 :])
    return list(rows)


def _last_probability_event_id(rows: Sequence[object]) -> str | None:
    for row in reversed(rows):
        if isinstance(row, dict):
            event_id = row.get("event_id")
            if isinstance(event_id, str) and event_id:
                return event_id
    return None


def _runtime_live_payload(
    *,
    status_path: Path,
    duckdb_path: Path,
    normalized_health_path: Path,
    target_cache_path: Path,
    volatility_status_path: Path,
    limit: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    server_sent_at = datetime.now(timezone.utc)
    payload, read_error = _read_json_or_error(status_path)
    if payload is None:
        status = _status_error_payload(
            path=status_path,
            state=read_error["state"],
            error=read_error["error"],
        )
        gates = evaluate_runtime_gates(
            status_path=status_path,
            normalized_health_path=normalized_health_path,
        )
        monitor = _empty_monitor_payload(state=read_error["state"], error=read_error["error"])
    else:
        shape_error = _status_shape_error(payload, require_generated_at=True)
        if shape_error is not None:
            status = _status_error_payload(
                path=status_path,
                state="INVALID",
                error=shape_error,
                payload=payload,
            )
            gates = _runtime_gates_from_status_payload(
                status_path=status_path,
                normalized_health_path=normalized_health_path,
                payload=payload,
            )
            monitor = _empty_monitor_payload(state="INVALID", error=shape_error)
        else:
            status = _status_payload_from_valid_status(status_path, payload)
            gates = _runtime_gates_from_status_payload(
                status_path=status_path,
                normalized_health_path=normalized_health_path,
                payload=payload,
            )
            try:
                target_cache, _target_cache_error = _read_json_or_error(target_cache_path)
                monitor = _snapshot_to_json(
                    snapshot_from_status_payload(
                        payload,
                        limit=limit,
                        target_cache=target_cache,
                    )
                )
            except (TypeError, ValueError, KeyError) as exc:
                monitor = _empty_monitor_payload(
                    state="INVALID",
                    error=f"runtime monitor unavailable: {_format_error(exc)}",
                )

    latency = _live_latency_payload(
        status=status,
        monitor=monitor,
        server_sent_at=server_sent_at,
        api_build_ms=int((time.perf_counter() - started) * 1000),
    )
    volatility = _live_volatility_payload(
        duckdb_path=duckdb_path,
        volatility_status_path=volatility_status_path,
        limit=limit,
    )
    return {
        "ok": bool(status.get("ok"))
        and bool(gates.get("ok"))
        and bool(monitor.get("orderbooks", [])),
        "server_sent_at": server_sent_at.isoformat(),
        "status": status,
        "gates": gates,
        "monitor": monitor,
        "volatility": volatility,
        "latency": latency,
    }


def _live_volatility_payload(
    *,
    duckdb_path: Path,
    volatility_status_path: Path,
    limit: int,
) -> dict[str, Any]:
    if volatility_status_path.exists():
        return _live_volatility_payload_from_status_file(
            volatility_status_path=volatility_status_path,
            limit=limit,
        )
    if not duckdb_path.exists():
        return {
            "state": "MISSING",
            "rows": [],
            "errors": [f"{duckdb_path} missing"],
        }
    try:
        with _connect_read_only_with_retry(duckdb_path, lock_retry_seconds=2.0) as conn:
            rows = conn.execute(
                """
                select
                    asset,
                    cast(asof_ts as varchar) as asof_ts,
                    short_realized_vol,
                    medium_realized_vol,
                    long_realized_vol,
                    sigma_tau,
                    volatility_regime,
                    data_quality_flags_json
                from (
                    select
                        state_inputs.*,
                        row_number() over (
                            partition by asset
                            order by asof_ts desc, created_at desc
                        ) as row_number
                    from features.asof_state_inputs as state_inputs
                ) as latest
                where row_number = 1
                order by case asset when 'BTC' then 0 when 'ETH' then 1 else 2 end
                limit ?
                """,
                [limit],
            ).fetchall()
    except duckdb.Error as exc:
        return {
            "state": "INVALID",
            "rows": [],
            "errors": [f"DuckDB volatility unavailable: {_format_error(exc)}"],
        }

    return {
        "state": "OK",
        "rows": [_volatility_row_from_db_row(row) for row in rows],
        "errors": [],
    }


def _live_volatility_payload_from_status_file(
    *,
    volatility_status_path: Path,
    limit: int,
) -> dict[str, Any]:
    payload, read_error = _read_json_or_error(volatility_status_path)
    if payload is None:
        return {
            "state": read_error["state"],
            "rows": [],
            "errors": [read_error["error"]],
        }
    if payload.get("schema_version") != VOLATILITY_STATUS_SCHEMA_VERSION:
        return {
            "state": "INVALID",
            "rows": [],
            "errors": [
                f"volatility status schema invalid: {payload.get('schema_version')!r}"
            ],
        }
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return {
            "state": "INVALID",
            "rows": [],
            "errors": ["volatility status shape invalid: rows must be a list"],
        }
    raw_errors = payload.get("errors", [])
    errors = [str(error) for error in raw_errors] if isinstance(raw_errors, list) else []
    return {
        "state": str(payload.get("state") or "OK"),
        "generated_at": payload.get("generated_at"),
        "source_key": payload.get("source_key"),
        "lookback_limit": payload.get("lookback_limit"),
        "rows": [
            _volatility_row_from_mapping(row)
            for row in rows[:limit]
            if isinstance(row, dict)
        ],
        "errors": errors,
    }


def _volatility_row_from_db_row(row: Sequence[Any]) -> dict[str, Any]:
    return _volatility_row_from_mapping(
        {
            "asset": row[0],
            "asof_ts": row[1],
            "short_realized_vol": row[2],
            "medium_realized_vol": row[3],
            "long_realized_vol": row[4],
            "sigma_tau": row[5],
            "volatility_regime": row[6],
            "data_quality_flags_json": row[7],
        }
    )


def _volatility_row_from_mapping(row: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    asof_ts = _parse_timestamp_or_none(row.get("asof_ts"))
    sigma_tau = _optional_float(row.get("sigma_tau"))
    return {
        "asset": str(row.get("asset")),
        "asof_ts": None if asof_ts is None else asof_ts.isoformat(),
        "short_realized_vol": _optional_float(row.get("short_realized_vol")),
        "medium_realized_vol": _optional_float(row.get("medium_realized_vol")),
        "long_realized_vol": _optional_float(row.get("long_realized_vol")),
        "sigma_tau": sigma_tau,
        "volatility_regime": row.get("volatility_regime"),
        "age_ms": None
        if asof_ts is None
        else max(0, int((now - asof_ts).total_seconds() * 1000)),
        "flags": _volatility_flags(
            row.get("flags", row.get("data_quality_flags_json")),
            sigma_tau=sigma_tau,
        ),
    }


def _connect_read_only_with_retry(
    duckdb_path: Path,
    *,
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


def _volatility_flags(raw_flags: object, *, sigma_tau: object) -> list[str]:
    flags: list[str] = []
    if isinstance(raw_flags, list):
        flags = [str(flag) for flag in raw_flags]
    elif isinstance(raw_flags, str):
        try:
            loaded = json.loads(raw_flags)
        except json.JSONDecodeError:
            loaded = ["invalid_flags_json"]
        if isinstance(loaded, list):
            flags = [str(flag) for flag in loaded]
        else:
            flags = ["invalid_flags_json"]
    if sigma_tau is None and "missing_volatility" not in flags:
        flags.append("missing_volatility")
    return flags if flags else ["OK"]


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(cast(Any, value))


def _read_json_or_error(path: Path) -> tuple[dict[str, Any] | None, dict[str, str]]:
    if not path.exists():
        return None, {"state": "MISSING", "error": f"{path} missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, {"state": "INVALID", "error": f"JSON parse failed: {_format_error(exc)}"}
    except OSError as exc:
        return None, {"state": "INVALID", "error": f"file read failed: {_format_error(exc)}"}
    if not isinstance(payload, dict):
        return None, {"state": "INVALID", "error": "JSON root must be an object"}
    return payload, {}


def _format_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _snapshot_to_json(snapshot: MonitorSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    payload.pop("prices", None)
    payload["generated_at"] = snapshot.generated_at.isoformat()
    return payload


def _empty_monitor_payload(*, state: str, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "state": state,
        "error": error,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "price_rows": [],
        "orderbooks": [],
        "contracts": [],
        "ingest_counts": [],
        "normalized_health": [],
        "source_freshness": [],
        "source_disagreements": [],
        "orderbook_freshness": [],
        "source_errors": {"runtime_status": error},
        "latency_marks": [],
        "hot_decision_telemetry": None,
        "health_flags": ["runtime_status_invalid"],
        "websocket_status": [],
    }


def _status_payload_from_valid_status(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        generated_at = _parse_timestamp(payload.get("generated_at"))
    except ValueError as exc:
        return _status_error_payload(
            path=path,
            state="INVALID",
            error=f"generated_at timestamp parse failed: {exc}",
            payload=payload,
        )

    return {
        "ok": not bool(payload.get("health_flags", [])),
        "state": "OK" if not payload.get("health_flags", []) else "STALE",
        "status_path": str(path),
        "schema_kind": payload.get("schema_version", "legacy"),
        "mode": payload.get("mode", "legacy"),
        "generated_at": payload.get("generated_at"),
        "age_ms": _age_ms(generated_at),
        "counts": _status_counts(payload),
        "websocket_status": payload.get("websocket_status", []),
        "latency_marks": payload.get("latency_marks", []),
        "source_errors": payload.get("source_errors", {}),
        "health_flags": payload.get("health_flags", []),
    }


def _runtime_gates_from_status_payload(
    *,
    status_path: Path,
    normalized_health_path: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    generated_at = _parse_timestamp_or_none(payload.get("generated_at"))
    age_seconds = None
    if generated_at is None:
        failures.append("status generated_at invalid")
    else:
        age_seconds = (datetime.now(timezone.utc) - generated_at).total_seconds()
        if age_seconds > 30:
            failures.append("status file stale")

    price_rows = _status_price_rows(payload)
    orderbook_rows = list(_list_field(payload, "orderbooks"))
    if not price_rows:
        failures.append("status has no price rows")
    if not orderbook_rows:
        failures.append("status has no orderbook rows")
    health_flags = payload.get("health_flags")
    if isinstance(health_flags, list) and health_flags:
        failures.append(f"status health_flags present: {', '.join(map(str, health_flags))}")
    elif health_flags is not None and not isinstance(health_flags, list):
        failures.append("status health_flags invalid")

    normalized_health = (
        _normalized_health_gate_payload(path=normalized_health_path, failures=failures)
        if normalized_health_path.exists()
        else None
    )
    return {
        "ok": not failures,
        "status_path": str(status_path),
        "normalized_health_path": str(normalized_health_path),
        "thresholds": {
            "max_status_age_seconds": 30,
            "max_normalized_health_age_seconds": 30,
        },
        "failures": failures,
        "status": {
            "state": "OK",
            "generated_at": payload.get("generated_at"),
            "age_seconds": age_seconds,
            "counts": {
                "prices": len(price_rows),
                "orderbooks": len(orderbook_rows),
            },
            "health_flags": health_flags if isinstance(health_flags, list) else [],
        },
        "normalized_health": normalized_health,
    }


def _normalized_health_gate_payload(
    *,
    path: Path,
    failures: list[str],
) -> dict[str, Any]:
    payload, read_error = _read_json_or_error(path)
    if payload is None:
        failures.append("normalized health missing")
        return {"state": "INVALID", "schema_version": None, "tables": []}

    generated_at = _parse_timestamp_or_none(payload.get("generated_at"))
    age_seconds = None
    if generated_at is None:
        failures.append("normalized health generated_at invalid")
    else:
        age_seconds = (datetime.now(timezone.utc) - generated_at).total_seconds()
        if age_seconds > 30:
            failures.append("normalized health stale")

    schema_version = payload.get("schema_version")
    if schema_version != NORMALIZED_HEALTH_SCHEMA_VERSION:
        failures.append("normalized health schema stale")
    tables = payload.get("tables", [])
    if not isinstance(tables, list):
        failures.append("normalized health tables invalid")
        tables = []
    return {
        "state": "OK" if not read_error else read_error["state"],
        "schema_version": schema_version,
        "generated_at": payload.get("generated_at"),
        "age_seconds": age_seconds,
        "tables": tables,
    }


def _live_latency_payload(
    *,
    status: dict[str, Any],
    monitor: dict[str, Any],
    server_sent_at: datetime,
    api_build_ms: int,
) -> dict[str, Any]:
    latency_marks = status.get("latency_marks", [])
    return {
        "status_age_ms": status.get("age_ms"),
        "api_build_ms": api_build_ms,
        "server_sent_at": server_sent_at.isoformat(),
        "source_to_observed_ms": _latency_mark_value(
            latency_marks,
            "source_to_observed_ms",
        ),
        "observed_to_state_us": _latency_mark_value(
            latency_marks,
            "observed_to_state_us",
        ),
        "orderbook_rows": len(monitor.get("orderbooks", [])),
    }


def _latency_mark_value(rows: object, name: str) -> int | None:
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or row.get("name") != name:
            continue
        value = row.get("elapsed_ms", row.get("value"))
        if isinstance(value, bool) or value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _container_error_payload(exc: BaseException) -> dict[str, Any]:
    return {
        "ok": False,
        "stdout": "",
        "stderr": "",
        "returncode": None,
        "error": f"container status unavailable: {_format_error(exc)}",
    }


def _status_error_payload(
    *,
    path: Path,
    state: str,
    error: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {} if payload is None else payload
    return {
        "ok": False,
        "state": state,
        "error": error,
        "status_path": str(path),
        "schema_kind": payload.get("schema_version", "unknown"),
        "mode": payload.get("mode", "unknown"),
        "generated_at": payload.get("generated_at"),
        "age_ms": None,
        "counts": _status_counts(payload),
        "websocket_status": list(_list_field(payload, "websocket_status")),
        "latency_marks": list(_list_field(payload, "latency_marks")),
        "source_errors": _source_errors_field(payload, error),
        "health_flags": list(_list_field(payload, "health_flags")),
    }


def _status_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {
        "prices": len(_status_price_rows(payload)),
        "orderbooks": len(_list_field(payload, "orderbooks")),
        "current": len(_list_field(payload, "current")),
        "next": len(_list_field(payload, "next")),
        "next_next": len(_list_field(payload, "next_next")),
        "websocket_status": len(_list_field(payload, "websocket_status")),
    }


def _status_price_rows(payload: dict[str, Any]) -> list[Any]:
    if "prices" in payload:
        return list(_list_field(payload, "prices"))
    return list(_list_field(payload, "chainlink_prices"))


def _status_shape_error(
    payload: dict[str, Any],
    *,
    require_generated_at: bool = False,
) -> str | None:
    if require_generated_at and payload.get("generated_at") is None:
        return "status shape invalid: missing generated_at"

    for field in (
        "prices",
        "chainlink_prices",
        "orderbooks",
        "current",
        "next",
        "next_next",
        "websocket_status",
        "latency_marks",
        "health_flags",
    ):
        value = payload.get(field)
        if value is not None and not _is_json_list(value):
            return f"status shape invalid: {field} must be a list"

    for field in (
        "prices",
        "chainlink_prices",
        "orderbooks",
        "current",
        "next",
        "next_next",
        "websocket_status",
        "latency_marks",
        "normalized_health",
    ):
        row_error = _object_rows_error(payload, field)
        if row_error is not None:
            return row_error

    price_error = _required_row_fields_error(
        payload,
        fields=("prices", "chainlink_prices"),
        required=("source_key", "symbol", "price"),
        row_name="price",
    )
    if price_error is not None:
        return price_error

    orderbook_error = _required_row_fields_error(
        payload,
        fields=("orderbooks",),
        required=(
            "contract_id",
            "token_id",
            "observed_ts",
            "best_bid",
            "best_ask",
            "spread",
        ),
        row_name="orderbook",
    )
    if orderbook_error is not None:
        return orderbook_error

    health_flags = payload.get("health_flags")
    if isinstance(health_flags, list):
        for index, flag in enumerate(health_flags):
            if not isinstance(flag, str):
                return f"status shape invalid: health_flags[{index}] must be a string"

    source_errors = payload.get("source_errors")
    if source_errors is not None and not isinstance(source_errors, dict):
        return "status shape invalid: source_errors must be an object"
    return None


def _object_rows_error(payload: dict[str, Any], field: str) -> str | None:
    rows = payload.get(field)
    if not isinstance(rows, list):
        return None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return f"status shape invalid: {field}[{index}] must be an object"
    return None


def _required_row_fields_error(
    payload: dict[str, Any],
    *,
    fields: tuple[str, ...],
    required: tuple[str, ...],
    row_name: str,
) -> str | None:
    for field in fields:
        rows = payload.get(field)
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for required_field in required:
                if required_field not in row:
                    return (
                        f"status shape invalid: {field}[{index}] missing required "
                        f"{row_name} field {required_field}"
                    )
    return None


def _list_field(payload: dict[str, Any], field: str) -> Sequence[Any]:
    value: object = payload.get(field)
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(value)
    return ()


def _source_errors_field(payload: dict[str, Any], error: str) -> dict[str, Any]:
    source_errors = payload.get("source_errors")
    if isinstance(source_errors, dict):
        return source_errors
    return {"runtime_status": error}


def _is_json_list(value: object) -> bool:
    return isinstance(value, list)


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    raw = str(value)
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_timestamp_or_none(value: object) -> datetime | None:
    try:
        return _parse_timestamp(value)
    except ValueError:
        return None


def _age_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int((datetime.now(timezone.utc) - value).total_seconds() * 1000)


def _storage_payload(data_dir: Path) -> dict[str, Any]:
    if not data_dir.exists():
        return {
            "data_dir": str(data_dir),
            "bytes": 0,
            "estimated": False,
            "children": [],
        }
    if data_dir.is_file():
        return {
            "data_dir": str(data_dir),
            "bytes": data_dir.stat().st_size,
            "estimated": False,
            "children": [_storage_child_payload(data_dir)],
        }

    children = [_storage_child_payload(child) for child in sorted(data_dir.iterdir())]
    return {
        "data_dir": str(data_dir),
        "bytes": sum(child["bytes"] for child in children if child["bytes"] is not None),
        "estimated": any(child["estimated"] for child in children),
        "children": children,
    }


def _storage_child_payload(path: Path) -> dict[str, Any]:
    if path.is_file():
        return {
            "name": path.name,
            "bytes": path.stat().st_size,
            "estimated": False,
            "type": "file",
        }
    if path.is_dir():
        return {
            "name": path.name,
            "bytes": _shallow_directory_size(path),
            "estimated": True,
            "type": "directory",
        }
    return {
        "name": path.name,
        "bytes": 0,
        "estimated": False,
        "type": "other",
    }


def _shallow_directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.iterdir() if item.is_file())
