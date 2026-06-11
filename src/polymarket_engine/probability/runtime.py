from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import duckdb

from polymarket_engine.probability.ensemble_runtime import run_four_generator_ensemble
from polymarket_engine.probability.generator_fragments import FragmentSelection
from polymarket_engine.probability.generator_fragments import GeneratorFragment
from polymarket_engine.probability.generator_fragments import read_probability_fragments
from polymarket_engine.probability.generator_fragments import select_fragments_for_input
from polymarket_engine.probability.hot_inputs import read_hot_probability_inputs
from polymarket_engine.probability.pair_coherence import normalize_binary_probability_pairs
from polymarket_engine.probability.runtime_inputs import (
    ProbabilityRuntimeInput,
    ThresholdDiagnostics,
    contract_label,
)
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


DEFAULT_PROBABILITY_CACHE_SECONDS = 1.0
DEFAULT_PROBABILITY_PATH_COUNT = 1024
DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS = 600.0
DEFAULT_PROBABILITY_GRID_VALID_SECONDS = 30.0
DEFAULT_MIN_FRAGMENT_COUNT = 2

_contract_label = contract_label


class ProbabilityRuntimeCache:
    def __init__(self, min_interval_seconds: float = DEFAULT_PROBABILITY_CACHE_SECONDS) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._cached_at_monotonic: float | None = None
        self._cached_key: tuple[Any, ...] | None = None
        self._cached_payload: dict[str, Any] | None = None

    def payload(
        self,
        *,
        duckdb_path: Path,
        limit: int,
        allow_compute: bool = False,
        probability_inputs_path: Path | None = None,
        probability_fragments_path: Path | None = None,
    ) -> dict[str, Any]:
        now_monotonic = time.monotonic()
        cache_key = _probability_cache_key(
            duckdb_path=duckdb_path,
            limit=limit,
            allow_compute=allow_compute,
            probability_inputs_path=probability_inputs_path,
            probability_fragments_path=probability_fragments_path,
        )
        if (
            self._cached_payload is not None
            and self._cached_key == cache_key
            and self._cached_at_monotonic is not None
            and now_monotonic - self._cached_at_monotonic < self.min_interval_seconds
        ):
            cached = dict(self._cached_payload)
            cached["cached"] = True
            return cached

        payload_kwargs: dict[str, Any] = {"duckdb_path": duckdb_path, "limit": limit}
        if allow_compute:
            payload_kwargs["allow_compute"] = allow_compute
        if probability_inputs_path is not None:
            payload_kwargs["probability_inputs_path"] = probability_inputs_path
        if probability_fragments_path is not None:
            payload_kwargs["probability_fragments_path"] = probability_fragments_path
        payload = build_probability_payload(**payload_kwargs)
        self._cached_payload = dict(payload)
        self._cached_key = cache_key
        self._cached_at_monotonic = time.monotonic()
        return payload


def build_probability_payload(
    *,
    duckdb_path: Path,
    limit: int,
    allow_compute: bool = False,
    probability_inputs_path: Path | None = None,
    probability_fragments_path: Path | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    hot_input_error: str | None = None
    if probability_inputs_path is not None:
        try:
            hot_payload = read_hot_probability_inputs(
                out_path=probability_inputs_path,
                limit=limit,
                max_age_seconds=DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS,
            )
        except FileNotFoundError:
            hot_payload = None
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            hot_input_error = (
                f"hot probability inputs unavailable: {type(exc).__name__}: {exc}"
            )
            hot_payload = None
        if hot_payload is not None:
            hot_rows, hot_errors = _compute_rows_without_persistence(
                inputs=hot_payload.inputs,
                probability_fragments_path=probability_fragments_path,
            )
            hot_rows = normalize_binary_probability_pairs(hot_rows)
            return {
                "ok": not hot_errors,
                "state": "OK" if not hot_errors else "PARTIAL",
                "source": "hot_inputs",
                "generated_at": generated_at.isoformat(),
                "cached": False,
                "model_version": hot_rows[0]["model_version"] if hot_rows else None,
                "rows": hot_rows,
                "skipped": hot_payload.skipped,
                "errors": hot_errors,
            }

    if not duckdb_path.exists():
        return _with_hot_input_warning(
            _empty_payload(
                state="MISSING",
                error=f"{duckdb_path} missing",
                generated_at=generated_at,
            ),
            hot_input_error,
        )

    try:
        persisted_rows = latest_probability_output_rows(duckdb_path=duckdb_path, limit=limit)
        if persisted_rows:
            persisted_rows = normalize_binary_probability_pairs(persisted_rows)
            return _with_hot_input_warning(
                {
                    "ok": True,
                    "state": "OK",
                    "source": "duckdb_persisted",
                    "generated_at": generated_at.isoformat(),
                    "cached": False,
                    "model_version": persisted_rows[0]["model_version"],
                    "rows": persisted_rows,
                    "skipped": 0,
                    "errors": [],
                },
                hot_input_error,
            )
        if not allow_compute:
            return _with_hot_input_warning(
                _empty_payload(
                    state="COMPUTE_DISABLED",
                    error=(
                        "probability status missing and runtime probability compute "
                        "fallback disabled"
                    ),
                    generated_at=generated_at,
                ),
                hot_input_error,
            )
        inputs, skipped = latest_probability_inputs(duckdb_path=duckdb_path, limit=limit)
    except duckdb.Error as exc:
        return _with_hot_input_warning(
            _empty_payload(
                state="INVALID",
                error=f"DuckDB probability unavailable: {type(exc).__name__}: {exc}",
                generated_at=generated_at,
            ),
            hot_input_error,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return _with_hot_input_warning(
            _empty_payload(
                state="INVALID",
                error=f"probability input unavailable: {type(exc).__name__}: {exc}",
                generated_at=generated_at,
            ),
            hot_input_error,
        )

    store = DuckDbIngestStore(duckdb_path)
    rows, errors = _compute_and_persist_rows(
        store=store,
        inputs=inputs,
        probability_fragments_path=probability_fragments_path,
    )
    rows = normalize_binary_probability_pairs(rows)

    return _with_hot_input_warning(
        {
            "ok": not errors,
            "state": "OK" if not errors else "PARTIAL",
            "source": "duckdb_compute",
            "generated_at": generated_at.isoformat(),
            "cached": False,
            "model_version": rows[0]["model_version"] if rows else None,
            "rows": rows,
            "skipped": skipped,
            "errors": errors,
        },
        hot_input_error,
    )


def _probability_cache_key(
    *,
    duckdb_path: Path,
    limit: int,
    allow_compute: bool,
    probability_inputs_path: Path | None,
    probability_fragments_path: Path | None,
) -> tuple[Any, ...]:
    return (
        str(duckdb_path),
        limit,
        allow_compute,
        _hot_inputs_cache_fingerprint(probability_inputs_path),
        _hot_inputs_cache_fingerprint(probability_fragments_path),
    )


def _hot_inputs_cache_fingerprint(path: Path | None) -> tuple[Any, ...] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (str(path), "missing")
    except OSError as exc:
        return (str(path), "unavailable", type(exc).__name__, str(exc))
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _with_hot_input_warning(
    payload: dict[str, Any],
    hot_input_error: str | None,
) -> dict[str, Any]:
    if hot_input_error is None:
        return payload
    payload = dict(payload)
    payload["hot_input_error"] = hot_input_error
    warnings = payload.get("warnings")
    payload["warnings"] = (
        [*warnings, hot_input_error] if isinstance(warnings, list) else [hot_input_error]
    )
    return payload


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
            contracts.expiry_ts::varchar as expiry_ts,
            contracts.slug as market_slug,
            state.volatility_regime
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
                market_slug=str(row[16]),
                volatility_regime=None if row[17] is None else str(row[17]),
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
    has_decision_table = _table_exists(
        conn=conn,
        schema_name="features",
        table_name="ensemble_decisions",
    )
    rows = conn.execute(
        _latest_probability_output_rows_sql(include_decisions=has_decision_table),
        [cutoff, cutoff, active_now, active_now, limit],
    ).fetchall()
    return normalize_binary_probability_pairs([_persisted_runtime_row(row) for row in rows])


def _latest_probability_output_rows_sql(*, include_decisions: bool) -> str:
    decision_columns = ""
    decision_join = ""
    if include_decisions:
        decision_columns = """
            ,
            decisions.decision_hint,
            decisions.edge_after_costs,
            decisions.required_edge,
            decisions.skip_reasons_json,
            decisions.generator_summary_json,
            decisions.execution_summary_json,
            decisions.supervised_live_json"""
        decision_join = """
        left join (
            select
                state_id,
                decision_hint,
                edge_after_costs,
                required_edge,
                skip_reasons_json,
                generator_summary_json,
                execution_summary_json,
                supervised_live_json
            from (
                select
                    decisions.*,
                    row_number() over (
                        partition by state_id
                        order by asof_ts desc, created_at desc
                    ) as row_number
                from features.ensemble_decisions as decisions
            )
            where row_number = 1
        ) as decisions using (state_id)"""

    return f"""
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
            {decision_columns}
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
        ) as outputs
        {decision_join}
        where row_number = 1
          and (? is null or asof_ts >= ?)
          and (? is null or expiry_ts > ?)
        order by
            case asset when 'BTC' then 0 when 'ETH' then 1 else 2 end,
            start_ts,
            case side when 'UP' then 0 when 'DOWN' then 1 else 2 end
        limit ?
        """


def _table_exists(
    *,
    conn: duckdb.DuckDBPyConnection,
    schema_name: str,
    table_name: str,
) -> bool:
    row = conn.execute(
        """
        select count(*)
        from information_schema.tables
        where table_schema = ?
          and table_name = ?
        """,
        [schema_name, table_name],
    ).fetchone()
    return bool(row is not None and row[0] == 1)


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


def _compute_rows_without_persistence(
    *,
    inputs: tuple[ProbabilityRuntimeInput, ...],
    probability_fragments_path: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    prior_fragments, prior_fragment_error = _load_probability_fragments(
        path=probability_fragments_path,
    )
    for runtime_input in inputs:
        if _runtime_input_blocked(runtime_input):
            rows.append(_blocked_runtime_row(runtime_input))
            continue
        probability_input = runtime_input.probability_input
        seed = _seed_for_input(probability_input)
        steps = _steps_for_input(probability_input)
        try:
            fragment_selection = _select_prior_fragments(
                fragments=prior_fragments,
                probability_input=probability_input,
                fragment_error=prior_fragment_error,
            )
            output = run_four_generator_ensemble(
                probability_input,
                path_count=DEFAULT_PROBABILITY_PATH_COUNT,
                steps=steps,
                seed=seed,
                history_fragments=tuple(
                    fragment.prices for fragment in fragment_selection.fragments
                )
                or None,
            )
            output = _output_with_prior_diagnostics(
                output,
                fragment_selection=fragment_selection,
                fragment_error=prior_fragment_error,
            )
            output_id = _output_id(probability_input, output)
        except Exception as exc:
            errors.append(f"{probability_input.state_id}: {type(exc).__name__}: {exc}")
            continue
        rows.append(_runtime_row(runtime_input, output=output, output_id=output_id))
    return rows, errors


def _compute_and_persist_rows(
    *,
    store: DuckDbIngestStore,
    inputs: tuple[ProbabilityRuntimeInput, ...],
    probability_fragments_path: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    output_rows: list[tuple[str, ProbabilityInput, ProbabilityOutput]] = []
    prior_fragments, prior_fragment_error = _load_probability_fragments(
        path=probability_fragments_path,
    )
    for runtime_input in inputs:
        if _runtime_input_blocked(runtime_input):
            rows.append(_blocked_runtime_row(runtime_input))
            continue
        probability_input = runtime_input.probability_input
        seed = _seed_for_input(probability_input)
        steps = _steps_for_input(probability_input)
        try:
            fragment_selection = _select_prior_fragments(
                fragments=prior_fragments,
                probability_input=probability_input,
                fragment_error=prior_fragment_error,
            )
            output = run_four_generator_ensemble(
                probability_input,
                path_count=DEFAULT_PROBABILITY_PATH_COUNT,
                steps=steps,
                seed=seed,
                history_fragments=tuple(
                    fragment.prices for fragment in fragment_selection.fragments
                )
                or None,
            )
            output = _output_with_prior_diagnostics(
                output,
                fragment_selection=fragment_selection,
                fragment_error=prior_fragment_error,
            )
            output_id = _output_id(probability_input, output)
        except Exception as exc:
            errors.append(f"{probability_input.state_id}: {type(exc).__name__}: {exc}")
            continue
        output_rows.append((output_id, probability_input, output))
        rows.append(_runtime_row(runtime_input, output=output, output_id=output_id))

    if output_rows:
        try:
            store.insert_probability_outputs(output_rows)
        except (duckdb.Error, ValueError) as exc:
            errors.extend(
                f"{probability_input.state_id}: {type(exc).__name__}: {exc}"
                for _, probability_input, _ in output_rows
            )
            rows = []
    return rows, errors


def _runtime_input_blocked(runtime_input: ProbabilityRuntimeInput) -> bool:
    return (
        runtime_input.probability_state != "READY"
        or not runtime_input.sigma_valid
        or not runtime_input.offload_allowed
        or not runtime_input.k_stable
    )


def _load_probability_fragments(
    *,
    path: Path | None,
) -> tuple[tuple[GeneratorFragment, ...], str | None]:
    if path is None:
        return (), None
    try:
        payload = read_probability_fragments(
            out_path=path,
            max_age_seconds=DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS,
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        return (), f"{type(exc).__name__}: {exc}"
    return payload.fragments, None


def _select_prior_fragments(
    *,
    fragments: tuple[GeneratorFragment, ...],
    probability_input: ProbabilityInput,
    fragment_error: str | None,
) -> FragmentSelection:
    if fragment_error is not None:
        return FragmentSelection(fragments=(), sparse=True, reason="unavailable")
    return select_fragments_for_input(
        fragments,
        probability_input=probability_input,
        min_fragment_count=DEFAULT_MIN_FRAGMENT_COUNT,
        max_fragment_count=DEFAULT_PROBABILITY_PATH_COUNT,
    )


def _output_with_prior_diagnostics(
    output: ProbabilityOutput,
    *,
    fragment_selection: FragmentSelection,
    fragment_error: str | None,
) -> ProbabilityOutput:
    diagnostics = dict(output.diagnostics)
    diagnostics.update(
        {
            "prior_fragment_count": len(fragment_selection.fragments),
            "prior_fragment_reason": fragment_selection.reason,
            "prior_fragment_sparse": fragment_selection.sparse,
            "prior_fragment_ids": [
                fragment.fragment_id for fragment in fragment_selection.fragments
            ],
        }
    )
    if fragment_selection.sparse:
        diagnostics["sparse_scope"] = True
        diagnostics["path_diagnosis"] = "SPARSE"
    if fragment_error is not None:
        diagnostics["prior_fragment_error"] = fragment_error
    return ProbabilityOutput(
        state_id=output.state_id,
        asof_ts=output.asof_ts,
        p_finish=output.p_finish,
        p_no_touch=output.p_no_touch,
        z_path=output.z_path,
        model_version=output.model_version,
        seed=output.seed,
        diagnostics=diagnostics,
    )


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
    _merge_grid_diagnostics(row=row, diagnostics=output.diagnostics, preview_is_current=True)
    return row


def _blocked_runtime_row(runtime_input: ProbabilityRuntimeInput) -> dict[str, Any]:
    probability_input = runtime_input.probability_input
    age_ms = max(
        0,
        int((datetime.now(timezone.utc) - probability_input.asof_ts).total_seconds() * 1000),
    )
    row = {
        "contract": runtime_input.contract,
        "contract_id": runtime_input.contract_id,
        "asset": probability_input.asset,
        "side": probability_input.side,
        "asof_ts": probability_input.asof_ts.isoformat(),
        "start_ts": runtime_input.start_ts.isoformat(),
        "expiry_ts": runtime_input.expiry_ts.isoformat(),
        "market_slug": runtime_input.market_slug,
        "p_finish": None,
        "p_no_touch": None,
        "z_path": probability_input.z_path,
        "sigma_tau": runtime_input.sigma_tau,
        "age_ms": age_ms,
        "flags": list(runtime_input.flags),
        "model_version": None,
        "seed": None,
        "output_id": None,
        "probability_state": runtime_input.probability_state,
        "sigma_valid": runtime_input.sigma_valid,
        "sigma_age_ms": runtime_input.sigma_age_ms,
        "last_sigma_update_ts": (
            None
            if runtime_input.last_sigma_update_ts is None
            else runtime_input.last_sigma_update_ts.isoformat()
        ),
        "short_vol": runtime_input.short_vol,
        "medium_vol": runtime_input.medium_vol,
        "long_vol": runtime_input.long_vol,
        "volatility_floor_applied": runtime_input.volatility_floor_applied,
        "regime_multiplier_applied": runtime_input.regime_multiplier_applied,
        "failure_reason": runtime_input.failure_reason,
        "input_sample_count": runtime_input.input_sample_count,
        "offload_allowed": runtime_input.offload_allowed,
        "block_reasons": list(runtime_input.block_reasons),
        "k_stable": runtime_input.k_stable,
        "threshold_diagnostics": _threshold_diagnostics_row(
            runtime_input.threshold_diagnostics
        ),
    }
    return row


def _threshold_diagnostics_row(value: ThresholdDiagnostics | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "contract_id": value.contract_id,
        "market_slug": value.market_slug,
        "asset": value.asset,
        "side": value.side,
        "K": value.K,
        "K_source": value.K_source,
        "rule_hash": value.rule_hash,
        "timestamp": value.timestamp.isoformat(),
        "previous_K": value.previous_K,
        "new_K": value.new_K,
        "reason_for_change": value.reason_for_change,
    }


def _persisted_runtime_row(row: tuple[Any, ...]) -> dict[str, Any]:
    asof_ts = _parse_datetime(row[2])
    start_ts = _parse_datetime(row[14])
    expiry_ts = _parse_datetime(row[15])
    flags = tuple(str(flag) for flag in json.loads(row[16]))
    age_ms = max(0, int((datetime.now(timezone.utc) - asof_ts).total_seconds() * 1000))
    runtime_row: dict[str, Any] = {
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
    output_json = _json_dict(row[9], default={})
    diagnostics = output_json.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        _merge_grid_diagnostics(
            row=runtime_row,
            diagnostics=cast(Mapping[str, Any], diagnostics),
            preview_is_current=True,
        )
    if len(row) > 17 and row[17] is not None:
        runtime_row.update(
            {
                "decision_hint": str(row[17]),
                "edge_after_costs": float(row[18]),
                "required_edge": float(row[19]),
                "skip_reasons": _json_list(row[20], default=[]),
                "generator_summary": _json_dict(row[21], default={}),
                "execution_summary": _json_dict(row[22], default={}),
                "supervised_live": _json_dict(row[23], default={"action": "DISABLED"}),
            }
        )
    return runtime_row


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


def _merge_grid_diagnostics(
    *,
    row: dict[str, Any],
    diagnostics: Mapping[str, Any],
    preview_is_current: bool,
) -> None:
    for key in (
        "p_hat",
        "p_hat_std",
        "p_hat_ci_low",
        "p_hat_ci_high",
        "paths_per_seed",
        "seed_count",
        "prior_sensitivity",
        "decision_hint",
        "edge_after_costs",
        "required_edge",
        "path_risk_buffer",
        "backend",
        "generator_version",
        "path_count",
        "steps",
        "effective_weights",
        "effective_generator_values",
        "generator_runs",
        "generator_summary",
        "u_gen",
        "mc_dispersion",
        "uncertainty_buffer",
        "path_diagnosis",
        "sparse_scope",
        "prior_fragment_count",
        "prior_fragment_reason",
        "prior_fragment_sparse",
        "prior_fragment_ids",
        "prior_fragment_error",
        "prior_fragment_generators",
        "terminal_probability_source",
        "risk_adjusted_p_finish",
        "risk_adjusted_p_no_touch",
        "risk_adjustment",
        "pair_probability_sum_before",
        "pair_complement_gap",
        "pair_normalized",
        "counterparty_p_finish",
    ):
        if key in diagnostics and row.get(key) is None:
            row[key] = diagnostics[key]
    if row.get("p_hat") is None and row.get("p_finish") is not None:
        row["p_hat"] = row["p_finish"]
    if preview_is_current and isinstance(diagnostics.get("simulation_preview"), Mapping):
        row["simulation_preview"] = dict(cast(Mapping[str, Any], diagnostics["simulation_preview"]))
    generator_metadata = dict(row.get("generator_metadata", {}))
    for metadata_key in ("cache", "generator", "generator_metadata"):
        metadata = diagnostics.get(metadata_key)
        if isinstance(metadata, Mapping):
            generator_metadata.update(dict(cast(Mapping[str, Any], metadata)))
    if generator_metadata:
        row["generator_metadata"] = generator_metadata


def probability_gate_diagnostics(
    *,
    probability_input: ProbabilityInput,
    output: ProbabilityOutput,
    latency_ms: float | None = None,
) -> dict[str, Any]:
    del latency_ms
    edge_after_costs = output.p_finish - probability_input.executable_price
    required_edge = 0.02
    reasons: list[str] = []
    if probability_input.book_age_ms > 5_000:
        reasons.append("stale_book")
    if probability_input.source_age_ms > 5_000:
        reasons.append("stale_source")
    if abs(output.z_path) > 2:
        reasons.append("path_risk")
    return {
        "decision_hint": "SKIP" if reasons or edge_after_costs < required_edge else "PAPER_TRADE",
        "edge_after_costs": edge_after_costs,
        "required_edge": required_edge,
        "path_risk_buffer": 0.0,
        "reasons": reasons,
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


def _json_list(value: object, *, default: list[Any]) -> list[Any]:
    if value is None:
        return list(default)
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return list(default)
    return parsed if isinstance(parsed, list) else list(default)


def _json_dict(value: object, *, default: dict[str, Any]) -> dict[str, Any]:
    if value is None:
        return dict(default)
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return dict(default)
    return parsed if isinstance(parsed, dict) else dict(default)


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
