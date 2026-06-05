import math
from datetime import datetime, timezone

import pytest

from polymarket_engine.probability.ensemble_weights import (
    DEFAULT_SEED_WEIGHTS,
    DynamicWeightSet,
    brier_loss,
    dynamic_weights_from_losses,
    log_loss,
)
from polymarket_engine.probability.generator_contracts import (
    GeneratorId,
    HistoricalValidationWindow,
)


def _validation_window() -> HistoricalValidationWindow:
    return HistoricalValidationWindow(
        asof_ts=datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc),
        evaluated_through_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
        label_window_seconds=3600,
    )


def _runtime_asof() -> datetime:
    return datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)


def test_default_seed_weights_match_contract() -> None:
    assert DEFAULT_SEED_WEIGHTS == {
        GeneratorId.LOGNORMAL_BASELINE: 0.45,
        GeneratorId.EMPIRICAL_CONDITIONAL: 0.25,
        GeneratorId.BLOCK_BOOTSTRAP: 0.15,
        GeneratorId.FILTERED_HISTORICAL: 0.10,
        GeneratorId.STRESS_OVERLAY: 0.05,
    }


@pytest.mark.parametrize(
    ("probability", "label", "expected"),
    (
        (0.80, 1, -math.log(0.80)),
        (0.20, 0, -math.log(0.80)),
        (0.0, 1, -math.log(1e-6)),
        (1.0, 0, -math.log(1e-6)),
    ),
)
def test_log_loss_uses_binary_labels_and_clipping(
    probability: float,
    label: int,
    expected: float,
) -> None:
    assert log_loss(probability, label) == pytest.approx(expected)


def test_log_loss_rejects_invalid_probability_or_label() -> None:
    with pytest.raises(ValueError, match="probability"):
        log_loss(math.nan, 1)
    with pytest.raises(ValueError, match="label"):
        log_loss(0.5, 2)


@pytest.mark.parametrize(
    ("probability", "label", "expected"),
    (
        (0.80, 1, 0.04),
        (0.20, 0, 0.04),
    ),
)
def test_brier_loss_scores_binary_probability(
    probability: float,
    label: int,
    expected: float,
) -> None:
    assert brier_loss(probability, label) == pytest.approx(expected)


def test_dynamic_weights_from_losses_returns_historical_weight_set() -> None:
    losses = {
        GeneratorId.LOGNORMAL_BASELINE: 1.60,
        GeneratorId.EMPIRICAL_CONDITIONAL: 0.10,
        GeneratorId.BLOCK_BOOTSTRAP: 0.60,
        GeneratorId.FILTERED_HISTORICAL: 0.80,
        GeneratorId.STRESS_OVERLAY: 1.20,
    }
    validation_window = _validation_window()

    weight_set = dynamic_weights_from_losses(
        losses,
        DEFAULT_SEED_WEIGHTS,
        eta=1.5,
        weight_floor=0.02,
        stress_weight_cap=0.10,
        validation_window=validation_window,
        runtime_asof_ts=_runtime_asof(),
    )

    assert isinstance(weight_set, DynamicWeightSet)
    assert weight_set.validation_window == validation_window
    assert weight_set.runtime_asof_ts == _runtime_asof()
    weights = weight_set.weights
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights[GeneratorId.EMPIRICAL_CONDITIONAL] > weights[GeneratorId.LOGNORMAL_BASELINE]
    assert weights[GeneratorId.EMPIRICAL_CONDITIONAL] > weights[GeneratorId.BLOCK_BOOTSTRAP]
    assert weights[GeneratorId.BLOCK_BOOTSTRAP] > weights[GeneratorId.STRESS_OVERLAY]
    assert weights[GeneratorId.STRESS_OVERLAY] <= 0.10


def test_dynamic_weight_set_defensively_freezes_weights() -> None:
    weights = {
        GeneratorId.LOGNORMAL_BASELINE: 0.80,
        GeneratorId.STRESS_OVERLAY: 0.20,
    }

    weight_set = DynamicWeightSet(
        weights=weights,
        validation_window=_validation_window(),
        runtime_asof_ts=_runtime_asof(),
    )
    weights[GeneratorId.LOGNORMAL_BASELINE] = 0.01

    assert weight_set.weights[GeneratorId.LOGNORMAL_BASELINE] == pytest.approx(0.80)
    with pytest.raises(TypeError):
        weight_set.weights[GeneratorId.STRESS_OVERLAY] = 0.99  # type: ignore[index]


@pytest.mark.parametrize(
    "weights",
    (
        {GeneratorId.LOGNORMAL_BASELINE: -0.10, GeneratorId.STRESS_OVERLAY: 1.10},
        {GeneratorId.LOGNORMAL_BASELINE: math.nan, GeneratorId.STRESS_OVERLAY: 1.0},
        {GeneratorId.LOGNORMAL_BASELINE: math.inf, GeneratorId.STRESS_OVERLAY: 1.0},
    ),
)
def test_dynamic_weight_set_rejects_invalid_direct_weights(
    weights: dict[GeneratorId, float],
) -> None:
    with pytest.raises(ValueError, match="weight"):
        DynamicWeightSet(
            weights=weights,
            validation_window=_validation_window(),
            runtime_asof_ts=_runtime_asof(),
        )


def test_dynamic_weights_from_losses_applies_floor_before_normalizing() -> None:
    losses = {
        GeneratorId.LOGNORMAL_BASELINE: 0.20,
        GeneratorId.EMPIRICAL_CONDITIONAL: 0.20,
        GeneratorId.BLOCK_BOOTSTRAP: 100.0,
        GeneratorId.FILTERED_HISTORICAL: 0.20,
        GeneratorId.STRESS_OVERLAY: 0.20,
    }

    weights = dynamic_weights_from_losses(
        losses,
        DEFAULT_SEED_WEIGHTS,
        eta=2.0,
        weight_floor=0.05,
        stress_weight_cap=0.20,
        validation_window=_validation_window(),
        runtime_asof_ts=_runtime_asof(),
    ).weights

    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights[GeneratorId.BLOCK_BOOTSTRAP] > 0.0
    assert weights[GeneratorId.STRESS_OVERLAY] <= 0.20


def test_dynamic_weights_from_losses_rejects_future_label_artifact_for_runtime_asof() -> None:
    validation_window = HistoricalValidationWindow(
        asof_ts=datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc),
        evaluated_through_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
        label_window_seconds=3600,
    )

    with pytest.raises(ValueError, match="evaluated_through_ts"):
        dynamic_weights_from_losses(
            {GeneratorId.LOGNORMAL_BASELINE: 0.20},
            DEFAULT_SEED_WEIGHTS,
            eta=2.0,
            weight_floor=0.05,
            stress_weight_cap=0.20,
            validation_window=validation_window,
            runtime_asof_ts=datetime(2026, 6, 5, 16, 59, 59, tzinfo=timezone.utc),
        )
