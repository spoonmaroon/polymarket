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

from polymarket_engine.probability.decision_gates import ExecutableQualityInput
from polymarket_engine.probability.decision_gates import evaluate_probability_gates
from polymarket_engine.probability.ensemble_outputs import EnsembleOutput
from polymarket_engine.probability.grid_cache import grid_runtime_row
from polymarket_engine.probability.grid_cache import grid_entry_from_probability_input
from polymarket_engine.probability.grid_cache import lookup_probability_grid_entry
from polymarket_engine.probability.grid_cache import ProbabilityGridHit
from polymarket_engine.probability.grid_cache import upsert_probability_grid_entry
from polymarket_engine.probability.event_log import ProbabilityEventLogRow
from polymarket_engine.probability.fast_nowcast import FastNowcastInput
from polymarket_engine.probability.fast_nowcast import compute_fast_nowcast
from polymarket_engine.probability.latency import ProbabilityLatencyTrace
from polymarket_engine.probability.monte_carlo import run_seeded_monte_carlo
from polymarket_engine.probability.path_policy import runtime_path_count_for_state
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

    nowcast_rows, nowcast_errors = _nowcast_rows_and_events(
        store=store,
        inputs=inputs,
        generated_at=generated_at,
    )
    rows, missed_inputs, errors = _grid_rows_and_misses(
        store=store,
        inputs=inputs,
        runtime_ts=generated_at,
    )
    errors.extend(nowcast_errors)
    if missed_inputs:
        computed_rows, compute_errors = _compute_and_persist_rows(
            store=store,
            inputs=missed_inputs,
            runtime_ts=generated_at,
        )
        rows.extend(computed_rows)
        errors.extend(compute_errors)

    payload = {
        "ok": not errors,
        "state": "OK" if not errors else "PARTIAL",
        "generated_at": generated_at.isoformat(),
        "cached": False,
        "model_version": rows[0]["model_version"] if rows else None,
        "rows": rows,
        "nowcast_rows": nowcast_rows,
        "skipped": skipped,
        "errors": errors,
    }
    _enrich_probability_payload(payload)
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
                _finalize_confirmed_row(
                    row=row,
                    probability_input=probability_input,
                    generated_at=runtime_ts,
                )
                try:
                    _persist_probability_event(
                        store=store,
                        runtime_input=runtime_input,
                        row=row,
                        generated_at=runtime_ts,
                        output_id=None,
                    )
                except (duckdb.Error, ValueError) as exc:
                    errors.append(
                        f"{probability_input.state_id}: probability event persistence "
                        f"{type(exc).__name__}: {exc}"
                    )
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
    _with_probability_aliases(row)
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
        nowcast = compute_fast_nowcast(
            FastNowcastInput(
                state_id=probability_input.state_id,
                asof_ts=probability_input.asof_ts,
                asset=cast(Any, probability_input.asset),
                side=cast(Any, probability_input.side),
                z_path=probability_input.z_path,
                seconds_left=probability_input.seconds_left,
                executable_price=probability_input.executable_price,
                sigma_tau=probability_input.sigma_tau,
                source_age_ms=probability_input.source_age_ms,
                book_age_ms=probability_input.book_age_ms,
            )
        )
        seed = _seed_for_input(probability_input)
        steps = _steps_for_input(probability_input)
        path_count = runtime_path_count_for_state(
            seconds_left=probability_input.seconds_left,
            z_path=probability_input.z_path,
            executable_price=probability_input.executable_price,
            wave_phase=nowcast.wave_phase,
        )
        mc_started_ts = datetime.now(timezone.utc)
        try:
            output = run_seeded_monte_carlo(
                probability_input,
                path_count=path_count,
                steps=steps,
                seed=seed,
            )
            mc_finished_ts = datetime.now(timezone.utc)
            output_id = _output_id(probability_input, output)
            store.insert_probability_output(
                output_id=output_id,
                probability_input=probability_input,
                output=output,
            )
            status_written_ts = datetime.now(timezone.utc)
            diagnostics = dict(output.diagnostics)
            latency = ProbabilityLatencyTrace(
                state_asof_ts=probability_input.asof_ts,
                tick_observed_ts=None,
                worker_received_ts=generated_at,
                mc_started_ts=mc_started_ts,
                mc_finished_ts=mc_finished_ts,
                status_written_ts=status_written_ts,
                ui_seen_ts=None,
            )
            diagnostics["cache"] = {
                "source": "runtime-grid-refresh",
                "market_slug": runtime_input.market_slug,
                "start_ts": runtime_input.start_ts.isoformat(),
                "expiry_ts": runtime_input.expiry_ts.isoformat(),
                "asof_ts": probability_input.asof_ts.isoformat(),
                "path_count": path_count,
            }
            diagnostics["latency"] = latency.to_json_dict()
            diagnostics.setdefault(
                "gate",
                probability_gate_diagnostics(
                    probability_input=probability_input,
                    output=output,
                    latency_ms=latency.runtime_ms(),
                ),
            )
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
            _finalize_confirmed_row(
                row=row,
                probability_input=probability_input,
                generated_at=status_written_ts,
            )
        except (duckdb.Error, ValueError) as exc:
            errors.append(f"{probability_input.state_id}: {type(exc).__name__}: {exc}")
            continue
        try:
            _persist_probability_event(
                store=store,
                runtime_input=runtime_input,
                row=row,
                generated_at=status_written_ts,
                output_id=output_id,
            )
            _persist_simulation_artifact(store=store, row=row, output_id=output_id)
        except (duckdb.Error, ValueError) as exc:
            errors.append(
                f"{probability_input.state_id}: probability event persistence "
                f"{type(exc).__name__}: {exc}"
            )
        rows.append(row)
    return rows, errors


def _nowcast_rows_and_events(
    *,
    store: DuckDbIngestStore,
    inputs: tuple[ProbabilityRuntimeInput, ...],
    generated_at: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for runtime_input in inputs:
        try:
            row = _nowcast_runtime_row(runtime_input, generated_at=generated_at)
        except ValueError as exc:
            errors.append(
                f"{runtime_input.probability_input.state_id}: "
                f"nowcast {type(exc).__name__}: {exc}"
            )
            continue
        try:
            _persist_probability_event(
                store=store,
                runtime_input=runtime_input,
                row=row,
                generated_at=generated_at,
                output_id=None,
            )
        except (duckdb.Error, ValueError) as exc:
            errors.append(
                f"{runtime_input.probability_input.state_id}: "
                f"nowcast event persistence {type(exc).__name__}: {exc}"
            )
        rows.append(row)
    return rows, errors


def _nowcast_runtime_row(
    runtime_input: ProbabilityRuntimeInput,
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    probability_input = runtime_input.probability_input
    nowcast = compute_fast_nowcast(
        FastNowcastInput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            asset=cast(Any, probability_input.asset),
            side=cast(Any, probability_input.side),
            z_path=probability_input.z_path,
            seconds_left=probability_input.seconds_left,
            executable_price=probability_input.executable_price,
            sigma_tau=probability_input.sigma_tau,
            source_age_ms=probability_input.source_age_ms,
            book_age_ms=probability_input.book_age_ms,
        )
    )
    age_ms = max(0, int((generated_at - probability_input.asof_ts).total_seconds() * 1000))
    latency = ProbabilityLatencyTrace(
        state_asof_ts=probability_input.asof_ts,
        tick_observed_ts=None,
        worker_received_ts=generated_at,
        mc_started_ts=generated_at,
        mc_finished_ts=generated_at,
        status_written_ts=generated_at,
        ui_seen_ts=None,
    )
    row = {
        "contract": runtime_input.contract,
        "state_id": probability_input.state_id,
        "contract_id": runtime_input.contract_id,
        "market_slug": runtime_input.market_slug,
        "asset": probability_input.asset,
        "side": probability_input.side,
        "start_ts": runtime_input.start_ts.isoformat(),
        "expiry_ts": runtime_input.expiry_ts.isoformat(),
        "asof_ts": probability_input.asof_ts.isoformat(),
        "p_finish": nowcast.p_finish,
        "p_no_touch": nowcast.p_no_touch,
        "z_path": nowcast.z_path,
        "sigma_tau": probability_input.sigma_tau,
        "age_ms": age_ms,
        "seconds_left": probability_input.seconds_left,
        "executable_price": probability_input.executable_price,
        "source_age_ms": probability_input.source_age_ms,
        "book_age_ms": probability_input.book_age_ms,
        "flags": list(runtime_input.flags) if runtime_input.flags else ["OK"],
        "backend": nowcast.backend,
        "probability_kind": nowcast.probability_kind,
        "model_version": nowcast.model_version,
        "seed": None,
        "generated_at": generated_at.isoformat(),
        "valid_from": generated_at.isoformat(),
        "valid_until": (generated_at + timedelta(seconds=2)).isoformat(),
        "latency": latency.to_json_dict(),
        "wave_phase": nowcast.wave_phase,
        "wave_score": nowcast.wave_score,
        "wave_reasons": nowcast.wave_reasons,
        "wave_markers": nowcast.wave_markers,
        "dynamic_edge": nowcast.dynamic_edge,
        "dynamic_required_edge": nowcast.dynamic_required_edge,
    }
    return _with_probability_aliases(row)


def _finalize_confirmed_row(
    *,
    row: dict[str, Any],
    probability_input: ProbabilityInput,
    generated_at: datetime,
) -> None:
    _apply_wave_signal(row, probability_input)
    row["state_id"] = probability_input.state_id
    row["probability_kind"] = _confirmed_probability_kind(row)
    row["backend"] = _confirmed_backend(row)
    row["seconds_left"] = probability_input.seconds_left
    row["executable_price"] = probability_input.executable_price
    row["source_age_ms"] = probability_input.source_age_ms
    row["book_age_ms"] = probability_input.book_age_ms
    if not isinstance(row.get("latency"), Mapping):
        row["latency"] = ProbabilityLatencyTrace(
            state_asof_ts=probability_input.asof_ts,
            tick_observed_ts=None,
            worker_received_ts=None,
            mc_started_ts=None,
            mc_finished_ts=None,
            status_written_ts=generated_at,
            ui_seen_ts=None,
        ).to_json_dict()
    _with_probability_aliases(row)


def _confirmed_probability_kind(row: Mapping[str, Any]) -> str:
    raw_kind = row.get("probability_kind")
    if isinstance(raw_kind, str) and raw_kind:
        return raw_kind
    return "CACHE" if row.get("cache_status") == "HIT" else "MC"


def _confirmed_backend(row: Mapping[str, Any]) -> str:
    raw_backend = row.get("backend")
    if isinstance(raw_backend, str) and raw_backend:
        return raw_backend
    return "cache" if row.get("cache_status") == "HIT" else "cpu"


def _enrich_probability_payload(payload: dict[str, Any]) -> None:
    payload["lanes"] = _probability_lane_counts(payload)
    payload["latency"] = _probability_latency_summary(payload)


def _probability_lane_counts(payload: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in _payload_probability_rows(payload):
        lane = str(row.get("probability_kind") or "MC")
        counts[lane] = counts.get(lane, 0) + 1
    return counts


def _probability_latency_summary(payload: Mapping[str, Any]) -> dict[str, float | None]:
    lags = [
        lag
        for row in _payload_probability_rows(payload)
        for lag in [_row_latency_value(row, "total_lag_ms")]
        if lag is not None
    ]
    runtimes = [
        runtime
        for row in _payload_probability_rows(payload)
        for runtime in [_row_latency_value(row, "runtime_ms")]
        if runtime is not None
    ]
    return {
        "max_total_lag_ms": max(lags) if lags else None,
        "avg_total_lag_ms": round(sum(lags) / len(lags), 3) if lags else None,
        "max_runtime_ms": max(runtimes) if runtimes else None,
        "avg_runtime_ms": round(sum(runtimes) / len(runtimes), 3) if runtimes else None,
    }


def probability_gate_diagnostics(
    *,
    probability_input: ProbabilityInput,
    output: ProbabilityOutput,
    latency_ms: float | None = None,
) -> dict[str, Any]:
    diagnostics = output.diagnostics
    ensemble = _gate_ensemble_output(output, diagnostics=diagnostics)
    gate = evaluate_probability_gates(
        ensemble,
        ExecutableQualityInput(
            executable_entry_price=probability_input.executable_price,
            execution_costs=0.0,
            quote_age_ms=probability_input.book_age_ms,
            source_age_ms=probability_input.source_age_ms,
            book_age_ms=probability_input.book_age_ms,
            latency_ms=max(0, int(latency_ms or 0.0)),
        ),
    )
    return {
        "decision_hint": gate.decision_hint,
        "edge_after_costs": gate.edge_after_costs,
        "required_edge": gate.required_edge,
        "path_risk_buffer": gate.path_risk_buffer,
        "reasons": list(gate.reasons),
    }


def _gate_ensemble_output(
    output: ProbabilityOutput,
    *,
    diagnostics: Mapping[str, Any],
) -> EnsembleOutput:
    ensemble_diagnostics = _optional_mapping(diagnostics.get("ensemble"), "ensemble") or {}
    mc_dispersion = _first_optional_float(
        ensemble_diagnostics.get("mc_dispersion"),
        diagnostics.get("p_hat_std"),
        default=0.0,
    )
    uncertainty_buffer = _first_optional_float(
        ensemble_diagnostics.get("uncertainty_buffer"),
        default=0.02,
    )
    path_diagnosis = tuple(
        _string_list(
            ensemble_diagnostics.get("path_diagnosis")
            or _default_path_diagnosis(
                p_no_touch=output.p_no_touch,
                z_path=output.z_path,
                mc_dispersion=mc_dispersion,
            ),
            "path_diagnosis",
        )
    )
    return EnsembleOutput(
        p_finish=output.p_finish,
        p_no_touch=output.p_no_touch,
        z_path=output.z_path,
        mc_dispersion=mc_dispersion,
        uncertainty_buffer=uncertainty_buffer,
        path_diagnosis=path_diagnosis,
        effective_weights={},
    )


def _first_optional_float(*values: object, default: float) -> float:
    for value in values:
        if value is not None:
            return _float(value, "value")
    return default


def _default_path_diagnosis(
    *,
    p_no_touch: float,
    z_path: float,
    mc_dispersion: float,
) -> list[str]:
    labels: list[str] = []
    if z_path < 0:
        labels.append("WRONG_SIDE")
    elif z_path < 0.5:
        labels.append("NEAR_THRESHOLD")
    if p_no_touch < 0.55:
        labels.append("TERMINAL_ONLY")
    if mc_dispersion > 0.05:
        labels.append("FRAGILE")
    return labels or ["CLEAN"]


def _payload_probability_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for key in ("rows", "nowcast_rows"):
        raw_rows = payload.get(key)
        if isinstance(raw_rows, Sequence) and not isinstance(raw_rows, (str, bytes)):
            rows.extend(row for row in raw_rows if isinstance(row, Mapping))
    return rows


def _row_latency_value(row: Mapping[str, Any], field_name: str) -> float | None:
    latency = row.get("latency")
    if not isinstance(latency, Mapping):
        return None
    value = latency.get(field_name)
    if value is None:
        return None
    return _float(value, f"latency.{field_name}")


def _persist_probability_event(
    *,
    store: DuckDbIngestStore,
    runtime_input: ProbabilityRuntimeInput,
    row: Mapping[str, Any],
    generated_at: datetime,
    output_id: str | None,
) -> None:
    probability_input = runtime_input.probability_input
    valid_from = _optional_datetime(row.get("valid_from")) or generated_at
    valid_until = _optional_datetime(row.get("valid_until")) or (
        generated_at + timedelta(seconds=DEFAULT_PROBABILITY_GRID_VALID_SECONDS)
    )
    latency = row.get("latency") if isinstance(row.get("latency"), Mapping) else {}
    probability_kind = str(row.get("probability_kind") or "MC")
    event_id_source = "|".join(
        (
            probability_input.state_id,
            probability_input.asof_ts.isoformat(),
            probability_kind,
            output_id or "",
            generated_at.isoformat(),
        )
    )
    event_id = f"prob-event-{hashlib.sha256(event_id_source.encode()).hexdigest()[:24]}"
    diagnostics = {
        "latency": dict(cast(Mapping[str, Any], latency)),
        "cache_status": row.get("cache_status"),
        "generator_metadata": row.get("generator_metadata"),
    }
    store.insert_probability_event(
        ProbabilityEventLogRow(
            event_id=event_id,
            output_id=output_id,
            state_id=probability_input.state_id,
            contract_id=runtime_input.contract_id,
            market_slug=runtime_input.market_slug,
            asset=probability_input.asset,
            side=probability_input.side,
            start_ts=runtime_input.start_ts,
            expiry_ts=runtime_input.expiry_ts,
            asof_ts=probability_input.asof_ts,
            probability_kind=probability_kind,
            backend=str(row.get("backend") or "unknown"),
            model_version=str(row.get("model_version") or "unknown"),
            generator_version=_optional_str(row.get("generator_version")),
            cache_key=_optional_str(row.get("cache_key")),
            cache_status=_optional_str(row.get("cache_status")),
            p_finish=_float(row.get("p_finish"), "p_finish"),
            p_no_touch=_float(row.get("p_no_touch"), "p_no_touch"),
            z_path=_float(row.get("z_path"), "z_path"),
            sigma_tau=_optional_runtime_float(row.get("sigma_tau"), "sigma_tau"),
            executable_price=probability_input.executable_price,
            spread=None,
            seconds_left=probability_input.seconds_left,
            wave_phase=str(row.get("wave_phase") or "none"),
            wave_score=_float(row.get("wave_score", 0.0), "wave_score"),
            path_count=_optional_runtime_int(row.get("path_count"), "path_count"),
            seed=_optional_runtime_int(row.get("seed"), "seed"),
            queue_ms=_optional_float_from_mapping(latency, "queue_ms"),
            runtime_ms=_optional_float_from_mapping(latency, "runtime_ms"),
            state_to_status_ms=_optional_float_from_mapping(latency, "state_to_status_ms"),
            total_lag_ms=_optional_float_from_mapping(latency, "total_lag_ms"),
            generated_at=generated_at,
            valid_from=valid_from,
            valid_until=valid_until,
            diagnostics=diagnostics,
        )
    )


def _persist_simulation_artifact(
    *,
    store: DuckDbIngestStore,
    row: Mapping[str, Any],
    output_id: str,
) -> None:
    preview = row.get("simulation_preview")
    if not isinstance(preview, Mapping):
        return
    path_count = _optional_runtime_int(preview.get("path_count"), "path_count")
    terminal_win_count = _optional_runtime_int(
        preview.get("terminal_win_count"),
        "terminal_win_count",
    )
    no_touch_win_count = _optional_runtime_int(
        preview.get("no_touch_win_count"),
        "no_touch_win_count",
    )
    if path_count is None or terminal_win_count is None or no_touch_win_count is None:
        return
    sampled_paths = preview.get("sampled_paths")
    artifact_id = f"sim-artifact-{hashlib.sha256((output_id + ':artifact').encode()).hexdigest()[:24]}"
    store.insert_simulation_artifact(
        artifact_id=artifact_id,
        output_id=output_id,
        state_id=str(row["state_id"]) if "state_id" in row else str(row.get("contract_id")),
        asof_ts=_parse_datetime(row["asof_ts"]),
        model_version=str(row.get("model_version") or "unknown"),
        backend=str(row.get("backend") or "unknown"),
        path_count=path_count,
        terminal_win_count=terminal_win_count,
        no_touch_win_count=no_touch_win_count,
        terminal_price_quantiles=_terminal_price_quantiles(preview),
        crossing_count_quantiles={},
        sampled_paths=[
            dict(cast(Mapping[str, object], path))
            for path in cast(Sequence[object], sampled_paths or [])
            if isinstance(path, Mapping)
        ],
        diagnostics={"source": "runtime-simulation-preview"},
    )


def _terminal_price_quantiles(preview: Mapping[str, Any]) -> dict[str, float]:
    histogram = preview.get("terminal_histogram")
    if not isinstance(histogram, Sequence) or isinstance(histogram, (str, bytes)):
        return {}
    rows = [row for row in histogram if isinstance(row, Mapping)]
    if not rows:
        return {}
    return {
        "p05": _histogram_quantile(rows, 0.05),
        "p50": _histogram_quantile(rows, 0.50),
        "p95": _histogram_quantile(rows, 0.95),
    }


def _histogram_quantile(rows: Sequence[Mapping[str, Any]], quantile: float) -> float:
    counts = [_optional_runtime_int(row.get("count"), "count") or 0 for row in rows]
    total = sum(counts)
    if total <= 0:
        return 0.0
    threshold = total * quantile
    cumulative = 0
    for row, count in zip(rows, counts, strict=True):
        cumulative += count
        if cumulative >= threshold:
            lower = _float(row.get("lower"), "lower")
            upper = _float(row.get("upper"), "upper")
            return (lower + upper) / 2.0
    last = rows[-1]
    return (_float(last.get("lower"), "lower") + _float(last.get("upper"), "upper")) / 2.0


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
    return _with_probability_aliases({
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
    })


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
        "path_risk_buffer": _optional_runtime_float(
            gate.get("path_risk_buffer"),
            "path_risk_buffer",
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


def _with_probability_aliases(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("p_hat") is None and row.get("p_finish") is not None:
        row["p_hat"] = row["p_finish"]
    return row


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


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    return value


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _parse_datetime(value)


def _optional_float_from_mapping(mapping: object, field_name: str) -> float | None:
    if not isinstance(mapping, Mapping):
        return None
    value = mapping.get(field_name)
    if value is None:
        return None
    return _float(value, field_name)


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
