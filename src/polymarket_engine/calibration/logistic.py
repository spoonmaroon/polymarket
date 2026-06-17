from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeAlias


_FloatLike: TypeAlias = str | int | float


_MODEL_VERSION = "MC_Calibrator_LogReg_v1"


@dataclass(frozen=True)
class LogisticCalibrator:
    model_version: str
    feature_names: tuple[str, ...]
    intercept: float
    coefficients: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.feature_names) != len(self.coefficients):
            raise ValueError("feature_names length must match coefficients length")

    def predict_proba(self, matrix: Sequence[Sequence[float]]) -> list[float]:
        outputs: list[float] = []
        feature_count = len(self.feature_names)
        for row in matrix:
            values = tuple(float(value) for value in row)
            if len(values) != feature_count:
                raise ValueError("feature length does not match model")
            logit = self.intercept + sum(
                weight * value for weight, value in zip(self.coefficients, values, strict=True)
            )
            outputs.append(_sigmoid(logit))
        return outputs

    def to_json_dict(self) -> dict[str, object]:
        return {
            "model_version": self.model_version,
            "feature_names": list(self.feature_names),
            "intercept": self.intercept,
            "coefficients": list(self.coefficients),
        }

    @classmethod
    def from_json_dict(cls, payload: dict[str, object]) -> LogisticCalibrator:
        return cls(
            model_version=str(payload["model_version"]),
            feature_names=tuple(str(name) for name in _string_sequence(payload.get("feature_names"))),
            intercept=_float(payload.get("intercept")),
            coefficients=tuple(_float(value) for value in _numeric_sequence(payload.get("coefficients"))),
        )


def fit_logistic_calibrator(
    matrix: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    feature_names: Sequence[str],
    learning_rate: float,
    iterations: int,
    l2: float,
) -> LogisticCalibrator:
    if not matrix:
        raise ValueError("matrix must be non-empty")
    if len(matrix) != len(labels):
        raise ValueError("labels length must match matrix rows")
    if not feature_names:
        raise ValueError("feature_names must be non-empty")

    rows = [tuple(float(value) for value in row) for row in matrix]
    y = [float(label) for label in labels]
    feature_count = len(feature_names)
    for row in rows:
        if len(row) != feature_count:
            raise ValueError("feature_names length must match matrix columns")

    coefficients = [0.0] * feature_count
    intercept = 0.0

    for _ in range(iterations):
        gradients = [0.0] * feature_count
        intercept_gradient = 0.0
        for row, label in zip(rows, y, strict=True):
            logit = intercept + sum(weight * value for weight, value in zip(coefficients, row, strict=True))
            probability = _sigmoid(logit)
            error = probability - label
            intercept_gradient += error
            for index, value in enumerate(row):
                gradients[index] += error * value

        row_count = float(len(rows))
        intercept -= learning_rate * (intercept_gradient / row_count)
        for index in range(feature_count):
            gradient = gradients[index] / row_count + l2 * coefficients[index]
            coefficients[index] -= learning_rate * gradient

    return LogisticCalibrator(
        model_version=_MODEL_VERSION,
        feature_names=tuple(feature_names),
        intercept=intercept,
        coefficients=tuple(coefficients),
    )


def _sigmoid(value: float) -> float:
    clipped = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-clipped))


def _string_sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    raise ValueError("feature_names must be a sequence")


def _numeric_sequence(value: object) -> Sequence[_FloatLike]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("coefficients must be a sequence")
    numeric_values: list[_FloatLike] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise ValueError("coefficients must be numeric")
        numeric_values.append(item)
    return numeric_values


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("value must be numeric")
    return float(value)
