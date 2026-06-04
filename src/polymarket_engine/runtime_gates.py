from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from polymarket_engine.health.normalized_status import NORMALIZED_HEALTH_SCHEMA_VERSION


DEFAULT_MAX_STATUS_AGE_SECONDS = 30
DEFAULT_MAX_NORMALIZED_HEALTH_AGE_SECONDS = 30


def evaluate_runtime_gates(
    *,
    status_path: Path,
    normalized_health_path: Path | None = None,
    max_status_age_seconds: int = DEFAULT_MAX_STATUS_AGE_SECONDS,
    max_normalized_health_age_seconds: int = DEFAULT_MAX_NORMALIZED_HEALTH_AGE_SECONDS,
) -> dict[str, Any]:
    failures: list[str] = []
    status = _evaluate_status(
        path=status_path,
        max_age_seconds=max_status_age_seconds,
        failures=failures,
    )
    normalized_health = None
    if normalized_health_path is not None:
        normalized_health = _evaluate_normalized_health(
            path=normalized_health_path,
            max_age_seconds=max_normalized_health_age_seconds,
            failures=failures,
        )

    return {
        "ok": not failures,
        "status_path": str(status_path),
        "normalized_health_path": (
            str(normalized_health_path) if normalized_health_path is not None else None
        ),
        "thresholds": {
            "max_status_age_seconds": max_status_age_seconds,
            "max_normalized_health_age_seconds": max_normalized_health_age_seconds,
        },
        "failures": failures,
        "status": status,
        "normalized_health": normalized_health,
    }


def _evaluate_status(
    *,
    path: Path,
    max_age_seconds: int,
    failures: list[str],
) -> dict[str, Any]:
    payload = _read_json_object(path, label="status", failures=failures)
    if payload is None:
        return {"state": "INVALID", "counts": {"prices": 0, "orderbooks": 0}}

    generated_at = _timestamp_field(payload, label="status", failures=failures)
    age_seconds = _age_seconds(generated_at)
    if age_seconds is not None and age_seconds > max_age_seconds:
        failures.append("status file stale")

    price_rows = _rows(payload, fields=("prices", "chainlink_prices"), failures=failures)
    orderbook_rows = _rows(payload, fields=("orderbooks",), failures=failures)
    if not price_rows:
        failures.append("status has no price rows")
    if not orderbook_rows:
        failures.append("status has no orderbook rows")

    health_flags = payload.get("health_flags")
    if isinstance(health_flags, list) and health_flags:
        failures.append(f"status health_flags present: {', '.join(map(str, health_flags))}")
    elif health_flags is not None and not isinstance(health_flags, list):
        failures.append("status health_flags invalid")

    return {
        "state": "OK",
        "generated_at": payload.get("generated_at"),
        "age_seconds": age_seconds,
        "counts": {
            "prices": len(price_rows),
            "orderbooks": len(orderbook_rows),
        },
        "health_flags": health_flags if isinstance(health_flags, list) else [],
    }


def _evaluate_normalized_health(
    *,
    path: Path,
    max_age_seconds: int,
    failures: list[str],
) -> dict[str, Any]:
    payload = _read_json_object(path, label="normalized health", failures=failures)
    if payload is None:
        return {"state": "INVALID", "schema_version": None, "tables": []}

    generated_at = _timestamp_field(payload, label="normalized health", failures=failures)
    age_seconds = _age_seconds(generated_at)
    if age_seconds is not None and age_seconds > max_age_seconds:
        failures.append("normalized health stale")

    schema_version = payload.get("schema_version")
    if schema_version != NORMALIZED_HEALTH_SCHEMA_VERSION:
        failures.append("normalized health schema stale")

    tables = payload.get("tables", [])
    if not isinstance(tables, list):
        failures.append("normalized health tables invalid")
        tables = []
    for index, row in enumerate(tables):
        if not isinstance(row, dict):
            failures.append(f"normalized health table {index} invalid")
            continue
        if _table_failed(row):
            table_name = row.get("table") or row.get("name") or index
            failures.append(f"normalized health table {table_name} failed")

    return {
        "state": "OK",
        "schema_version": schema_version,
        "generated_at": payload.get("generated_at"),
        "age_seconds": age_seconds,
        "tables": tables,
    }


def _read_json_object(
    path: Path,
    *,
    label: str,
    failures: list[str],
) -> dict[str, Any] | None:
    if not path.exists():
        failures.append(f"{label} missing")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        failures.append(f"{label} invalid JSON")
        return None
    except OSError:
        failures.append(f"{label} unreadable")
        return None
    if not isinstance(payload, dict):
        failures.append(f"{label} root invalid")
        return None
    return payload


def _timestamp_field(
    payload: dict[str, Any],
    *,
    label: str,
    failures: list[str],
) -> datetime | None:
    value = payload.get("generated_at")
    if value is None:
        failures.append(f"{label} generated_at missing")
        return None
    if not isinstance(value, str):
        failures.append(f"{label} generated_at invalid")
        return None
    try:
        return _parse_timestamp(value)
    except ValueError:
        failures.append(f"{label} generated_at invalid")
        return None


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: datetime | None) -> float | None:
    if value is None:
        return None
    return (datetime.now(timezone.utc) - value).total_seconds()


def _rows(
    payload: dict[str, Any],
    *,
    fields: tuple[str, ...],
    failures: list[str],
) -> list[Any]:
    rows: list[Any] = []
    for field in fields:
        value = payload.get(field, [])
        if not isinstance(value, list):
            failures.append(f"status {field} invalid")
            continue
        rows.extend(value)
    return rows


def _table_failed(row: dict[str, Any]) -> bool:
    ok = row.get("ok")
    if ok is False:
        return True
    state = row.get("state") or row.get("status")
    if isinstance(state, str) and state.upper() not in {"OK", "HEALTHY", "PASS", "PASSING"}:
        return True
    return False
