from __future__ import annotations

from polymarket_engine.calibration.features import DEFAULT_FEATURE_NAMES
from polymarket_engine.calibration.features import feature_matrix


def _row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "p_finish_mc": 0.8,
        "p_no_touch_mc": 0.6,
        "mc_generator_dispersion": 0.04,
        "tte_seconds": 120,
        "z_path": 0.7,
        "sigma_tau": 0.015,
        "distance_to_threshold": 50.0,
        "spread": 0.03,
        "orderbook_imbalance": -0.2,
        "visible_depth": 1000.0,
        "quote_age_ms": 250.0,
        "source_age_ms": 800.0,
        "threshold_cross_count": 2,
        "near_threshold_congestion": 4,
        "recent_wick_size": 0.001,
        "asset": "BTC",
        "side": "UP",
        "volatility_regime": "normal",
        "final_label": 1,
    }
    values.update(overrides)
    return values


def test_feature_matrix_uses_stable_feature_order() -> None:
    matrix, labels = feature_matrix([_row(), _row(asset="ETH", side="DOWN", final_label=0)])

    assert labels == [1, 0]
    assert len(matrix) == 2
    assert len(matrix[0]) == len(DEFAULT_FEATURE_NAMES)
    assert DEFAULT_FEATURE_NAMES[:4] == (
        "logit_p_finish_mc",
        "p_no_touch_mc",
        "mc_generator_dispersion",
        "tte_seconds",
    )
    assert matrix[0][DEFAULT_FEATURE_NAMES.index("asset_BTC")] == 1.0
    assert matrix[1][DEFAULT_FEATURE_NAMES.index("asset_ETH")] == 1.0
    assert matrix[0][DEFAULT_FEATURE_NAMES.index("side_UP")] == 1.0
    assert matrix[1][DEFAULT_FEATURE_NAMES.index("side_DOWN")] == 1.0


def test_feature_matrix_clips_logit_input() -> None:
    matrix, labels = feature_matrix([_row(p_finish_mc=1.0), _row(p_finish_mc=0.0, final_label=0)])

    assert labels == [1, 0]
    logit_index = DEFAULT_FEATURE_NAMES.index("logit_p_finish_mc")
    assert matrix[0][logit_index] < 20.0
    assert matrix[1][logit_index] > -20.0
