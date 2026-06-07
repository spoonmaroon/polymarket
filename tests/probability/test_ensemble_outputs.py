from __future__ import annotations

from datetime import UTC, datetime

import pytest

from polymarket_engine.probability.ensemble_outputs import (
    GeneratorWeight,
    PathDiagnosis,
    reduce_ensemble,
)
from polymarket_engine.probability.generator_contracts import GeneratorId, GeneratorRun


def _run(
    generator_id: GeneratorId,
    p_finish: float,
    p_no_touch: float,
    sparse: bool = False,
) -> GeneratorRun:
    return GeneratorRun(
        generator_id=generator_id,
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        path_count=1000,
        effective_path_count=900,
        seed=7,
        asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        runtime_ms=3.0,
        sparse=sparse,
        diagnostics={},
    )


def test_reduce_ensemble_caps_stress_overlay_so_it_cannot_improve_probability() -> None:
    result = reduce_ensemble(
        runs=(
            _run(GeneratorId.EMPIRICAL_CONDITIONAL, 0.60, 0.55),
            _run(GeneratorId.BLOCK_BOOTSTRAP, 0.58, 0.52),
            _run(GeneratorId.FILTERED_HISTORICAL, 0.62, 0.57),
            _run(GeneratorId.STRESS_OVERLAY, 0.90, 0.88),
        ),
        weights=(
            GeneratorWeight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.40),
            GeneratorWeight(GeneratorId.BLOCK_BOOTSTRAP, 0.25),
            GeneratorWeight(GeneratorId.FILTERED_HISTORICAL, 0.25),
            GeneratorWeight(GeneratorId.STRESS_OVERLAY, 0.10),
        ),
        base_model_buffer=0.01,
    )

    assert round(result.p_finish, 4) == 0.6000
    assert result.effective_generator_values["stress_overlay"]["p_finish"] == 0.60
    assert result.path_diagnosis == PathDiagnosis.CLEAN


def test_reduce_ensemble_marks_sparse_output() -> None:
    result = reduce_ensemble(
        runs=(
            _run(GeneratorId.EMPIRICAL_CONDITIONAL, 0.60, 0.55, sparse=True),
            _run(GeneratorId.BLOCK_BOOTSTRAP, 0.58, 0.52),
            _run(GeneratorId.FILTERED_HISTORICAL, 0.62, 0.57),
            _run(GeneratorId.STRESS_OVERLAY, 0.50, 0.44),
        ),
        weights=(
            GeneratorWeight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.40),
            GeneratorWeight(GeneratorId.BLOCK_BOOTSTRAP, 0.25),
            GeneratorWeight(GeneratorId.FILTERED_HISTORICAL, 0.25),
            GeneratorWeight(GeneratorId.STRESS_OVERLAY, 0.10),
        ),
        base_model_buffer=0.01,
    )

    assert result.path_diagnosis == PathDiagnosis.SPARSE
    assert result.uncertainty_buffer > 0.01


def test_reduce_ensemble_requires_weights_to_match_runs_exactly() -> None:
    with pytest.raises(ValueError, match="weights must match runs"):
        reduce_ensemble(
            runs=(
                _run(GeneratorId.EMPIRICAL_CONDITIONAL, 0.60, 0.55),
                _run(GeneratorId.BLOCK_BOOTSTRAP, 0.58, 0.52),
            ),
            weights=(
                GeneratorWeight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.40),
                GeneratorWeight(GeneratorId.STRESS_OVERLAY, 0.10),
            ),
            base_model_buffer=0.01,
        )


def test_reduce_ensemble_rejects_zero_total_weight() -> None:
    with pytest.raises(ValueError, match="weights must sum positive"):
        reduce_ensemble(
            runs=(
                _run(GeneratorId.EMPIRICAL_CONDITIONAL, 0.60, 0.55),
                _run(GeneratorId.BLOCK_BOOTSTRAP, 0.58, 0.52),
            ),
            weights=(
                GeneratorWeight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.0),
                GeneratorWeight(GeneratorId.BLOCK_BOOTSTRAP, 0.0),
            ),
            base_model_buffer=0.01,
        )
