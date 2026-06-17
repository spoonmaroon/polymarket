from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


DEFAULT_FEATURE_NAMES = (
    "logit_p_finish_mc",
    "p_no_touch_mc",
    "mc_generator_dispersion",
    "tte_seconds",
    "z_path",
    "sigma_tau",
    "distance_to_threshold",
    "spread",
    "orderbook_imbalance",
    "visible_depth",
    "quote_age_ms",
    "source_age_ms",
    "threshold_cross_count",
    "near_threshold_congestion",
    "recent_wick_size",
    "asset_BTC",
    "asset_ETH",
    "side_UP",
    "side_DOWN",
    "volatility_regime_low",
    "volatility_regime_normal",
    "volatility_regime_high",
    "volatility_regime_unknown",
)

_NUMERIC_FEATURES = {
    "p_no_touch_mc",
    "mc_generator_dispersion",
    "tte_seconds",
    "z_path",
    "sigma_tau",
    "distance_to_threshold",
    "spread",
    "orderbook_imbalance",
    "visible_depth",
    "quote_age_ms",
    "source_age_ms",
    "threshold_cross_count",
    "near_threshold_congestion",
    "recent_wick_size",
}


def feature_matrix(
    rows: Sequence[Mapping[str, object]],
    *,
    feature_names: Sequence[str] = DEFAULT_FEATURE_NAMES,
) -> tuple[list[list[float]], list[int]]:
    matrix: list[list[float]] = []
    labels: list[int] = []
    for row in rows:
        labels.append(_label(row.get("final_label")))
        matrix.append([_feature(row, name) for name in feature_names])
    return matrix, labels


def prediction_matrix(
    rows: Sequence[Mapping[str, object]],
    *,
    feature_names: Sequence[str] = DEFAULT_FEATURE_NAMES,
) -> list[list[float]]:
    return [[_feature(row, name) for name in feature_names] for row in rows]


def _feature(row: Mapping[str, object], name: str) -> float:
    if name == "logit_p_finish_mc":
        return _logit(_probability(row.get("p_finish_mc")))
    if name in _NUMERIC_FEATURES:
        return _float(row.get(name))
    if name == "asset_BTC":
        return 1.0 if row.get("asset") == "BTC" else 0.0
    if name == "asset_ETH":
        return 1.0 if row.get("asset") == "ETH" else 0.0
    if name == "side_UP":
        return 1.0 if row.get("side") == "UP" else 0.0
    if name == "side_DOWN":
        return 1.0 if row.get("side") == "DOWN" else 0.0
    if name == "volatility_regime_low":
        return 1.0 if row.get("volatility_regime") == "low" else 0.0
    if name == "volatility_regime_normal":
        return 1.0 if row.get("volatility_regime") == "normal" else 0.0
    if name == "volatility_regime_high":
        return 1.0 if row.get("volatility_regime") == "high" else 0.0
    if name == "volatility_regime_unknown":
        return 1.0 if row.get("volatility_regime") not in {"low", "normal", "high"} else 0.0
    raise ValueError(f"unsupported feature name: {name}")


def _probability(value: object) -> float:
    return min(1.0 - 1e-6, max(1e-6, _float(value)))


def _float(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _label(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    raise ValueError("final_label must be 0 or 1")


def _logit(probability: float) -> float:
    return math.log(probability / (1.0 - probability))
