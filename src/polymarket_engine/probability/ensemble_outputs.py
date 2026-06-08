from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from polymarket_engine.probability.generator_contracts import GeneratorId, GeneratorRun


class PathDiagnosis(StrEnum):
    CLEAN = "CLEAN"
    FRAGILE = "FRAGILE"
    SPARSE = "SPARSE"


@dataclass(frozen=True)
class GeneratorWeight:
    generator_id: GeneratorId
    weight: float

    def __post_init__(self) -> None:
        if not isinstance(self.generator_id, GeneratorId):
            raise ValueError("generator_id must be a GeneratorId")
        if (
            isinstance(self.weight, bool)
            or not isinstance(self.weight, (int, float))
            or not math.isfinite(self.weight)
            or self.weight < 0
        ):
            raise ValueError("weight must be nonnegative and finite")


@dataclass(frozen=True)
class EnsembleProbability:
    p_finish: float
    p_no_touch: float
    risk_adjusted_p_finish: float
    risk_adjusted_p_no_touch: float
    risk_adjustment: float
    u_gen_finish: float
    u_gen_touch: float
    u_gen: float
    mc_dispersion: float
    uncertainty_buffer: float
    path_diagnosis: PathDiagnosis
    effective_generator_values: dict[str, dict[str, float]]


def reduce_ensemble(
    runs: Sequence[GeneratorRun],
    weights: Sequence[GeneratorWeight],
    base_model_buffer: float,
) -> EnsembleProbability:
    if not runs:
        raise ValueError("runs must be non-empty")
    if (
        isinstance(base_model_buffer, bool)
        or not isinstance(base_model_buffer, (int, float))
        or not math.isfinite(base_model_buffer)
        or base_model_buffer < 0
    ):
        raise ValueError("base_model_buffer must be nonnegative and finite")

    runs_by_id = _runs_by_id(runs)
    weights_by_id = _weights_by_id(weights)
    if set(runs_by_id) != set(weights_by_id):
        raise ValueError("weights must match runs")

    total_weight = sum(weights_by_id.values())
    if total_weight <= 0:
        raise ValueError("weights must sum positive")

    normalized = {
        generator_id: weight / total_weight
        for generator_id, weight in weights_by_id.items()
    }
    effective_finish, effective_touch = _effective_values(runs_by_id)

    terminal_weights = _terminal_probability_weights(weights_by_id, runs_by_id)
    p_finish = _weighted_mean(effective_finish, terminal_weights)
    p_no_touch = _weighted_mean(effective_touch, terminal_weights)
    risk_adjusted_p_finish = _weighted_mean(effective_finish, normalized)
    risk_adjusted_p_no_touch = _weighted_mean(effective_touch, normalized)
    risk_adjustment = max(0.0, p_finish - risk_adjusted_p_finish)
    u_gen_finish = _weighted_std(effective_finish, normalized, risk_adjusted_p_finish)
    u_gen_touch = _weighted_std(effective_touch, normalized, risk_adjusted_p_no_touch)
    u_gen = max(u_gen_finish, u_gen_touch)
    mc_dispersion = max(
        max(abs(value - p_finish) for value in effective_finish.values()),
        max(abs(value - p_no_touch) for value in effective_touch.values()),
    )
    sparse_penalty = 0.03 if any(run.sparse for run in runs) else 0.0
    path_diagnosis = _diagnose_paths(sparse_penalty=sparse_penalty, dispersion=mc_dispersion)
    effective_generator_values = {
        generator_id.value: {
            "p_finish": effective_finish[generator_id],
            "p_no_touch": effective_touch[generator_id],
            "weight": normalized[generator_id],
        }
        for generator_id in normalized
    }

    return EnsembleProbability(
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        risk_adjusted_p_finish=risk_adjusted_p_finish,
        risk_adjusted_p_no_touch=risk_adjusted_p_no_touch,
        risk_adjustment=risk_adjustment,
        u_gen_finish=u_gen_finish,
        u_gen_touch=u_gen_touch,
        u_gen=u_gen,
        mc_dispersion=mc_dispersion,
        uncertainty_buffer=base_model_buffer + 0.5 * u_gen + sparse_penalty + risk_adjustment,
        path_diagnosis=path_diagnosis,
        effective_generator_values=effective_generator_values,
    )


def _runs_by_id(runs: Sequence[GeneratorRun]) -> dict[GeneratorId, GeneratorRun]:
    rows: dict[GeneratorId, GeneratorRun] = {}
    for run in runs:
        if run.generator_id in rows:
            raise ValueError("runs must not contain duplicate generators")
        rows[run.generator_id] = run
    return rows


def _weights_by_id(weights: Sequence[GeneratorWeight]) -> dict[GeneratorId, float]:
    rows: dict[GeneratorId, float] = {}
    for weight in weights:
        if weight.generator_id in rows:
            raise ValueError("weights must not contain duplicate generators")
        rows[weight.generator_id] = weight.weight
    return rows


def _terminal_probability_weights(
    weights_by_id: dict[GeneratorId, float],
    runs_by_id: dict[GeneratorId, GeneratorRun],
) -> dict[GeneratorId, float]:
    terminal_raw = {
        generator_id: weight
        for generator_id, weight in weights_by_id.items()
        if generator_id in runs_by_id and generator_id != GeneratorId.STRESS_OVERLAY
    }
    total = sum(terminal_raw.values())
    if total <= 0:
        fallback_total = sum(weights_by_id.values())
        return {
            generator_id: weight / fallback_total
            for generator_id, weight in weights_by_id.items()
        }
    return {
        generator_id: weight / total
        for generator_id, weight in terminal_raw.items()
    }


def _effective_values(
    runs_by_id: dict[GeneratorId, GeneratorRun],
) -> tuple[dict[GeneratorId, float], dict[GeneratorId, float]]:
    finish = {generator_id: run.p_finish for generator_id, run in runs_by_id.items()}
    touch = {generator_id: run.p_no_touch for generator_id, run in runs_by_id.items()}
    stress = runs_by_id.get(GeneratorId.STRESS_OVERLAY)
    if stress is None:
        return finish, touch

    non_stress_finish = [
        run.p_finish
        for generator_id, run in runs_by_id.items()
        if generator_id != GeneratorId.STRESS_OVERLAY
    ]
    non_stress_touch = [
        run.p_no_touch
        for generator_id, run in runs_by_id.items()
        if generator_id != GeneratorId.STRESS_OVERLAY
    ]
    if non_stress_finish:
        finish[GeneratorId.STRESS_OVERLAY] = min(
            stress.p_finish,
            _median(non_stress_finish),
        )
    if non_stress_touch:
        touch[GeneratorId.STRESS_OVERLAY] = min(
            stress.p_no_touch,
            _median(non_stress_touch),
        )
    return finish, touch


def _weighted_mean(
    values: dict[GeneratorId, float],
    weights: dict[GeneratorId, float],
) -> float:
    return sum(weight * values[generator_id] for generator_id, weight in weights.items())


def _weighted_std(
    values: dict[GeneratorId, float],
    weights: dict[GeneratorId, float],
    center: float,
) -> float:
    variance = sum(
        weights[generator_id] * (value - center) ** 2
        for generator_id, value in values.items()
    )
    return math.sqrt(variance)


def _diagnose_paths(*, sparse_penalty: float, dispersion: float) -> PathDiagnosis:
    if sparse_penalty:
        return PathDiagnosis.SPARSE
    if dispersion >= 0.12:
        return PathDiagnosis.FRAGILE
    return PathDiagnosis.CLEAN


def _median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median requires at least one value")
    sorted_values = sorted(values)
    midpoint = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[midpoint]
    return (sorted_values[midpoint - 1] + sorted_values[midpoint]) / 2
