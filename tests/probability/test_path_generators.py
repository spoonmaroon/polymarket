from __future__ import annotations

from datetime import UTC, datetime

from polymarket_engine.probability.generator_contracts import GeneratorId
from polymarket_engine.probability.path_generators import run_generator_suite
from polymarket_engine.probability.schema import ProbabilityInput


def _input() -> ProbabilityInput:
    return ProbabilityInput(
        state_id="state-btc-up",
        asof_ts=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        asset="BTC",
        side="UP",
        comparison_operator=">=",
        seconds_left=120.0,
        settlement_price=100.0,
        threshold=100.5,
        sigma_tau=0.01,
        executable_price=0.52,
        source_age_ms=100,
        book_age_ms=100,
        z_path=-0.4,
    )


def test_generator_suite_uses_prior_fragments_for_all_four_generators() -> None:
    results = run_generator_suite(
        _input(),
        path_count=3,
        steps=2,
        seed=11,
        history_fragments=((100.0, 101.0, 102.0),),
    )

    paths_by_generator = {
        result.generator_id: result.paths for result in results
    }

    assert set(paths_by_generator) == {
        GeneratorId.EMPIRICAL_CONDITIONAL.value,
        GeneratorId.BLOCK_BOOTSTRAP.value,
        GeneratorId.FILTERED_HISTORICAL.value,
        GeneratorId.STRESS_OVERLAY.value,
    }
    for generator_id, paths in paths_by_generator.items():
        assert all(path[0] == 100.0 for path in paths)
        if generator_id != GeneratorId.STRESS_OVERLAY.value:
            assert all(path[-1] > 100.0 for path in paths)
    assert paths_by_generator[GeneratorId.STRESS_OVERLAY.value][0][-1] < paths_by_generator[
        GeneratorId.EMPIRICAL_CONDITIONAL.value
    ][0][-1]
