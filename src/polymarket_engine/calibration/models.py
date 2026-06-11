from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from polymarket_engine.calibration.dataset import CalibrationDecisionRow

MODEL_NAME = "MC_Calibrator_LogReg_v1"
MODEL_OUTPUT = "p_finish_calibrated"
FEATURE_NAMES = (
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
)

_PROBABILITY_EPSILON = 1e-6
_ALLOWED_MODEL_PATH = ("data", "research", "calibration", "models")
_ALLOWED_REPORT_PATH = ("reports", "calibration")
_FORBIDDEN_PATHS = (
    ("data", "live"),
    ("worker", "status"),
    ("worker_status",),
    ("worker-status",),
    ("tui", "state"),
    ("tui_state",),
    ("tui-state",),
    ("decision", "gate"),
    ("decision_gate",),
    ("decision_gates",),
    ("decision_gate_outputs",),
    ("decision-gate-outputs",),
)
_VOLATILITY_REGIME_ENCODING = {
    "low": -1.0,
    "normal": 0.0,
    "medium": 0.0,
    "high": 1.0,
    "unknown": 0.0,
}
_ASSET_ENCODING = {
    "BTC": -0.5,
    "ETH": 0.5,
    "unknown": 0.0,
}
_SIDE_ENCODING = {
    "DOWN": -0.5,
    "UP": 0.5,
    "unknown": 0.0,
}

JsonObject = dict[str, object]
RowInput = CalibrationDecisionRow | Mapping[str, object]


@dataclass(frozen=True)
class CalibrationModelConfig:
    min_labeled_rows: int = 100
    min_train_rows: int = 80
    min_validation_rows: int = 20
    max_iterations: int = 400
    learning_rate: float = 0.05

    def __post_init__(self) -> None:
        _require_positive_int(self.min_labeled_rows, "min_labeled_rows")
        _require_positive_int(self.min_train_rows, "min_train_rows")
        _require_positive_int(self.min_validation_rows, "min_validation_rows")
        _require_positive_int(self.max_iterations, "max_iterations")
        if not math.isfinite(self.learning_rate) or self.learning_rate <= 0.0:
            raise ValueError("learning_rate must be a positive finite number")


@dataclass(frozen=True)
class CalibrationTrainingResult:
    trained: bool
    reasons: tuple[str, ...]
    model_name: str
    labeled_row_count: int
    train_row_count: int
    validation_row_count: int
    artifact_paths: tuple[Path, ...]
    coefficients: tuple[float, ...] = ()
    intercept: float | None = None
    validation_brier: float | None = None
    validation_predictions: tuple[float, ...] = ()


@dataclass(frozen=True)
class _ValidatedRow:
    state_id: str | None
    asof_ts: datetime
    label: int
    features: tuple[float, ...]
    p_finish_mc: float


@dataclass(frozen=True)
class _FittedLogisticModel:
    coefficients: tuple[float, ...]
    intercept: float
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]


def train_logistic_calibrator(
    rows: Iterable[RowInput],
    *,
    config: CalibrationModelConfig | None = None,
    model_out_dir: Path | str | None = None,
    report_out_dir: Path | str | None = None,
) -> CalibrationTrainingResult:
    model_config = config if config is not None else CalibrationModelConfig()
    reasons: list[str] = []
    artifact_paths: list[Path] = []

    output_path_reason = _validate_output_dirs(
        model_out_dir=model_out_dir,
        report_out_dir=report_out_dir,
    )
    if output_path_reason is not None:
        return _not_trained(
            reasons=(output_path_reason,),
            labeled_row_count=0,
            train_row_count=0,
            validation_row_count=0,
        )

    clean_rows = _validated_rows(rows, reasons)
    labeled_row_count = len(clean_rows)

    if labeled_row_count < model_config.min_labeled_rows:
        _add_reason(reasons, "insufficient_labeled_rows")
    if labeled_row_count < model_config.min_train_rows + model_config.min_validation_rows:
        _add_reason(reasons, "walk_forward_split_impossible")
    if reasons:
        return _not_trained(
            reasons=tuple(reasons),
            labeled_row_count=labeled_row_count,
            train_row_count=0,
            validation_row_count=0,
        )

    chronological_rows = tuple(sorted(clean_rows, key=lambda row: row.asof_ts))
    split_index = len(chronological_rows) - model_config.min_validation_rows
    train_rows = chronological_rows[:split_index]
    validation_rows = chronological_rows[split_index:]
    if len(train_rows) < model_config.min_train_rows or len(validation_rows) < (
        model_config.min_validation_rows
    ):
        return _not_trained(
            reasons=("walk_forward_split_impossible",),
            labeled_row_count=labeled_row_count,
            train_row_count=0,
            validation_row_count=0,
        )

    model = _fit_logistic_model(train_rows, model_config)
    validation_predictions = tuple(
        _predict_probability(row.features, model) for row in validation_rows
    )
    validation_brier = _brier_score(validation_predictions, validation_rows)

    if model_out_dir is not None:
        model_path = Path(model_out_dir) / f"{MODEL_NAME}.json"
        _write_json(model_path, _model_payload(model, model_config, train_rows, validation_rows))
        artifact_paths.append(model_path)
    if report_out_dir is not None:
        report_path = Path(report_out_dir) / f"{MODEL_NAME}_report.json"
        _write_json(
            report_path,
            _report_payload(validation_rows, validation_predictions, validation_brier),
        )
        artifact_paths.append(report_path)

    return CalibrationTrainingResult(
        trained=True,
        reasons=(),
        model_name=MODEL_NAME,
        labeled_row_count=labeled_row_count,
        train_row_count=len(train_rows),
        validation_row_count=len(validation_rows),
        artifact_paths=tuple(artifact_paths),
        coefficients=model.coefficients,
        intercept=model.intercept,
        validation_brier=validation_brier,
        validation_predictions=validation_predictions,
    )


def _validated_rows(rows: Iterable[RowInput], reasons: list[str]) -> list[_ValidatedRow]:
    clean_rows: list[_ValidatedRow] = []
    for row in rows:
        payload = _row_payload(row)
        if _skip_or_block_reason(payload) is not None:
            continue

        label = _label(payload)
        if label is None:
            _add_reason(reasons, "missing_labels")
            continue

        asof_ts = _timestamp(payload.get("asof_ts"))
        if asof_ts is None:
            _add_reason(reasons, "invalid_asof_ts")
            continue
        if _feature_timestamp_after_asof(payload, asof_ts):
            _add_reason(reasons, "feature_timestamp_after_asof")
            continue

        features = _feature_vector(payload)
        if features is None:
            _add_reason(reasons, "invalid_features")
            continue
        p_finish_mc = _probability(payload, "p_finish_mc")
        if p_finish_mc is None:
            _add_reason(reasons, "invalid_features")
            continue

        clean_rows.append(
            _ValidatedRow(
                state_id=_state_id(payload),
                asof_ts=asof_ts,
                label=label,
                features=features,
                p_finish_mc=p_finish_mc,
            )
        )
    return clean_rows


def _fit_logistic_model(
    train_rows: Sequence[_ValidatedRow],
    config: CalibrationModelConfig,
) -> _FittedLogisticModel:
    raw_vectors = tuple(row.features for row in train_rows)
    labels = tuple(float(row.label) for row in train_rows)
    means = _feature_means(raw_vectors)
    scales = _feature_scales(raw_vectors, means)
    vectors = tuple(_standardize(vector, means, scales) for vector in raw_vectors)
    coefficients = [0.0 for _ in FEATURE_NAMES]
    intercept = _logit(sum(labels) / len(labels))

    for _ in range(config.max_iterations):
        gradient = [0.0 for _ in FEATURE_NAMES]
        intercept_gradient = 0.0
        for vector, label in zip(vectors, labels, strict=True):
            prediction = _sigmoid(_dot(coefficients, vector) + intercept)
            error = prediction - label
            intercept_gradient += error
            for index, value in enumerate(vector):
                gradient[index] += error * value

        row_scale = 1.0 / len(vectors)
        intercept -= config.learning_rate * intercept_gradient * row_scale
        for index, value in enumerate(gradient):
            coefficients[index] -= config.learning_rate * value * row_scale

    return _FittedLogisticModel(
        coefficients=tuple(coefficients),
        intercept=intercept,
        feature_means=means,
        feature_scales=scales,
    )


def _predict_probability(features: tuple[float, ...], model: _FittedLogisticModel) -> float:
    standardized = _standardize(features, model.feature_means, model.feature_scales)
    return _sigmoid(_dot(model.coefficients, standardized) + model.intercept)


def _feature_vector(payload: Mapping[str, object]) -> tuple[float, ...] | None:
    p_finish_mc = _probability(payload, "p_finish_mc")
    p_no_touch_mc = _probability(payload, "p_no_touch_mc")
    z_path = _finite_number(payload, "z_path")
    tte_seconds = _non_negative_number(payload, "tte_seconds")
    sigma_tau = _non_negative_number(payload, "sigma_tau")
    spread = _non_negative_number(payload, "spread")
    orderbook_imbalance = _finite_number(payload, "orderbook_imbalance")
    if (
        p_finish_mc is None
        or p_no_touch_mc is None
        or z_path is None
        or tte_seconds is None
        or sigma_tau is None
        or spread is None
        or orderbook_imbalance is None
    ):
        return None
    return (
        _logit(p_finish_mc),
        p_no_touch_mc,
        z_path,
        tte_seconds,
        sigma_tau,
        spread,
        orderbook_imbalance,
        _encoded_category(
            _string_field(payload, "volatility_regime"),
            _VOLATILITY_REGIME_ENCODING,
            upper=False,
        ),
        _encoded_category(_string_field(payload, "asset"), _ASSET_ENCODING, upper=True),
        _encoded_category(_string_field(payload, "side"), _SIDE_ENCODING, upper=True),
    )


def _feature_timestamp_after_asof(
    payload: Mapping[str, object],
    asof_ts: datetime,
) -> bool:
    timestamps: list[datetime] = []
    for field in ("feature_generated_at", "feature_observed_ts"):
        timestamp = _timestamp(payload.get(field))
        if timestamp is not None:
            timestamps.append(timestamp)

    nested = payload.get("feature_timestamps")
    if isinstance(nested, Mapping):
        for value in nested.values():
            timestamp = _timestamp(value)
            if timestamp is not None:
                timestamps.append(timestamp)

    return any(timestamp > asof_ts for timestamp in timestamps)


def _validate_output_dirs(
    *,
    model_out_dir: Path | str | None,
    report_out_dir: Path | str | None,
) -> str | None:
    if model_out_dir is not None and not _is_allowed_output_dir(
        Path(model_out_dir),
        allowed_parts=_ALLOWED_MODEL_PATH,
    ):
        return "forbidden_output_path"
    if report_out_dir is not None and not _is_allowed_output_dir(
        Path(report_out_dir),
        allowed_parts=_ALLOWED_REPORT_PATH,
    ):
        return "forbidden_output_path"
    return None


def _is_allowed_output_dir(path: Path, *, allowed_parts: tuple[str, ...]) -> bool:
    normalized_parts = _normalized_parts(path)
    if any(_contains_parts(normalized_parts, forbidden) for forbidden in _FORBIDDEN_PATHS):
        return False
    return _contains_parts(normalized_parts, allowed_parts)


def _normalized_parts(path: Path) -> tuple[str, ...]:
    return tuple(part.lower() for part in path.expanduser().parts if part not in ("", "."))


def _contains_parts(parts: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if len(needle) > len(parts):
        return False
    return any(parts[index : index + len(needle)] == needle for index in range(len(parts)))


def _model_payload(
    model: _FittedLogisticModel,
    config: CalibrationModelConfig,
    train_rows: Sequence[_ValidatedRow],
    validation_rows: Sequence[_ValidatedRow],
) -> JsonObject:
    return {
        "model_name": MODEL_NAME,
        "output": MODEL_OUTPUT,
        "features": list(FEATURE_NAMES),
        "category_encoding": {
            "volatility_regime": dict(_VOLATILITY_REGIME_ENCODING),
            "asset": dict(_ASSET_ENCODING),
            "side": dict(_SIDE_ENCODING),
        },
        "coefficients": list(model.coefficients),
        "intercept": model.intercept,
        "feature_means": list(model.feature_means),
        "feature_scales": list(model.feature_scales),
        "config": {
            "min_labeled_rows": config.min_labeled_rows,
            "min_train_rows": config.min_train_rows,
            "min_validation_rows": config.min_validation_rows,
            "max_iterations": config.max_iterations,
            "learning_rate": config.learning_rate,
        },
        "train_row_count": len(train_rows),
        "validation_row_count": len(validation_rows),
    }


def _report_payload(
    validation_rows: Sequence[_ValidatedRow],
    validation_predictions: Sequence[float],
    validation_brier: float,
) -> JsonObject:
    return {
        "model_name": MODEL_NAME,
        "output": MODEL_OUTPUT,
        "validation_brier": validation_brier,
        "validation_rows": [
            {
                "state_id": row.state_id,
                "asof_ts": row.asof_ts.astimezone(timezone.utc).isoformat(),
                "final_label": row.label,
                "p_finish_mc": row.p_finish_mc,
                MODEL_OUTPUT: probability,
            }
            for row, probability in zip(validation_rows, validation_predictions, strict=True)
        ],
    }


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _not_trained(
    *,
    reasons: tuple[str, ...],
    labeled_row_count: int,
    train_row_count: int,
    validation_row_count: int,
) -> CalibrationTrainingResult:
    return CalibrationTrainingResult(
        trained=False,
        reasons=reasons,
        model_name=MODEL_NAME,
        labeled_row_count=labeled_row_count,
        train_row_count=train_row_count,
        validation_row_count=validation_row_count,
        artifact_paths=(),
    )


def _row_payload(row: RowInput) -> Mapping[str, object]:
    if isinstance(row, CalibrationDecisionRow):
        return row.to_json_dict()
    return row


def _label(payload: Mapping[str, object]) -> int | None:
    value = payload.get("final_label")
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    return None


def _probability(payload: Mapping[str, object], field: str) -> float | None:
    value = _finite_number(payload, field)
    if value is None or value < 0.0 or value > 1.0:
        return None
    return value


def _finite_number(payload: Mapping[str, object], field: str) -> float | None:
    value = payload.get(field)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _non_negative_number(payload: Mapping[str, object], field: str) -> float | None:
    value = _finite_number(payload, field)
    if value is None or value < 0.0:
        return None
    return value


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _aware_utc(value)
    if isinstance(value, str):
        try:
            return _aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _string_field(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text if text else "unknown"


def _skip_or_block_reason(payload: Mapping[str, object]) -> str | None:
    value = payload.get("skip_or_block_reason")
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _state_id(payload: Mapping[str, object]) -> str | None:
    value = payload.get("state_id")
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _encoded_category(
    value: str,
    encoding: Mapping[str, float],
    *,
    upper: bool,
) -> float:
    key = value.upper() if upper else value.lower()
    encoded = encoding.get(key)
    if encoded is not None:
        return encoded
    return _stable_text_code(key)


def _stable_text_code(value: str) -> float:
    if not value:
        return 0.0
    total = sum((index + 1) * ord(char) for index, char in enumerate(value))
    return (float(total % 2001) / 1000.0) - 1.0


def _feature_means(vectors: Sequence[tuple[float, ...]]) -> tuple[float, ...]:
    return tuple(
        sum(vector[index] for vector in vectors) / len(vectors)
        for index in range(len(FEATURE_NAMES))
    )


def _feature_scales(
    vectors: Sequence[tuple[float, ...]],
    means: tuple[float, ...],
) -> tuple[float, ...]:
    scales: list[float] = []
    for index, mean in enumerate(means):
        variance = sum((vector[index] - mean) ** 2 for vector in vectors) / len(vectors)
        scale = math.sqrt(variance)
        scales.append(scale if scale > 0.0 else 1.0)
    return tuple(scales)


def _standardize(
    vector: tuple[float, ...],
    means: tuple[float, ...],
    scales: tuple[float, ...],
) -> tuple[float, ...]:
    return tuple((value - mean) / scale for value, mean, scale in zip(vector, means, scales))


def _brier_score(
    probabilities: Sequence[float],
    rows: Sequence[_ValidatedRow],
) -> float:
    return sum(
        (probability - row.label) ** 2
        for probability, row in zip(probabilities, rows, strict=True)
    ) / len(rows)


def _logit(probability: float) -> float:
    clipped = min(1.0 - _PROBABILITY_EPSILON, max(_PROBABILITY_EPSILON, probability))
    return math.log(clipped / (1.0 - clipped))


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(left_value * right_value for left_value, right_value in zip(left, right))


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _require_positive_int(value: int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")
