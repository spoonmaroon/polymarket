from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, cast

import duckdb

from polymarket_engine.probability.cuda_monte_carlo import (
    run_cuda_monte_carlo_batch as _run_cuda_monte_carlo_batch_impl,
)
from polymarket_engine.probability.cuda_monte_carlo import run_cuda_monte_carlo_multi_seed
from polymarket_engine.probability.cpu_budget import adjust_total_path_budget
from polymarket_engine.probability.cpu_budget import cycle_cpu_percent
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
from polymarket_engine.probability.offload_gate import OffloadDecision
from polymarket_engine.probability.offload_gate import DEFAULT_MAX_PROBABILITY_INPUT_AGE_MS
from polymarket_engine.probability.offload_gate import OffloadGateConfig
from polymarket_engine.probability.offload_gate import OffloadGateInputs
from polymarket_engine.probability.offload_gate import evaluate_offload_readiness
from polymarket_engine.probability.path_policy import runtime_path_count_for_state
from polymarket_engine.probability.pair_coherence import normalize_binary_probability_pairs
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
from polymarket_engine.probability.runtime_inputs import ProbabilityState
from polymarket_engine.probability.runtime_inputs import ThresholdDiagnostics
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine.ops.recovery_manager import RuntimePhase
from polymarket_engine.storage.atomic import durable_replace


DEFAULT_GPU_PROBABILITY_LIMIT = 24
DEFAULT_GPU_PROBABILITY_INTERVAL_SECONDS = 1.0
DEFAULT_INPUT_SNAPSHOT_MAX_AGE_SECONDS = 30.0
PROBABILITY_INPUTS_SCHEMA_VERSION = "polymarket-probability-inputs-v1"
DEFAULT_WORKER_MODE = "ensemble"
DEFAULT_GENERATOR_POLICY = "all_four_every_cycle"
DEFAULT_CPU_TARGET_PERCENT = 15.0
DEFAULT_CPU_SOFT_MAX_PERCENT = 20.0
DEFAULT_MAX_RSS_MB = 512
DEFAULT_MAX_CYCLE_RUNTIME_MS = 10_000
DEFAULT_MAX_TOTAL_PATHS = 320_000
DEFAULT_MIN_TOTAL_PATHS = 4_000
DEFAULT_SUSTAINED_BREACH_CYCLES = 3
DEFAULT_FRAGMENT_MAX_ROWS = 250_000
DEFAULT_MIN_FRAGMENT_COUNT = 2
DEFAULT_CPU_THREADS = 1
DEFAULT_MAX_INPUT_STATE_LAG_MS = DEFAULT_MAX_PROBABILITY_INPUT_AGE_MS
DEFAULT_MIN_SECONDS_LEFT_FOR_MC = 20.0
_RECOVERY_STATUS_GATE_CONFIG = OffloadGateConfig(
    warmup_min_seconds=0,
    required_healthy_cycles=0,
)


@dataclass(frozen=True)
class _InputMcEligibility:
    runtime_input: ProbabilityRuntimeInput
    allowed: bool
    reason_codes: tuple[str, ...]
    input_state_lag_ms: int


@dataclass(frozen=True)
class ProbabilityWorkerBudget:
    worker_mode: str = DEFAULT_WORKER_MODE
    generator_policy: str = DEFAULT_GENERATOR_POLICY
    cpu_target_percent: float = DEFAULT_CPU_TARGET_PERCENT
    cpu_soft_max_percent: float = DEFAULT_CPU_SOFT_MAX_PERCENT
    max_rss_mb: int = DEFAULT_MAX_RSS_MB
    max_cycle_runtime_ms: int = DEFAULT_MAX_CYCLE_RUNTIME_MS
    max_total_paths: int = DEFAULT_MAX_TOTAL_PATHS
    configured_max_total_paths: int | None = None
    min_total_paths: int = DEFAULT_MIN_TOTAL_PATHS
    sustained_breach_cycles: int = DEFAULT_SUSTAINED_BREACH_CYCLES
    fragment_max_rows: int = DEFAULT_FRAGMENT_MAX_ROWS
    use_prior_fragments: bool = False
    cpu_threads: int = DEFAULT_CPU_THREADS

    def __post_init__(self) -> None:
        if self.worker_mode == "":
            raise ValueError("worker_mode must not be empty")
        if self.generator_policy == "":
            raise ValueError("generator_policy must not be empty")
        if self.cpu_target_percent <= 0:
            raise ValueError("cpu_target_percent must be positive")
        if self.cpu_soft_max_percent < self.cpu_target_percent:
            raise ValueError("cpu_soft_max_percent must be >= cpu_target_percent")
        if self.max_rss_mb <= 0:
            raise ValueError("max_rss_mb must be positive")
        if self.max_cycle_runtime_ms <= 0:
            raise ValueError("max_cycle_runtime_ms must be positive")
        if self.max_total_paths <= 0:
            raise ValueError("max_total_paths must be positive")
        if self.configured_max_total_paths is not None:
            if self.configured_max_total_paths <= 0:
                raise ValueError("configured_max_total_paths must be positive")
            if self.configured_max_total_paths < self.max_total_paths:
                raise ValueError("configured_max_total_paths must be >= max_total_paths")
        if self.min_total_paths <= 0 or self.min_total_paths > self.max_total_paths:
            raise ValueError("min_total_paths must be positive and <= max_total_paths")
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


def _offload_decision_from_override(
    value: Mapping[str, Any] | None,
    *,
    generated_at: datetime,
) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "schema_version": "polymarket-offload-runtime-v1",
        "generated_at": generated_at.isoformat(),
        "offload_allowed": bool(value.get("offload_allowed")),
        "reason_codes": list(value.get("reason_codes") or []),
        "recommended_worker_mode": str(
            value.get("recommended_worker_mode") or "disabled"
        ),
        "recommended_max_total_paths": int(
            value.get("recommended_max_total_paths") or 0
        ),
    }


def _offload_decision_from_inputs(
    *,
    inputs: Sequence[ProbabilityRuntimeInput],
    budget: ProbabilityWorkerBudget,
    generated_at: datetime,
    recovery_status_path: Path,
) -> dict[str, Any]:
    if not inputs:
        return _blocked_offload_decision(
            reason_codes=("no_probability_inputs",),
            recommended_worker_mode="disabled",
            generated_at=generated_at,
        )

    recovery_status = _read_recovery_status(recovery_status_path)
    runtime_phase = recovery_status.runtime_phase
    gate_inputs = OffloadGateInputs(
        runtime_phase=runtime_phase,
        uptime_seconds=recovery_status.uptime_seconds,
        consecutive_healthy_cycles=recovery_status.consecutive_healthy_cycles,
        price_age_ms=0,
        orderbook_age_ms=0,
        probability_input_age_ms=0,
        volatility_age_ms=0,
        target_status_age_ms=0,
        sigma_tau_valid=True,
        sigma_tau_age_ms=0,
        k_stable=True,
        api_status="OK",
        normalized_health_status="OK",
        duckdb_status="OK",
        websocket_status="OK",
        cpu_percent=None,
        memory_mb=None,
        queue_length=None,
        recent_api_blocked=False,
        recent_decode_error=False,
        configured_max_total_paths=(
            budget.configured_max_total_paths
            if budget.configured_max_total_paths is not None
            else budget.max_total_paths
        ),
        min_total_paths=budget.min_total_paths,
    )
    decision = evaluate_offload_readiness(
        gate_inputs,
        _RECOVERY_STATUS_GATE_CONFIG,
    )
    reason_codes = _dedupe_reasons(
        (*decision.reason_codes, *recovery_status.reasons)
    )
    if reason_codes:
        worker_mode = (
            "nowcast_only"
            if not _hard_offload_reasons(reason_codes)
            else "disabled"
        )
        return _blocked_offload_decision(
            reason_codes=reason_codes,
            recommended_worker_mode=worker_mode,
            generated_at=generated_at,
        )
    return _offload_decision_payload(decision, generated_at=generated_at)


@dataclass(frozen=True)
class _RecoveryStatus:
    runtime_phase: RuntimePhase
    uptime_seconds: float
    consecutive_healthy_cycles: int
    reasons: tuple[str, ...]


def _read_recovery_status(path: Path) -> _RecoveryStatus:
    try:
        raw_payload = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return _missing_recovery_status()
    try:
        payload = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _invalid_recovery_status()
    if not isinstance(payload, Mapping):
        return _invalid_recovery_status()
    runtime_phase = _recovery_phase(payload.get("runtime_phase"))
    try:
        reasons = _string_list(payload.get("reasons", []), "reasons")
        uptime_seconds = _nonnegative_float(
            payload.get("uptime_seconds", 0.0), "uptime_seconds"
        )
        consecutive_healthy_cycles = _nonnegative_int(
            payload.get("consecutive_healthy_cycles", 0),
            "consecutive_healthy_cycles",
        )
    except (TypeError, ValueError):
        return _invalid_recovery_status()
    if payload.get("ready") is not True and "runtime_not_ready" not in reasons:
        reasons.append("runtime_not_ready")
    return _RecoveryStatus(
        runtime_phase=runtime_phase,
        uptime_seconds=uptime_seconds,
        consecutive_healthy_cycles=consecutive_healthy_cycles,
        reasons=tuple(reasons),
    )


def _missing_recovery_status() -> _RecoveryStatus:
    return _RecoveryStatus(
        runtime_phase=RuntimePhase.WARMING,
        uptime_seconds=0.0,
        consecutive_healthy_cycles=0,
        reasons=("recovery_status_missing",),
    )


def _invalid_recovery_status() -> _RecoveryStatus:
    return _RecoveryStatus(
        runtime_phase=RuntimePhase.WARMING,
        uptime_seconds=0.0,
        consecutive_healthy_cycles=0,
        reasons=("recovery_status_invalid",),
    )


def _recovery_phase(value: object) -> RuntimePhase:
    try:
        return RuntimePhase(str(value))
    except ValueError:
        return RuntimePhase.WARMING


def _runtime_input_age_ms(
    runtime_input: ProbabilityRuntimeInput,
    generated_at: datetime,
) -> int:
    return max(
        0,
        int((generated_at - runtime_input.probability_input.asof_ts).total_seconds() * 1000),
    )


def _input_mc_eligibility(
    runtime_input: ProbabilityRuntimeInput,
    *,
    generated_at: datetime,
    gate_config: OffloadGateConfig,
) -> _InputMcEligibility:
    probability_input = runtime_input.probability_input
    input_state_lag_ms = _runtime_input_age_ms(runtime_input, generated_at)
    effective_seconds_left = max(
        0.0,
        (runtime_input.expiry_ts - generated_at).total_seconds(),
    )
    reasons: list[str] = list(_runtime_input_mc_block_reasons(runtime_input))
    if effective_seconds_left <= 0.0 or probability_input.seconds_left <= 0:
        reasons.append("probability_input_expired")
    elif effective_seconds_left < DEFAULT_MIN_SECONDS_LEFT_FOR_MC:
        reasons.append("near_expiry")
    if input_state_lag_ms > DEFAULT_MAX_INPUT_STATE_LAG_MS:
        reasons.append("probability_inputs_stale")
    if probability_input.source_age_ms > gate_config.max_price_age_ms:
        reasons.append("price_stale")
    if probability_input.book_age_ms > gate_config.max_orderbook_age_ms:
        reasons.append("orderbook_stale")
    if runtime_input.sigma_age_ms > gate_config.max_sigma_tau_age_ms:
        reasons.append("sigma_stale")
    return _InputMcEligibility(
        runtime_input=runtime_input,
        allowed=not reasons,
        reason_codes=_dedupe_reasons(tuple(reasons)),
        input_state_lag_ms=input_state_lag_ms,
    )


def _input_mc_eligibilities(
    inputs: Sequence[ProbabilityRuntimeInput],
    *,
    generated_at: datetime,
    gate_config: OffloadGateConfig,
) -> tuple[_InputMcEligibility, ...]:
    return tuple(
        _input_mc_eligibility(
            runtime_input,
            generated_at=generated_at,
            gate_config=gate_config,
        )
        for runtime_input in inputs
    )


def _eligible_mc_inputs(
    eligibilities: Sequence[_InputMcEligibility],
) -> tuple[ProbabilityRuntimeInput, ...]:
    blocked_contract_ids = {
        eligibility.runtime_input.contract_id
        for eligibility in eligibilities
        if not eligibility.allowed
    }
    return tuple(
        eligibility.runtime_input
        for eligibility in eligibilities
        if eligibility.allowed
        and eligibility.runtime_input.contract_id not in blocked_contract_ids
    )


def _offload_input_diagnostics(
    eligibilities: Sequence[_InputMcEligibility],
) -> dict[str, Any]:
    blocked = [eligibility for eligibility in eligibilities if not eligibility.allowed]
    diagnostics = {
        "input_count": len(eligibilities),
        "mc_eligible_input_count": len(eligibilities) - len(blocked),
        "blocked_input_count": len(blocked),
        "max_input_state_lag_ms": max(
            (eligibility.input_state_lag_ms for eligibility in eligibilities),
            default=None,
        ),
        "max_source_age_ms": max(
            (
                eligibility.runtime_input.probability_input.source_age_ms
                for eligibility in eligibilities
            ),
            default=None,
        ),
        "max_book_age_ms": max(
            (
                eligibility.runtime_input.probability_input.book_age_ms
                for eligibility in eligibilities
            ),
            default=None,
        ),
        "min_seconds_left": min(
            (
                eligibility.runtime_input.probability_input.seconds_left
                for eligibility in eligibilities
            ),
            default=None,
        ),
        "blocked_inputs": [
            {
                "state_id": eligibility.runtime_input.probability_input.state_id,
                "contract_id": eligibility.runtime_input.contract_id,
                "market_slug": eligibility.runtime_input.market_slug,
                "asset": eligibility.runtime_input.probability_input.asset,
                "side": eligibility.runtime_input.probability_input.side,
                "reason_codes": list(eligibility.reason_codes),
                "input_state_lag_ms": eligibility.input_state_lag_ms,
                "source_age_ms": eligibility.runtime_input.probability_input.source_age_ms,
                "book_age_ms": eligibility.runtime_input.probability_input.book_age_ms,
                "seconds_left": eligibility.runtime_input.probability_input.seconds_left,
            }
            for eligibility in blocked[:12]
        ],
    }
    return diagnostics


def _with_offload_input_diagnostics(
    payload: dict[str, Any],
    eligibilities: Sequence[_InputMcEligibility],
) -> dict[str, Any]:
    diagnostics = _offload_input_diagnostics(eligibilities)
    merged = dict(payload)
    merged.update(diagnostics)
    merged["input_diagnostics"] = diagnostics
    return merged


def _eligibility_reason_codes(
    eligibilities: Sequence[_InputMcEligibility],
) -> tuple[str, ...]:
    return _dedupe_reasons(
        tuple(
            reason
            for eligibility in eligibilities
            for reason in eligibility.reason_codes
        )
    )


def _runtime_input_mc_block_reasons(
    runtime_input: ProbabilityRuntimeInput,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if runtime_input.probability_state != "READY":
        reasons.append("runtime_not_ready")
    if not runtime_input.k_stable:
        reasons.append("k_unstable")
    if not runtime_input.sigma_valid:
        reasons.append("sigma_invalid")
    if not runtime_input.offload_allowed:
        reasons.extend(runtime_input.block_reasons or ("runtime_not_ready",))
    reasons.extend(runtime_input.block_reasons)
    return _dedupe_reasons(tuple(reasons))


def _dedupe_reasons(reasons: tuple[str, ...]) -> tuple[str, ...]:
    deduped: list[str] = []
    for reason in reasons:
        if reason and reason not in deduped:
            deduped.append(reason)
    return tuple(deduped)


def _hard_offload_reasons(reasons: tuple[str, ...]) -> bool:
    return bool(
        {
            "sigma_invalid",
            "k_unstable",
            "duckdb_unhealthy",
            "price_stale",
            "orderbook_stale",
            "probability_inputs_stale",
            "volatility_stale",
            "target_stale",
            "sigma_stale",
            "api_unhealthy",
            "normalized_health_unhealthy",
            "websocket_unhealthy",
            "api_blocked_recent",
            "decode_error_recent",
        }.intersection(reasons)
    )


def _blocked_offload_decision(
    *,
    reason_codes: tuple[str, ...],
    recommended_worker_mode: str,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": "polymarket-offload-runtime-v1",
        "generated_at": generated_at.isoformat(),
        "offload_allowed": False,
        "reason_codes": list(reason_codes),
        "recommended_worker_mode": recommended_worker_mode,
        "recommended_max_total_paths": 0,
    }


def _offload_decision_payload(
    decision: OffloadDecision,
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": "polymarket-offload-runtime-v1",
        "generated_at": generated_at.isoformat(),
        "offload_allowed": decision.offload_allowed,
        "reason_codes": list(decision.reason_codes),
        "recommended_worker_mode": decision.recommended_worker_mode,
        "recommended_max_total_paths": decision.recommended_max_total_paths,
    }


def _write_offload_status(path: Path, payload: Mapping[str, Any]) -> None:
    _write_status(path, dict(payload))


def run_cuda_probability_worker_cycle(
    *,
    duckdb_path: Path,
    probability_status_path: Path,
    probability_inputs_path: Path | None = None,
    probability_fragments_path: Path | None = None,
    recovery_status_path: Path | None = None,
    offload_status_path: Path | None = None,
    limit: int = DEFAULT_GPU_PROBABILITY_LIMIT,
    valid_seconds: int = int(DEFAULT_PROBABILITY_GRID_VALID_SECONDS),
    max_state_age_seconds: float | None = DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS,
    max_input_snapshot_age_seconds: float | None = DEFAULT_INPUT_SNAPSHOT_MAX_AGE_SECONDS,
    probability_event_path: Path | None = None,
    budget: ProbabilityWorkerBudget | None = None,
    offload_decision_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if valid_seconds <= 0:
        raise ValueError("valid_seconds must be positive")

    cycle_started_monotonic = time.monotonic()
    cycle_started_process = time.process_time()
    budget = budget or ProbabilityWorkerBudget()
    requested_total_paths = 0
    allocated_total_paths = 0
    clamped_inputs = 0
    mc_input_skipped = 0
    path_budget_per_input = 0
    previous_rows = _read_status_rows(probability_status_path)
    recovery_status_path = recovery_status_path or probability_status_path.with_name(
        "recovery_status.json"
    )
    offload_status_path = offload_status_path or probability_status_path.with_name(
        "offload_status.json"
    )
    probability_event_path = probability_event_path or probability_status_path.with_name(
        "probability-events.jsonl"
    )
    generated_at = datetime.now(timezone.utc)
    retained_mc_rows = _retained_mc_rows(previous_rows, now=generated_at)
    rows: list[dict[str, Any]] = []
    nowcast_rows: list[dict[str, Any]] = []
    nowcast_by_state_id: dict[str, dict[str, Any]] = {}
    event_rows: list[dict[str, Any]] = []
    mc_output_ids_by_state_id: dict[str, str] = {}
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
        input_error_offload_decision = _blocked_offload_decision(
            reason_codes=("probability_input_unavailable",),
            recommended_worker_mode="disabled",
            generated_at=generated_at,
        )
        _write_offload_status(offload_status_path, input_error_offload_decision)
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
                    cycle_started_process=cycle_started_process,
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
            last_good_rows=None,
            budget=_budget_diagnostics(
                budget=budget,
                cycle_started_monotonic=cycle_started_monotonic,
                cycle_started_process=cycle_started_process,
                requested_total_paths=requested_total_paths,
                allocated_total_paths=allocated_total_paths,
                clamped_inputs=clamped_inputs,
                mc_input_skipped=mc_input_skipped,
                path_budget_per_input=path_budget_per_input,
            ),
        )
        _write_status(probability_status_path, payload)
        return payload

    offload_decision: dict[str, Any] | None = _offload_decision_from_override(
        offload_decision_override,
        generated_at=generated_at,
    )
    if offload_decision is None:
        offload_decision = _offload_decision_from_inputs(
            inputs=inputs,
            budget=budget,
            generated_at=generated_at,
            recovery_status_path=recovery_status_path,
        )
    assert offload_decision is not None
    gate_config = OffloadGateConfig()
    eligibilities = _input_mc_eligibilities(
        inputs,
        generated_at=generated_at,
        gate_config=gate_config,
    )
    offload_decision = _with_offload_input_diagnostics(offload_decision, eligibilities)

    global_offload_allowed = bool(offload_decision["offload_allowed"])
    mc_inputs = _eligible_mc_inputs(eligibilities) if global_offload_allowed else ()
    if len(mc_inputs) > budget.max_total_paths:
        mc_input_skipped = len(mc_inputs) - budget.max_total_paths
        mc_inputs = mc_inputs[: budget.max_total_paths]
    path_budget_per_input = _path_budget_per_input(
        input_count=len(mc_inputs),
        budget=budget,
    )
    if (not global_offload_allowed or not mc_inputs) and not offload_decision.get(
        "reason_codes"
    ):
        offload_decision["reason_codes"] = list(_eligibility_reason_codes(eligibilities))
    _write_offload_status(offload_status_path, offload_decision)

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
                    cycle_started_process=cycle_started_process,
                    requested_total_paths=requested_total_paths,
                    allocated_total_paths=allocated_total_paths,
                    clamped_inputs=clamped_inputs,
                    mc_input_skipped=mc_input_skipped,
                    path_budget_per_input=path_budget_per_input,
                ),
            )
            _write_status(probability_status_path, payload)
            return payload

    eligibility_by_state_id = {
        eligibility.runtime_input.probability_input.state_id: eligibility
        for eligibility in eligibilities
    }
    for runtime_input in inputs:
        nowcast_row = _nowcast_row(runtime_input, generated_at=generated_at)
        eligibility = eligibility_by_state_id.get(runtime_input.probability_input.state_id)
        if eligibility is not None and not eligibility.allowed:
            nowcast_row["offload_allowed"] = False
            nowcast_row["block_reasons"] = list(eligibility.reason_codes)
        nowcast_rows.append(nowcast_row)
    nowcast_rows = normalize_binary_probability_pairs(nowcast_rows)
    runtime_inputs_by_state_id = {
        runtime_input.probability_input.state_id: runtime_input
        for runtime_input in inputs
    }
    nowcast_by_state_id = {
        str(row["state_id"]): row
        for row in nowcast_rows
        if isinstance(row.get("state_id"), str)
    }
    for nowcast_row in nowcast_rows:
        nowcast_runtime_input = runtime_inputs_by_state_id.get(
            str(nowcast_row.get("state_id") or "")
        )
        if nowcast_runtime_input is None:
            continue
        event_rows.append(
            _event_payload_from_row(
                runtime_input=nowcast_runtime_input,
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
        nowcast_payload = _status_payload(
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
                cycle_started_process=cycle_started_process,
                requested_total_paths=requested_total_paths,
                allocated_total_paths=allocated_total_paths,
                clamped_inputs=clamped_inputs,
                mc_input_skipped=mc_input_skipped,
                path_budget_per_input=path_budget_per_input,
            ),
        )
        nowcast_payload["offload"] = offload_decision
        _write_status(
            probability_status_path,
            nowcast_payload,
        )

    if not global_offload_allowed or not mc_inputs:
        blocked_rows, _ = _merge_missing_retained_mc_rows(
            fresh_rows=retained_mc_rows,
            previous_rows=previous_rows,
            now=generated_at,
            enabled=True,
        )
        reason_codes = tuple(offload_decision.get("reason_codes") or ())
        if not reason_codes:
            reason_codes = _eligibility_reason_codes(eligibilities)
            offload_decision["reason_codes"] = list(reason_codes)
        payload = _status_payload(
            generated_at=generated_at,
            rows=blocked_rows or nowcast_rows,
            nowcast_rows=nowcast_rows,
            skipped=quality_skipped,
            errors=[],
            rows_seen=len(inputs),
            rows_written=0,
            last_good_rows=blocked_rows or previous_rows or None,
            state_override="OFFLOAD_BLOCKED",
            retained_mc_rows=len(blocked_rows),
            budget=_budget_diagnostics(
                budget=budget,
                cycle_started_monotonic=cycle_started_monotonic,
                cycle_started_process=cycle_started_process,
                requested_total_paths=requested_total_paths,
                allocated_total_paths=allocated_total_paths,
                clamped_inputs=clamped_inputs,
                mc_input_skipped=mc_input_skipped,
                path_budget_per_input=path_budget_per_input,
            ),
        )
        payload["offload"] = offload_decision
        _write_status(probability_status_path, payload)
        return payload

    prior_fragments: tuple[GeneratorFragment, ...] = ()
    prior_fragment_error: str | None = None
    if budget.use_prior_fragments:
        prior_fragments, prior_fragment_error = _load_probability_fragments(
            path=probability_fragments_path,
            max_age_seconds=max_input_snapshot_age_seconds,
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
        generator_count = _generator_count_for_budget(budget)
        requested_total_paths += requested_path_count * len(group) * generator_count
        path_count, was_clamped = _clamp_path_count(
            requested_path_count,
            path_budget_per_input=path_budget_per_input,
        )
        if was_clamped:
            clamped_inputs += len(group)
        seed_count = min(_gpu_seed_count_for_total_paths(path_count), path_count)
        paths_per_seed = max(1, path_count // seed_count)
        path_count = paths_per_seed * seed_count
        allocated_total_paths += path_count * len(group) * generator_count
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
                    enabled=budget.use_prior_fragments,
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
                        prior_fragments_enabled=budget.use_prior_fragments,
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
            mc_output_ids_by_state_id[output.state_id] = output_id

    mc_rows = rows
    rows_by_contract_id: dict[str, dict[str, Any]] = {}
    for row in nowcast_rows:
        contract_id = str(row.get("contract_id") or "")
        if contract_id:
            existing = rows_by_contract_id.get(contract_id)
            if existing is not None and _is_blocked_runtime_row(existing):
                continue
            rows_by_contract_id[contract_id] = row
    for row in mc_rows:
        contract_id = str(row.get("contract_id") or "")
        if contract_id:
            existing = rows_by_contract_id.get(contract_id)
            if existing is not None and _is_blocked_runtime_row(existing):
                continue
            rows_by_contract_id[contract_id] = row
    rows = list(rows_by_contract_id.values())

    rows, partial_retained_mc_rows = _merge_missing_retained_mc_rows(
        fresh_rows=rows,
        previous_rows=previous_rows,
        now=generated_at,
        enabled=quality_skipped > 0 and bool(rows),
    )
    rows = normalize_binary_probability_pairs(rows)
    has_nowcast_rows = any(
        str(row.get("probability_kind") or "MC") == "NOWCAST"
        for row in rows
    )
    for row in rows:
        state_id = str(row.get("state_id") or "")
        event_output_id = mc_output_ids_by_state_id.get(state_id)
        event_runtime_input = runtime_inputs_by_state_id.get(state_id)
        if event_output_id is None or event_runtime_input is None:
            continue
        event_rows.append(
            _event_payload_from_row(
                runtime_input=event_runtime_input,
                row=row,
                generated_at=generated_at,
                output_id=event_output_id,
            )
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
        state_override="NOWCAST" if has_nowcast_rows else None,
        retained_mc_rows=partial_retained_mc_rows,
        budget=_budget_diagnostics(
            budget=budget,
            cycle_started_monotonic=cycle_started_monotonic,
            cycle_started_process=cycle_started_process,
            requested_total_paths=requested_total_paths,
            allocated_total_paths=allocated_total_paths,
            clamped_inputs=clamped_inputs,
            mc_input_skipped=mc_input_skipped,
            path_budget_per_input=path_budget_per_input,
        ),
    )
    payload["offload"] = offload_decision
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
    enabled: bool,
) -> FragmentSelection:
    if not enabled:
        return FragmentSelection(
            fragments=(),
            sparse=False,
            reason="disabled_uncalibrated_live_prior",
        )
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
    prior_fragments_enabled: bool,
) -> ProbabilityOutput:
    diagnostics = dict(output.diagnostics)
    diagnostics.update(
        {
            "prior_fragment_enabled": prior_fragments_enabled,
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
    recovery_status_path: Path | None = None,
    offload_status_path: Path | None = None,
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
    effective_max_total_paths = budget.max_total_paths
    last_snapshot_fingerprint = _snapshot_fingerprint(probability_inputs_path)
    while True:
        generated_at = datetime.now(timezone.utc)
        cycle_started_monotonic = time.monotonic()
        cycle_started_process = time.process_time()
        loop_budget = replace(
            budget,
            max_total_paths=effective_max_total_paths,
            configured_max_total_paths=budget.max_total_paths,
        )
        try:
            payload = run_cuda_probability_worker_cycle(
                duckdb_path=duckdb_path,
                probability_status_path=probability_status_path,
                probability_inputs_path=probability_inputs_path,
                probability_fragments_path=probability_fragments_path,
                recovery_status_path=recovery_status_path,
                offload_status_path=offload_status_path,
                limit=limit,
                valid_seconds=valid_seconds,
                max_state_age_seconds=max_state_age_seconds,
                max_input_snapshot_age_seconds=max_input_snapshot_age_seconds,
                probability_event_path=probability_event_path,
                budget=loop_budget,
            )
        except duckdb.Error as exc:
            _write_offload_status(
                offload_status_path or probability_status_path.with_name("offload_status.json"),
                _blocked_offload_decision(
                    reason_codes=("duckdb_unhealthy",),
                    recommended_worker_mode="disabled",
                    generated_at=generated_at,
                ),
            )
            payload = _status_payload(
                generated_at=generated_at,
                rows=[],
                skipped=0,
                errors=[f"probability worker duckdb unavailable: {type(exc).__name__}: {exc}"],
                rows_seen=0,
                rows_written=0,
                last_good_rows=_read_status_rows(probability_status_path),
                budget=_budget_diagnostics(
                    budget=loop_budget,
                    cycle_started_monotonic=cycle_started_monotonic,
                    cycle_started_process=cycle_started_process,
                    requested_total_paths=0,
                    allocated_total_paths=0,
                    clamped_inputs=0,
                    mc_input_skipped=0,
                    path_budget_per_input=0,
                ),
            )
            _write_status(probability_status_path, payload)
        budget_payload = payload.get("budget")
        cpu_percent: float | None = None
        cycle_runtime_breached = True
        if isinstance(budget_payload, Mapping):
            allocated_paths = int(budget_payload.get("allocated_total_paths") or 0)
            raw_cpu_percent = budget_payload.get("cpu_percent")
            if allocated_paths > 0 and raw_cpu_percent is not None:
                cpu_percent = float(raw_cpu_percent)
            raw_cycle_runtime_breached = budget_payload.get("cycle_runtime_breached")
            if isinstance(raw_cycle_runtime_breached, bool):
                cycle_runtime_breached = raw_cycle_runtime_breached
        adjustment = adjust_total_path_budget(
            current_total_paths=effective_max_total_paths,
            configured_max_total_paths=budget.max_total_paths,
            min_total_paths=budget.min_total_paths,
            cpu_percent=cpu_percent,
            target_percent=budget.cpu_target_percent,
            soft_max_percent=budget.cpu_soft_max_percent,
            cycle_runtime_breached=cycle_runtime_breached,
        )
        effective_max_total_paths = adjustment.next_total_paths
        if isinstance(budget_payload, Mapping):
            payload["budget"] = {
                **dict(budget_payload),
                "next_max_total_paths": adjustment.next_total_paths,
                "cpu_budget_adjustment_reason": adjustment.reason,
            }
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
        "probability_state": runtime_input.probability_state,
        "k_stable": runtime_input.k_stable,
        "sigma_valid": runtime_input.sigma_valid,
        "sigma_age_ms": runtime_input.sigma_age_ms,
        "offload_allowed": runtime_input.offload_allowed,
        "block_reasons": list(runtime_input.block_reasons),
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
    if runtime_input.threshold_diagnostics is not None:
        row["threshold_diagnostics"] = _threshold_diagnostics_to_json_dict(
            runtime_input.threshold_diagnostics
        )
    return row


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
    effective_path_count = int(diagnostics.get("path_count", path_count))
    paths_per_generator = int(diagnostics.get("paths_per_generator", path_count))
    generator_count = int(
        diagnostics.get(
            "generator_count",
            4 if output.model_version == "ensemble-v1" else 1,
        )
    )
    diagnostics["path_count"] = effective_path_count
    diagnostics["paths_per_generator"] = paths_per_generator
    diagnostics["generator_count"] = generator_count
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
        "path_count": effective_path_count,
        "paths_per_generator": paths_per_generator,
        "generator_count": generator_count,
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
        path_count=effective_path_count,
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
            "flags": list(runtime_input.flags) if runtime_input.flags else ["OK"],
            "probability_state": runtime_input.probability_state,
            "prior_fragment_enabled": bool(
                diagnostics.get("prior_fragment_enabled", False)
            ),
            "k_stable": runtime_input.k_stable,
            "sigma_valid": runtime_input.sigma_valid,
            "sigma_age_ms": runtime_input.sigma_age_ms,
            "offload_allowed": runtime_input.offload_allowed,
            "block_reasons": list(runtime_input.block_reasons),
            "path_count": effective_path_count,
            "paths_per_generator": paths_per_generator,
            "generator_count": generator_count,
            "latency": diagnostics["latency"],
            "wave_phase": nowcast_row["wave_phase"],
            "wave_score": nowcast_row["wave_score"],
            "wave_reasons": nowcast_row["wave_reasons"],
            "wave_markers": nowcast_row["wave_markers"],
            "dynamic_edge": nowcast_row["dynamic_edge"],
            "dynamic_required_edge": nowcast_row["dynamic_required_edge"],
        }
    )
    if runtime_input.threshold_diagnostics is not None:
        row["threshold_diagnostics"] = _threshold_diagnostics_to_json_dict(
            runtime_input.threshold_diagnostics
        )
    row["output_id"] = output_id
    return row, output_id


def _is_blocked_runtime_row(row: Mapping[str, Any]) -> bool:
    return (
        row.get("probability_state") != "READY"
        or row.get("k_stable") is False
        or row.get("sigma_valid") is False
        or row.get("offload_allowed") is False
        or bool(row.get("block_reasons"))
    )


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
    generator_count = _generator_count_for_budget(budget)
    return max(1, budget.max_total_paths // (input_count * generator_count))


def _generator_count_for_budget(budget: ProbabilityWorkerBudget) -> int:
    return 4 if budget.worker_mode == "ensemble" else 1


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
    cycle_started_process: float,
    requested_total_paths: int,
    allocated_total_paths: int,
    clamped_inputs: int,
    mc_input_skipped: int,
    path_budget_per_input: int,
) -> dict[str, Any]:
    end_monotonic = time.monotonic()
    end_process = time.process_time()
    elapsed_ms = round((end_monotonic - cycle_started_monotonic) * 1000.0, 3)
    cpu_percent = cycle_cpu_percent(
        start_process_seconds=cycle_started_process,
        end_process_seconds=end_process,
        start_monotonic_seconds=cycle_started_monotonic,
        end_monotonic_seconds=end_monotonic,
    )
    configured_max_total_paths = (
        budget.configured_max_total_paths
        if budget.configured_max_total_paths is not None
        else budget.max_total_paths
    )
    return {
        "worker_mode": budget.worker_mode,
        "generator_policy": budget.generator_policy,
        "cpu_target_percent": budget.cpu_target_percent,
        "cpu_soft_max_percent": budget.cpu_soft_max_percent,
        "cpu_percent": cpu_percent,
        "max_rss_mb": budget.max_rss_mb,
        "max_cycle_runtime_ms": budget.max_cycle_runtime_ms,
        "max_total_paths": configured_max_total_paths,
        "effective_max_total_paths": budget.max_total_paths,
        "min_total_paths": budget.min_total_paths,
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
        "paths_per_generator": _optional_event_int(row.get("paths_per_generator")),
        "generator_count": _optional_event_int(row.get("generator_count")),
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
        "prior_fragment_enabled",
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
                probability_state=_probability_state(row.get("probability_state")),
                k_stable=_optional_bool(row.get("k_stable"), "k_stable", default=True),
                threshold_diagnostics=_optional_threshold_diagnostics(
                    row.get("threshold_diagnostics")
                ),
                sigma_tau=_optional_float(row.get("sigma_tau"), "sigma_tau"),
                sigma_valid=_optional_bool(row.get("sigma_valid"), "sigma_valid", default=True),
                sigma_age_ms=_nonnegative_int(row.get("sigma_age_ms", 0), "sigma_age_ms"),
                last_sigma_update_ts=_optional_datetime(row.get("last_sigma_update_ts")),
                short_vol=_optional_float(row.get("short_vol"), "short_vol"),
                medium_vol=_optional_float(row.get("medium_vol"), "medium_vol"),
                long_vol=_optional_float(row.get("long_vol"), "long_vol"),
                volatility_floor_applied=_optional_bool(
                    row.get("volatility_floor_applied"),
                    "volatility_floor_applied",
                    default=False,
                ),
                regime_multiplier_applied=_optional_bool(
                    row.get("regime_multiplier_applied"),
                    "regime_multiplier_applied",
                    default=False,
                ),
                failure_reason=_optional_str(row.get("failure_reason"), "failure_reason"),
                input_sample_count=_nonnegative_int(
                    row.get("input_sample_count", 0),
                    "input_sample_count",
                ),
                offload_allowed=_optional_bool(
                    row.get("offload_allowed"),
                    "offload_allowed",
                    default=True,
                ),
                block_reasons=tuple(
                    _string_list(row.get("block_reasons", []), "block_reasons")
                ),
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


def _probability_state(value: object) -> ProbabilityState:
    if value is None:
        return "READY"
    if value not in {"READY", "BLOCKED", "BLOCKED_OR_STALE"}:
        raise ValueError("probability_state must be READY, BLOCKED, or BLOCKED_OR_STALE")
    return cast(ProbabilityState, value)


def _optional_bool(value: object, field_name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _threshold_diagnostics_to_json_dict(
    diagnostics: ThresholdDiagnostics,
) -> dict[str, Any]:
    return {
        "contract_id": diagnostics.contract_id,
        "market_slug": diagnostics.market_slug,
        "asset": diagnostics.asset,
        "side": diagnostics.side,
        "K": diagnostics.K,
        "K_source": diagnostics.K_source,
        "rule_hash": diagnostics.rule_hash,
        "timestamp": diagnostics.timestamp.isoformat(),
        "previous_K": diagnostics.previous_K,
        "new_K": diagnostics.new_K,
        "reason_for_change": diagnostics.reason_for_change,
    }


def _optional_threshold_diagnostics(value: object) -> ThresholdDiagnostics | None:
    if value is None:
        return None
    payload = _mapping(value, "threshold_diagnostics")
    return ThresholdDiagnostics(
        contract_id=_nonempty_str(payload.get("contract_id"), "contract_id"),
        market_slug=_nonempty_str(payload.get("market_slug"), "market_slug"),
        asset=_nonempty_str(payload.get("asset"), "asset"),
        side=_nonempty_str(payload.get("side"), "side"),
        K=_float(payload.get("K"), "K"),
        K_source=_optional_str(payload.get("K_source"), "K_source"),
        rule_hash=_nonempty_str(payload.get("rule_hash"), "rule_hash"),
        timestamp=_parse_datetime(payload.get("timestamp")),
        previous_K=_optional_float(payload.get("previous_K"), "previous_K"),
        new_K=_float(payload.get("new_K"), "new_K"),
        reason_for_change=_nonempty_str(
            payload.get("reason_for_change"),
            "reason_for_change",
        ),
    )


def _optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _float(value, field_name)


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


def _nonnegative_float(value: object, field_name: str) -> float:
    number = _float(value, field_name)
    if number < 0:
        raise ValueError(f"{field_name} must be nonnegative")
    return number
