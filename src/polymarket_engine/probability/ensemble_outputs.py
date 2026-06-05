from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from polymarket_engine.probability.generator_contracts import GeneratorId, GeneratorRun, GeneratorWeight


@dataclass(frozen=True)
class EnsembleOutput:
    p_finish: float
    p_no_touch: float
    z_path: float
    mc_dispersion: float
    uncertainty_buffer: float
    path_diagnosis: tuple[str, ...]
    effective_weights: dict[GeneratorId, float]

    def __post_init__(self) -> None:
        _require_probability(self.p_finish, "p_finish")
        _require_probability(self.p_no_touch, "p_no_touch")
        _require_finite(self.z_path, "z_path")
        _require_nonnegative_finite(self.mc_dispersion, "mc_dispersion")
        _require_nonnegative_finite(self.uncertainty_buffer, "uncertainty_buffer")
        if not isinstance(self.path_diagnosis, tuple) or not all(
            isinstance(label, str) for label in self.path_diagnosis
        ):
            raise ValueError("path_diagnosis must be a tuple of strings")
        if not isinstance(self.effective_weights, dict):
            raise ValueError("effective_weights must be a dict")


def reduce_generator_runs(
    runs: Sequence[GeneratorRun],
    weights: Sequence[GeneratorWeight] | Mapping[GeneratorId, float],
    sparse_scope: bool,
    calibration_penalty: float,
    stale_weight_penalty: float,
    *,
    runtime_asof_ts: datetime | None = None,
) -> EnsembleOutput:
    if not runs:
        raise ValueError("runs must not be empty")
    _require_bool(sparse_scope, "sparse_scope")
    _require_nonnegative_finite(calibration_penalty, "calibration_penalty")
    _require_nonnegative_finite(stale_weight_penalty, "stale_weight_penalty")

    run_by_id = {_coerce_generator_id(run.generator_id): run for run in runs}
    weight_by_id = _coerce_weights(weights, runtime_asof_ts=runtime_asof_ts)
    effective_weights = _normalize_weights(
        {
            generator_id: weight_by_id[generator_id]
            for generator_id in run_by_id
            if generator_id in weight_by_id
        }
    )
    if set(effective_weights) != set(run_by_id):
        missing = ", ".join(
            sorted(generator.value for generator in set(run_by_id) - set(effective_weights))
        )
        raise ValueError(f"weights missing generator runs: {missing}")

    effective_values = _effective_generator_values(run_by_id)
    p_finish = sum(
        effective_weights[generator_id] * values[0]
        for generator_id, values in effective_values.items()
    )
    p_no_touch = sum(
        effective_weights[generator_id] * values[1]
        for generator_id, values in effective_values.items()
    )
    z_path = sum(
        effective_weights[generator_id] * values[2]
        for generator_id, values in effective_values.items()
    )
    mc_dispersion = _mc_dispersion(tuple(effective_values.values()))
    sparse = sparse_scope or any(run.sparse for run in runs)
    uncertainty_buffer = (
        0.01
        + 0.50 * mc_dispersion
        + (0.04 if sparse else 0.0)
        + calibration_penalty
        + stale_weight_penalty
    )

    return EnsembleOutput(
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        z_path=z_path,
        mc_dispersion=mc_dispersion,
        uncertainty_buffer=uncertainty_buffer,
        path_diagnosis=_diagnose_path(
            sparse=sparse,
            z_path=z_path,
            p_no_touch=p_no_touch,
            mc_dispersion=mc_dispersion,
        ),
        effective_weights=effective_weights,
    )


def _effective_generator_values(
    run_by_id: Mapping[GeneratorId, GeneratorRun],
) -> dict[GeneratorId, tuple[float, float, float]]:
    values = {
        generator_id: (run.p_finish, run.p_no_touch, run.z_path)
        for generator_id, run in run_by_id.items()
    }
    stress_run = values.get(GeneratorId.STRESS_OVERLAY)
    non_stress_values = [
        value
        for generator_id, value in values.items()
        if generator_id != GeneratorId.STRESS_OVERLAY
    ]
    if stress_run is not None and non_stress_values:
        non_stress_finish_median = median(value[0] for value in non_stress_values)
        non_stress_no_touch_median = median(value[1] for value in non_stress_values)
        non_stress_z_path_median = median(value[2] for value in non_stress_values)
        values[GeneratorId.STRESS_OVERLAY] = (
            min(stress_run[0], non_stress_finish_median),
            min(stress_run[1], non_stress_no_touch_median),
            min(stress_run[2], non_stress_z_path_median),
        )
    return values


def _mc_dispersion(values: Sequence[tuple[float, float, float]]) -> float:
    finish_values = tuple(value[0] for value in values)
    no_touch_values = tuple(value[1] for value in values)
    finish_median = median(finish_values)
    no_touch_median = median(no_touch_values)
    return max(
        max(abs(value - finish_median) for value in finish_values),
        max(abs(value - no_touch_median) for value in no_touch_values),
    )


def _diagnose_path(
    *,
    sparse: bool,
    z_path: float,
    p_no_touch: float,
    mc_dispersion: float,
) -> tuple[str, ...]:
    labels: list[str] = []
    if sparse:
        labels.append("SPARSE")
    if abs(z_path) < 0.5:
        labels.append("NEAR_THRESHOLD")
    if p_no_touch < 0.55:
        labels.append("TERMINAL_ONLY")
    if mc_dispersion > 0.05:
        labels.append("FRAGILE")
    if not labels:
        labels.append("CLEAN")
    return tuple(labels)


def _coerce_weights(
    weights: Sequence[GeneratorWeight] | Mapping[GeneratorId, float],
    *,
    runtime_asof_ts: datetime | None,
) -> dict[GeneratorId, float]:
    if isinstance(weights, Mapping):
        return {
            _coerce_generator_id(generator_id): weight
            for generator_id, weight in weights.items()
        }
    _require_runtime_asof_for_generator_weights(runtime_asof_ts)
    return {
        _coerce_generator_id(generator_weight.generator_id): generator_weight.weight
        for generator_weight in _validated_generator_weights(weights, runtime_asof_ts)
    }


def _normalize_weights(weights: Mapping[GeneratorId, float]) -> dict[GeneratorId, float]:
    if not weights:
        raise ValueError("weights must not be empty")
    for weight in weights.values():
        _require_nonnegative_finite(weight, "weight")
    total = sum(weights.values())
    if total <= 0 or not math.isfinite(total):
        raise ValueError("weights must sum to a positive finite value")
    return {generator_id: weight / total for generator_id, weight in weights.items()}


def _coerce_generator_id(value: GeneratorId) -> GeneratorId:
    try:
        return GeneratorId(value)
    except ValueError as exc:
        raise ValueError("generator_id must be a supported GeneratorId") from exc


def _validated_generator_weights(
    weights: Sequence[GeneratorWeight],
    runtime_asof_ts: datetime | None,
) -> tuple[GeneratorWeight, ...]:
    _require_runtime_asof_for_generator_weights(runtime_asof_ts)
    assert runtime_asof_ts is not None
    validated = tuple(weights)
    for generator_weight in validated:
        if not isinstance(generator_weight, GeneratorWeight):
            raise ValueError("weights must be GeneratorWeight objects")
        if generator_weight.validation_window.evaluated_through_ts > runtime_asof_ts:
            raise ValueError("evaluated_through_ts must not be after runtime_asof_ts")
    return validated


def _require_runtime_asof_for_generator_weights(runtime_asof_ts: datetime | None) -> None:
    if runtime_asof_ts is None:
        raise ValueError("runtime_asof_ts is required for GeneratorWeight objects")
    _require_timezone_aware(runtime_asof_ts, "runtime_asof_ts")


def _require_probability(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_finite(value: float, field_name: str) -> None:
    if not _is_finite_number(value):
        raise ValueError(f"{field_name} must be finite")


def _require_nonnegative_finite(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or value < 0:
        raise ValueError(f"{field_name} must be nonnegative and finite")


def _require_bool(value: bool, field_name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be bool")


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
