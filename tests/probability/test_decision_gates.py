from typing import Any

import pytest

from polymarket_engine.probability.decision_gates import (
    ExecutableQualityInput,
    evaluate_probability_gates,
)
from polymarket_engine.probability.ensemble_outputs import EnsembleOutput


def _ensemble(
    *,
    p_finish: float = 0.80,
    p_no_touch: float = 0.80,
    z_path: float = 0.75,
    mc_dispersion: float = 0.03,
    uncertainty_buffer: float = 0.02,
    path_diagnosis: tuple[str, ...] = ("CLEAN",),
) -> EnsembleOutput:
    return EnsembleOutput(
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        z_path=z_path,
        mc_dispersion=mc_dispersion,
        uncertainty_buffer=uncertainty_buffer,
        path_diagnosis=path_diagnosis,
        effective_weights={},
    )


def _quality(
    *,
    executable_entry_price: float = 0.70,
    execution_costs: float = 0.01,
    quote_age_ms: int = 250,
    source_age_ms: int = 300,
    book_age_ms: int = 250,
    latency_ms: int = 150,
    hard_failures: Any = (),
    source_fresh: bool = True,
    book_fresh: bool = True,
) -> ExecutableQualityInput:
    return ExecutableQualityInput(
        executable_entry_price=executable_entry_price,
        execution_costs=execution_costs,
        quote_age_ms=quote_age_ms,
        source_age_ms=source_age_ms,
        book_age_ms=book_age_ms,
        latency_ms=latency_ms,
        hard_failures=hard_failures,
        source_fresh=source_fresh,
        book_fresh=book_fresh,
    )


def test_evaluate_probability_gates_returns_trade_candidate_when_edge_clears_buffers() -> None:
    result = evaluate_probability_gates(
        _ensemble(),
        _quality(),
    )

    assert result.decision_hint == "TRADE_CANDIDATE"
    assert result.edge_after_costs == pytest.approx(0.09)
    assert result.required_edge == pytest.approx(0.05)
    assert result.reasons == ()


def test_evaluate_probability_gates_demands_more_edge_when_edge_is_too_small() -> None:
    result = evaluate_probability_gates(
        _ensemble(p_finish=0.75),
        _quality(executable_entry_price=0.71),
    )

    assert result.decision_hint == "DEMAND_MORE_EDGE"
    assert result.edge_after_costs == pytest.approx(0.03)
    assert result.required_edge == pytest.approx(0.05)
    assert result.reasons == ("INSUFFICIENT_EDGE",)


def test_evaluate_probability_gates_waits_on_terminal_or_near_threshold_risk() -> None:
    result = evaluate_probability_gates(
        _ensemble(
            p_no_touch=0.52,
            z_path=0.20,
            path_diagnosis=("TERMINAL_ONLY", "NEAR_THRESHOLD"),
        ),
        _quality(executable_entry_price=0.60),
    )

    assert result.decision_hint == "WAIT"
    assert result.required_edge == pytest.approx(0.09)
    assert result.reasons == (
        "P_NO_TOUCH_BELOW_FLOOR",
        "Z_PATH_BELOW_FLOOR",
        "TERMINAL_ONLY",
        "NEAR_THRESHOLD",
    )


def test_evaluate_probability_gates_blocks_wrong_side_z_path_even_when_edge_clears() -> None:
    result = evaluate_probability_gates(
        _ensemble(p_finish=0.95, z_path=-2.0, path_diagnosis=("CLEAN",)),
        _quality(executable_entry_price=0.40),
    )

    assert result.decision_hint == "BLOCK"
    assert "WRONG_SIDE" in result.reasons
    assert "Z_PATH_BELOW_FLOOR" not in result.reasons
    assert result.edge_after_costs > result.required_edge


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
        _quality(executable_entry_price=0.60, hard_failures=hard_failures),
    )

    assert result.decision_hint == "BLOCK"
    assert expected_reason in result.reasons


def test_executable_quality_input_normalizes_list_hard_failures_and_rejects_string() -> None:
    quality = _quality(hard_failures=["STALE_OR_UNSAFE", "MANUAL_BLOCK"])

    assert quality.hard_failures == ("STALE_OR_UNSAFE", "MANUAL_BLOCK")
    with pytest.raises(ValueError, match="hard_failures"):
        _quality(hard_failures="STALE_OR_UNSAFE")


def test_evaluate_probability_gates_blocks_high_uncertainty_even_when_edge_clears() -> None:
    result = evaluate_probability_gates(
        _ensemble(p_finish=0.95, uncertainty_buffer=0.121),
        _quality(executable_entry_price=0.40),
    )

    assert result.decision_hint == "BLOCK"
    assert "UNCERTAINTY_BUFFER" in result.reasons
    assert result.edge_after_costs > result.required_edge


@pytest.mark.parametrize(
    ("quality", "expected_reason"),
    (
        (_quality(quote_age_ms=1501), "QUOTE_STALE"),
        (_quality(source_age_ms=2001), "SOURCE_STALE"),
        (_quality(book_age_ms=1501), "BOOK_STALE"),
        (_quality(latency_ms=501), "LATENCY_STALE"),
        (_quality(source_fresh=False), "SOURCE_NOT_FRESH"),
        (_quality(book_fresh=False), "BOOK_NOT_FRESH"),
    ),
)
def test_evaluate_probability_gates_blocks_on_first_class_freshness_inputs(
    quality: ExecutableQualityInput,
    expected_reason: str,
) -> None:
    result = evaluate_probability_gates(
        _ensemble(),
        quality,
    )

    assert result.decision_hint == "BLOCK"
    assert expected_reason in result.reasons
