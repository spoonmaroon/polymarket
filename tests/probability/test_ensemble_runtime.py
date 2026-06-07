from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Sequence

import pytest

from polymarket_engine.probability.generator_contracts import GeneratorId
from polymarket_engine.probability.path_generators import PathSimulationResult
from polymarket_engine.probability.schema import ProbabilityInput


def _probability_input() -> ProbabilityInput:
    return ProbabilityInput(
        state_id="state-1",
        asof_ts=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        asset="BTC",
        side="UP",
        comparison_operator=">",
        seconds_left=300.0,
        settlement_price=100.0,
        threshold=101.0,
        sigma_tau=0.02,
        executable_price=0.51,
        source_age_ms=100,
        book_age_ms=120,
        z_path=-0.25,
    )


def _result(
    generator_id: GeneratorId,
    *,
    p_finish_wins: int,
    p_no_touch_wins: int,
    path_count: int = 10,
    point_count: int = 2,
) -> PathSimulationResult:
    path = tuple(100.0 + index for index in range(point_count))
    return PathSimulationResult(
        generator_id=generator_id.value,
        paths=tuple(path for _ in range(path_count)),
        terminal_prices=tuple(path[-1] for _ in range(path_count)),
        terminal_wins=tuple(index < p_finish_wins for index in range(path_count)),
        no_touch_survivals=tuple(index < p_no_touch_wins for index in range(path_count)),
        max_adverse_excursions=tuple(0.0 for _ in range(path_count)),
    )


def _suite(
    _probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    history_fragments: Sequence[Sequence[float]] | None = None,
) -> tuple[PathSimulationResult, PathSimulationResult, PathSimulationResult, PathSimulationResult]:
    return (
        _result(GeneratorId.EMPIRICAL_CONDITIONAL, p_finish_wins=6, p_no_touch_wins=6),
        _result(GeneratorId.BLOCK_BOOTSTRAP, p_finish_wins=5, p_no_touch_wins=5),
        _result(GeneratorId.FILTERED_HISTORICAL, p_finish_wins=7, p_no_touch_wins=7),
        _result(GeneratorId.STRESS_OVERLAY, p_finish_wins=9, p_no_touch_wins=9),
    )


def test_run_four_generator_ensemble_caps_stress_overlay_so_it_cannot_improve_probability(
    monkeypatch,
) -> None:
    from polymarket_engine.probability import ensemble_runtime

    monkeypatch.setattr(ensemble_runtime, "run_generator_suite", _suite)

    output = ensemble_runtime.run_four_generator_ensemble(
        _probability_input(),
        path_count=10,
        steps=1,
        seed=17,
        history_fragments=((100.0, 101.0),),
    )

    assert output.model_version == "ensemble-v1"
    assert output.seed == 17
    assert round(output.p_finish, 4) == 0.6000
    assert output.diagnostics["effective_generator_values"]["stress_overlay"][
        "p_finish"
    ] == 0.6


def test_run_four_generator_ensemble_marks_sparse_scope_without_fragments(
    monkeypatch,
) -> None:
    from polymarket_engine.probability import ensemble_runtime

    monkeypatch.setattr(ensemble_runtime, "run_generator_suite", _suite)

    output = ensemble_runtime.run_four_generator_ensemble(
        _probability_input(),
        path_count=10,
        steps=1,
        seed=17,
    )

    assert output.diagnostics["sparse_scope"] is True
    assert output.diagnostics["path_diagnosis"] == "SPARSE"
    assert all(run["sparse"] is True for run in output.diagnostics["generator_runs"])
    assert all(
        row["sparse"] is True
        for row in output.diagnostics["generator_summary"].values()
    )


def test_run_four_generator_ensemble_diagnostics_include_four_generators_and_seed_weights(
    monkeypatch,
) -> None:
    from polymarket_engine.probability import ensemble_runtime

    monkeypatch.setattr(ensemble_runtime, "run_generator_suite", _suite)

    output = ensemble_runtime.run_four_generator_ensemble(
        _probability_input(),
        path_count=10,
        steps=1,
        seed=17,
        history_fragments=((100.0, 101.0),),
    )

    diagnostics = output.diagnostics
    expected_ids = {
        "empirical_conditional",
        "block_bootstrap",
        "filtered_historical",
        "stress_overlay",
    }

    assert set(diagnostics["generator_summary"]) == expected_ids
    assert {
        row["generator_id"] for row in diagnostics["generator_runs"]
    } == expected_ids
    assert diagnostics["effective_weights"] == {
        "empirical_conditional": 0.40,
        "block_bootstrap": 0.25,
        "filtered_historical": 0.25,
        "stress_overlay": 0.10,
    }
    assert diagnostics["model"] == "ensemble-v1"
    assert diagnostics["generator_version"] == "four-generator-ensemble-v1"
    assert diagnostics["path_count"] == 10
    assert diagnostics["steps"] == 1
    json.dumps(diagnostics, sort_keys=True, allow_nan=False)


def test_run_four_generator_ensemble_rejects_wrong_actual_path_count(
    monkeypatch,
) -> None:
    from polymarket_engine.probability import ensemble_runtime

    def suite_with_short_bootstrap(
        _probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: Sequence[Sequence[float]] | None = None,
    ) -> tuple[
        PathSimulationResult,
        PathSimulationResult,
        PathSimulationResult,
        PathSimulationResult,
    ]:
        return (
            _result(GeneratorId.EMPIRICAL_CONDITIONAL, p_finish_wins=6, p_no_touch_wins=6),
            _result(
                GeneratorId.BLOCK_BOOTSTRAP,
                p_finish_wins=5,
                p_no_touch_wins=5,
                path_count=9,
            ),
            _result(GeneratorId.FILTERED_HISTORICAL, p_finish_wins=7, p_no_touch_wins=7),
            _result(GeneratorId.STRESS_OVERLAY, p_finish_wins=4, p_no_touch_wins=4),
        )

    monkeypatch.setattr(ensemble_runtime, "run_generator_suite", suite_with_short_bootstrap)

    with pytest.raises(ValueError, match="path_count"):
        ensemble_runtime.run_four_generator_ensemble(
            _probability_input(),
            path_count=10,
            steps=1,
            seed=17,
            history_fragments=((100.0, 101.0),),
        )


def test_run_four_generator_ensemble_rejects_wrong_actual_step_count(
    monkeypatch,
) -> None:
    from polymarket_engine.probability import ensemble_runtime

    def suite_with_long_stress_path(
        _probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: Sequence[Sequence[float]] | None = None,
    ) -> tuple[
        PathSimulationResult,
        PathSimulationResult,
        PathSimulationResult,
        PathSimulationResult,
    ]:
        return (
            _result(GeneratorId.EMPIRICAL_CONDITIONAL, p_finish_wins=6, p_no_touch_wins=6),
            _result(GeneratorId.BLOCK_BOOTSTRAP, p_finish_wins=5, p_no_touch_wins=5),
            _result(GeneratorId.FILTERED_HISTORICAL, p_finish_wins=7, p_no_touch_wins=7),
            _result(
                GeneratorId.STRESS_OVERLAY,
                p_finish_wins=4,
                p_no_touch_wins=4,
                point_count=3,
            ),
        )

    monkeypatch.setattr(ensemble_runtime, "run_generator_suite", suite_with_long_stress_path)

    with pytest.raises(ValueError, match="steps"):
        ensemble_runtime.run_four_generator_ensemble(
            _probability_input(),
            path_count=10,
            steps=1,
            seed=17,
            history_fragments=((100.0, 101.0),),
        )
