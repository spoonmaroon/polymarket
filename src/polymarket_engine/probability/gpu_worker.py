from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from polymarket_engine.probability.cuda_monte_carlo import run_cuda_monte_carlo_multi_seed
from polymarket_engine.probability.grid_cache import ProbabilityGridHit
from polymarket_engine.probability.grid_cache import grid_entry_from_probability_input
from polymarket_engine.probability.grid_cache import grid_runtime_row
from polymarket_engine.probability.path_policy import runtime_paths_per_seed_for_seconds_left
from polymarket_engine.probability.path_policy import runtime_seed_count_for_seconds_left
from polymarket_engine.probability.path_policy import runtime_total_path_count_for_seconds_left
from polymarket_engine.probability.runtime import DEFAULT_PROBABILITY_GRID_VALID_SECONDS
from polymarket_engine.probability.runtime import DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS
from polymarket_engine.probability.runtime import ProbabilityRuntimeInput
from polymarket_engine.probability.runtime import _float
from polymarket_engine.probability.runtime import _int
from polymarket_engine.probability.runtime import _merge_grid_diagnostics
from polymarket_engine.probability.runtime import _output_id
from polymarket_engine.probability.runtime import _parse_datetime
from polymarket_engine.probability.runtime import _seed_for_input
from polymarket_engine.probability.runtime import _steps_for_input
from polymarket_engine.probability.runtime import latest_probability_inputs
from polymarket_engine.probability.schema import ProbabilityInput
from polymarket_engine.storage.atomic import durable_replace


DEFAULT_GPU_PROBABILITY_LIMIT = 24
DEFAULT_GPU_PROBABILITY_INTERVAL_SECONDS = 1.0
DEFAULT_INPUT_SNAPSHOT_MAX_AGE_SECONDS = 10.0


def run_cuda_probability_worker_cycle(
    *,
    duckdb_path: Path,
    probability_status_path: Path,
    probability_inputs_path: Path | None = None,
    limit: int = DEFAULT_GPU_PROBABILITY_LIMIT,
    valid_seconds: int = int(DEFAULT_PROBABILITY_GRID_VALID_SECONDS),
    max_state_age_seconds: float | None = DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS,
    max_input_snapshot_age_seconds: float | None = DEFAULT_INPUT_SNAPSHOT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if valid_seconds <= 0:
        raise ValueError("valid_seconds must be positive")

    previous_rows = _read_status_rows(probability_status_path)
    generated_at = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        if probability_inputs_path is not None:
            inputs, quality_skipped = _latest_probability_inputs_from_snapshot(
                path=probability_inputs_path,
                limit=limit,
                max_state_age_seconds=max_state_age_seconds,
                max_snapshot_age_seconds=max_input_snapshot_age_seconds,
            )
        else:
            inputs, quality_skipped = latest_probability_inputs(
                duckdb_path=duckdb_path,
                limit=limit,
                max_state_age_seconds=max_state_age_seconds,
                active_only=True,
            )
    except (duckdb.Error, json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        payload = _status_payload(
            generated_at=generated_at,
            rows=[],
            skipped=0,
            errors=[f"probability input unavailable: {type(exc).__name__}: {exc}"],
            rows_seen=0,
            rows_written=0,
            last_good_rows=previous_rows,
        )
        _write_status(probability_status_path, payload)
        return payload

    for runtime_input in inputs:
        probability_input = runtime_input.probability_input
        seed = _seed_for_input(probability_input)
        steps = _steps_for_input(probability_input)
        paths_per_seed = runtime_paths_per_seed_for_seconds_left(probability_input.seconds_left)
        seed_count = runtime_seed_count_for_seconds_left(probability_input.seconds_left)
        path_count = runtime_total_path_count_for_seconds_left(probability_input.seconds_left)
        try:
            output = run_cuda_monte_carlo_multi_seed(
                probability_input,
                paths_per_seed=paths_per_seed,
                steps=steps,
                seed=seed,
                seed_count=seed_count,
            )
            diagnostics = dict(output.diagnostics)
            diagnostics["path_count"] = path_count
            diagnostics["paths_per_seed"] = paths_per_seed
            diagnostics["seed_count"] = seed_count
            diagnostics["cache"] = {
                "source": "cuda-probability-worker",
                "market_slug": runtime_input.market_slug,
                "start_ts": runtime_input.start_ts.isoformat(),
                "expiry_ts": runtime_input.expiry_ts.isoformat(),
                "asof_ts": probability_input.asof_ts.isoformat(),
                "path_count": path_count,
                "paths_per_seed": paths_per_seed,
                "seed_count": seed_count,
            }
            entry = grid_entry_from_probability_input(
                probability_input,
                market_slug=runtime_input.market_slug,
                start_ts=runtime_input.start_ts,
                expiry_ts=runtime_input.expiry_ts,
                p_finish=output.p_finish,
                p_no_touch=output.p_no_touch,
                u_gen=0.0,
                path_count=path_count,
                seed=seed,
                volatility_regime=runtime_input.volatility_regime,
                generator_version=output.model_version,
                training_cutoff_ts=probability_input.asof_ts,
                max_event_ts=probability_input.asof_ts,
                max_observed_ts=probability_input.asof_ts,
                generated_at=generated_at,
                valid_from=generated_at,
                valid_until=generated_at + timedelta(seconds=valid_seconds),
                diagnostics=diagnostics,
            )
            output_id = _output_id(probability_input, output)
        except (duckdb.Error, ValueError, RuntimeError) as exc:
            errors.append(f"{probability_input.state_id}: {type(exc).__name__}: {exc}")
            continue
        row = grid_runtime_row(
            probability_input=probability_input,
            contract=runtime_input.contract,
            contract_id=runtime_input.contract_id,
            market_slug=runtime_input.market_slug,
            start_ts=runtime_input.start_ts,
            expiry_ts=runtime_input.expiry_ts,
            hit=ProbabilityGridHit(entry=entry, cache_status="REFRESH"),
            now=generated_at,
        )
        _merge_grid_diagnostics(
            row=row,
            diagnostics=entry.diagnostics,
            preview_is_current=True,
        )
        row["output_id"] = output_id
        rows.append(row)

    payload = _status_payload(
        generated_at=generated_at,
        rows=rows,
        skipped=quality_skipped,
        errors=errors,
        rows_seen=len(inputs),
        rows_written=len(rows),
        last_good_rows=previous_rows if errors and not rows else None,
    )
    _write_status(probability_status_path, payload)
    return payload


def run_cuda_probability_worker_loop(
    *,
    duckdb_path: Path,
    probability_status_path: Path,
    probability_inputs_path: Path | None = None,
    interval_seconds: float = DEFAULT_GPU_PROBABILITY_INTERVAL_SECONDS,
    limit: int = DEFAULT_GPU_PROBABILITY_LIMIT,
    valid_seconds: int = int(DEFAULT_PROBABILITY_GRID_VALID_SECONDS),
    max_state_age_seconds: float | None = DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS,
    max_input_snapshot_age_seconds: float | None = DEFAULT_INPUT_SNAPSHOT_MAX_AGE_SECONDS,
) -> None:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    while True:
        generated_at = datetime.now(timezone.utc)
        try:
            payload = run_cuda_probability_worker_cycle(
                duckdb_path=duckdb_path,
                probability_status_path=probability_status_path,
                probability_inputs_path=probability_inputs_path,
                limit=limit,
                valid_seconds=valid_seconds,
                max_state_age_seconds=max_state_age_seconds,
                max_input_snapshot_age_seconds=max_input_snapshot_age_seconds,
            )
        except duckdb.Error as exc:
            payload = _status_payload(
                generated_at=generated_at,
                rows=[],
                skipped=0,
                errors=[f"probability worker duckdb unavailable: {type(exc).__name__}: {exc}"],
                rows_seen=0,
                rows_written=0,
                last_good_rows=_read_status_rows(probability_status_path),
            )
            _write_status(probability_status_path, payload)
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
        time.sleep(interval_seconds)


def _status_payload(
    *,
    generated_at: datetime,
    rows: list[dict[str, Any]],
    skipped: int,
    errors: list[str],
    rows_seen: int,
    rows_written: int,
    last_good_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "polymarket-probability-runtime-v1",
        "ok": not errors,
        "state": "OK" if not errors else "PARTIAL",
        "error": None if not errors else errors[0],
        "generated_at": generated_at.isoformat(),
        "cached": False,
        "model_version": rows[0]["model_version"] if rows else None,
        "rows": rows,
        "skipped": skipped,
        "errors": errors,
        "rows_seen": rows_seen,
        "rows_written": rows_written,
    }
    if last_good_rows:
        payload["last_good_rows"] = last_good_rows
    return payload


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    durable_replace(tmp_path, path)


def _read_status_rows(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    valid_rows = [row for row in rows if isinstance(row, dict)]
    if valid_rows:
        return valid_rows
    last_good_rows = payload.get("last_good_rows")
    if not isinstance(last_good_rows, list):
        return []
    return [row for row in last_good_rows if isinstance(row, dict)]


def _latest_probability_inputs_from_snapshot(
    *,
    path: Path,
    limit: int,
    max_state_age_seconds: float | None,
    max_snapshot_age_seconds: float | None,
) -> tuple[tuple[ProbabilityRuntimeInput, ...], int]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    payload = _mapping(json.loads(path.read_text(encoding="utf-8")), "probability input snapshot")
    schema_version = payload.get("schema_version")
    if schema_version != "polymarket-probability-inputs-v1":
        raise ValueError(f"unsupported probability input snapshot schema: {schema_version}")
    generated_at = _parse_datetime(payload.get("generated_at"))
    now = datetime.now(timezone.utc)
    if max_snapshot_age_seconds is not None:
        if max_snapshot_age_seconds <= 0:
            raise ValueError("max_input_snapshot_age_seconds must be positive")
        age_seconds = (now - generated_at).total_seconds()
        if age_seconds > max_snapshot_age_seconds:
            raise ValueError(
                "probability input snapshot stale: "
                f"age_seconds={age_seconds:.3f} max={max_snapshot_age_seconds:.3f}"
            )
    rows = payload.get("rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("probability input snapshot rows must be a list")

    cutoff = (
        now - timedelta(seconds=max_state_age_seconds)
        if max_state_age_seconds is not None
        else None
    )
    inputs: list[ProbabilityRuntimeInput] = []
    skipped = _nonnegative_int(payload.get("skipped", 0), "skipped")
    for item in rows:
        row = _mapping(item, "probability input snapshot row")
        probability_input = _probability_input_from_snapshot_row(row)
        if cutoff is not None and probability_input.asof_ts < cutoff:
            skipped += 1
            continue
        inputs.append(
            ProbabilityRuntimeInput(
                probability_input=probability_input,
                contract_id=_nonempty_str(row.get("contract_id"), "contract_id"),
                contract=_nonempty_str(row.get("contract"), "contract"),
                market_slug=_nonempty_str(row.get("market_slug"), "market_slug"),
                start_ts=_parse_datetime(row.get("start_ts")),
                expiry_ts=_parse_datetime(row.get("expiry_ts")),
                volatility_regime=_optional_str(
                    row.get("volatility_regime"),
                    "volatility_regime",
                ),
                flags=tuple(_string_list(row.get("flags", ["OK"]), "flags")),
            )
        )
        if len(inputs) >= limit:
            break
    return tuple(inputs), skipped


def _probability_input_from_snapshot_row(row: Mapping[str, Any]) -> ProbabilityInput:
    payload = _mapping(row.get("probability_input"), "probability_input")
    return ProbabilityInput(
        state_id=_nonempty_str(payload.get("state_id"), "state_id"),
        asof_ts=_parse_datetime(payload.get("asof_ts")),
        asset=_nonempty_str(payload.get("asset"), "asset"),
        side=_nonempty_str(payload.get("side"), "side"),
        comparison_operator=_nonempty_str(
            payload.get("comparison_operator"),
            "comparison_operator",
        ),
        seconds_left=_float(payload.get("seconds_left"), "seconds_left"),
        settlement_price=_float(payload.get("settlement_price"), "settlement_price"),
        threshold=_float(payload.get("threshold"), "threshold"),
        sigma_tau=_float(payload.get("sigma_tau"), "sigma_tau"),
        executable_price=_float(payload.get("executable_price"), "executable_price"),
        source_age_ms=_int(payload.get("source_age_ms"), "source_age_ms"),
        book_age_ms=_int(payload.get("book_age_ms"), "book_age_ms"),
        z_path=_float(payload.get("z_path"), "z_path"),
    )


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _nonempty_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _nonempty_str(value, field_name)


def _string_list(value: object, field_name: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a list of strings")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return list(value)


def _nonnegative_int(value: object, field_name: str) -> int:
    number = _int(value, field_name)
    if number < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return number
