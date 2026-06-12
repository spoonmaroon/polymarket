from __future__ import annotations

import math
from collections.abc import Mapping

import pytest

from polymarket_engine.calibration.reports import build_calibration_report


JsonRecord = dict[str, object]


def _row(**overrides: object) -> JsonRecord:
    values: JsonRecord = {
        "state_id": "state-1",
        "contract_id": "condition-1",
        "market_slug": "btc-updown-5m-1781102700",
        "asset": "BTC",
        "side": "UP",
        "asof_ts": "2026-06-10T12:00:00+00:00",
        "expiry_ts": "2026-06-10T12:05:00+00:00",
        "tte_seconds": 30,
        "k": 65000.0,
        "current_price": 65020.0,
        "distance_to_threshold": 20.0,
        "z_path": 0.2,
        "sigma_tau": 0.015,
        "p_finish_mc": 0.8,
        "p_no_touch_mc": 0.64,
        "spread": 0.02,
        "best_bid": 0.79,
        "best_ask": 0.81,
        "midpoint": 0.8,
        "visible_depth": 1800.0,
        "orderbook_imbalance": 0.35,
        "quote_age_ms": 250.0,
        "source_age_ms": 1000.0,
        "volatility_regime": "normal",
        "probability_model_version": "mc-v1",
        "skip_or_block_reason": None,
        "final_label": 1,
        "resolved_outcome": "UP",
        "settlement_price_at_expiry": 65080.0,
    }
    values.update(overrides)
    return values


def _fixed_rows() -> list[JsonRecord]:
    return [
        _row(
            state_id="state-1",
            asset="BTC",
            side="UP",
            tte_seconds=30,
            z_path=0.2,
            distance_to_threshold=20.0,
            p_finish_mc=0.8,
            spread=0.02,
            visible_depth=1800.0,
            orderbook_imbalance=0.35,
            volatility_regime="normal",
            final_label=1,
        ),
        _row(
            state_id="state-2",
            asset="ETH",
            side="DOWN",
            tte_seconds=45,
            z_path=-0.8,
            distance_to_threshold=-75.0,
            p_finish_mc=0.7,
            spread=0.05,
            visible_depth=400.0,
            orderbook_imbalance=-0.3,
            volatility_regime="high",
            final_label=0,
        ),
        _row(
            state_id="state-3",
            asset="BTC",
            side="DOWN",
            tte_seconds=90,
            z_path=1.4,
            distance_to_threshold=150.0,
            p_finish_mc=0.6,
            spread=0.02,
            visible_depth=450.0,
            orderbook_imbalance=0.0,
            volatility_regime="low",
            final_label=1,
        ),
        _row(
            state_id="state-4",
            asset="ETH",
            side="UP",
            tte_seconds=240,
            z_path=-1.7,
            distance_to_threshold=-220.0,
            p_finish_mc=0.2,
            spread=0.08,
            visible_depth=2200.0,
            orderbook_imbalance=0.1,
            volatility_regime="normal",
            final_label=0,
        ),
    ]


def test_build_calibration_report_computes_core_metrics() -> None:
    report = build_calibration_report(_fixed_rows(), ece_bucket_count=4)

    assert report.input_row_count == 4
    assert report.evaluated_row_count == 4
    assert report.skipped_row_count == 0
    assert report.report_ready is True
    assert report.not_ready_reasons == ()
    assert report.brier_score is not None
    assert report.log_loss is not None
    assert report.expected_calibration_error is not None
    assert round(report.brier_score, 4) == 0.1825
    assert round(report.log_loss, 4) == 0.5403
    assert round(report.expected_calibration_error, 4) == 0.175
    assert report.bucket_counts["tte_0_60"] == 2
    assert report.min_bucket_sample_count == 1


def test_build_calibration_report_exposes_ece_buckets() -> None:
    report = build_calibration_report(_fixed_rows(), ece_bucket_count=4)
    assert report.expected_calibration_error is not None

    buckets = {bucket.bucket_key: bucket for bucket in report.ece_buckets}

    assert tuple(buckets) == ("prob_0.00_0.25", "prob_0.50_0.75", "prob_0.75_1.00")
    assert buckets["prob_0.00_0.25"].count == 1
    assert buckets["prob_0.50_0.75"].count == 2
    assert buckets["prob_0.75_1.00"].count == 1
    assert round(buckets["prob_0.50_0.75"].mean_probability, 4) == 0.65
    assert round(buckets["prob_0.50_0.75"].observed_rate, 4) == 0.5
    assert round(buckets["prob_0.50_0.75"].ece_contribution, 4) == 0.075


@pytest.mark.parametrize(
    ("expected_key", "expected_count"),
    [
        ("tte_0_60", 2),
        ("tte_60_180", 1),
        ("tte_180_plus", 1),
        ("z_path_near", 1),
        ("z_path_below", 2),
        ("z_path_above", 1),
        ("distance_near", 1),
        ("distance_mid", 1),
        ("distance_far", 2),
        ("volatility_regime_normal", 2),
        ("asset_BTC", 2),
        ("asset_ETH", 2),
        ("side_UP", 2),
        ("side_DOWN", 2),
        ("spread_depth_tight_deep", 1),
        ("spread_depth_wide_thin", 1),
        ("spread_depth_tight_thin", 1),
        ("spread_depth_wide_deep", 1),
        ("orderbook_imbalance_buy", 1),
        ("orderbook_imbalance_sell", 1),
        ("orderbook_imbalance_neutral", 2),
        ("final_window_30_60", 2),
        ("final_window_other", 2),
        ("threshold_congestion_near", 1),
        ("threshold_congestion_mid", 1),
        ("threshold_congestion_far", 2),
    ],
)
def test_build_calibration_report_counts_required_slice_buckets(
    expected_key: str,
    expected_count: int,
) -> None:
    report = build_calibration_report(_fixed_rows(), ece_bucket_count=4)

    assert report.bucket_counts[expected_key] == expected_count


@pytest.mark.parametrize(
    ("overrides", "field", "reason"),
    [
        ({"final_label": None}, "final_label", "missing_label"),
        ({"final_label": 2}, "final_label", "non_binary_label"),
        ({"p_finish_mc": 1.01}, "p_finish_mc", "probability_out_of_range"),
        ({"p_finish_mc": math.inf}, "p_finish_mc", "probability_non_finite"),
        ({"visible_depth": -1.0}, "visible_depth", "negative_replay_metric"),
    ],
)
def test_build_calibration_report_skips_bad_rows_with_validation_errors(
    overrides: Mapping[str, object],
    field: str,
    reason: str,
) -> None:
    rows = [
        _row(state_id="good", p_finish_mc=0.8, final_label=1),
        _row(state_id="bad", **overrides),
    ]

    report = build_calibration_report(rows, ece_bucket_count=4)

    assert report.input_row_count == 2
    assert report.evaluated_row_count == 1
    assert report.skipped_row_count == 1
    assert report.validation_error_counts == {reason: 1}
    assert len(report.validation_errors) == 1
    issue = report.validation_errors[0]
    assert issue.row_index == 1
    assert issue.state_id == "bad"
    assert issue.field == field
    assert issue.reason == reason
    assert report.brier_score is not None
    assert round(report.brier_score, 4) == 0.04


@pytest.mark.parametrize("block_reason", ["sigma_invalid", "k_unstable", "offload_blocked", "manual_hold"])
def test_build_calibration_report_skips_blocked_rows_before_metrics(block_reason: str) -> None:
    rows = [
        _row(state_id="good", p_finish_mc=0.8, final_label=1, skip_or_block_reason=None),
        _row(
            state_id="blocked",
            p_finish_mc=0.0,
            p_no_touch_mc=0.0,
            best_bid=0.0,
            best_ask=0.0,
            midpoint=0.0,
            final_label=1,
            skip_or_block_reason=block_reason,
        ),
    ]

    report = build_calibration_report(rows, ece_bucket_count=10)

    assert report.input_row_count == 2
    assert report.evaluated_row_count == 1
    assert report.skipped_row_count == 1
    expected_reason = f"blocked_{block_reason}"
    assert report.validation_error_counts == {expected_reason: 1}
    assert len(report.validation_errors) == 1
    issue = report.validation_errors[0]
    assert issue.row_index == 1
    assert issue.state_id == "blocked"
    assert issue.field == "skip_or_block_reason"
    assert issue.reason == expected_reason
    assert report.brier_score is not None
    assert round(report.brier_score, 4) == 0.04


def test_build_calibration_report_all_skipped_rows_has_unavailable_metrics() -> None:
    report = build_calibration_report(
        [
            _row(state_id="missing-label", final_label=None),
            _row(state_id="blocked", skip_or_block_reason="sigma_invalid"),
        ],
        ece_bucket_count=4,
    )

    assert report.input_row_count == 2
    assert report.evaluated_row_count == 0
    assert report.skipped_row_count == 2
    assert report.report_ready is False
    assert report.not_ready_reasons == ("no_evaluated_rows",)
    assert report.brier_score is None
    assert report.log_loss is None
    assert report.expected_calibration_error is None
    assert report.ece_buckets == ()
    assert report.bucket_counts == {}
    assert report.min_bucket_sample_count == 0
    assert report.validation_error_counts == {
        "blocked_sigma_invalid": 1,
        "missing_label": 1,
    }


def test_build_calibration_report_assigns_probability_boundary_buckets() -> None:
    report = build_calibration_report(
        [
            _row(
                state_id="prob-zero",
                p_finish_mc=0.0,
                p_no_touch_mc=0.0,
                best_bid=0.0,
                best_ask=0.0,
                midpoint=0.0,
                final_label=0,
            ),
            _row(
                state_id="prob-point-one",
                p_finish_mc=0.1,
                p_no_touch_mc=0.1,
                best_bid=0.1,
                best_ask=0.1,
                midpoint=0.1,
                final_label=0,
            ),
            _row(
                state_id="prob-one",
                p_finish_mc=1.0,
                p_no_touch_mc=1.0,
                best_bid=1.0,
                best_ask=1.0,
                midpoint=1.0,
                final_label=1,
            ),
        ],
        ece_bucket_count=10,
    )

    buckets = {bucket.bucket_key: bucket for bucket in report.ece_buckets}

    assert tuple(buckets) == ("prob_0.00_0.10", "prob_0.10_0.20", "prob_0.90_1.00")
    assert buckets["prob_0.00_0.10"].count == 1
    assert buckets["prob_0.10_0.20"].count == 1
    assert buckets["prob_0.90_1.00"].count == 1


def test_report_serializes_to_strict_json_shape() -> None:
    report = build_calibration_report(_fixed_rows(), ece_bucket_count=4)

    payload = report.to_json_dict()

    assert payload["schema_version"] == "polymarket-calibration-report-v1"
    assert payload["report_ready"] is True
    assert payload["not_ready_reasons"] == []
    assert payload["brier_score"] == report.brier_score
    assert _keys(payload["bucket_counts"]) == sorted(report.bucket_counts)
    assert _keys(payload["validation_error_counts"]) == []
    assert isinstance(payload["ece_buckets"], list)
    assert isinstance(payload["validation_errors"], list)


def _keys(value: object) -> list[str]:
    assert isinstance(value, dict)
    return sorted(str(key) for key in value)
