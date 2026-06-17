from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from polymarket_engine.calibration.features import DEFAULT_FEATURE_NAMES
from polymarket_engine.calibration.features import feature_matrix
from polymarket_engine.calibration.logistic import fit_logistic_calibrator
from polymarket_engine.calibration.reports import load_calibration_jsonl
from polymarket_engine.calibration.xgboost_model import fit_xgboost_calibrator


@dataclass(frozen=True)
class TrainCalibratorConfig:
    input_path: Path
    model_path: Path
    predictions_path: Path
    model_type: str


@dataclass(frozen=True)
class TrainCalibratorResult:
    model_type: str
    rows_trained: int
    model_path: str
    predictions_path: str

    def to_json_dict(self) -> dict[str, object]:
        return {
            "model_type": self.model_type,
            "rows_trained": self.rows_trained,
            "model_path": self.model_path,
            "predictions_path": self.predictions_path,
        }


def train_calibrator(config: TrainCalibratorConfig) -> TrainCalibratorResult:
    rows = list(load_calibration_jsonl(config.input_path))
    matrix, labels = feature_matrix(rows, feature_names=DEFAULT_FEATURE_NAMES)
    probabilities: list[float]
    model_payload: dict[str, object]

    if config.model_type == "logreg":
        logistic_model = fit_logistic_calibrator(
            matrix,
            labels,
            feature_names=DEFAULT_FEATURE_NAMES,
            learning_rate=0.05,
            iterations=500,
            l2=0.001,
        )
        probabilities = logistic_model.predict_proba(matrix)
        model_payload = logistic_model.to_json_dict()
    elif config.model_type == "xgboost":
        xgboost_model = fit_xgboost_calibrator(
            matrix,
            labels,
            feature_names=DEFAULT_FEATURE_NAMES,
            max_depth=3,
            eta=0.1,
            rounds=50,
        )
        probabilities = xgboost_model.predict_proba(matrix)
        booster_path = config.model_path.with_suffix(".xgboost.json")
        booster_path.parent.mkdir(parents=True, exist_ok=True)
        xgboost_model.save_model(str(booster_path))
        model_payload = {
            "model_version": xgboost_model.model_version,
            "feature_names": list(xgboost_model.feature_names),
            "booster_path": str(booster_path),
        }
    else:
        raise ValueError("model_type must be logreg or xgboost")

    config.model_path.parent.mkdir(parents=True, exist_ok=True)
    config.model_path.write_text(
        json.dumps(model_payload, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    config.predictions_path.parent.mkdir(parents=True, exist_ok=True)
    with config.predictions_path.open("w", encoding="utf-8") as handle:
        for row, probability in zip(rows, probabilities, strict=True):
            output = dict(row)
            output["p_finish_final"] = probability
            output["calibration_model_type"] = config.model_type
            handle.write(json.dumps(output, allow_nan=False, sort_keys=True) + "\n")

    return TrainCalibratorResult(
        model_type=config.model_type,
        rows_trained=len(rows),
        model_path=str(config.model_path),
        predictions_path=str(config.predictions_path),
    )
