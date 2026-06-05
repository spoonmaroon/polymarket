from __future__ import annotations

import math
from collections.abc import Mapping

from polymarket_engine.probability.generator_contracts import GeneratorId


DEFAULT_SEED_WEIGHTS: dict[GeneratorId, float] = {
    GeneratorId.EMPIRICAL_CONDITIONAL: 0.40,
    GeneratorId.BLOCK_BOOTSTRAP: 0.25,
    GeneratorId.FILTERED_HISTORICAL: 0.25,
    GeneratorId.STRESS_OVERLAY: 0.10,
}


def log_loss(probability: float, label: int, eps: float = 1e-6) -> float:
    _require_probability(probability, "probability")
    _require_binary_label(label)
    _require_probability(eps, "eps")
    if eps <= 0 or eps >= 0.5:
        raise ValueError("eps must be greater than 0 and less than 0.5")

    clipped = min(max(probability, eps), 1.0 - eps)
    if label == 1:
        return -math.log(clipped)
    return -math.log(1.0 - clipped)


def brier_loss(probability: float, label: int) -> float:
    _require_probability(probability, "probability")
    _require_binary_label(label)
    return (probability - label) ** 2


def dynamic_weights_from_losses(
    losses: Mapping[GeneratorId, float],
    seed_weights: Mapping[GeneratorId, float],
    eta: float,
    weight_floor: float,
    stress_weight_cap: float,
) -> dict[GeneratorId, float]:
    _require_nonnegative_finite(eta, "eta")
    _require_probability(weight_floor, "weight_floor")
    _require_probability(stress_weight_cap, "stress_weight_cap")
    if not seed_weights:
        raise ValueError("seed_weights must not be empty")

    raw_weights: dict[GeneratorId, float] = {}
    for raw_generator_id, seed_weight in seed_weights.items():
        generator_id = GeneratorId(raw_generator_id)
        _require_nonnegative_finite(seed_weight, "seed_weight")
        loss = losses.get(generator_id, 0.0)
        _require_nonnegative_finite(loss, "loss")
        raw_weights[generator_id] = seed_weight * math.exp(-eta * loss)

    floored = {
        generator_id: max(raw_weight, weight_floor)
        for generator_id, raw_weight in raw_weights.items()
    }
    if GeneratorId.STRESS_OVERLAY in floored:
        floored[GeneratorId.STRESS_OVERLAY] = min(
            floored[GeneratorId.STRESS_OVERLAY],
            stress_weight_cap,
        )

    normalized = _normalize(floored)
    return _cap_final_stress_weight(normalized, stress_weight_cap)


def _cap_final_stress_weight(
    weights: dict[GeneratorId, float],
    stress_weight_cap: float,
) -> dict[GeneratorId, float]:
    stress_weight = weights.get(GeneratorId.STRESS_OVERLAY)
    if stress_weight is None or stress_weight <= stress_weight_cap:
        return weights

    excess = stress_weight - stress_weight_cap
    weights[GeneratorId.STRESS_OVERLAY] = stress_weight_cap
    redistribution_total = sum(
        weight for generator_id, weight in weights.items() if generator_id != GeneratorId.STRESS_OVERLAY
    )
    if redistribution_total <= 0:
        raise ValueError("non-stress weights must be positive when stress cap binds")

    for generator_id, weight in tuple(weights.items()):
        if generator_id != GeneratorId.STRESS_OVERLAY:
            weights[generator_id] = weight + excess * (weight / redistribution_total)
    return _normalize(weights)


def _normalize(weights: Mapping[GeneratorId, float]) -> dict[GeneratorId, float]:
    total = sum(weights.values())
    if not math.isfinite(total) or total <= 0:
        raise ValueError("weights must sum to a positive finite value")
    return {generator_id: weight / total for generator_id, weight in weights.items()}


def _require_probability(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_nonnegative_finite(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or value < 0:
        raise ValueError(f"{field_name} must be nonnegative and finite")


def _require_binary_label(label: int) -> None:
    if isinstance(label, bool) or label not in (0, 1):
        raise ValueError("label must be 0 or 1")


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
