from __future__ import annotations

import json
from pathlib import Path

from polymarket_engine.calibration.train import TrainCalibratorConfig
from polymarket_engine.calibration.train import train_calibrator


def _row(state_id: str, probability: float, label: int) -> dict[str, object]:
    return {
        "state_id": state_id,
        "asof_ts": "2026-06-10T12:00:00+00:00",
        "p_finish_mc": probability,
        "p_no_touch_mc": 0.6,
        "mc_generator_dispersion": 0.03,
        "tte_seconds": 120,
        "z_path": probability,
        "sigma_tau": 0.015,
        "distance_to_threshold": 10.0,
        "spread": 0.02,
        "orderbook_imbalance": 0.1,
        "visible_depth": 1000.0,
        "quote_age_ms": 200.0,
        "source_age_ms": 500.0,
        "threshold_cross_count": 1,
        "near_threshold_congestion": 2,
        "recent_wick_size": 0.001,
        "asset": "BTC",
        "side": "UP",
        "volatility_regime": "normal",
        "final_label": label,
    }


def test_train_logreg_writes_model_and_predictions(tmp_path: Path) -> None:
    input_path = tmp_path / "dataset.jsonl"
    model_path = tmp_path / "model.json"
    predictions_path = tmp_path / "predictions.jsonl"
    input_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                _row("a", 0.2, 0),
                _row("b", 0.3, 0),
                _row("c", 0.8, 1),
                _row("d", 0.9, 1),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = train_calibrator(
        TrainCalibratorConfig(
            input_path=input_path,
            model_path=model_path,
            predictions_path=predictions_path,
            model_type="logreg",
        )
    )

    assert result.rows_trained == 4
    model_payload = json.loads(model_path.read_text(encoding="utf-8"))
    assert model_payload["model_version"] == "MC_Calibrator_LogReg_v1"
    predictions = [
        json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["state_id"] for row in predictions] == ["a", "b", "c", "d"]
    assert all("p_finish_final" in row for row in predictions)
