from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


_MODEL_VERSION = "MC_Calibrator_GBDT_v1"


@dataclass(frozen=True)
class XGBoostCalibrator:
    model_version: str
    feature_names: tuple[str, ...]
    booster: Any

    def predict_proba(self, matrix: Sequence[Sequence[float]]) -> list[float]:
        xgb = _xgboost()
        dmatrix = xgb.DMatrix(matrix, feature_names=list(self.feature_names))
        return [float(value) for value in self.booster.predict(dmatrix)]

    def save_model(self, path: str) -> None:
        self.booster.save_model(path)


def fit_xgboost_calibrator(
    matrix: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    feature_names: Sequence[str],
    max_depth: int,
    eta: float,
    rounds: int,
) -> XGBoostCalibrator:
    if not matrix:
        raise ValueError("matrix must be non-empty")
    if len(matrix) != len(labels):
        raise ValueError("labels length must match matrix rows")
    if not feature_names:
        raise ValueError("feature_names must be non-empty")

    rows = [tuple(float(value) for value in row) for row in matrix]
    feature_count = len(feature_names)
    for row in rows:
        if len(row) != feature_count:
            raise ValueError("feature_names length must match matrix columns")

    xgb = _xgboost()
    dtrain = xgb.DMatrix(rows, label=list(labels), feature_names=list(feature_names))
    booster = xgb.train(
        {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "max_depth": max_depth,
            "eta": eta,
            "seed": 7,
        },
        dtrain,
        num_boost_round=rounds,
    )
    return XGBoostCalibrator(
        model_version=_MODEL_VERSION,
        feature_names=tuple(feature_names),
        booster=booster,
    )


def _xgboost() -> Any:
    try:
        import xgboost as xgb
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "xgboost is required for MC_Calibrator_GBDT_v1; run "
            "`uv sync --group research`"
        ) from exc
    return xgb
