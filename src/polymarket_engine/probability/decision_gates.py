from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from polymarket_engine.probability.ensemble_outputs import EnsembleOutput


@dataclass(frozen=True)
class ProbabilityGateResult:
    decision_hint: str
    edge_after_costs: float
    required_edge: float
    reasons: tuple[str, ...]


def evaluate_probability_gates(
    ensemble: EnsembleOutput,
    z_path: float,
    executable_entry_price: float,
    execution_costs: float,
    hard_failures: Sequence[str],
    base_edge: float = 0.03,
    p_no_touch_floor: float = 0.65,
    z_path_floor: float = 0.50,
) -> ProbabilityGateResult:
    _require_finite(z_path, "z_path")
    _require_probability(executable_entry_price, "executable_entry_price")
    _require_nonnegative_finite(execution_costs, "execution_costs")
    _require_nonnegative_finite(base_edge, "base_edge")
    _require_probability(p_no_touch_floor, "p_no_touch_floor")
    _require_nonnegative_finite(z_path_floor, "z_path_floor")

    edge_after_costs = ensemble.p_finish - executable_entry_price - execution_costs
    required_edge = base_edge + ensemble.uncertainty_buffer
    reasons: list[str] = []

    if ensemble.p_no_touch < p_no_touch_floor:
        required_edge += 0.02
        reasons.append("P_NO_TOUCH_BELOW_FLOOR")
    if abs(z_path) < z_path_floor:
        required_edge += 0.02
        reasons.append("Z_PATH_BELOW_FLOOR")

    blockers = _block_reasons(ensemble, hard_failures)
    if blockers:
        return ProbabilityGateResult(
            decision_hint="BLOCK",
            edge_after_costs=edge_after_costs,
            required_edge=required_edge,
            reasons=tuple(reasons + blockers),
        )

    wait_reasons = [
        label
        for label in ("TERMINAL_ONLY", "NEAR_THRESHOLD")
        if label in ensemble.path_diagnosis
    ]
    if wait_reasons:
        return ProbabilityGateResult(
            decision_hint="WAIT",
            edge_after_costs=edge_after_costs,
            required_edge=required_edge,
            reasons=tuple(reasons + wait_reasons),
        )

    if edge_after_costs >= required_edge:
        return ProbabilityGateResult(
            decision_hint="TRADE_CANDIDATE",
            edge_after_costs=edge_after_costs,
            required_edge=required_edge,
            reasons=(),
        )

    return ProbabilityGateResult(
        decision_hint="DEMAND_MORE_EDGE",
        edge_after_costs=edge_after_costs,
        required_edge=required_edge,
        reasons=tuple(reasons + ["INSUFFICIENT_EDGE"]),
    )


def _block_reasons(ensemble: EnsembleOutput, hard_failures: Sequence[str]) -> list[str]:
    blockers = list(hard_failures)
    if ensemble.mc_dispersion > 0.10:
        blockers.append("MC_DISPERSION")
    for label in ("SPARSE", "STALE_OR_UNSAFE"):
        if label in ensemble.path_diagnosis and label not in blockers:
            blockers.append(label)
    return blockers


def _require_probability(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_finite(value: float, field_name: str) -> None:
    if not _is_finite_number(value):
        raise ValueError(f"{field_name} must be finite")


def _require_nonnegative_finite(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or value < 0:
        raise ValueError(f"{field_name} must be nonnegative and finite")


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
