from datetime import datetime, timezone

import pytest

from polymarket_engine.probability.ensemble_outputs import reduce_generator_runs
from polymarket_engine.probability.generator_contracts import (
    DynamicWeightScope,
    GeneratorId,
    GeneratorRun,
    GeneratorWeight,
)


def _asof() -> datetime:
    return datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc)


def _scope() -> DynamicWeightScope:
    return DynamicWeightScope(
        asset="BTC",
        horizon_seconds=300,
        seconds_left_bucket="60-120",
        z_path_bucket="far",
        vol_regime="normal",
        vol_trend="flat",
        wick_regime="quiet",
        source_quality_state="ready",
    )


def _run(
    generator_id: GeneratorId,
    *,
    p_finish: float,
    p_no_touch: float,
    sparse: bool = False,
) -> GeneratorRun:
    return GeneratorRun(
        generator_id=generator_id,
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        path_count=10_000,
        seed=11,
        asof_ts=_asof(),
        diagnostics={},
        sparse=sparse,
    )


def _weight(generator_id: GeneratorId, weight: float) -> GeneratorWeight:
    return GeneratorWeight(
        generator_id=generator_id,
        weight=weight,
        scope=_scope(),
        label_count=100,
        source="fixture",
    )


def test_reduce_generator_runs_clamps_stress_overlay_and_computes_buffer() -> None:
    runs = (
        _run(GeneratorId.EMPIRICAL_CONDITIONAL, p_finish=0.80, p_no_touch=0.90),
        _run(GeneratorId.BLOCK_BOOTSTRAP, p_finish=0.60, p_no_touch=0.80),
        _run(GeneratorId.FILTERED_HISTORICAL, p_finish=0.70, p_no_touch=0.70),
        _run(GeneratorId.STRESS_OVERLAY, p_finish=0.95, p_no_touch=0.95),
    )
    weights = (
        _weight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.40),
        _weight(GeneratorId.BLOCK_BOOTSTRAP, 0.25),
        _weight(GeneratorId.FILTERED_HISTORICAL, 0.25),
        _weight(GeneratorId.STRESS_OVERLAY, 0.10),
    )

    output = reduce_generator_runs(
        runs,
        weights,
        z_path=1.0,
        sparse_scope=False,
        calibration_penalty=0.015,
        stale_weight_penalty=0.020,
    )

    assert output.p_finish == pytest.approx(0.715)
    assert output.p_no_touch == pytest.approx(0.815)
    assert output.mc_dispersion == pytest.approx(0.10)
    assert output.uncertainty_buffer == pytest.approx(0.095)
    assert output.path_diagnosis == ("FRAGILE",)
    assert output.effective_weights == {
        GeneratorId.EMPIRICAL_CONDITIONAL: pytest.approx(0.40),
        GeneratorId.BLOCK_BOOTSTRAP: pytest.approx(0.25),
        GeneratorId.FILTERED_HISTORICAL: pytest.approx(0.25),
        GeneratorId.STRESS_OVERLAY: pytest.approx(0.10),
    }


def test_reduce_generator_runs_reports_all_path_diagnosis_labels() -> None:
    runs = (
        _run(GeneratorId.EMPIRICAL_CONDITIONAL, p_finish=0.90, p_no_touch=0.40, sparse=True),
        _run(GeneratorId.BLOCK_BOOTSTRAP, p_finish=0.10, p_no_touch=0.45),
        _run(GeneratorId.FILTERED_HISTORICAL, p_finish=0.55, p_no_touch=0.50),
    )
    weights = (
        _weight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.34),
        _weight(GeneratorId.BLOCK_BOOTSTRAP, 0.33),
        _weight(GeneratorId.FILTERED_HISTORICAL, 0.33),
    )

    output = reduce_generator_runs(
        runs,
        weights,
        z_path=0.10,
        sparse_scope=True,
        calibration_penalty=0.0,
        stale_weight_penalty=0.0,
    )

    assert output.path_diagnosis == (
        "SPARSE",
        "NEAR_THRESHOLD",
        "TERMINAL_ONLY",
        "FRAGILE",
    )


def test_reduce_generator_runs_reports_clean_when_no_risk_labels_apply() -> None:
    runs = (
        _run(GeneratorId.EMPIRICAL_CONDITIONAL, p_finish=0.61, p_no_touch=0.80),
        _run(GeneratorId.BLOCK_BOOTSTRAP, p_finish=0.60, p_no_touch=0.79),
        _run(GeneratorId.FILTERED_HISTORICAL, p_finish=0.62, p_no_touch=0.81),
    )
    weights = (
        _weight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.40),
        _weight(GeneratorId.BLOCK_BOOTSTRAP, 0.30),
        _weight(GeneratorId.FILTERED_HISTORICAL, 0.30),
    )

    output = reduce_generator_runs(
        runs,
        weights,
        z_path=1.2,
        sparse_scope=False,
        calibration_penalty=0.0,
        stale_weight_penalty=0.0,
    )

    assert output.path_diagnosis == ("CLEAN",)
    assert output.uncertainty_buffer == pytest.approx(0.015)
