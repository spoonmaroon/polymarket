import math
from datetime import datetime, timezone

import pytest

from polymarket_engine.probability.ensemble_outputs import EnsembleOutput, reduce_generator_runs
from polymarket_engine.probability.generator_contracts import (
    DynamicWeightScope,
    GeneratorId,
    GeneratorResult,
    GeneratorRun,
    GeneratorWeight,
    HistoricalValidationWindow,
)


def _asof() -> datetime:
    return datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc)


def _runtime_asof() -> datetime:
    return datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)


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


def _validation_window() -> HistoricalValidationWindow:
    return HistoricalValidationWindow(
        asof_ts=datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc),
        evaluated_through_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
        label_window_seconds=3600,
    )


def _run(
    generator_id: GeneratorId,
    *,
    p_finish: float,
    p_no_touch: float,
    z_path: float,
    asof_ts: datetime | None = None,
    scope: DynamicWeightScope | None = None,
    sparse: bool = False,
) -> GeneratorRun:
    return GeneratorRun(
        generator_id=generator_id,
        generator_name=generator_id.value,
        generator_version="fixture-v1",
        scope=scope or _scope(),
        conditioning={"asset": "BTC"},
        result=GeneratorResult(
            p_finish=p_finish,
            p_no_touch=p_no_touch,
            z_path=z_path,
            diagnostics={},
        ),
        path_count=10_000,
        steps=60,
        seed=11,
        asof_ts=asof_ts or _asof(),
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
        validation_window=_validation_window(),
    )


def _weight_with_scope(
    generator_id: GeneratorId,
    weight: float,
    scope: DynamicWeightScope,
) -> GeneratorWeight:
    return GeneratorWeight(
        generator_id=generator_id,
        weight=weight,
        scope=scope,
        label_count=100,
        source="fixture",
        validation_window=_validation_window(),
    )


def test_reduce_generator_runs_clamps_stress_overlay_and_computes_buffer() -> None:
    runs = (
        _run(GeneratorId.EMPIRICAL_CONDITIONAL, p_finish=0.80, p_no_touch=0.90, z_path=1.20),
        _run(GeneratorId.BLOCK_BOOTSTRAP, p_finish=0.60, p_no_touch=0.80, z_path=0.80),
        _run(GeneratorId.FILTERED_HISTORICAL, p_finish=0.70, p_no_touch=0.70, z_path=1.00),
        _run(GeneratorId.STRESS_OVERLAY, p_finish=0.95, p_no_touch=0.95, z_path=2.00),
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
        runtime_asof_ts=_runtime_asof(),
        sparse_scope=False,
        calibration_penalty=0.015,
        stale_weight_penalty=0.020,
    )

    assert output.p_finish == pytest.approx(0.715)
    assert output.p_no_touch == pytest.approx(0.815)
    assert output.z_path == pytest.approx(1.03)
    assert output.mc_dispersion == pytest.approx(0.10)
    assert output.uncertainty_buffer == pytest.approx(0.095)
    assert output.path_diagnosis == ("FRAGILE",)
    assert output.effective_weights == {
        GeneratorId.EMPIRICAL_CONDITIONAL: pytest.approx(0.40),
        GeneratorId.BLOCK_BOOTSTRAP: pytest.approx(0.25),
        GeneratorId.FILTERED_HISTORICAL: pytest.approx(0.25),
        GeneratorId.STRESS_OVERLAY: pytest.approx(0.10),
    }
    with pytest.raises(TypeError):
        output.effective_weights[GeneratorId.STRESS_OVERLAY] = 0.99  # type: ignore[index]


@pytest.mark.parametrize(
    "effective_weights",
    (
        {GeneratorId.LOGNORMAL_BASELINE: -0.10, GeneratorId.STRESS_OVERLAY: 1.10},
        {GeneratorId.LOGNORMAL_BASELINE: math.nan},
        {GeneratorId.LOGNORMAL_BASELINE: math.inf},
    ),
)
def test_ensemble_output_rejects_invalid_direct_effective_weights(
    effective_weights: dict[GeneratorId, float],
) -> None:
    with pytest.raises(ValueError, match="effective_weights"):
        EnsembleOutput(
            p_finish=0.50,
            p_no_touch=0.80,
            z_path=1.0,
            mc_dispersion=0.01,
            uncertainty_buffer=0.02,
            path_diagnosis=("CLEAN",),
            effective_weights=effective_weights,
        )


def test_reduce_generator_runs_rejects_future_generator_run_when_runtime_asof_provided() -> None:
    with pytest.raises(ValueError, match="asof_ts"):
        reduce_generator_runs(
            (
                _run(
                    GeneratorId.LOGNORMAL_BASELINE,
                    p_finish=0.60,
                    p_no_touch=0.80,
                    z_path=1.20,
                    asof_ts=datetime(2026, 6, 5, 18, 0, 1, tzinfo=timezone.utc),
                ),
            ),
            {GeneratorId.LOGNORMAL_BASELINE: 1.0},
            runtime_asof_ts=_runtime_asof(),
            sparse_scope=False,
            calibration_penalty=0.0,
            stale_weight_penalty=0.0,
        )


def test_reduce_generator_runs_rejects_mixed_run_asof_or_scopes() -> None:
    with pytest.raises(ValueError, match="asof_ts"):
        reduce_generator_runs(
            (
                _run(GeneratorId.LOGNORMAL_BASELINE, p_finish=0.60, p_no_touch=0.80, z_path=1.20),
                _run(
                    GeneratorId.EMPIRICAL_CONDITIONAL,
                    p_finish=0.61,
                    p_no_touch=0.81,
                    z_path=1.30,
                    asof_ts=datetime(2026, 6, 5, 16, 0, 1, tzinfo=timezone.utc),
                ),
            ),
            {
                GeneratorId.LOGNORMAL_BASELINE: 0.50,
                GeneratorId.EMPIRICAL_CONDITIONAL: 0.50,
            },
            sparse_scope=False,
            calibration_penalty=0.0,
            stale_weight_penalty=0.0,
        )

    with pytest.raises(ValueError, match="scope"):
        reduce_generator_runs(
            (
                _run(GeneratorId.LOGNORMAL_BASELINE, p_finish=0.60, p_no_touch=0.80, z_path=1.20),
                _run(
                    GeneratorId.EMPIRICAL_CONDITIONAL,
                    p_finish=0.61,
                    p_no_touch=0.81,
                    z_path=1.30,
                    scope=DynamicWeightScope(
                        asset="BTC",
                        horizon_seconds=300,
                        seconds_left_bucket="120-180",
                        z_path_bucket="far",
                        vol_regime="normal",
                        vol_trend="flat",
                        wick_regime="quiet",
                        source_quality_state="ready",
                    ),
                ),
            ),
            {
                GeneratorId.LOGNORMAL_BASELINE: 0.50,
                GeneratorId.EMPIRICAL_CONDITIONAL: 0.50,
            },
            sparse_scope=False,
            calibration_penalty=0.0,
            stale_weight_penalty=0.0,
        )


def test_reduce_generator_runs_rejects_duplicate_run_or_weight_generator_ids() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        reduce_generator_runs(
            (
                _run(GeneratorId.LOGNORMAL_BASELINE, p_finish=0.60, p_no_touch=0.80, z_path=1.20),
                _run(GeneratorId.LOGNORMAL_BASELINE, p_finish=0.61, p_no_touch=0.81, z_path=1.30),
            ),
            {GeneratorId.LOGNORMAL_BASELINE: 1.0},
            sparse_scope=False,
            calibration_penalty=0.0,
            stale_weight_penalty=0.0,
        )

    with pytest.raises(ValueError, match="duplicate"):
        reduce_generator_runs(
            (
                _run(GeneratorId.LOGNORMAL_BASELINE, p_finish=0.60, p_no_touch=0.80, z_path=1.20),
            ),
            (
                _weight(GeneratorId.LOGNORMAL_BASELINE, 0.50),
                _weight(GeneratorId.LOGNORMAL_BASELINE, 0.50),
            ),
            runtime_asof_ts=_runtime_asof(),
            sparse_scope=False,
            calibration_penalty=0.0,
            stale_weight_penalty=0.0,
        )


def test_reduce_generator_runs_rejects_generator_weight_scope_mismatch() -> None:
    run = _run(GeneratorId.LOGNORMAL_BASELINE, p_finish=0.60, p_no_touch=0.80, z_path=1.20)
    mismatched_scope = DynamicWeightScope(
        asset="BTC",
        horizon_seconds=300,
        seconds_left_bucket="120-180",
        z_path_bucket="far",
        vol_regime="normal",
        vol_trend="flat",
        wick_regime="quiet",
        source_quality_state="ready",
    )

    with pytest.raises(ValueError, match="scope"):
        reduce_generator_runs(
            (run,),
            (_weight_with_scope(GeneratorId.LOGNORMAL_BASELINE, 1.0, mismatched_scope),),
            runtime_asof_ts=_runtime_asof(),
            sparse_scope=False,
            calibration_penalty=0.0,
            stale_weight_penalty=0.0,
        )


def test_reduce_generator_runs_reports_all_path_diagnosis_labels() -> None:
    runs = (
        _run(
            GeneratorId.EMPIRICAL_CONDITIONAL,
            p_finish=0.90,
            p_no_touch=0.40,
            z_path=0.10,
            sparse=True,
        ),
        _run(GeneratorId.BLOCK_BOOTSTRAP, p_finish=0.10, p_no_touch=0.45, z_path=0.20),
        _run(GeneratorId.FILTERED_HISTORICAL, p_finish=0.55, p_no_touch=0.50, z_path=0.30),
    )
    weights = (
        _weight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.34),
        _weight(GeneratorId.BLOCK_BOOTSTRAP, 0.33),
        _weight(GeneratorId.FILTERED_HISTORICAL, 0.33),
    )

    output = reduce_generator_runs(
        runs,
        weights,
        runtime_asof_ts=_runtime_asof(),
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
        _run(GeneratorId.EMPIRICAL_CONDITIONAL, p_finish=0.61, p_no_touch=0.80, z_path=1.20),
        _run(GeneratorId.BLOCK_BOOTSTRAP, p_finish=0.60, p_no_touch=0.79, z_path=1.10),
        _run(GeneratorId.FILTERED_HISTORICAL, p_finish=0.62, p_no_touch=0.81, z_path=1.30),
    )
    weights = (
        _weight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.40),
        _weight(GeneratorId.BLOCK_BOOTSTRAP, 0.30),
        _weight(GeneratorId.FILTERED_HISTORICAL, 0.30),
    )

    output = reduce_generator_runs(
        runs,
        weights,
        runtime_asof_ts=_runtime_asof(),
        sparse_scope=False,
        calibration_penalty=0.0,
        stale_weight_penalty=0.0,
    )

    assert output.path_diagnosis == ("CLEAN",)
    assert output.uncertainty_buffer == pytest.approx(0.015)


def test_reduce_generator_runs_reports_wrong_side_for_negative_z_path() -> None:
    output = reduce_generator_runs(
        (
            _run(
                GeneratorId.LOGNORMAL_BASELINE,
                p_finish=0.62,
                p_no_touch=0.80,
                z_path=-2.0,
            ),
        ),
        {GeneratorId.LOGNORMAL_BASELINE: 1.0},
        sparse_scope=False,
        calibration_penalty=0.0,
        stale_weight_penalty=0.0,
    )

    assert output.z_path == pytest.approx(-2.0)
    assert output.path_diagnosis == ("WRONG_SIDE",)


def test_reduce_generator_runs_rejects_future_label_weight_artifact_at_runtime() -> None:
    future_weight = GeneratorWeight(
        generator_id=GeneratorId.LOGNORMAL_BASELINE,
        weight=1.0,
        scope=_scope(),
        label_count=100,
        source="fixture",
        validation_window=HistoricalValidationWindow(
            asof_ts=datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc),
            evaluated_through_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
            label_window_seconds=3600,
        ),
    )

    with pytest.raises(ValueError, match="evaluated_through_ts"):
        reduce_generator_runs(
            (
                _run(
                    GeneratorId.LOGNORMAL_BASELINE,
                    p_finish=0.60,
                    p_no_touch=0.80,
                    z_path=1.20,
                ),
            ),
            (future_weight,),
            runtime_asof_ts=datetime(2026, 6, 5, 16, 59, 59, tzinfo=timezone.utc),
            sparse_scope=False,
            calibration_penalty=0.0,
            stale_weight_penalty=0.0,
        )


def test_reduce_generator_runs_allows_static_mapping_weights_without_runtime_asof() -> None:
    output = reduce_generator_runs(
        (
            _run(
                GeneratorId.LOGNORMAL_BASELINE,
                p_finish=0.60,
                p_no_touch=0.80,
                z_path=1.20,
            ),
        ),
        {GeneratorId.LOGNORMAL_BASELINE: 1.0},
        sparse_scope=False,
        calibration_penalty=0.0,
        stale_weight_penalty=0.0,
    )

    assert output.p_finish == pytest.approx(0.60)
    assert output.p_no_touch == pytest.approx(0.80)
    assert output.z_path == pytest.approx(1.20)


def test_reduce_generator_runs_clamps_stress_overlay_against_lognormal_baseline_only() -> None:
    runs = (
        _run(GeneratorId.LOGNORMAL_BASELINE, p_finish=0.58, p_no_touch=0.76, z_path=0.90),
        _run(GeneratorId.STRESS_OVERLAY, p_finish=0.99, p_no_touch=0.99, z_path=2.00),
    )
    weights = (
        _weight(GeneratorId.LOGNORMAL_BASELINE, 0.80),
        _weight(GeneratorId.STRESS_OVERLAY, 0.20),
    )

    output = reduce_generator_runs(
        runs,
        weights,
        runtime_asof_ts=_runtime_asof(),
        sparse_scope=False,
        calibration_penalty=0.0,
        stale_weight_penalty=0.0,
    )

    assert output.p_finish == pytest.approx(0.58)
    assert output.p_no_touch == pytest.approx(0.76)
    assert output.z_path == pytest.approx(0.90)


def test_reduce_generator_runs_caps_and_redistributes_dominant_stress_weight() -> None:
    runs = (
        _run(GeneratorId.LOGNORMAL_BASELINE, p_finish=0.01, p_no_touch=0.20, z_path=0.10),
        _run(GeneratorId.STRESS_OVERLAY, p_finish=0.99, p_no_touch=0.99, z_path=2.00),
    )
    weights = {
        GeneratorId.LOGNORMAL_BASELINE: 0.01,
        GeneratorId.STRESS_OVERLAY: 0.99,
    }

    output = reduce_generator_runs(
        runs,
        weights,
        sparse_scope=False,
        calibration_penalty=0.0,
        stale_weight_penalty=0.0,
    )

    assert output.effective_weights[GeneratorId.STRESS_OVERLAY] == pytest.approx(0.15)
    assert output.effective_weights[GeneratorId.LOGNORMAL_BASELINE] == pytest.approx(0.85)
    assert output.p_finish == pytest.approx(0.01)
    assert output.p_no_touch == pytest.approx(0.20)
    assert output.z_path == pytest.approx(0.10)


def test_reduce_generator_runs_rejects_stress_only_reduction() -> None:
    with pytest.raises(ValueError, match="non-stress"):
        reduce_generator_runs(
            (
                _run(
                    GeneratorId.STRESS_OVERLAY,
                    p_finish=0.10,
                    p_no_touch=0.20,
                    z_path=-1.0,
                ),
            ),
            {GeneratorId.STRESS_OVERLAY: 1.0},
            sparse_scope=False,
            calibration_penalty=0.0,
            stale_weight_penalty=0.0,
        )
