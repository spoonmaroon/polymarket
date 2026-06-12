from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from polymarket_engine.probability.generator_contracts import GeneratorId
from polymarket_engine.research.generator_validation import (
    CalibrationBucket,
    GeneratorLabel,
    GeneratorPrediction,
    ProbabilityCalibrationRow,
    build_weight_candidate,
    build_calibration_buckets,
)


def test_weight_candidate_uses_only_labels_before_decision_time() -> None:
    candidate = build_weight_candidate(
        predictions=(
            GeneratorPrediction(
                "state-1",
                datetime(2026, 6, 1, tzinfo=UTC),
                GeneratorId.EMPIRICAL_CONDITIONAL,
                0.60,
                0.55,
            ),
            GeneratorPrediction(
                "state-2",
                datetime(2026, 6, 3, tzinfo=UTC),
                GeneratorId.EMPIRICAL_CONDITIONAL,
                0.40,
                0.45,
            ),
        ),
        labels=(
            GeneratorLabel("state-1", datetime(2026, 6, 2, tzinfo=UTC), True, True),
            GeneratorLabel("state-2", datetime(2026, 6, 7, tzinfo=UTC), False, False),
        ),
        decision_asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        min_labels=1,
        eta=3.0,
    )

    assert candidate.label_count == 1
    assert candidate.trained_through_ts == datetime(2026, 6, 2, tzinfo=UTC)
    assert candidate.sparse is False
    assert math.isclose(sum(candidate.weights.values()), 1.0)


def test_build_calibration_buckets_reports_underconfidence_near_certain_market() -> None:
    rows = build_calibration_buckets(
        (
            ProbabilityCalibrationRow(
                state_id="state-down-1",
                asof_ts=datetime(2026, 6, 7, 21, 44, tzinfo=UTC),
                side="DOWN",
                model_probability=0.889,
                market_probability=0.99,
                did_finish_win=True,
                seconds_left=8.0,
            ),
            ProbabilityCalibrationRow(
                state_id="state-down-2",
                asof_ts=datetime(2026, 6, 7, 21, 49, tzinfo=UTC),
                side="DOWN",
                model_probability=0.91,
                market_probability=0.98,
                did_finish_win=True,
                seconds_left=12.0,
            ),
        ),
        bucket_count=10,
    )

    assert rows[:1] == (
        CalibrationBucket(
            lower=0.8,
            upper=0.9,
            count=1,
            win_rate=1.0,
            mean_model_probability=0.889,
            mean_market_probability=0.99,
            mean_market_model_gap=0.101,
            brier=rows[0].brier,
        ),
    )
    assert rows[0].brier == pytest.approx((1.0 - 0.889) ** 2)
    assert rows[1:] == (
        CalibrationBucket(
            lower=0.9,
            upper=1.0,
            count=1,
            win_rate=1.0,
            mean_model_probability=0.91,
            mean_market_probability=0.98,
            mean_market_model_gap=0.07,
            brier=rows[1].brier,
        ),
    )
    assert rows[1].brier == pytest.approx((1.0 - 0.91) ** 2)


def test_weight_candidate_marks_sparse_when_labels_are_insufficient() -> None:
    candidate = build_weight_candidate(
        predictions=(),
        labels=(),
        decision_asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        min_labels=100,
        eta=3.0,
    )

    assert candidate.weights == {
        GeneratorId.EMPIRICAL_CONDITIONAL: 0.40,
        GeneratorId.BLOCK_BOOTSTRAP: 0.25,
        GeneratorId.FILTERED_HISTORICAL: 0.25,
        GeneratorId.STRESS_OVERLAY: 0.10,
    }
    assert candidate.label_count == 0
    assert candidate.trained_through_ts is None
    assert candidate.sparse is True


def test_weight_candidate_accepts_positional_arguments() -> None:
    candidate = build_weight_candidate(
        (),
        (),
        datetime(2026, 6, 6, tzinfo=UTC),
        100,
        3.0,
    )

    assert candidate.weights == {
        GeneratorId.EMPIRICAL_CONDITIONAL: 0.40,
        GeneratorId.BLOCK_BOOTSTRAP: 0.25,
        GeneratorId.FILTERED_HISTORICAL: 0.25,
        GeneratorId.STRESS_OVERLAY: 0.10,
    }
    assert candidate.label_count == 0
    assert candidate.trained_through_ts is None
    assert candidate.sparse is True


def test_weight_candidate_excludes_equal_or_later_asof_inputs() -> None:
    decision_ts = datetime(2026, 6, 6, tzinfo=UTC)

    candidate = build_weight_candidate(
        predictions=(
            GeneratorPrediction(
                "included",
                datetime(2026, 6, 4, 23, 59, tzinfo=UTC),
                GeneratorId.EMPIRICAL_CONDITIONAL,
                0.90,
                0.90,
            ),
            GeneratorPrediction(
                "prediction-at-decision",
                decision_ts,
                GeneratorId.BLOCK_BOOTSTRAP,
                0.99,
                0.99,
            ),
            GeneratorPrediction(
                "label-at-decision",
                datetime(2026, 6, 5, tzinfo=UTC),
                GeneratorId.FILTERED_HISTORICAL,
                0.99,
                0.99,
            ),
        ),
        labels=(
            GeneratorLabel("included", datetime(2026, 6, 5, tzinfo=UTC), True, True),
            GeneratorLabel("prediction-at-decision", datetime(2026, 6, 5, tzinfo=UTC), True, True),
            GeneratorLabel("label-at-decision", decision_ts, True, True),
        ),
        decision_asof_ts=decision_ts,
        min_labels=1,
        eta=2.0,
    )

    assert candidate.label_count == 1
    assert candidate.trained_through_ts == datetime(2026, 6, 5, tzinfo=UTC)
    assert candidate.sparse is False
    assert candidate.weights[GeneratorId.EMPIRICAL_CONDITIONAL] > candidate.weights[GeneratorId.BLOCK_BOOTSTRAP]


def test_weight_candidate_excludes_predictions_after_matched_label_timestamp() -> None:
    candidate = build_weight_candidate(
        predictions=(
            GeneratorPrediction(
                "settled-state",
                datetime(2026, 6, 4, tzinfo=UTC),
                GeneratorId.EMPIRICAL_CONDITIONAL,
                0.99,
                0.99,
            ),
        ),
        labels=(
            GeneratorLabel(
                "settled-state",
                datetime(2026, 6, 3, tzinfo=UTC),
                True,
                True,
            ),
        ),
        decision_asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        min_labels=1,
        eta=3.0,
    )

    assert candidate.weights == {
        GeneratorId.EMPIRICAL_CONDITIONAL: 0.40,
        GeneratorId.BLOCK_BOOTSTRAP: 0.25,
        GeneratorId.FILTERED_HISTORICAL: 0.25,
        GeneratorId.STRESS_OVERLAY: 0.10,
    }
    assert candidate.label_count == 0
    assert candidate.trained_through_ts is None
    assert candidate.sparse is True


def test_weight_candidate_matches_predictions_to_labels_by_state_id() -> None:
    candidate = build_weight_candidate(
        predictions=(
            GeneratorPrediction(
                "state-a",
                datetime(2026, 6, 1, tzinfo=UTC),
                GeneratorId.EMPIRICAL_CONDITIONAL,
                0.99,
                0.99,
            ),
            GeneratorPrediction(
                "state-b",
                datetime(2026, 6, 1, tzinfo=UTC),
                GeneratorId.BLOCK_BOOTSTRAP,
                0.01,
                0.01,
            ),
        ),
        labels=(
            GeneratorLabel("state-a", datetime(2026, 6, 2, tzinfo=UTC), True, True),
            GeneratorLabel("state-c", datetime(2026, 6, 2, tzinfo=UTC), False, False),
        ),
        decision_asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        min_labels=1,
        eta=3.0,
    )

    assert candidate.label_count == 1
    assert candidate.weights[GeneratorId.EMPIRICAL_CONDITIONAL] > candidate.weights[GeneratorId.BLOCK_BOOTSTRAP]


@pytest.mark.parametrize(("min_labels", "eta"), [(0, 1.0), (-1, 1.0), (1, 0.0), (1, -0.1)])
def test_weight_candidate_rejects_invalid_training_parameters(min_labels: int, eta: float) -> None:
    with pytest.raises(ValueError):
        build_weight_candidate(
            predictions=(),
            labels=(),
            decision_asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
            min_labels=min_labels,
            eta=eta,
        )
