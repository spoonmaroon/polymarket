import pytest

from polymarket_engine.probability.decision_gates import evaluate_probability_gates
from polymarket_engine.probability.ensemble_outputs import EnsembleOutput


def _ensemble(
    *,
    p_finish: float = 0.80,
    p_no_touch: float = 0.80,
    mc_dispersion: float = 0.03,
    uncertainty_buffer: float = 0.02,
    path_diagnosis: tuple[str, ...] = ("CLEAN",),
) -> EnsembleOutput:
    return EnsembleOutput(
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        mc_dispersion=mc_dispersion,
        uncertainty_buffer=uncertainty_buffer,
        path_diagnosis=path_diagnosis,
        effective_weights={},
    )


def test_evaluate_probability_gates_returns_trade_candidate_when_edge_clears_buffers() -> None:
    result = evaluate_probability_gates(
        _ensemble(),
        z_path=0.75,
        executable_entry_price=0.70,
        execution_costs=0.01,
        hard_failures=(),
    )

    assert result.decision_hint == "TRADE_CANDIDATE"
    assert result.edge_after_costs == pytest.approx(0.09)
    assert result.required_edge == pytest.approx(0.05)
    assert result.reasons == ()


def test_evaluate_probability_gates_demands_more_edge_when_edge_is_too_small() -> None:
    result = evaluate_probability_gates(
        _ensemble(p_finish=0.75),
        z_path=0.75,
        executable_entry_price=0.71,
        execution_costs=0.01,
        hard_failures=(),
    )

    assert result.decision_hint == "DEMAND_MORE_EDGE"
    assert result.edge_after_costs == pytest.approx(0.03)
    assert result.required_edge == pytest.approx(0.05)
    assert result.reasons == ("INSUFFICIENT_EDGE",)


def test_evaluate_probability_gates_waits_on_terminal_or_near_threshold_risk() -> None:
    result = evaluate_probability_gates(
        _ensemble(p_no_touch=0.52, path_diagnosis=("TERMINAL_ONLY", "NEAR_THRESHOLD")),
        z_path=0.20,
        executable_entry_price=0.60,
        execution_costs=0.01,
        hard_failures=(),
    )

    assert result.decision_hint == "WAIT"
    assert result.required_edge == pytest.approx(0.09)
    assert result.reasons == (
        "P_NO_TOUCH_BELOW_FLOOR",
        "Z_PATH_BELOW_FLOOR",
        "TERMINAL_ONLY",
        "NEAR_THRESHOLD",
    )


@pytest.mark.parametrize(
    ("ensemble", "hard_failures", "expected_reason"),
    (
        (_ensemble(), ("STALE_OR_UNSAFE",), "STALE_OR_UNSAFE"),
        (_ensemble(mc_dispersion=0.11), (), "MC_DISPERSION"),
        (_ensemble(path_diagnosis=("SPARSE",)), (), "SPARSE"),
        (_ensemble(path_diagnosis=("STALE_OR_UNSAFE",)), (), "STALE_OR_UNSAFE"),
    ),
)
def test_evaluate_probability_gates_blocks_on_hard_failures_or_hard_diagnoses(
    ensemble: EnsembleOutput,
    hard_failures: tuple[str, ...],
    expected_reason: str,
) -> None:
    result = evaluate_probability_gates(
        ensemble,
        z_path=0.75,
        executable_entry_price=0.60,
        execution_costs=0.01,
        hard_failures=hard_failures,
    )

    assert result.decision_hint == "BLOCK"
    assert expected_reason in result.reasons
