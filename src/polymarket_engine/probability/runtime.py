from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import duckdb

from polymarket_engine.probability.native import run_native_or_python
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


DEFAULT_PROBABILITY_CACHE_SECONDS = 1.0
DEFAULT_PROBABILITY_PATH_COUNT = 1024
DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS = 600.0


@dataclass(frozen=True)
class ProbabilityRuntimeInput:
    probability_input: ProbabilityInput
    contract_id: str
    contract: str
    start_ts: datetime
    expiry_ts: datetime
    flags: tuple[str, ...]


class ProbabilityRuntimeCache:
    def __init__(self, min_interval_seconds: float = DEFAULT_PROBABILITY_CACHE_SECONDS) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._cached_at_monotonic: float | None = None
        self._cached_payload: dict[str, Any] | None = None

    def payload(self, *, duckdb_path: Path, limit: int) -> dict[str, Any]:
        now_monotonic = time.monotonic()
        if (
            self._cached_payload is not None
            and self._cached_at_monotonic is not None
            and now_monotonic - self._cached_at_monotonic < self.min_interval_seconds
        ):
            cached = dict(self._cached_payload)
            cached["cached"] = True
            return cached

        payload = build_probability_payload(duckdb_path=duckdb_path, limit=limit)
        self._cached_payload = dict(payload)
        self._cached_at_monotonic = now_monotonic
        return payload


def build_probability_payload(*, duckdb_path: Path, limit: int) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    if not duckdb_path.exists():
        return _empty_payload(
            state="MISSING",
            error=f"{duckdb_path} missing",
            generated_at=generated_at,
        )

    try:
        persisted_rows = latest_probability_output_rows(duckdb_path=duckdb_path, limit=limit)
        if persisted_rows:
            return {
                "ok": True,
                "state": "OK",
                "generated_at": generated_at.isoformat(),
                "cached": False,
                "model_version": persisted_rows[0]["model_version"],
                "rows": persisted_rows,
                "skipped": 0,
                "errors": [],
            }
        inputs, skipped = latest_probability_inputs(duckdb_path=duckdb_path, limit=limit)
    except duckdb.Error as exc:
        return _empty_payload(
            state="INVALID",
            error=f"DuckDB probability unavailable: {type(exc).__name__}: {exc}",
            generated_at=generated_at,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return _empty_payload(
            state="INVALID",
            error=f"probability input unavailable: {type(exc).__name__}: {exc}",
            generated_at=generated_at,
        )

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    store = DuckDbIngestStore(duckdb_path)
    rows, errors = _compute_and_persist_rows(store=store, inputs=inputs)

    return {
        "ok": not errors,
        "state": "OK" if not errors else "PARTIAL",
        "generated_at": generated_at.isoformat(),
        "cached": False,
        "model_version": rows[0]["model_version"] if rows else None,
        "rows": rows,
        "skipped": skipped,
        "errors": errors,
    }


def latest_probability_inputs(
    *,
    duckdb_path: Path,
    limit: int,
    max_state_age_seconds: float | None = None,
    active_only: bool = False,
) -> tuple[tuple[ProbabilityRuntimeInput, ...], int]:
    if limit <= 0:
        raise ValueError("limit must be positive")

    with _connect_read_only_with_retry(duckdb_path, lock_retry_seconds=2.0) as conn:
        return latest_probability_inputs_from_connection(
            conn=conn,
            limit=limit,
            max_state_age_seconds=max_state_age_seconds,
            active_only=active_only,
        )


def latest_probability_inputs_from_connection(
    *,
    conn: duckdb.DuckDBPyConnection,
    limit: int,
    max_state_age_seconds: float | None = None,
    active_only: bool = False,
) -> tuple[tuple[ProbabilityRuntimeInput, ...], int]:
    if limit <= 0:
        raise ValueError("limit must be positive")

    cutoff = _cutoff_timestamp(max_state_age_seconds)
    active_now = datetime.now(timezone.utc) if active_only else None
    rows = conn.execute(
        """
        select
            state_id,
            state.contract_id,
            cast(asof_ts as varchar) as asof_ts,
            state.asset,
            state.side,
            contracts.comparison_operator,
            seconds_left,
            settlement_price,
            threshold,
            sigma_tau,
            executable_price,
            source_age_ms,
            book_age_ms,
            data_quality_flags_json,
            contracts.start_ts::varchar as start_ts,
            contracts.expiry_ts::varchar as expiry_ts
        from (
            select
                state_inputs.*,
                row_number() over (
                    partition by contract_id
                    order by asof_ts desc, created_at desc
                ) as row_number
            from features.asof_state_inputs as state_inputs
        ) as state
        join core.contracts as contracts using (contract_id)
        where row_number = 1
          and (? is null or asof_ts >= ?)
          and (? is null or contracts.expiry_ts > ?)
        order by
            case state.asset when 'BTC' then 0 when 'ETH' then 1 else 2 end,
            contracts.start_ts,
            case state.side when 'UP' then 0 when 'DOWN' then 1 else 2 end
        limit ?
        """,
        [cutoff, cutoff, active_now, active_now, limit],
    ).fetchall()

    inputs: list[ProbabilityRuntimeInput] = []
    skipped = 0
    for row in rows:
        flags = tuple(str(flag) for flag in json.loads(row[13]))
        if flags:
            skipped += 1
            continue
        probability_input = _probability_input_from_row(row)
        start_ts = _parse_datetime(row[14])
        expiry_ts = _parse_datetime(row[15])
        inputs.append(
            ProbabilityRuntimeInput(
                probability_input=probability_input,
                contract_id=str(row[1]),
                contract=_contract_label(
                    asset=probability_input.asset,
                    side=probability_input.side,
                    start_ts=start_ts,
                    expiry_ts=expiry_ts,
                ),
                start_ts=start_ts,
                expiry_ts=expiry_ts,
                flags=("OK",),
            )
        )

    return tuple(inputs), skipped


def latest_probability_output_rows(
    *,
    duckdb_path: Path,
    limit: int,
    max_state_age_seconds: float | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    with _connect_read_only_with_retry(duckdb_path, lock_retry_seconds=2.0) as conn:
        return latest_probability_output_rows_from_connection(
            conn=conn,
            limit=limit,
            max_state_age_seconds=max_state_age_seconds,
            active_only=active_only,
        )


def latest_probability_output_rows_from_connection(
    *,
    conn: duckdb.DuckDBPyConnection,
    limit: int,
    max_state_age_seconds: float | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    cutoff = _cutoff_timestamp(max_state_age_seconds)
    active_now = datetime.now(timezone.utc) if active_only else None
    rows = conn.execute(
        """
        select
            output_id,
            state_id,
            cast(asof_ts as varchar) as asof_ts,
            model_version,
            p_finish,
            p_no_touch,
            z_path,
            seed,
            input_json,
            output_json,
            contract_id,
            asset,
            side,
            sigma_tau,
            cast(start_ts as varchar) as start_ts,
            cast(expiry_ts as varchar) as expiry_ts,
            data_quality_flags_json
        from (
            select
                outputs.*,
                state.contract_id,
                state.asset,
                state.side,
                state.sigma_tau,
                state.data_quality_flags_json,
                contracts.start_ts,
                contracts.expiry_ts,
                row_number() over (
                    partition by state.contract_id
                    order by outputs.asof_ts desc, outputs.created_at desc
                ) as row_number
            from features.probability_outputs as outputs
            join features.asof_state_inputs as state using (state_id)
            join core.contracts as contracts using (contract_id)
        )
        where row_number = 1
          and (? is null or asof_ts >= ?)
          and (? is null or expiry_ts > ?)
        order by
            case asset when 'BTC' then 0 when 'ETH' then 1 else 2 end,
            start_ts,
            case side when 'UP' then 0 when 'DOWN' then 1 else 2 end
        limit ?
        """,
        [cutoff, cutoff, active_now, active_now, limit],
    ).fetchall()
    return [_persisted_runtime_row(row) for row in rows]


def compute_and_persist_probability_outputs(
    *,
    store: DuckDbIngestStore,
    limit: int,
    max_state_age_seconds: float | None = None,
    active_only: bool = False,
) -> tuple[int, int, tuple[str, ...]]:
    with store._connection() as conn:
        inputs, skipped = latest_probability_inputs_from_connection(
            conn=conn,
            limit=limit,
            max_state_age_seconds=max_state_age_seconds,
            active_only=active_only,
        )
    _, errors = _compute_and_persist_rows(store=store, inputs=inputs)
    return len(inputs) - len(errors), skipped, tuple(errors)


def _compute_and_persist_rows(
    *,
    store: DuckDbIngestStore,
    inputs: tuple[ProbabilityRuntimeInput, ...],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for runtime_input in inputs:
        probability_input = runtime_input.probability_input
        seed = _seed_for_input(probability_input)
        steps = _steps_for_input(probability_input)
        try:
            output = run_native_or_python(
                probability_input,
                path_count=DEFAULT_PROBABILITY_PATH_COUNT,
                steps=steps,
                seed=seed,
                backend=os.environ.get("POLYMARKET_PROBABILITY_BACKEND", "cpu_rayon"),
            )
            output_id = _output_id(probability_input, output)
            store.insert_probability_output(
                output_id=output_id,
                probability_input=probability_input,
                output=output,
            )
        except (duckdb.Error, ValueError) as exc:
            errors.append(f"{probability_input.state_id}: {type(exc).__name__}: {exc}")
            continue
        rows.append(_runtime_row(runtime_input, output=output, output_id=output_id))
    return rows, errors


def _probability_input_from_row(row: tuple[Any, ...]) -> ProbabilityInput:
    settlement_price = _float(row[7], "settlement_price")
    threshold = _float(row[8], "threshold")
    sigma_tau = _float(row[9], "sigma_tau")
    side = str(row[4])
    signed_log_distance = math.log(settlement_price / threshold)
    if side == "DOWN":
        signed_log_distance *= -1
    z_path = signed_log_distance / sigma_tau
    return ProbabilityInput(
        state_id=str(row[0]),
        asof_ts=_parse_datetime(row[2]),
        asset=str(row[3]),
        side=side,
        comparison_operator=str(row[5]),
        seconds_left=_float(row[6], "seconds_left"),
        settlement_price=settlement_price,
        threshold=threshold,
        sigma_tau=sigma_tau,
        executable_price=_float(row[10], "executable_price"),
        source_age_ms=_int(row[11], "source_age_ms"),
        book_age_ms=_int(row[12], "book_age_ms"),
        z_path=z_path,
    )


def _runtime_row(
    runtime_input: ProbabilityRuntimeInput,
    *,
    output: ProbabilityOutput,
    output_id: str,
) -> dict[str, Any]:
    probability_input = runtime_input.probability_input
    age_ms = max(
        0,
        int((datetime.now(timezone.utc) - probability_input.asof_ts).total_seconds() * 1000),
    )
    return {
        "contract": runtime_input.contract,
        "contract_id": runtime_input.contract_id,
        "asset": probability_input.asset,
        "side": probability_input.side,
        "asof_ts": probability_input.asof_ts.isoformat(),
        "expiry_ts": runtime_input.expiry_ts.isoformat(),
        "p_finish": output.p_finish,
        "p_no_touch": output.p_no_touch,
        "z_path": output.z_path,
        "sigma_tau": probability_input.sigma_tau,
        "age_ms": age_ms,
        "flags": list(runtime_input.flags),
        "model_version": output.model_version,
        "seed": output.seed,
        "output_id": output_id,
    }


def _persisted_runtime_row(row: tuple[Any, ...]) -> dict[str, Any]:
    asof_ts = _parse_datetime(row[2])
    start_ts = _parse_datetime(row[14])
    expiry_ts = _parse_datetime(row[15])
    flags = tuple(str(flag) for flag in json.loads(row[16]))
    age_ms = max(0, int((datetime.now(timezone.utc) - asof_ts).total_seconds() * 1000))
    return {
        "contract": _contract_label(
            asset=str(row[11]),
            side=str(row[12]),
            start_ts=start_ts,
            expiry_ts=expiry_ts,
        ),
        "contract_id": str(row[10]),
        "asset": str(row[11]),
        "side": str(row[12]),
        "asof_ts": asof_ts.isoformat(),
        "expiry_ts": expiry_ts.isoformat(),
        "p_finish": float(row[4]),
        "p_no_touch": float(row[5]),
        "z_path": float(row[6]),
        "sigma_tau": _float(row[13], "sigma_tau"),
        "age_ms": age_ms,
        "flags": list(flags) if flags else ["OK"],
        "model_version": str(row[3]),
        "seed": _optional_int(row[7]),
        "output_id": str(row[0]),
    }


def _empty_payload(*, state: str, error: str, generated_at: datetime) -> dict[str, Any]:
    return {
        "ok": False,
        "state": state,
        "error": error,
        "generated_at": generated_at.isoformat(),
        "cached": False,
        "model_version": None,
        "rows": [],
        "skipped": 0,
        "errors": [error],
    }


def _seed_for_input(probability_input: ProbabilityInput) -> int:
    digest = hashlib.sha256(
        f"{probability_input.state_id}|{probability_input.asof_ts.isoformat()}".encode()
    ).hexdigest()
    return int(digest[:8], 16)


def _steps_for_input(probability_input: ProbabilityInput) -> int:
    return max(1, min(300, int(math.ceil(probability_input.seconds_left))))


def _output_id(probability_input: ProbabilityInput, output: ProbabilityOutput) -> str:
    digest = hashlib.sha256(
        "|".join(
            (
                probability_input.state_id,
                probability_input.asof_ts.isoformat(),
                output.model_version,
                str(output.seed),
                str(output.diagnostics.get("path_count")),
                str(output.diagnostics.get("steps")),
            )
        ).encode()
    ).hexdigest()
    return f"prob-{digest[:24]}"


def _contract_label(*, asset: str, side: str, start_ts: datetime, expiry_ts: datetime) -> str:
    interval_minutes = max(1, round((expiry_ts - start_ts).total_seconds() / 60))
    return f"{asset} {interval_minutes}m {side}"


def _float(value: object, field_name: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field_name} is required")
    number = float(cast(Any, value))
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _int(value: object, field_name: str) -> int:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field_name} is required")
    number = float(cast(Any, value))
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return int(number)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _int(value, "seed")


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("probability timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


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


def _cutoff_timestamp(max_state_age_seconds: float | None) -> datetime | None:
    if max_state_age_seconds is None:
        return None
    if max_state_age_seconds <= 0 or not math.isfinite(max_state_age_seconds):
        raise ValueError("max_state_age_seconds must be positive and finite")
    return datetime.fromtimestamp(time.time() - max_state_age_seconds, tz=timezone.utc)
