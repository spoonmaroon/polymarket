from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, cast

import duckdb

from polymarket_engine.probability.cuda_monte_carlo import (
    run_cuda_monte_carlo_batch as _run_cuda_monte_carlo_batch_impl,
)
from polymarket_engine.probability.cuda_monte_carlo import run_cuda_monte_carlo_multi_seed
from polymarket_engine.probability.ensemble_runtime import (
    run_four_generator_ensemble,
)
from polymarket_engine.probability.fast_nowcast import FastNowcastInput
from polymarket_engine.probability.fast_nowcast import compute_fast_nowcast
from polymarket_engine.probability.generator_fragments import FragmentSelection
from polymarket_engine.probability.generator_fragments import GeneratorFragment
from polymarket_engine.probability.generator_fragments import read_probability_fragments
from polymarket_engine.probability.generator_fragments import select_fragments_for_input
from polymarket_engine.probability.grid_cache import ProbabilityGridHit
from polymarket_engine.probability.grid_cache import grid_entry_from_probability_input
from polymarket_engine.probability.grid_cache import grid_runtime_row
from polymarket_engine.probability.hot_inputs import HOT_PROBABILITY_INPUTS_SCHEMA_VERSION
from polymarket_engine.probability.latency import ProbabilityLatencyTrace
from polymarket_engine.probability.path_policy import runtime_path_count_for_state
from polymarket_engine.probability.runtime import DEFAULT_PROBABILITY_GRID_VALID_SECONDS
from polymarket_engine.probability.runtime import DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS
from polymarket_engine.probability.runtime import _float
from polymarket_engine.probability.runtime import _int
from polymarket_engine.probability.runtime import _merge_grid_diagnostics
from polymarket_engine.probability.runtime import _output_id
from polymarket_engine.probability.runtime import _parse_datetime
from polymarket_engine.probability.runtime import _seed_for_input
from polymarket_engine.probability.runtime import _steps_for_input
from polymarket_engine.probability.runtime import latest_probability_inputs
from polymarket_engine.probability.runtime import probability_gate_diagnostics
from polymarket_engine.probability.runtime_inputs import ProbabilityRuntimeInput
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine.storage.atomic import durable_replace


DEFAULT_GPU_PROBABILITY_LIMIT = 24
DEFAULT_GPU_PROBABILITY_INTERVAL_SECONDS = 1.0
DEFAULT_INPUT_SNAPSHOT_MAX_AGE_SECONDS = 10.0
PROBABILITY_INPUTS_SCHEMA_VERSION = "polymarket-probability-inputs-v1"
DEFAULT_WORKER_MODE = "ensemble"
DEFAULT_GENERATOR_POLICY = "all_four_every_cycle"
DEFAULT_CPU_TARGET_PERCENT = 20.0
DEFAULT_MAX_RSS_MB = 512
DEFAULT_MAX_CYCLE_RUNTIME_MS = 750
DEFAULT_MAX_TOTAL_PATHS = 320_000
DEFAULT_SUSTAINED_BREACH_CYCLES = 3
DEFAULT_FRAGMENT_MAX_ROWS = 250_000
DEFAULT_MIN_FRAGMENT_COUNT = 2
DEFAULT_CPU_THREADS = 1


@dataclass(frozen=True)
class ProbabilityWorkerBudget:
    worker_mode: str = DEFAULT_WORKER_MODE
    generator_policy: str = DEFAULT_GENERATOR_POLICY
    cpu_target_percent: float = DEFAULT_CPU_TARGET_PERCENT
    max_rss_mb: int = DEFAULT_MAX_RSS_MB
    max_cycle_runtime_ms: int = DEFAULT_MAX_CYCLE_RUNTIME_MS
    max_total_paths: int = DEFAULT_MAX_TOTAL_PATHS
    sustained_breach_cycles: int = DEFAULT_SUSTAINED_BREACH_CYCLES
    fragment_max_rows: int = DEFAULT_FRAGMENT_MAX_ROWS
    cpu_threads: int = DEFAULT_CPU_THREADS

    def __post_init__(self) -> None:
        if self.worker_mode == "":
            raise ValueError("worker_mode must not be empty")
        if self.generator_policy == "":
            raise ValueError("generator_policy must not be empty")
        if self.cpu_target_percent <= 0:
            raise ValueError("cpu_target_percent must be positive")
        if self.max_rss_mb <= 0:
            raise ValueError("max_rss_mb must be positive")
        if self.max_cycle_runtime_ms <= 0:
            raise ValueError("max_cycle_runtime_ms must be positive")
        if self.max_total_paths <= 0:
            raise ValueError("max_total_paths must be positive")
        if self.sustained_breach_cycles <= 0:
            raise ValueError("sustained_breach_cycles must be positive")
        if self.fragment_max_rows <= 0:
            raise ValueError("fragment_max_rows must be positive")
        if self.cpu_threads <= 0:
            raise ValueError("cpu_threads must be positive")


def run_cuda_monte_carlo_batch(
    probability_inputs: Sequence[ProbabilityInput],
    *,
    paths_per_seed: int,
    steps: int,
    seed: int,
    seed_count: int,
) -> tuple[ProbabilityOutput, ...]:
    if len(probability_inputs) == 1:
        return (
            run_cuda_monte_carlo_multi_seed(
                probability_inputs[0],
                paths_per_seed=paths_per_seed,
                steps=steps,
                seed=seed,
                seed_count=seed_count,
            ),
        )
    return _run_cuda_monte_carlo_batch_impl(
        probability_inputs,
        paths_per_seed=paths_per_seed,
        steps=steps,
        seed=seed,
        seed_count=seed_count,
    )


def run_cuda_probability_worker_cycle(
    *,
    duckdb_path: Path,
    probability_status_path: Path,
    probability_inputs_path: Path | None = None,
    probability_fragments_path: Path | None = None,
    limit: int = DEFAULT_GPU_PROBABILITY_LIMIT,
    valid_seconds: int = int(DEFAULT_PROBABILITY_GRID_VALID_SECONDS),
    max_state_age_seconds: float | None = DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS,
    max_input_snapshot_age_seconds: float | None = DEFAULT_INPUT_SNAPSHOT_MAX_AGE_SECONDS,
    probability_event_path: Path | None = None,
    budget: ProbabilityWorkerBudget | None = None,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if valid_seconds <= 0:
        raise ValueError("valid_seconds must be positive")

    cycle_started_monotonic = time.monotonic()
    budget = budget or ProbabilityWorkerBudget()
    requested_total_paths = 0
    allocated_total_paths = 0
    clamped_inputs = 0
    mc_input_skipped = 0
    path_budget_per_input = 0
    previous_rows = _read_status_rows(probability_status_path)
    probability_event_path = probability_event_path or probability_status_path.with_name(
        "probability-events.jsonl"
    )
    generated_at = datetime.now(timezone.utc)
    retained_mc_rows = _retained_mc_rows(previous_rows, now=generated_at)
    rows: list[dict[str, Any]] = []
    nowcast_rows: list[dict[str, Any]] = []
    nowcast_by_state_id: dict[str, dict[str, Any]] = {}
    event_rows: list[dict[str, Any]] = []
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
        input_error = f"probability input unavailable: {type(exc).__name__}: {exc}"
        input_gap_rows = _retained_mc_rows(
            previous_rows,
            now=generated_at,
            require_valid_until=False,
        )
        if input_gap_rows:
            payload = _status_payload(
                generated_at=generated_at,
                rows=input_gap_rows,
                skipped=0,
                errors=[],
                rows_seen=0,
                rows_written=0,
                last_good_rows=input_gap_rows,
                state_override="STALE_INPUTS",
                retained_mc_rows=len(input_gap_rows),
                budget=_budget_diagnostics(
                    budget=budget,
                    cycle_started_monotonic=cycle_started_monotonic,
                    requested_total_paths=requested_total_paths,
                    allocated_total_paths=allocated_total_paths,
                    clamped_inputs=clamped_inputs,
                    mc_input_skipped=mc_input_skipped,
                    path_budget_per_input=path_budget_per_input,
                ),
            )
            payload["input_error"] = input_error
            _write_status(probability_status_path, payload)
            return payload
        payload = _status_payload(
            generated_at=generated_at,
            rows=[],
            skipped=0,
            errors=[input_error],
            rows_seen=0,
            rows_written=0,
            last_good_rows=previous_rows,
            budget=_budget_diagnostics(
                budget=budget,
                cycle_started_monotonic=cycle_started_monotonic,
                requested_total_paths=requested_total_paths,
                allocated_total_paths=allocated_total_paths,
                clamped_inputs=clamped_inputs,
                mc_input_skipped=mc_input_skipped,
                path_budget_per_input=path_budget_per_input,
            ),
        )
        _write_status(probability_status_path, payload)
        return payload

    prior_fragments, prior_fragment_error = _load_probability_fragments(
        path=probability_fragments_path,
        max_age_seconds=max_input_snapshot_age_seconds,
    )
    mc_inputs = tuple(inputs)
    if len(mc_inputs) > budget.max_total_paths:
        mc_input_skipped = len(mc_inputs) - budget.max_total_paths
        mc_inputs = mc_inputs[: budget.max_total_paths]
    path_budget_per_input = _path_budget_per_input(
        input_count=len(mc_inputs),
        budget=budget,
    )

    if not inputs:
        input_gap_rows = _retained_mc_rows(
            previous_rows,
            now=generated_at,
            require_valid_until=False,
        )
        if input_gap_rows:
            payload = _status_payload(
                generated_at=generated_at,
                rows=input_gap_rows,
                skipped=quality_skipped,
                errors=[],
                rows_seen=0,
                rows_written=0,
                last_good_rows=input_gap_rows,
                state_override="STALE_INPUTS",
                retained_mc_rows=len(input_gap_rows),
                budget=_budget_diagnostics(
                    budget=budget,
                    cycle_started_monotonic=cycle_started_monotonic,
                    requested_total_paths=requested_total_paths,
                    allocated_total_paths=allocated_total_paths,
                    clamped_inputs=clamped_inputs,
                    mc_input_skipped=mc_input_skipped,
                    path_budget_per_input=path_budget_per_input,
                ),
            )
            _write_status(probability_status_path, payload)
            return payload

    for runtime_input in inputs:
        nowcast_row = _nowcast_row(runtime_input, generated_at=generated_at)
        nowcast_rows.append(nowcast_row)
        nowcast_by_state_id[runtime_input.probability_input.state_id] = nowcast_row
        event_rows.append(
            _event_payload_from_row(
                runtime_input=runtime_input,
                row=nowcast_row,
                generated_at=generated_at,
                output_id=None,
            )
        )

    if nowcast_rows:
        nowcast_mc_rows, _ = _merge_missing_retained_mc_rows(
            fresh_rows=retained_mc_rows,
            previous_rows=previous_rows,
            now=generated_at,
            enabled=True,
        )
        _write_status(
            probability_status_path,
            _status_payload(
                generated_at=generated_at,
                rows=nowcast_mc_rows,
                nowcast_rows=nowcast_rows,
                skipped=quality_skipped,
                errors=[],
                rows_seen=len(inputs),
                rows_written=0,
                last_good_rows=nowcast_mc_rows or previous_rows or None,
                state_override="NOWCAST",
                retained_mc_rows=len(nowcast_mc_rows),
                budget=_budget_diagnostics(
                    budget=budget,
                    cycle_started_monotonic=cycle_started_monotonic,
                    requested_total_paths=requested_total_paths,
                    allocated_total_paths=allocated_total_paths,
                    clamped_inputs=clamped_inputs,
                    mc_input_skipped=mc_input_skipped,
                    path_budget_per_input=path_budget_per_input,
                ),
            ),
        )

    for group in _batch_runtime_inputs(mc_inputs):
        representative = group[0].probability_input
        steps = _steps_for_input(representative)
        requested_path_count = max(
            runtime_path_count_for_state(
                seconds_left=runtime_input.probability_input.seconds_left,
                z_path=runtime_input.probability_input.z_path,
                executable_price=runtime_input.probability_input.executable_price,
                wave_phase=str(
                    nowcast_by_state_id[runtime_input.probability_input.state_id]["wave_phase"]
                ),
            )
            for runtime_input in group
        )
        requested_total_paths += requested_path_count * len(group)
        path_count, was_clamped = _clamp_path_count(
            requested_path_count,
            path_budget_per_input=path_budget_per_input,
        )
        if was_clamped:
            clamped_inputs += len(group)
        seed_count = min(_gpu_seed_count_for_total_paths(path_count), path_count)
        paths_per_seed = max(1, path_count // seed_count)
        path_count = paths_per_seed * seed_count
        allocated_total_paths += path_count * len(group)
        seed = min(_seed_for_input(runtime_input.probability_input) for runtime_input in group)
        mc_started_ts = datetime.now(timezone.utc)
        try:
            outputs: list[ProbabilityOutput] = []
            for runtime_input in group:
                fragment_selection = _select_prior_fragments(
                    fragments=prior_fragments,
                    probability_input=runtime_input.probability_input,
                    max_fragment_count=min(budget.fragment_max_rows, path_count),
                    fragment_error=prior_fragment_error,
                )
                output = run_four_generator_ensemble(
                    runtime_input.probability_input,
                    path_count=path_count,
                    steps=steps,
                    seed=seed,
                    history_fragments=tuple(
                        fragment.prices for fragment in fragment_selection.fragments
                    )
                    or None,
                )
                outputs.append(
                    _output_with_prior_diagnostics(
                        output,
                        fragment_selection=fragment_selection,
                        fragment_error=prior_fragment_error,
                    )
                )
            mc_finished_ts = datetime.now(timezone.utc)
        except (duckdb.Error, ValueError, RuntimeError) as exc:
            state_ids = ",".join(
                runtime_input.probability_input.state_id for runtime_input in group
            )
            errors.append(f"{state_ids}: {type(exc).__name__}: {exc}")
            continue

        for runtime_input, output in zip(group, outputs, strict=True):
            row, output_id = _mc_row_from_output(
                runtime_input=runtime_input,
                output=output,
                nowcast_row=nowcast_by_state_id[output.state_id],
                generated_at=generated_at,
                mc_started_ts=mc_started_ts,
                mc_finished_ts=mc_finished_ts,
                valid_seconds=valid_seconds,
                path_count=path_count,
                paths_per_seed=paths_per_seed,
                seed_count=seed_count,
                seed=seed,
            )
            rows.append(row)
            event_rows.append(
                _event_payload_from_row(
                    runtime_input=runtime_input,
                    row=row,
                    generated_at=generated_at,
                    output_id=output_id,
                )
            )

    rows, partial_retained_mc_rows = _merge_missing_retained_mc_rows(
        fresh_rows=rows,
        previous_rows=previous_rows,
        now=generated_at,
        enabled=quality_skipped > 0 and bool(rows),
    )
    payload = _status_payload(
        generated_at=generated_at,
        rows=rows,
        nowcast_rows=nowcast_rows,
        skipped=quality_skipped,
        errors=errors,
        rows_seen=len(inputs),
        rows_written=len(rows) - partial_retained_mc_rows,
        last_good_rows=previous_rows if errors and not rows else None,
        retained_mc_rows=partial_retained_mc_rows,
        budget=_budget_diagnostics(
            budget=budget,
            cycle_started_monotonic=cycle_started_monotonic,
            requested_total_paths=requested_total_paths,
            allocated_total_paths=allocated_total_paths,
            clamped_inputs=clamped_inputs,
            mc_input_skipped=mc_input_skipped,
            path_budget_per_input=path_budget_per_input,
        ),
    )
    _write_status(probability_status_path, payload)
    if event_rows:
        _append_probability_event_rows(probability_event_path, event_rows)
    return payload


def _load_probability_fragments(
    *,
    path: Path | None,
    max_age_seconds: float | None,
) -> tuple[tuple[GeneratorFragment, ...], str | None]:
    if path is None:
        return (), None
    max_age = (
        max_age_seconds
        if max_age_seconds is not None
        else 365.0 * 24.0 * 60.0 * 60.0
    )
    try:
        payload = read_probability_fragments(out_path=path, max_age_seconds=max_age)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        return (), f"{type(exc).__name__}: {exc}"
    return payload.fragments, None


def _select_prior_fragments(
    *,
    fragments: Sequence[GeneratorFragment],
    probability_input: ProbabilityInput,
    max_fragment_count: int,
    fragment_error: str | None,
) -> FragmentSelection:
    if fragment_error is not None:
        return FragmentSelection(fragments=(), sparse=True, reason="unavailable")
    return select_fragments_for_input(
        fragments,
        probability_input=probability_input,
        min_fragment_count=DEFAULT_MIN_FRAGMENT_COUNT,
        max_fragment_count=max(1, max_fragment_count),
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


def run_cuda_probability_worker_loop(
    *,
    duckdb_path: Path,
    probability_status_path: Path,
    probability_inputs_path: Path | None = None,
    probability_fragments_path: Path | None = None,
    interval_seconds: float = DEFAULT_GPU_PROBABILITY_INTERVAL_SECONDS,
    limit: int = DEFAULT_GPU_PROBABILITY_LIMIT,
    valid_seconds: int = int(DEFAULT_PROBABILITY_GRID_VALID_SECONDS),
    max_state_age_seconds: float | None = DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS,
    max_input_snapshot_age_seconds: float | None = DEFAULT_INPUT_SNAPSHOT_MAX_AGE_SECONDS,
    probability_event_path: Path | None = None,
    snapshot_poll_seconds: float = 0.1,
    budget: ProbabilityWorkerBudget | None = None,
) -> None:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if snapshot_poll_seconds <= 0:
        raise ValueError("snapshot_poll_seconds must be positive")
    budget = budget or ProbabilityWorkerBudget()
    last_snapshot_fingerprint = _snapshot_fingerprint(probability_inputs_path)
    while True:
        generated_at = datetime.now(timezone.utc)
        cycle_started_monotonic = time.monotonic()
        try:
            payload = run_cuda_probability_worker_cycle(
                duckdb_path=duckdb_path,
                probability_status_path=probability_status_path,
                probability_inputs_path=probability_inputs_path,
                probability_fragments_path=probability_fragments_path,
                limit=limit,
                valid_seconds=valid_seconds,
                max_state_age_seconds=max_state_age_seconds,
                max_input_snapshot_age_seconds=max_input_snapshot_age_seconds,
                probability_event_path=probability_event_path,
                budget=budget,
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
                budget=_budget_diagnostics(
                    budget=budget,
                    cycle_started_monotonic=cycle_started_monotonic,
                    requested_total_paths=0,
                    allocated_total_paths=0,
                    clamped_inputs=0,
                    mc_input_skipped=0,
                    path_budget_per_input=0,
                ),
            )
            _write_status(probability_status_path, payload)
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
        last_snapshot_fingerprint = _sleep_until_next_refresh(
            probability_inputs_path=probability_inputs_path,
            interval_seconds=interval_seconds,
            snapshot_poll_seconds=snapshot_poll_seconds,
            last_snapshot_fingerprint=last_snapshot_fingerprint,
        )


def _sleep_until_next_refresh(
    *,
    probability_inputs_path: Path | None,
    interval_seconds: float,
    snapshot_poll_seconds: float,
    last_snapshot_fingerprint: str | None,
) -> str | None:
    if probability_inputs_path is None:
        time.sleep(interval_seconds)
        return last_snapshot_fingerprint

    deadline = time.monotonic() + interval_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _snapshot_fingerprint(probability_inputs_path)
        time.sleep(min(snapshot_poll_seconds, remaining))
        current_fingerprint = _snapshot_fingerprint(probability_inputs_path)
        if current_fingerprint is not None and current_fingerprint != last_snapshot_fingerprint:
            return current_fingerprint


def _snapshot_fingerprint(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        stat = path.stat()
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    rows = payload.get("rows")
    row_count = len(rows) if isinstance(rows, list) else 0
    generated_at = payload.get("generated_at")
    return f"{generated_at}|{row_count}|{stat.st_mtime_ns}"


def _status_payload(
    *,
    generated_at: datetime,
    rows: list[dict[str, Any]],
    nowcast_rows: list[dict[str, Any]] | None = None,
    skipped: int,
    errors: list[str],
    rows_seen: int,
    rows_written: int,
    last_good_rows: list[dict[str, Any]] | None = None,
    state_override: str | None = None,
    retained_mc_rows: int = 0,
    budget: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "polymarket-probability-runtime-v1",
        "ok": not errors,
        "state": state_override or ("OK" if not errors else "PARTIAL"),
        "error": None if not errors else errors[0],
        "generated_at": generated_at.isoformat(),
        "cached": False,
        "model_version": rows[0]["model_version"] if rows else None,
        "rows": rows,
        "nowcast_rows": nowcast_rows or [],
        "skipped": skipped,
        "errors": errors,
        "rows_seen": rows_seen,
        "rows_written": rows_written,
        "retained_mc_rows": retained_mc_rows,
        "previous_mc_retained": retained_mc_rows > 0,
        "budget": dict(budget or {}),
    }
    payload["lanes"] = _status_lanes(payload)
    payload["latency"] = _status_latency(payload)
    if last_good_rows:
        payload["last_good_rows"] = last_good_rows
    return payload


def _nowcast_row(
    runtime_input: ProbabilityRuntimeInput,
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    probability_input = runtime_input.probability_input
    nowcast = compute_fast_nowcast(
        FastNowcastInput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            asset=_asset_literal(probability_input.asset),
            side=_side_literal(probability_input.side),
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
    return {
        "contract": runtime_input.contract,
        "state_id": probability_input.state_id,
        "contract_id": runtime_input.contract_id,
        "market_slug": runtime_input.market_slug,
        "asset": probability_input.asset,
        "side": probability_input.side,
        "start_ts": runtime_input.start_ts.isoformat(),
        "expiry_ts": runtime_input.expiry_ts.isoformat(),
        "asof_ts": probability_input.asof_ts.isoformat(),
        "threshold": probability_input.threshold,
        "threshold_price": f"{probability_input.threshold:.12g}",
        "settlement_price": probability_input.settlement_price,
        "p_finish": nowcast.p_finish,
        "p_hat": nowcast.p_finish,
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


def _mc_row_from_output(
    *,
    runtime_input: ProbabilityRuntimeInput,
    output: ProbabilityOutput,
    nowcast_row: Mapping[str, Any],
    generated_at: datetime,
    mc_started_ts: datetime,
    mc_finished_ts: datetime,
    valid_seconds: int,
    path_count: int,
    paths_per_seed: int,
    seed_count: int,
    seed: int,
) -> tuple[dict[str, Any], str]:
    probability_input = runtime_input.probability_input
    diagnostics = dict(output.diagnostics)
    diagnostics["path_count"] = path_count
    diagnostics["paths_per_seed"] = paths_per_seed
    diagnostics["seed_count"] = seed_count
    latency = ProbabilityLatencyTrace(
        state_asof_ts=probability_input.asof_ts,
        tick_observed_ts=None,
        worker_received_ts=generated_at,
        mc_started_ts=mc_started_ts,
        mc_finished_ts=mc_finished_ts,
        status_written_ts=mc_finished_ts,
        ui_seen_ts=None,
    )
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
        u_gen=_float(diagnostics.get("u_gen", 0.0), "u_gen"),
        path_count=path_count,
        seed=seed,
        volatility_regime=runtime_input.volatility_regime,
        generator_version=str(
            diagnostics.get("generator_version") or output.model_version
        ),
        model_version=output.model_version,
        training_cutoff_ts=probability_input.asof_ts,
        max_event_ts=probability_input.asof_ts,
        max_observed_ts=probability_input.asof_ts,
        generated_at=generated_at,
        valid_from=generated_at,
        valid_until=generated_at + timedelta(seconds=valid_seconds),
        diagnostics=diagnostics,
    )
    output_id = _output_id(probability_input, output)
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
    row.update(
        {
            "state_id": probability_input.state_id,
            "probability_kind": "MC",
            "backend": "ensemble",
            "seconds_left": probability_input.seconds_left,
            "threshold": probability_input.threshold,
            "threshold_price": f"{probability_input.threshold:.12g}",
            "settlement_price": probability_input.settlement_price,
            "executable_price": probability_input.executable_price,
            "source_age_ms": probability_input.source_age_ms,
            "book_age_ms": probability_input.book_age_ms,
            "latency": diagnostics["latency"],
            "wave_phase": nowcast_row["wave_phase"],
            "wave_score": nowcast_row["wave_score"],
            "wave_reasons": nowcast_row["wave_reasons"],
            "wave_markers": nowcast_row["wave_markers"],
            "dynamic_edge": nowcast_row["dynamic_edge"],
            "dynamic_required_edge": nowcast_row["dynamic_required_edge"],
        }
    )
    row["output_id"] = output_id
    return row, output_id


def _batch_runtime_inputs(
    inputs: Sequence[ProbabilityRuntimeInput],
) -> list[list[ProbabilityRuntimeInput]]:
    groups: dict[tuple[object, ...], list[ProbabilityRuntimeInput]] = {}
    for runtime_input in inputs:
        probability_input = runtime_input.probability_input
        steps = _steps_for_input(probability_input)
        key = (
            probability_input.asset,
            probability_input.asof_ts,
            round(probability_input.settlement_price, 8),
            round(probability_input.sigma_tau, 12),
            int(round(probability_input.seconds_left)),
            steps,
        )
        groups.setdefault(key, []).append(runtime_input)
    return list(groups.values())


def _gpu_seed_count_for_total_paths(path_count: int) -> int:
    if path_count >= 250_000:
        return 5
    if path_count >= 80_000:
        return 4
    return 3


def _path_budget_per_input(
    *,
    input_count: int,
    budget: ProbabilityWorkerBudget,
) -> int:
    if input_count <= 0:
        return 0
    return max(1, budget.max_total_paths // input_count)


def _clamp_path_count(
    requested_path_count: int,
    *,
    path_budget_per_input: int,
) -> tuple[int, bool]:
    if requested_path_count <= 0:
        raise ValueError("requested_path_count must be positive")
    if path_budget_per_input <= 0:
        return 1, True
    path_count = min(requested_path_count, path_budget_per_input)
    return path_count, path_count < requested_path_count


def _budget_diagnostics(
    *,
    budget: ProbabilityWorkerBudget,
    cycle_started_monotonic: float,
    requested_total_paths: int,
    allocated_total_paths: int,
    clamped_inputs: int,
    mc_input_skipped: int,
    path_budget_per_input: int,
) -> dict[str, Any]:
    elapsed_ms = round((time.monotonic() - cycle_started_monotonic) * 1000.0, 3)
    return {
        "worker_mode": budget.worker_mode,
        "generator_policy": budget.generator_policy,
        "cpu_target_percent": budget.cpu_target_percent,
        "max_rss_mb": budget.max_rss_mb,
        "max_cycle_runtime_ms": budget.max_cycle_runtime_ms,
        "max_total_paths": budget.max_total_paths,
        "sustained_breach_cycles": budget.sustained_breach_cycles,
        "fragment_max_rows": budget.fragment_max_rows,
        "cpu_threads": budget.cpu_threads,
        "path_budget_per_input": path_budget_per_input,
        "requested_total_paths": requested_total_paths,
        "allocated_total_paths": allocated_total_paths,
        "clamped_inputs": clamped_inputs,
        "mc_input_skipped": mc_input_skipped,
        "elapsed_ms": elapsed_ms,
        "cycle_runtime_breached": elapsed_ms > budget.max_cycle_runtime_ms,
    }


def _retained_mc_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    require_valid_until: bool = True,
) -> list[dict[str, Any]]:
    retained: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("probability_kind") or "MC") == "NOWCAST":
            continue
        valid_until = _optional_datetime(row.get("valid_until"))
        if require_valid_until and (valid_until is None or valid_until <= now):
            continue
        expiry_ts = _optional_datetime(row.get("expiry_ts"))
        if expiry_ts is None or expiry_ts <= now:
            continue
        retained.append(dict(row))
    return retained


def _merge_missing_retained_mc_rows(
    *,
    fresh_rows: list[dict[str, Any]],
    previous_rows: Sequence[Mapping[str, Any]],
    now: datetime,
    enabled: bool,
) -> tuple[list[dict[str, Any]], int]:
    if not enabled:
        return fresh_rows, 0

    seen = {_retention_key(row) for row in fresh_rows}
    merged = list(fresh_rows)
    retained_count = 0
    for row in _retained_mc_rows(previous_rows, now=now, require_valid_until=False):
        key = _retention_key(row)
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
        retained_count += 1
    return merged, retained_count


def _retention_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    contract_id = row.get("contract_id")
    if contract_id:
        return ("contract_id", str(contract_id))
    state_id = row.get("state_id")
    if state_id:
        return ("state_id", str(state_id))
    return (
        "selection",
        str(row.get("market_slug") or row.get("cache_market_slug") or ""),
        str(row.get("contract") or ""),
        str(row.get("asset") or ""),
        str(row.get("side") or ""),
        str(row.get("start_ts") or row.get("cache_start_ts") or ""),
        str(row.get("expiry_ts") or row.get("cache_expiry_ts") or ""),
    )


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return _parse_datetime(value)
    except (TypeError, ValueError):
        return None


def _asset_literal(value: str) -> Literal["BTC", "ETH"]:
    if value not in {"BTC", "ETH"}:
        raise ValueError("asset must be BTC or ETH")
    return cast(Literal["BTC", "ETH"], value)


def _side_literal(value: str) -> Literal["UP", "DOWN"]:
    if value not in {"UP", "DOWN"}:
        raise ValueError("side must be UP or DOWN")
    return cast(Literal["UP", "DOWN"], value)


def _status_lanes(payload: Mapping[str, Any]) -> dict[str, int]:
    lanes: dict[str, int] = {}
    for row in _status_probability_rows(payload):
        lane = str(row.get("probability_kind") or "MC")
        lanes[lane] = lanes.get(lane, 0) + 1
    return lanes


def _status_latency(payload: Mapping[str, Any]) -> dict[str, float | None]:
    total_lags = [
        lag
        for row in _status_probability_rows(payload)
        for lag in [_row_latency(row, "total_lag_ms")]
        if lag is not None
    ]
    runtimes = [
        runtime
        for row in _status_probability_rows(payload)
        for runtime in [_row_latency(row, "runtime_ms")]
        if runtime is not None
    ]
    return {
        "max_total_lag_ms": max(total_lags) if total_lags else None,
        "avg_total_lag_ms": round(sum(total_lags) / len(total_lags), 3) if total_lags else None,
        "max_runtime_ms": max(runtimes) if runtimes else None,
        "avg_runtime_ms": round(sum(runtimes) / len(runtimes), 3) if runtimes else None,
    }


def _status_probability_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for key in ("rows", "nowcast_rows"):
        raw_rows = payload.get(key)
        if isinstance(raw_rows, Sequence) and not isinstance(raw_rows, (str, bytes)):
            rows.extend(row for row in raw_rows if isinstance(row, Mapping))
    return rows


def _row_latency(row: Mapping[str, Any], field_name: str) -> float | None:
    latency = row.get("latency")
    if not isinstance(latency, Mapping):
        return None
    value = latency.get(field_name)
    if value is None:
        return None
    return float(cast(Any, value))


def _event_payload_from_row(
    *,
    runtime_input: ProbabilityRuntimeInput,
    row: Mapping[str, Any],
    generated_at: datetime,
    output_id: str | None,
) -> dict[str, Any]:
    probability_input = runtime_input.probability_input
    latency = row.get("latency") if isinstance(row.get("latency"), Mapping) else {}
    event_id = _event_id(
        probability_input.state_id,
        probability_input.asof_ts,
        str(row.get("probability_kind") or "MC"),
        generated_at,
    )
    payload: dict[str, Any] = {
        "event_id": event_id,
        "output_id": output_id,
        "state_id": probability_input.state_id,
        "contract_id": runtime_input.contract_id,
        "market_slug": runtime_input.market_slug,
        "asset": probability_input.asset,
        "side": probability_input.side,
        "start_ts": runtime_input.start_ts.isoformat(),
        "expiry_ts": runtime_input.expiry_ts.isoformat(),
        "asof_ts": probability_input.asof_ts.isoformat(),
        "probability_kind": str(row.get("probability_kind") or "MC"),
        "backend": str(row.get("backend") or "cuda"),
        "model_version": str(row.get("model_version") or "cached-grid-v1"),
        "generator_version": _optional_string(row.get("generator_version")),
        "cache_key": _optional_string(row.get("cache_key")),
        "cache_status": _optional_string(row.get("cache_status")),
        "p_finish": _float(row.get("p_finish"), "p_finish"),
        "p_no_touch": _float(row.get("p_no_touch"), "p_no_touch"),
        "z_path": _float(row.get("z_path"), "z_path"),
        "sigma_tau": _float(row.get("sigma_tau"), "sigma_tau"),
        "executable_price": probability_input.executable_price,
        "spread": None,
        "seconds_left": probability_input.seconds_left,
        "wave_phase": str(row.get("wave_phase") or "none"),
        "wave_score": _float(row.get("wave_score", 0.0), "wave_score"),
        "path_count": _optional_event_int(row.get("path_count")),
        "seed": _optional_event_int(row.get("seed")),
        "queue_ms": _latency_value(latency, "queue_ms"),
        "runtime_ms": _latency_value(latency, "runtime_ms"),
        "state_to_status_ms": _latency_value(latency, "state_to_status_ms"),
        "total_lag_ms": _latency_value(latency, "total_lag_ms"),
        "generated_at": generated_at.isoformat(),
        "valid_from": str(row.get("valid_from") or generated_at.isoformat()),
        "valid_until": str(
            row.get("valid_until")
            or (
                generated_at + timedelta(seconds=DEFAULT_PROBABILITY_GRID_VALID_SECONDS)
            ).isoformat()
        ),
        "diagnostics": {
            "source": "cuda-probability-worker",
            "latency": dict(cast(Mapping[str, Any], latency)),
        },
    }
    for key in (
        "effective_weights",
        "generator_summary",
        "generator_runs",
        "effective_generator_values",
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
    ):
        if key in row:
            payload[key] = row[key]
    preview = row.get("simulation_preview")
    if isinstance(preview, Mapping):
        payload["simulation_preview"] = dict(cast(Mapping[str, Any], preview))
    return payload


def _event_id(state_id: str, asof_ts: datetime, kind: str, generated_at: datetime) -> str:
    digest = hashlib.sha256(
        f"{state_id}|{asof_ts.isoformat()}|{kind}|{generated_at.isoformat()}".encode()
    ).hexdigest()
    return f"prob-event-{digest[:24]}"


def _append_probability_event_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False))
            handle.write("\n")


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_event_int(value: object) -> int | None:
    if value is None:
        return None
    return _int(value, "event_int")


def _latency_value(latency: object, field_name: str) -> float | None:
    if not isinstance(latency, Mapping):
        return None
    value = latency.get(field_name)
    if value is None:
        return None
    return float(cast(Any, value))


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
    if schema_version == PROBABILITY_INPUTS_SCHEMA_VERSION:
        row_field = "rows"
        require_market_slug = True
    elif schema_version == HOT_PROBABILITY_INPUTS_SCHEMA_VERSION:
        row_field = "inputs"
        require_market_slug = False
    else:
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
    rows = payload.get(row_field)
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError(f"probability input snapshot {row_field} must be a list")

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
                market_slug=_snapshot_market_slug(row, required=require_market_slug),
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


def _snapshot_market_slug(row: Mapping[str, Any], *, required: bool) -> str:
    if required:
        return _nonempty_str(row.get("market_slug"), "market_slug")
    explicit = _optional_str(row.get("market_slug"), "market_slug")
    if explicit:
        return explicit
    contract_id = _nonempty_str(row.get("contract_id"), "contract_id")
    if ":" in contract_id:
        market_slug = contract_id.split(":", 1)[0]
        if market_slug:
            return market_slug
    return contract_id


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
