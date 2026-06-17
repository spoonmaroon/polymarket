from __future__ import annotations

from polymarket_engine.calibration.logistic import LogisticCalibrator
from polymarket_engine.calibration.logistic import fit_logistic_calibrator


def test_fit_logistic_calibrator_learns_directional_signal() -> None:
    matrix = [[-2.0], [-1.0], [1.0], [2.0]]
    labels = [0, 0, 1, 1]

    model = fit_logistic_calibrator(
        matrix,
        labels,
        feature_names=("signal",),
        learning_rate=0.2,
        iterations=300,
        l2=0.0,
    )

    low, high = model.predict_proba([[-2.0], [2.0]])
    assert low < 0.35
    assert high > 0.65
    assert model.model_version == "MC_Calibrator_LogReg_v1"


def test_logistic_calibrator_round_trips_json() -> None:
    model = LogisticCalibrator(
        model_version="MC_Calibrator_LogReg_v1",
        feature_names=("signal",),
        intercept=-0.1,
        coefficients=(0.5,),
    )

    restored = LogisticCalibrator.from_json_dict(model.to_json_dict())

    assert restored == model
    assert restored.predict_proba([[0.0]]) == model.predict_proba([[0.0]])
