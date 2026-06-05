import json
import math
from datetime import datetime, timezone

import pytest

from polymarket_engine.probability.ensemble_weights import DynamicWeightSet
from polymarket_engine.probability.generator_contracts import (
    DynamicWeightScope,
    GeneratorId,
    GeneratorWeight,
    HistoricalValidationWindow,
)
from polymarket_engine.research.generator_validation import (
    generator_weight_snapshot_payload,
    generator_weight_snapshot_payload_from_weights,
    generator_weight_snapshot_report,
)


def _runtime_asof() -> datetime:
    return datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)


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
        asof_ts=datetime(2026, 5, 31, 18, 0, tzinfo=timezone.utc),
        evaluated_through_ts=datetime(2026, 5, 31, 19, 0, tzinfo=timezone.utc),
        label_window_seconds=3600,
    )


def _weight_set() -> DynamicWeightSet:
    return DynamicWeightSet(
        weights={
            GeneratorId.LOGNORMAL_BASELINE: 0.60,
            GeneratorId.EMPIRICAL_CONDITIONAL: 0.25,
            GeneratorId.STRESS_OVERLAY: 0.15,
        },
        validation_window=_validation_window(),
        runtime_asof_ts=_runtime_asof(),
        source="fixture_losses",
    )


def test_generator_weight_snapshot_payload_is_json_safe_and_reports_metadata() -> None:
    payload = generator_weight_snapshot_payload(
        _weight_set(),
        scope=_scope(),
        snapshot_id="snapshot-1",
        scores={
            GeneratorId.LOGNORMAL_BASELINE: 0.29,
            GeneratorId.EMPIRICAL_CONDITIONAL: 0.34,
        },
        label_counts={
            GeneratorId.LOGNORMAL_BASELINE: 45,
            GeneratorId.EMPIRICAL_CONDITIONAL: 30,
            GeneratorId.STRESS_OVERLAY: 12,
        },
    )

    json.dumps(payload, allow_nan=False)
    assert payload["snapshot_id"] == "snapshot-1"
    assert payload["runtime_asof_ts"] == "2026-05-31T20:00:00+00:00"
    assert payload["evaluated_through_ts"] == "2026-05-31T19:00:00+00:00"
    assert payload["scope"]["asset"] == "BTC"
    assert payload["weights"]["lognormal_baseline"] == pytest.approx(0.60)
    assert payload["scores"]["empirical_conditional"] == pytest.approx(0.34)
    assert payload["label_counts"]["stress_overlay"] == 12

    report = generator_weight_snapshot_report(payload)

    assert report["effective_weights"] == payload["weights"]
    assert report["validation_cutoff"] == "2026-05-31T19:00:00+00:00"
    assert report["label_counts"] == payload["label_counts"]
    assert report["scores"] == payload["scores"]
    assert report["uses_future_labels"] is False
    assert report["unsafe_reasons"] == ()


def test_generator_weight_snapshot_payload_from_generator_weights() -> None:
    generator_weights = (
        GeneratorWeight(
            generator_id=GeneratorId.LOGNORMAL_BASELINE,
            weight=0.60,
            scope=_scope(),
            label_count=45,
            source="fixture_losses",
            validation_window=_validation_window(),
            score=0.29,
        ),
        GeneratorWeight(
            generator_id=GeneratorId.EMPIRICAL_CONDITIONAL,
            weight=0.40,
            scope=_scope(),
            label_count=30,
            source="fixture_losses",
            validation_window=_validation_window(),
            score=0.34,
        ),
    )

    payload = generator_weight_snapshot_payload_from_weights(
        generator_weights,
        runtime_asof_ts=_runtime_asof(),
        snapshot_id="snapshot-from-weights",
    )

    assert payload["snapshot_id"] == "snapshot-from-weights"
    assert payload["weights"] == {
        "empirical_conditional": pytest.approx(0.40),
        "lognormal_baseline": pytest.approx(0.60),
    }
    assert payload["scores"] == {
        "empirical_conditional": pytest.approx(0.34),
        "lognormal_baseline": pytest.approx(0.29),
    }
    assert payload["label_counts"] == {
        "empirical_conditional": 30,
        "lognormal_baseline": 45,
    }


def test_generator_weight_snapshot_report_flags_future_label_payload() -> None:
    payload = {
        "snapshot_id": "unsafe",
        "runtime_asof_ts": "2026-05-31T20:00:00+00:00",
        "evaluated_through_ts": "2026-05-31T20:00:01+00:00",
        "label_window_seconds": 3600,
        "source": "fixture_losses",
        "scope": {"asset": "BTC"},
        "weights": {"lognormal_baseline": 1.0},
        "scores": {},
        "label_counts": {"lognormal_baseline": 10},
    }

    report = generator_weight_snapshot_report(payload)

    assert report["uses_future_labels"] is True
    assert report["unsafe_reasons"] == ("FUTURE_LABELS",)


def test_generator_weight_snapshot_payload_rejects_non_strict_json_metadata() -> None:
    with pytest.raises(ValueError, match="scores"):
        generator_weight_snapshot_payload(
            _weight_set(),
            scope=_scope(),
            scores={GeneratorId.LOGNORMAL_BASELINE: math.nan},
        )
