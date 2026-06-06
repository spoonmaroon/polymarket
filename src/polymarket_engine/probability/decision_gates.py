from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from polymarket_engine.execution.book import ExecutionBookMetrics
from polymarket_engine.probability.ensemble_outputs import (
    EnsembleProbability,
    PathDiagnosis,
)


class DecisionMode(StrEnum):
    READ_ONLY = "read_only"
    PAPER = "paper"
    SUPERVISED_LIVE = "supervised_live"


@dataclass(frozen=True)
class DecisionInputs:
    execution_mode: DecisionMode
    ensemble: EnsembleProbability
    execution: ExecutionBookMetrics
    z_path: float
    min_z_path: float
    min_p_no_touch: float
    base_edge: float
    latency_buffer: float
    source_buffer: float
    crowding_buffer: float
    support_resistance_buffer: float
    support_resistance_reasons: tuple[str, ...]
    crowding_reasons: tuple[str, ...]
    quality_reasons: tuple[str, ...]


@dataclass(frozen=True)
class DecisionOutput:
    decision_hint: str
    edge_after_costs: float
    required_edge: float
    skip_reasons: tuple[str, ...]
    edge_components: dict[str, float]
    supervised_live_action: str
    live_order_intent: None


_HARD_BLOCK_REASONS = {
    "stale_orderbook",
    "insufficient_entry_depth",
    "insufficient_exit_depth",
    "not_enough_distance",
    "weak_path_survival",
    "near_resistance",
    "near_support",
    "threshold_on_structure",
    "crowded_order_flow",
}


def evaluate_decision(inputs: DecisionInputs) -> DecisionOutput:
    _validate_inputs(inputs)
    edge_after_costs = inputs.ensemble.p_finish - inputs.execution.entry_vwap
    edge_components = {
        "base_edge": inputs.base_edge,
        "entry_slippage_buffer": inputs.execution.entry_slippage,
        "exit_slippage_buffer": inputs.execution.exit_slippage,
        "latency_buffer": inputs.latency_buffer,
        "source_buffer": inputs.source_buffer,
        "uncertainty_buffer": inputs.ensemble.uncertainty_buffer,
        "crowding_buffer": inputs.crowding_buffer,
        "support_resistance_buffer": inputs.support_resistance_buffer,
    }
    required_edge = sum(edge_components.values())

    reasons = _decision_reasons(inputs)
    if any(reason in _HARD_BLOCK_REASONS for reason in reasons):
        decision_hint = "BLOCK"
    elif inputs.ensemble.path_diagnosis == PathDiagnosis.SPARSE:
        decision_hint = "WAIT"
    elif edge_after_costs < required_edge:
        reasons.append("insufficient_edge")
        decision_hint = "DEMAND_MORE_EDGE"
    elif inputs.execution_mode == DecisionMode.READ_ONLY:
        decision_hint = "TRADE_CANDIDATE"
    elif inputs.execution_mode == DecisionMode.PAPER:
        decision_hint = "PAPER_TRADE"
    elif inputs.execution_mode == DecisionMode.SUPERVISED_LIVE:
        decision_hint = "REQUIRE_MANUAL_APPROVAL"
    else:
        decision_hint = "BLOCK"

    supervised_live_action = (
        "REQUIRE_MANUAL_APPROVAL"
        if decision_hint == "REQUIRE_MANUAL_APPROVAL"
        else "DISABLED"
    )
    return DecisionOutput(
        decision_hint=decision_hint,
        edge_after_costs=edge_after_costs,
        required_edge=required_edge,
        skip_reasons=_dedupe_reasons(reasons),
        edge_components=edge_components,
        supervised_live_action=supervised_live_action,
        live_order_intent=None,
    )


def _validate_inputs(inputs: DecisionInputs) -> None:
    _require_probability(inputs.ensemble.p_finish, "ensemble.p_finish")
    _require_probability(inputs.ensemble.p_no_touch, "ensemble.p_no_touch")
    _require_nonnegative_finite(
        inputs.ensemble.u_gen_finish,
        "ensemble.u_gen_finish",
    )
    _require_nonnegative_finite(inputs.ensemble.u_gen_touch, "ensemble.u_gen_touch")
    _require_nonnegative_finite(inputs.ensemble.u_gen, "ensemble.u_gen")
    _require_nonnegative_finite(
        inputs.ensemble.mc_dispersion,
        "ensemble.mc_dispersion",
    )
    _require_nonnegative_finite(
        inputs.ensemble.uncertainty_buffer,
        "ensemble.uncertainty_buffer",
    )
    _validate_effective_generator_values(inputs.ensemble.effective_generator_values)
    _require_probability(inputs.execution.entry_vwap, "execution.entry_vwap")
    _require_probability(inputs.execution.exit_vwap, "execution.exit_vwap")
    _require_nonnegative_finite(
        inputs.execution.entry_slippage,
        "execution.entry_slippage",
    )
    _require_nonnegative_finite(
        inputs.execution.exit_slippage,
        "execution.exit_slippage",
    )
    _require_nonnegative_finite(inputs.execution.spread, "execution.spread")
    _require_nonnegative_int(inputs.execution.quote_age_ms, "execution.quote_age_ms")
    _require_finite(inputs.z_path, "z_path")
    _require_nonnegative_finite(inputs.min_z_path, "min_z_path")
    _require_probability(inputs.min_p_no_touch, "min_p_no_touch")
    _require_nonnegative_finite(inputs.base_edge, "base_edge")
    _require_nonnegative_finite(inputs.latency_buffer, "latency_buffer")
    _require_nonnegative_finite(inputs.source_buffer, "source_buffer")
    _require_nonnegative_finite(inputs.crowding_buffer, "crowding_buffer")
    _require_nonnegative_finite(
        inputs.support_resistance_buffer,
        "support_resistance_buffer",
    )


def _validate_effective_generator_values(
    values: dict[str, dict[str, float]],
) -> None:
    for generator_id, metrics in values.items():
        if not isinstance(generator_id, str):
            raise ValueError("ensemble.effective_generator_values keys must be strings")
        if not isinstance(metrics, dict):
            raise ValueError(
                f"ensemble.effective_generator_values.{generator_id} must be a mapping"
            )
        for metric_name, value in metrics.items():
            if not isinstance(metric_name, str):
                raise ValueError(
                    f"ensemble.effective_generator_values.{generator_id} keys "
                    "must be strings"
                )
            field_name = (
                f"ensemble.effective_generator_values.{generator_id}.{metric_name}"
            )
            if metric_name in {"p_finish", "p_no_touch"}:
                _require_probability(value, field_name)
            elif metric_name == "weight":
                _require_nonnegative_finite(value, field_name)
            else:
                _require_finite(value, field_name)


def _decision_reasons(inputs: DecisionInputs) -> list[str]:
    reasons: list[str] = []
    reasons.extend(inputs.quality_reasons)
    reasons.extend(inputs.execution.skip_reasons)
    reasons.extend(inputs.crowding_reasons)
    reasons.extend(inputs.support_resistance_reasons)
    if inputs.z_path < inputs.min_z_path:
        reasons.append("not_enough_distance")
    if inputs.ensemble.p_no_touch < inputs.min_p_no_touch:
        reasons.append("weak_path_survival")
    if inputs.ensemble.path_diagnosis == PathDiagnosis.SPARSE:
        reasons.append("sparse_generator_scope")
    if inputs.ensemble.u_gen >= 0.12:
        reasons.append("generator_disagreement")
    return reasons


def _require_probability(value: float, field_name: str) -> None:
    _require_finite(value, field_name)
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")


def _require_nonnegative_finite(value: float, field_name: str) -> None:
    _require_finite(value, field_name)
    if value < 0.0:
        raise ValueError(f"{field_name} must be nonnegative")


def _require_nonnegative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")


def _require_finite(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{field_name} must be finite")


def _dedupe_reasons(reasons: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason in seen:
            continue
        deduped.append(reason)
        seen.add(reason)
    return tuple(deduped)
