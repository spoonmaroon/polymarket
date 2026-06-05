from dataclasses import replace
from datetime import datetime, timezone

import pytest

from polymarket_engine.probability.monte_carlo import run_seeded_monte_carlo, score_paths
from polymarket_engine.probability.schema import ProbabilityInput


def _probability_input(side: str = "UP", comparison_operator: str | None = None) -> ProbabilityInput:
    if comparison_operator is None:
        comparison_operator = ">=" if side == "UP" else "<"
    return ProbabilityInput(
        state_id=f"state-{side}",
        asof_ts=datetime(2026, 6, 2, 17, 0, tzinfo=timezone.utc),
        asset="BTC",
        side=side,
        comparison_operator=comparison_operator,
        seconds_left=120.0,
        settlement_price=100.0,
        threshold=100.0,
        sigma_tau=0.02,
        executable_price=0.52,
        source_age_ms=100,
        book_age_ms=150,
        z_path=0.0,
    )


def test_score_paths_counts_terminal_and_no_touch_wins_for_explicit_up_paths() -> None:
    probability_input = _probability_input()
    paths = ((100.0, 101.0, 102.0), (100.0, 99.0, 98.0))

    output = score_paths(
        probability_input,
        paths=paths,
        model_version="mc-fixture",
        seed=7,
    )

    assert output.state_id == probability_input.state_id
    assert output.asof_ts == probability_input.asof_ts
    assert output.z_path == probability_input.z_path
    assert output.p_finish == pytest.approx(0.5)
    assert output.p_no_touch == pytest.approx(0.5)
    assert output.model_version == "mc-fixture"
    assert output.seed == 7
    assert output.diagnostics == {"path_count": 2, "steps": 2, "model": "explicit_paths"}


@pytest.mark.parametrize(
    ("side", "comparison_operator", "expected_finish"),
    (
        ("UP", ">", 0.0),
        ("UP", ">=", 1.0),
        ("DOWN", "<", 0.0),
        ("DOWN", "<=", 1.0),
    ),
)
def test_score_paths_honors_exact_contract_comparison_operator(
    side: str,
    comparison_operator: str,
    expected_finish: float,
) -> None:
    probability_input = _probability_input(side=side, comparison_operator=comparison_operator)

    output = score_paths(
        probability_input,
        paths=((100.0, 100.0),),
        model_version="mc-fixture",
        seed=7,
    )

    assert output.p_finish == pytest.approx(expected_finish)


@pytest.mark.parametrize(
    ("side", "comparison_operator", "start_price"),
    (
        ("UP", ">", 100.0),
        ("UP", ">=", 99.99),
        ("DOWN", "<", 100.0),
        ("DOWN", "<=", 100.01),
    ),
)
def test_score_paths_sets_no_touch_zero_when_start_is_already_wrong_side(
    side: str,
    comparison_operator: str,
    start_price: float,
) -> None:
    probability_input = _probability_input(side=side, comparison_operator=comparison_operator)

    output = score_paths(
        probability_input,
        paths=((start_price, 101.0 if side == "UP" else 99.0),),
        model_version="mc-fixture",
        seed=7,
    )

    assert output.p_no_touch == pytest.approx(0.0)


def test_run_seeded_monte_carlo_is_deterministic_for_same_seed() -> None:
    probability_input = _probability_input()

    first = run_seeded_monte_carlo(
        probability_input,
        path_count=1000,
        steps=20,
        seed=123,
    )
    second = run_seeded_monte_carlo(
        probability_input,
        path_count=1000,
        steps=20,
        seed=123,
    )

    assert first == second
    assert 0.0 <= first.p_finish <= 1.0
    assert 0.0 <= first.p_no_touch <= 1.0
    assert first.seed == 123
    assert first.model_version == "offline-lognormal-chainlink-sigma-v1"
    assert first.diagnostics["path_count"] == 1000
    assert first.diagnostics["steps"] == 20
    assert first.diagnostics["model"] == "offline_lognormal_chainlink_sigma"


def test_run_seeded_monte_carlo_exposes_bounded_simulation_preview() -> None:
    probability_input = _probability_input()

    output = run_seeded_monte_carlo(
        probability_input,
        path_count=1000,
        steps=20,
        seed=123,
    )

    preview = output.diagnostics["simulation_preview"]
    assert preview["path_count"] == 1000
    assert preview["steps"] == 20
    assert preview["start_price"] == pytest.approx(100.0)
    assert preview["threshold"] == pytest.approx(100.0)
    assert preview["comparison_operator"] == ">="
    assert preview["terminal_win_count"] <= 1000
    assert preview["no_touch_win_count"] <= 1000
    assert len(preview["sampled_paths"]) == 24
    assert len(preview["sampled_paths"][0]["points"]) == 21
    assert preview["sampled_paths"][0]["points"][0] == pytest.approx(100.0)
    assert isinstance(preview["sampled_paths"][0]["terminal_win"], bool)
    assert isinstance(preview["sampled_paths"][0]["no_touch_win"], bool)
    assert len(preview["terminal_histogram"]) == 16
    assert sum(bucket["count"] for bucket in preview["terminal_histogram"]) == 1000


def test_run_seeded_monte_carlo_rejects_generated_nonfinite_prices() -> None:
    probability_input = replace(_probability_input(), sigma_tau=1000.0)

    with pytest.raises(ValueError, match="path prices"):
        run_seeded_monte_carlo(
            probability_input,
            path_count=1000,
            steps=20,
            seed=123,
        )


@pytest.mark.parametrize("path_count", (0, -1))
def test_run_seeded_monte_carlo_rejects_nonpositive_path_count(path_count: int) -> None:
    with pytest.raises(ValueError, match="path_count"):
        run_seeded_monte_carlo(_probability_input(), path_count=path_count, steps=20, seed=123)


@pytest.mark.parametrize("steps", (0, -1))
def test_run_seeded_monte_carlo_rejects_nonpositive_steps(steps: int) -> None:
    with pytest.raises(ValueError, match="steps"):
        run_seeded_monte_carlo(_probability_input(), path_count=1000, steps=steps, seed=123)


@pytest.mark.parametrize(
    "paths",
    (
        (),
        ((),),
        ((100.0, 0.0),),
        ((100.0, -1.0),),
        ((100.0, float("nan")),),
        ((100.0, float("inf")),),
    ),
)
def test_score_paths_rejects_invalid_paths(
    paths: tuple[tuple[float, ...], ...],
) -> None:
    with pytest.raises(ValueError, match="path"):
        score_paths(_probability_input(), paths=paths, model_version="mc-fixture", seed=7)


def test_score_paths_rejects_ragged_paths() -> None:
    paths = ((100.0, 101.0, 102.0), (100.0, 101.0))

    with pytest.raises(ValueError, match="same length"):
        score_paths(_probability_input(), paths=paths, model_version="mc-fixture", seed=7)
