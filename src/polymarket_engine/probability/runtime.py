from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import duckdb

from polymarket_engine.probability.grid_cache import grid_runtime_row
from polymarket_engine.probability.grid_cache import grid_entry_from_probability_input
from polymarket_engine.probability.grid_cache import lookup_probability_grid_entry
from polymarket_engine.probability.grid_cache import ProbabilityGridHit
from polymarket_engine.probability.grid_cache import upsert_probability_grid_entry
from polymarket_engine.probability.monte_carlo import run_seeded_monte_carlo
from polymarket_engine.probability.path_policy import runtime_path_count_for_seconds_left
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine.probability.wave_signal import WaveSignalInput
from polymarket_engine.probability.wave_signal import classify_wave_signal
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


DEFAULT_PROBABILITY_CACHE_SECONDS = 1.0
DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS = 600.0
DEFAULT_PROBABILITY_GRID_VALID_SECONDS = 30.0


@dataclass(frozen=True)
class ProbabilityRuntimeInput:
    probability_input: ProbabilityInput
    contract_id: str
    contract: str
    market_slug: str
    start_ts: datetime
    expiry_ts: datetime
    volatility_regime: str | None
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
        self._cached_at_monotonic = time.monotonic()
        return payload


def build_probability_payload(*, duckdb_path: Path, limit: int) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    if not duckdb_path.exists():
        return _empty_payload(
            state="MISSING",
            error=f"{duckdb_path} missing",
            generated_at=generated_at,
        )
    store = DuckDbIngestStore(duckdb_path)
    return build_probability_payload_from_store(
        store=store,
        limit=limit,
        generated_at=generated_at,
    )


def build_probability_payload_from_store(
    *,
    store: DuckDbIngestStore,
    limit: int,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    try:
        with store._connection() as conn:
            inputs, skipped = latest_probability_inputs_from_connection(
                conn=conn,
                limit=limit,
                max_state_age_seconds=DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS,
                active_only=True,
            )
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

    rows, missed_inputs, errors = _grid_rows_and_misses(
        store=store,
        inputs=inputs,
        runtime_ts=generated_at,
    )
    if missed_inputs:
        computed_rows, compute_errors = _compute_and_persist_rows(
            store=store,
            inputs=missed_inputs,
            runtime_ts=generated_at,
        )
        rows.extend(computed_rows)
        errors.extend(compute_errors)

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
            state.volatility_regime,
            contracts.slug,
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
        start_ts = _parse_datetime(row[16])
        expiry_ts = _parse_datetime(row[17])
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
                market_slug=str(row[15]),
                start_ts=start_ts,
                expiry_ts=expiry_ts,
                volatility_regime=_optional_string(row[14], "volatility_regime"),
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
            market_slug,
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
                contracts.slug as market_slug,
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
    _, errors = _compute_and_persist_rows(
        store=store,
        inputs=inputs,
        runtime_ts=datetime.now(timezone.utc),
    )
    return len(inputs) - len(errors), skipped, tuple(errors)


def _grid_rows_and_misses(
    *,
    store: DuckDbIngestStore,
    inputs: tuple[ProbabilityRuntimeInput, ...],
    runtime_ts: datetime,
) -> tuple[list[dict[str, Any]], tuple[ProbabilityRuntimeInput, ...], list[str]]:
    rows: list[dict[str, Any]] = []
    misses: list[ProbabilityRuntimeInput] = []
    errors: list[str] = []
    if not inputs:
        return rows, tuple(misses), errors
    try:
        with store._connection() as conn:
            for runtime_input in inputs:
                probability_input = runtime_input.probability_input
                hit = lookup_probability_grid_entry(
                    conn,
                    probability_input,
                    market_slug=runtime_input.market_slug,
                    start_ts=runtime_input.start_ts,
                    expiry_ts=runtime_input.expiry_ts,
                    volatility_regime=runtime_input.volatility_regime,
                    asof_ts=probability_input.asof_ts,
                    runtime_ts=runtime_ts,
                )
                if hit is None:
                    misses.append(runtime_input)
                    continue
                row = grid_runtime_row(
                    probability_input=probability_input,
                    contract=runtime_input.contract,
                    contract_id=runtime_input.contract_id,
                    market_slug=runtime_input.market_slug,
                    start_ts=runtime_input.start_ts,
                    expiry_ts=runtime_input.expiry_ts,
                    hit=hit,
                    now=datetime.now(timezone.utc),
                )
                _merge_grid_diagnostics(
                    row=row,
                    diagnostics=hit.entry.diagnostics,
                    preview_is_current=hit.entry.asof_ts == probability_input.asof_ts,
                )
                _apply_wave_signal(row, probability_input)
                rows.append(row)
    except duckdb.CatalogException:
        return rows, inputs, errors
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"probability grid unavailable: {type(exc).__name__}: {exc}")
        return rows, inputs, errors
    return rows, tuple(misses), errors


def _merge_grid_diagnostics(
    *,
    row: dict[str, Any],
    diagnostics: Mapping[str, Any],
    preview_is_current: bool,
) -> None:
    detail = _runtime_detail_from_diagnostics(diagnostics)
    if not preview_is_current:
        detail.pop("simulation_preview", None)
    cache_metadata = dict(row.get("generator_metadata", {}))
    cache_metadata.update(detail.get("generator_metadata", {}))
    row.update(detail)
    if row.get("p_hat") is None and row.get("p_finish") is not None:
        row["p_hat"] = row["p_finish"]
    row["generator_metadata"] = cache_metadata


def _compute_and_persist_rows(
    *,
    store: DuckDbIngestStore,
    inputs: tuple[ProbabilityRuntimeInput, ...],
    runtime_ts: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    generated_at = runtime_ts or datetime.now(timezone.utc)
    for runtime_input in inputs:
        probability_input = runtime_input.probability_input
        seed = _seed_for_input(probability_input)
        steps = _steps_for_input(probability_input)
        path_count = runtime_path_count_for_seconds_left(probability_input.seconds_left)
        try:
            output = run_seeded_monte_carlo(
                probability_input,
                path_count=path_count,
                steps=steps,
                seed=seed,
            )
            output_id = _output_id(probability_input, output)
            store.insert_probability_output(
                output_id=output_id,
                probability_input=probability_input,
                output=output,
            )
            diagnostics = dict(output.diagnostics)
            diagnostics["cache"] = {
                "source": "runtime-grid-refresh",
                "market_slug": runtime_input.market_slug,
                "start_ts": runtime_input.start_ts.isoformat(),
                "expiry_ts": runtime_input.expiry_ts.isoformat(),
                "asof_ts": probability_input.asof_ts.isoformat(),
                "path_count": path_count,
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
                valid_until=generated_at
                + timedelta(seconds=DEFAULT_PROBABILITY_GRID_VALID_SECONDS),
                diagnostics=diagnostics,
            )
            upsert_probability_grid_entry(store, entry)
        except (duckdb.Error, ValueError) as exc:
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
        _apply_wave_signal(row, probability_input)
        rows.append(row)
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
    row = {
        "contract": runtime_input.contract,
        "contract_id": runtime_input.contract_id,
        "market_slug": runtime_input.market_slug,
        "asset": probability_input.asset,
        "side": probability_input.side,
        "start_ts": runtime_input.start_ts.isoformat(),
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
        **_runtime_detail_from_diagnostics(output.diagnostics),
    }
    _apply_wave_signal(row, probability_input)
    return row


def _persisted_runtime_row(row: tuple[Any, ...]) -> dict[str, Any]:
    asof_ts = _parse_datetime(row[2])
    start_ts = _parse_datetime(row[15])
    expiry_ts = _parse_datetime(row[16])
    flags = tuple(str(flag) for flag in json.loads(row[17]))
    output_payload = json.loads(row[9])
    diagnostics = output_payload.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        raise ValueError("probability output diagnostics must be a JSON object")
    age_ms = max(0, int((datetime.now(timezone.utc) - asof_ts).total_seconds() * 1000))
    return {
        "contract": _contract_label(
            asset=str(row[11]),
            side=str(row[12]),
            start_ts=start_ts,
            expiry_ts=expiry_ts,
        ),
        "contract_id": str(row[10]),
        "market_slug": str(row[14]),
        "asset": str(row[11]),
        "side": str(row[12]),
        "start_ts": start_ts.isoformat(),
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
        **_runtime_detail_from_diagnostics(diagnostics),
    }


def _runtime_detail_from_diagnostics(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    ensemble = _optional_mapping(diagnostics.get("ensemble"), "ensemble") or diagnostics
    gate = _optional_mapping(diagnostics.get("gate"), "gate") or diagnostics
    generator_metadata = _metadata_mapping(
        diagnostics.get("generator_metadata"),
        "generator_metadata",
    )
    generator_metadata.update(
        _metadata_mapping(
            diagnostics.get("cache"),
            "cache",
        )
    )
    generator_metadata.update(
        _metadata_mapping(
            diagnostics.get("generator"),
            "generator",
        )
    )
    return {
        "p_hat": _optional_runtime_float(
            diagnostics.get("p_hat"),
            "p_hat",
        ),
        "p_hat_std": _optional_runtime_float(
            diagnostics.get("p_hat_std"),
            "p_hat_std",
        ),
        "p_hat_ci_low": _optional_runtime_float(
            diagnostics.get("p_hat_ci_low"),
            "p_hat_ci_low",
        ),
        "p_hat_ci_high": _optional_runtime_float(
            diagnostics.get("p_hat_ci_high"),
            "p_hat_ci_high",
        ),
        "paths_per_seed": _optional_runtime_int(
            diagnostics.get("paths_per_seed"),
            "paths_per_seed",
        ),
        "seed_count": _optional_runtime_int(
            diagnostics.get("seed_count"),
            "seed_count",
        ),
        "prior_sensitivity": _optional_json_list(
            diagnostics.get("prior_sensitivity"),
            "prior_sensitivity",
        ),
        "mc_dispersion": _optional_runtime_float(
            ensemble.get("mc_dispersion"),
            "mc_dispersion",
        ),
        "uncertainty_buffer": _optional_runtime_float(
            ensemble.get("uncertainty_buffer"),
            "uncertainty_buffer",
        ),
        "path_diagnosis": _string_list(
            ensemble.get("path_diagnosis"),
            "path_diagnosis",
        ),
        "effective_weights": _float_mapping(
            ensemble.get("effective_weights"),
            "effective_weights",
        ),
        "decision_hint": _optional_string(
            gate.get("decision_hint"),
            "decision_hint",
        ),
        "edge_after_costs": _optional_runtime_float(
            gate.get("edge_after_costs"),
            "edge_after_costs",
        ),
        "required_edge": _optional_runtime_float(
            gate.get("required_edge"),
            "required_edge",
        ),
        "gate_reasons": _string_list(
            gate.get("reasons", diagnostics.get("gate_reasons")),
            "gate_reasons",
        ),
        "generator_metadata": generator_metadata,
        "simulation_preview": _optional_json_object(
            diagnostics.get("simulation_preview"),
            "simulation_preview",
        ),
    }


def _apply_wave_signal(row: dict[str, Any], probability_input: ProbabilityInput) -> None:
    row.update(
        classify_wave_signal(
            WaveSignalInput(
                p_finish=_float(row["p_finish"], "p_finish"),
                p_no_touch=_float(row["p_no_touch"], "p_no_touch"),
                executable_price=probability_input.executable_price,
                edge_after_costs=_optional_runtime_float(
                    row.get("edge_after_costs"),
                    "edge_after_costs",
                ),
                required_edge=_optional_runtime_float(
                    row.get("required_edge"),
                    "required_edge",
                ),
                seconds_left=probability_input.seconds_left,
                source_age_ms=probability_input.source_age_ms,
                book_age_ms=probability_input.book_age_ms,
            )
        )
    )


def _optional_mapping(value: object, field_name: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return cast(Mapping[str, Any], value)


def _metadata_mapping(value: object, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    metadata = _optional_mapping(value, field_name)
    assert metadata is not None
    return dict(sorted((str(key), item) for key, item in metadata.items()))


def _optional_json_object(value: object, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    mapping = _optional_mapping(value, field_name)
    assert mapping is not None
    return dict(mapping)


def _optional_runtime_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _float(value, field_name)


def _optional_runtime_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _int(value, field_name)


def _optional_json_list(value: object, field_name: str) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a JSON list")
    return list(value)


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a list of strings")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return list(value)


def _float_mapping(value: object, field_name: str) -> dict[str, float]:
    if value is None:
        return {}
    mapping = _optional_mapping(value, field_name)
    assert mapping is not None
    result: dict[str, float] = {}
    for key, item in mapping.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{field_name} keys must be non-empty strings")
        result[key] = _float(item, f"{field_name}.{key}")
    return dict(sorted(result.items()))


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
