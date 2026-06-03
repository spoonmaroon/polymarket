from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
import json
import os
import subprocess

import duckdb
from fastapi import APIRouter, HTTPException

from polymarket_engine.monitor import MonitorSnapshot, fetch_monitor_snapshot


NORMALIZED_HEALTH_SCHEMA_VERSION = "polymarket-normalized-health-v1"


def build_runtime_router(
    *,
    status_path: Path = Path("data/live/status.json"),
    duckdb_path: Path = Path("data/db/polymarket.duckdb"),
    normalized_health_path: Path = Path("data/live/normalized_health.json"),
    data_dir: Path = Path("data"),
    enable_container_status: bool = False,
) -> APIRouter:
    router = APIRouter(prefix="/api/runtime")

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

        try:
            generated_at = _parse_timestamp(payload.get("generated_at"))
        except ValueError as exc:
            return _status_error_payload(
                path=status_path,
                state="INVALID",
                error=f"generated_at timestamp parse failed: {exc}",
                payload=payload,
            )

        return {
            "ok": not bool(payload.get("health_flags", [])),
            "state": "OK" if not payload.get("health_flags", []) else "STALE",
            "status_path": str(status_path),
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
    value = payload.get(field)
    if value is None:
        return ()
    if _is_json_list(value):
        return value
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
