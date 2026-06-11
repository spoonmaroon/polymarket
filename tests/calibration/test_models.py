from __future__ import annotations

import importlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest


JsonRecord = dict[str, object]
ASOF_TS = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


class CalibrationTrainingResult(Protocol):
    trained: bool
    reasons: tuple[str, ...]
    model_name: str
    labeled_row_count: int
    train_row_count: int
    validation_row_count: int
    artifact_paths: tuple[Path, ...]
    validation_predictions: tuple[float, ...]
    validation_brier: float | None


class CalibrationModelConfigFactory(Protocol):
    def __call__(
        self,
        *,
        min_labeled_rows: int = ...,
        min_train_rows: int = ...,
        min_validation_rows: int = ...,
        max_iterations: int = ...,
        learning_rate: float = ...,
    ) -> object:
        raise NotImplementedError


class TrainLogisticCalibrator(Protocol):
    def __call__(
        self,
        rows: Iterable[JsonRecord],
        *,
        config: object | None = ...,
        model_out_dir: Path | str | None = ...,
        report_out_dir: Path | str | None = ...,
    ) -> CalibrationTrainingResult:
        raise NotImplementedError


def _models_module() -> ModuleType:
    try:
        return importlib.import_module("polymarket_engine.calibration.models")
    except ModuleNotFoundError as exc:
        pytest.fail(f"calibration models module is missing: {exc}")


def _config_factory() -> CalibrationModelConfigFactory:
    config_factory = getattr(_models_module(), "CalibrationModelConfig", None)
    if not callable(config_factory):
        pytest.fail("CalibrationModelConfig is missing")
    return cast(CalibrationModelConfigFactory, config_factory)


def _train() -> TrainLogisticCalibrator:
    train = getattr(_models_module(), "train_logistic_calibrator", None)
    if not callable(train):
        pytest.fail("train_logistic_calibrator is missing")
    return cast(TrainLogisticCalibrator, train)


def _config(
    *,
    min_labeled_rows: int = 4,
    min_train_rows: int = 2,
    min_validation_rows: int = 2,
    max_iterations: int = 40,
    learning_rate: float = 0.08,
) -> object:
    return _config_factory()(
        min_labeled_rows=min_labeled_rows,
        min_train_rows=min_train_rows,
        min_validation_rows=min_validation_rows,
        max_iterations=max_iterations,
        learning_rate=learning_rate,
    )


def _row(index: int, *, final_label: int | None = 1, **overrides: object) -> JsonRecord:
    asof_ts = ASOF_TS + timedelta(minutes=index)
    values: JsonRecord = {
        "state_id": f"state-{index}",
        "contract_id": f"condition-{index}",
        "market_slug": "btc-updown-5m-1781102700",
        "asset": "BTC" if index % 2 == 0 else "ETH",
        "side": "UP" if index % 2 == 0 else "DOWN",
        "asof_ts": asof_ts.isoformat(),
        "expiry_ts": (asof_ts + timedelta(minutes=5)).isoformat(),
        "tte_seconds": 300 - (index * 10),
        "k": 65000.0,
        "current_price": 65000.0 + (index * 12.0),
        "distance_to_threshold": float(index * 15 - 30),
        "z_path": -0.8 + (index * 0.25),
        "sigma_tau": 0.01 + (index * 0.001),
        "p_finish_mc": min(0.9, max(0.1, 0.25 + (index * 0.08))),
        "p_no_touch_mc": min(0.9, max(0.1, 0.7 - (index * 0.04))),
        "spread": 0.01 + (index * 0.002),
        "best_bid": 0.45,
        "best_ask": 0.47,
        "midpoint": 0.46,
        "visible_depth": 1000.0 + (index * 50.0),
        "orderbook_imbalance": -0.3 + (index * 0.1),
        "quote_age_ms": 200.0,
        "source_age_ms": 900.0,
        "volatility_regime": "normal" if index % 3 else "high",
        "probability_model_version": "mc-v1",
        "skip_or_block_reason": None,
        "final_label": final_label,
        "resolved_outcome": "UP" if final_label == 1 else "DOWN",
        "settlement_price_at_expiry": 65100.0,
        "feature_generated_at": asof_ts.isoformat(),
    }
    values.update(overrides)
    return values


def _training_rows() -> list[JsonRecord]:
    labels = (0, 0, 1, 0, 1, 1)
    return [_row(index, final_label=label) for index, label in enumerate(labels)]


def test_trainer_refuses_insufficient_labeled_rows() -> None:
    result = _train()(
        [_row(0, final_label=1), _row(1, final_label=0)],
        config=_config(min_labeled_rows=3, min_train_rows=1, min_validation_rows=1),
    )

    assert result.trained is False
    assert "insufficient_labeled_rows" in result.reasons
    assert result.labeled_row_count == 2


def test_trainer_refuses_missing_labels() -> None:
    result = _train()(
        [_row(index, final_label=None) for index in range(4)],
        config=_config(min_labeled_rows=4, min_train_rows=2, min_validation_rows=2),
    )

    assert result.trained is False
    assert "missing_labels" in result.reasons
    assert result.labeled_row_count == 0


def test_trainer_refuses_impossible_walk_forward_split() -> None:
    result = _train()(
        [_row(index, final_label=index % 2) for index in range(3)],
        config=_config(min_labeled_rows=3, min_train_rows=3, min_validation_rows=1),
    )

    assert result.trained is False
    assert "walk_forward_split_impossible" in result.reasons
    assert result.train_row_count == 0
    assert result.validation_row_count == 0


@pytest.mark.parametrize(
    "timestamp_overrides",
    [
        {"feature_generated_at": (ASOF_TS + timedelta(seconds=1)).isoformat()},
        {"feature_observed_ts": (ASOF_TS + timedelta(seconds=1)).isoformat()},
        {
            "feature_timestamps": {
                "orderbook": (ASOF_TS + timedelta(seconds=1)).isoformat(),
                "volatility": ASOF_TS.isoformat(),
            }
        },
    ],
)
def test_trainer_refuses_feature_timestamps_after_asof(
    timestamp_overrides: JsonRecord,
) -> None:
    rows = _training_rows()
    rows[0] = _row(0, final_label=0, **timestamp_overrides)

    result = _train()(
        rows,
        config=_config(min_labeled_rows=6, min_train_rows=4, min_validation_rows=2),
    )

    assert result.trained is False
    assert "feature_timestamp_after_asof" in result.reasons


def test_valid_chronological_training_writes_only_research_and_report_artifacts(
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "data" / "research" / "calibration" / "models"
    report_dir = tmp_path / "reports" / "calibration"

    result = _train()(
        _training_rows(),
        config=_config(min_labeled_rows=6, min_train_rows=4, min_validation_rows=2),
        model_out_dir=model_dir,
        report_out_dir=report_dir,
    )

    assert result.trained is True
    assert result.reasons == ()
    assert result.model_name == "MC_Calibrator_LogReg_v1"
    assert result.labeled_row_count == 6
    assert result.train_row_count == 4
    assert result.validation_row_count == 2
    assert result.validation_brier is not None
    assert result.artifact_paths
    assert all(0.0 <= probability <= 1.0 for probability in result.validation_predictions)
    assert all(
        path.exists()
        and (path.is_relative_to(model_dir) or path.is_relative_to(report_dir))
        for path in result.artifact_paths
    )

    model_payload = json.loads((model_dir / "MC_Calibrator_LogReg_v1.json").read_text())
    report_payload = json.loads((report_dir / "MC_Calibrator_LogReg_v1_report.json").read_text())

    assert model_payload["model_name"] == "MC_Calibrator_LogReg_v1"
    assert model_payload["output"] == "p_finish_calibrated"
    assert model_payload["features"] == [
        "logit(p_finish_mc)",
        "p_no_touch_mc",
        "z_path",
        "tte_seconds",
        "sigma_tau",
        "spread",
        "orderbook_imbalance",
        "volatility_regime",
        "asset",
        "side",
    ]
    assert tuple(record["p_finish_calibrated"] for record in report_payload["validation_rows"]) == (
        pytest.approx(result.validation_predictions[0]),
        pytest.approx(result.validation_predictions[1]),
    )


@pytest.mark.parametrize(
    "forbidden_dir",
    [
        Path("data/live/calibration/models"),
        Path("data/live/worker_status/calibration"),
        Path("data/live/tui_state/calibration"),
        Path("data/live/decision_gate_outputs/calibration"),
    ],
)
def test_trainer_refuses_live_or_decision_gate_output_paths(
    tmp_path: Path,
    forbidden_dir: Path,
) -> None:
    result = _train()(
        _training_rows(),
        config=_config(min_labeled_rows=6, min_train_rows=4, min_validation_rows=2),
        model_out_dir=tmp_path / forbidden_dir,
        report_out_dir=tmp_path / "reports" / "calibration",
    )

    assert result.trained is False
    assert "forbidden_output_path" in result.reasons
    assert result.artifact_paths == ()
