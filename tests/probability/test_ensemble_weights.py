import math

import pytest

from polymarket_engine.probability.ensemble_weights import (
    DEFAULT_SEED_WEIGHTS,
    brier_loss,
    dynamic_weights_from_losses,
    log_loss,
)
from polymarket_engine.probability.generator_contracts import GeneratorId


def test_default_seed_weights_match_contract() -> None:
    assert DEFAULT_SEED_WEIGHTS == {
        GeneratorId.EMPIRICAL_CONDITIONAL: 0.40,
        GeneratorId.BLOCK_BOOTSTRAP: 0.25,
        GeneratorId.FILTERED_HISTORICAL: 0.25,
        GeneratorId.STRESS_OVERLAY: 0.10,
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


def test_dynamic_weights_from_losses_rewards_lower_loss_and_normalizes() -> None:
    losses = {
        GeneratorId.EMPIRICAL_CONDITIONAL: 0.10,
        GeneratorId.BLOCK_BOOTSTRAP: 0.60,
        GeneratorId.FILTERED_HISTORICAL: 0.80,
        GeneratorId.STRESS_OVERLAY: 1.20,
    }

    weights = dynamic_weights_from_losses(
        losses,
        DEFAULT_SEED_WEIGHTS,
        eta=1.5,
        weight_floor=0.02,
        stress_weight_cap=0.10,
    )

    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights[GeneratorId.EMPIRICAL_CONDITIONAL] > weights[GeneratorId.BLOCK_BOOTSTRAP]
    assert weights[GeneratorId.BLOCK_BOOTSTRAP] > weights[GeneratorId.STRESS_OVERLAY]
    assert weights[GeneratorId.STRESS_OVERLAY] <= 0.10


def test_dynamic_weights_from_losses_applies_floor_before_normalizing() -> None:
    losses = {
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
    )

    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights[GeneratorId.BLOCK_BOOTSTRAP] > 0.0
    assert weights[GeneratorId.STRESS_OVERLAY] <= 0.20
