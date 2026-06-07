from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from polymarket_engine.execution.book import ExecutionBookMetrics
from polymarket_engine.probability.decision_gates import (
    DecisionInputs,
    DecisionMode,
    evaluate_decision,
)
from polymarket_engine.probability.ensemble_outputs import (
    EnsembleProbability,
    PathDiagnosis,
)


def _ensemble(
    *,
    p_finish: float = 0.72,
    p_no_touch: float = 0.68,
    u_gen_finish: float | None = None,
    u_gen_touch: float = 0.03,
    u_gen: float = 0.03,
    mc_dispersion: float = 0.04,
    uncertainty_buffer: float = 0.025,
    path_diagnosis: PathDiagnosis = PathDiagnosis.CLEAN,
    effective_generator_values: dict[str, dict[str, float]] | None = None,
) -> EnsembleProbability:
    return EnsembleProbability(
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        u_gen_finish=u_gen if u_gen_finish is None else u_gen_finish,
        u_gen_touch=u_gen_touch,
        u_gen=u_gen,
        mc_dispersion=mc_dispersion,
        uncertainty_buffer=uncertainty_buffer,
        path_diagnosis=path_diagnosis,
        effective_generator_values=(
            {} if effective_generator_values is None else effective_generator_values
        ),
    )


def _execution(
    *,
    entry_vwap: float = 0.62,
    exit_vwap: float = 0.59,
    spread: float = 0.02,
    quote_age_ms: int = 100,
    exit_depth: bool = True,
) -> ExecutionBookMetrics:
    return ExecutionBookMetrics(
        entry_vwap=entry_vwap,
        exit_vwap=exit_vwap,
        entry_slippage=0.004,
        exit_slippage=0.005,
        spread=spread,
        entry_depth_available=True,
        exit_depth_available=exit_depth,
        quote_age_ms=quote_age_ms,
        skip_reasons=() if exit_depth else ("insufficient_exit_depth",),
    )


def _inputs(
    *,
    execution_mode: DecisionMode = DecisionMode.PAPER,
    ensemble: EnsembleProbability | None = None,
    execution: ExecutionBookMetrics | None = None,
    z_path: float = 1.2,
    min_z_path: float = 0.4,
    min_p_no_touch: float = 0.55,
    base_edge: float = 0.02,
    latency_buffer: float = 0.005,
    source_buffer: float = 0.005,
    crowding_buffer: float = 0.0,
    support_resistance_buffer: float = 0.0,
    support_resistance_reasons: tuple[str, ...] = (),
    crowding_reasons: tuple[str, ...] = (),
    quality_reasons: tuple[str, ...] = (),
) -> DecisionInputs:
    return DecisionInputs(
        execution_mode=execution_mode,
        ensemble=ensemble or _ensemble(),
        execution=execution or _execution(),
        z_path=z_path,
        min_z_path=min_z_path,
        min_p_no_touch=min_p_no_touch,
        base_edge=base_edge,
        latency_buffer=latency_buffer,
        source_buffer=source_buffer,
        crowding_buffer=crowding_buffer,
        support_resistance_buffer=support_resistance_buffer,
        support_resistance_reasons=support_resistance_reasons,
        crowding_reasons=crowding_reasons,
        quality_reasons=quality_reasons,
    )


def test_decision_promotes_to_paper_trade_when_edge_and_path_are_clean() -> None:
    decision = evaluate_decision(_inputs())

    assert decision.decision_hint == "PAPER_TRADE"
    assert decision.edge_after_costs == 0.09999999999999998
    assert decision.required_edge == 0.064
    assert decision.edge_components == {
        "base_edge": 0.02,
        "entry_slippage_buffer": 0.004,
        "exit_slippage_buffer": 0.005,
        "latency_buffer": 0.005,
        "source_buffer": 0.005,
        "uncertainty_buffer": 0.025,
        "crowding_buffer": 0.0,
        "support_resistance_buffer": 0.0,
    }
    assert decision.supervised_live_action == "DISABLED"
    assert decision.live_order_intent is None
    assert decision.skip_reasons == ()


def test_decision_blocks_when_exit_liquidity_is_missing() -> None:
    decision = evaluate_decision(_inputs(execution=_execution(exit_depth=False)))

    assert decision.decision_hint == "BLOCK"
    assert decision.skip_reasons == ("insufficient_exit_depth",)


def test_supervised_live_requires_manual_approval_and_no_order_intent() -> None:
    decision = evaluate_decision(
        _inputs(execution_mode=DecisionMode.SUPERVISED_LIVE)
    )

    assert decision.decision_hint == "REQUIRE_MANUAL_APPROVAL"
    assert decision.supervised_live_action == "REQUIRE_MANUAL_APPROVAL"
    assert decision.live_order_intent is None


def test_decision_demands_more_edge_when_edge_is_below_required_buffers() -> None:
    decision = evaluate_decision(_inputs(ensemble=_ensemble(p_finish=0.66)))

    assert decision.decision_hint == "DEMAND_MORE_EDGE"
    assert decision.edge_after_costs == 0.040000000000000036
    assert decision.required_edge == 0.064
    assert decision.skip_reasons == ("insufficient_edge",)


def test_sparse_path_waits_with_stable_deduped_reasons() -> None:
    decision = evaluate_decision(
        _inputs(
            ensemble=_ensemble(path_diagnosis=PathDiagnosis.SPARSE),
            quality_reasons=("source_lag", "sparse_generator_scope"),
            crowding_reasons=("source_lag",),
        )
    )

    assert decision.decision_hint == "WAIT"
    assert decision.skip_reasons == ("source_lag", "sparse_generator_scope")


@pytest.mark.parametrize(
    ("field_name", "input_factory"),
    (
        ("ensemble.p_finish", lambda: _inputs(ensemble=_ensemble(p_finish=math.nan))),
        ("ensemble.p_no_touch", lambda: _inputs(ensemble=_ensemble(p_no_touch=math.inf))),
        (
            "ensemble.u_gen_finish",
            lambda: _inputs(ensemble=_ensemble(u_gen_finish=math.nan)),
        ),
        (
            "ensemble.u_gen_touch",
            lambda: _inputs(ensemble=_ensemble(u_gen_touch=math.inf)),
        ),
        (
            "ensemble.mc_dispersion",
            lambda: _inputs(ensemble=_ensemble(mc_dispersion=math.nan)),
        ),
        (
            "ensemble.effective_generator_values.x.p_finish",
            lambda: _inputs(
                ensemble=_ensemble(
                    effective_generator_values={"x": {"p_finish": math.nan}},
                ),
            ),
        ),
        (
            "ensemble.effective_generator_values.x.p_finish",
            lambda: _inputs(
                ensemble=_ensemble(
                    effective_generator_values={"x": {"p_finish": -0.2}},
                ),
            ),
        ),
        (
            "ensemble.effective_generator_values.x.p_no_touch",
            lambda: _inputs(
                ensemble=_ensemble(
                    effective_generator_values={"x": {"p_no_touch": 1.2}},
                ),
            ),
        ),
        (
            "ensemble.effective_generator_values.x.weight",
            lambda: _inputs(
                ensemble=_ensemble(
                    effective_generator_values={"x": {"weight": -1.0}},
                ),
            ),
        ),
        (
            "execution.entry_vwap",
            lambda: _inputs(execution=_execution(entry_vwap=math.nan)),
        ),
        (
            "execution.exit_vwap",
            lambda: _inputs(execution=_execution(exit_vwap=math.inf)),
        ),
        ("execution.spread", lambda: _inputs(execution=_execution(spread=math.nan))),
        (
            "execution.quote_age_ms",
            lambda: _inputs(execution=_execution(quote_age_ms=-1)),
        ),
        ("base_edge", lambda: _inputs(base_edge=math.nan)),
        ("latency_buffer", lambda: _inputs(latency_buffer=math.inf)),
        ("min_p_no_touch", lambda: _inputs(min_p_no_touch=math.nan)),
        ("z_path", lambda: _inputs(z_path=math.inf)),
    ),
)
def test_decision_rejects_nonfinite_numeric_inputs(
    field_name: str,
    input_factory: Callable[[], DecisionInputs],
) -> None:
    with pytest.raises(ValueError, match=field_name):
        evaluate_decision(input_factory())
