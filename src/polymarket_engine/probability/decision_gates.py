from __future__ import annotations

import math
from dataclasses import dataclass

from polymarket_engine.probability.ensemble_outputs import EnsembleOutput


@dataclass(frozen=True)
class ProbabilityGateResult:
    decision_hint: str
    edge_after_costs: float
    required_edge: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExecutableQualityInput:
    executable_entry_price: float
    execution_costs: float
    quote_age_ms: int
    source_age_ms: int
    book_age_ms: int
    latency_ms: int
    hard_failures: tuple[str, ...] = ()
    quote_fresh: bool = True
    source_fresh: bool = True
    book_fresh: bool = True
    max_quote_age_ms: int = 1500
    max_source_age_ms: int = 2000
    max_book_age_ms: int = 1500
    max_latency_ms: int = 500

    def __post_init__(self) -> None:
        _require_probability(self.executable_entry_price, "executable_entry_price")
        _require_nonnegative_finite(self.execution_costs, "execution_costs")
        _require_nonnegative_int(self.quote_age_ms, "quote_age_ms")
        _require_nonnegative_int(self.source_age_ms, "source_age_ms")
        _require_nonnegative_int(self.book_age_ms, "book_age_ms")
        _require_nonnegative_int(self.latency_ms, "latency_ms")
        _require_positive_int(self.max_quote_age_ms, "max_quote_age_ms")
        _require_positive_int(self.max_source_age_ms, "max_source_age_ms")
        _require_positive_int(self.max_book_age_ms, "max_book_age_ms")
        _require_positive_int(self.max_latency_ms, "max_latency_ms")
        if not all(isinstance(value, str) for value in self.hard_failures):
            raise ValueError("hard_failures must be strings")
        for field_name in ("quote_fresh", "source_fresh", "book_fresh"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be bool")


def evaluate_probability_gates(
    ensemble: EnsembleOutput,
    executable_quality: ExecutableQualityInput,
    base_edge: float = 0.03,
    p_no_touch_floor: float = 0.65,
    z_path_floor: float = 0.50,
) -> ProbabilityGateResult:
    if not isinstance(executable_quality, ExecutableQualityInput):
        raise ValueError("executable_quality must be an ExecutableQualityInput")
    _require_nonnegative_finite(base_edge, "base_edge")
    _require_probability(p_no_touch_floor, "p_no_touch_floor")
    _require_nonnegative_finite(z_path_floor, "z_path_floor")

    edge_after_costs = (
        ensemble.p_finish
        - executable_quality.executable_entry_price
        - executable_quality.execution_costs
    )
    required_edge = base_edge + ensemble.uncertainty_buffer
    reasons: list[str] = []

    if ensemble.p_no_touch < p_no_touch_floor:
        required_edge += 0.02
        reasons.append("P_NO_TOUCH_BELOW_FLOOR")
    if abs(ensemble.z_path) < z_path_floor:
        required_edge += 0.02
        reasons.append("Z_PATH_BELOW_FLOOR")

    blockers = _block_reasons(ensemble, executable_quality)
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


def _block_reasons(
    ensemble: EnsembleOutput,
    executable_quality: ExecutableQualityInput,
) -> list[str]:
    blockers = list(executable_quality.hard_failures)
    blockers.extend(_quality_block_reasons(executable_quality))
    if ensemble.mc_dispersion > 0.10:
        blockers.append("MC_DISPERSION")
    for label in ("SPARSE", "STALE_OR_UNSAFE"):
        if label in ensemble.path_diagnosis and label not in blockers:
            blockers.append(label)
    return blockers


def _quality_block_reasons(executable_quality: ExecutableQualityInput) -> list[str]:
    reasons: list[str] = []
    if executable_quality.quote_age_ms > executable_quality.max_quote_age_ms:
        reasons.append("QUOTE_STALE")
    if executable_quality.source_age_ms > executable_quality.max_source_age_ms:
        reasons.append("SOURCE_STALE")
    if executable_quality.book_age_ms > executable_quality.max_book_age_ms:
        reasons.append("BOOK_STALE")
    if executable_quality.latency_ms > executable_quality.max_latency_ms:
        reasons.append("LATENCY_STALE")
    if not executable_quality.quote_fresh:
        reasons.append("QUOTE_NOT_FRESH")
    if not executable_quality.source_fresh:
        reasons.append("SOURCE_NOT_FRESH")
    if not executable_quality.book_fresh:
        reasons.append("BOOK_NOT_FRESH")
    return reasons


def _require_probability(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_finite(value: float, field_name: str) -> None:
    if not _is_finite_number(value):
        raise ValueError(f"{field_name} must be finite")


def _require_nonnegative_finite(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or value < 0:
        raise ValueError(f"{field_name} must be nonnegative and finite")


def _require_nonnegative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")


def _require_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
