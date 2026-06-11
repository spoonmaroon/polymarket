from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from polymarket_engine.calibration.dataset import CalibrationDecisionRow

SCHEMA_VERSION = "polymarket-calibration-report-v1"
_LOG_LOSS_EPSILON = 1e-15

JsonObject = dict[str, object]
RowInput = CalibrationDecisionRow | Mapping[str, object]


@dataclass(frozen=True)
class CalibrationValidationError:
    row_index: int
    state_id: str | None
    field: str
    reason: str

    def to_json_dict(self) -> JsonObject:
        return {
            "row_index": self.row_index,
            "state_id": self.state_id,
            "field": self.field,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CalibrationEceBucket:
    bucket_key: str
    lower: float
    upper: float
    count: int
    mean_probability: float
    observed_rate: float
    ece_contribution: float

    def to_json_dict(self) -> JsonObject:
        return {
            "bucket_key": self.bucket_key,
            "lower": self.lower,
            "upper": self.upper,
            "count": self.count,
            "mean_probability": self.mean_probability,
            "observed_rate": self.observed_rate,
            "ece_contribution": self.ece_contribution,
        }


@dataclass(frozen=True)
class CalibrationReport:
    schema_version: str
    input_row_count: int
    evaluated_row_count: int
    skipped_row_count: int
    brier_score: float
    log_loss: float
    expected_calibration_error: float
    ece_buckets: tuple[CalibrationEceBucket, ...]
    bucket_counts: dict[str, int]
    min_bucket_sample_count: int
    validation_errors: tuple[CalibrationValidationError, ...]
    validation_error_counts: dict[str, int]

    def to_json_dict(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "input_row_count": self.input_row_count,
            "evaluated_row_count": self.evaluated_row_count,
            "skipped_row_count": self.skipped_row_count,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss,
            "expected_calibration_error": self.expected_calibration_error,
            "ece_buckets": [bucket.to_json_dict() for bucket in self.ece_buckets],
            "bucket_counts": {
                key: self.bucket_counts[key] for key in sorted(self.bucket_counts)
            },
            "min_bucket_sample_count": self.min_bucket_sample_count,
            "validation_errors": [
                issue.to_json_dict() for issue in self.validation_errors
            ],
            "validation_error_counts": {
                key: self.validation_error_counts[key]
                for key in sorted(self.validation_error_counts)
            },
        }


@dataclass(frozen=True)
class _ValidatedRow:
    probability: float
    label: int
    tte_seconds: float
    z_path: float
    distance_to_threshold: float
    spread: float
    visible_depth: float
    orderbook_imbalance: float
    volatility_regime: str
    asset: str
    side: str


def build_calibration_report(
    rows: Iterable[RowInput],
    *,
    probability_field: str = "p_finish_mc",
    ece_bucket_count: int = 10,
) -> CalibrationReport:
    _require_positive_int(ece_bucket_count, "ece_bucket_count")
    raw_rows = tuple(rows)
    validation_errors: list[CalibrationValidationError] = []
    evaluated_rows: list[_ValidatedRow] = []

    for row_index, row in enumerate(raw_rows):
        payload = _row_payload(row)
        state_id = _state_id(payload)
        validated_row, issues = _validate_row(
            row_index,
            state_id,
            payload,
            probability_field=probability_field,
        )
        validation_errors.extend(issues)
        if validated_row is not None:
            evaluated_rows.append(validated_row)

    bucket_counts = _slice_bucket_counts(evaluated_rows)
    ece_buckets = _ece_buckets(evaluated_rows, bucket_count=ece_bucket_count)
    ece = sum(bucket.ece_contribution for bucket in ece_buckets)
    error_counts = Counter(issue.reason for issue in validation_errors)

    return CalibrationReport(
        schema_version=SCHEMA_VERSION,
        input_row_count=len(raw_rows),
        evaluated_row_count=len(evaluated_rows),
        skipped_row_count=len(raw_rows) - len(evaluated_rows),
        brier_score=_brier_score(evaluated_rows),
        log_loss=_log_loss(evaluated_rows),
        expected_calibration_error=ece,
        ece_buckets=ece_buckets,
        bucket_counts=bucket_counts,
        min_bucket_sample_count=min(bucket_counts.values()) if bucket_counts else 0,
        validation_errors=tuple(validation_errors),
        validation_error_counts=dict(error_counts),
    )


def load_calibration_jsonl(path: Path | str) -> tuple[JsonObject, ...]:
    rows: list[JsonObject] = []
    dataset_path = Path(path)
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid calibration JSONL at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError(
                    f"invalid calibration JSONL at line {line_number}: row must be an object"
                )
            rows.append(dict(payload))
    return tuple(rows)


def _row_payload(row: RowInput) -> Mapping[str, object]:
    if isinstance(row, CalibrationDecisionRow):
        return row.to_json_dict()
    return row


def _validate_row(
    row_index: int,
    state_id: str | None,
    payload: Mapping[str, object],
    *,
    probability_field: str,
) -> tuple[_ValidatedRow | None, tuple[CalibrationValidationError, ...]]:
    issues: list[CalibrationValidationError] = []
    probability = _probability_field(
        payload,
        probability_field,
        row_index=row_index,
        state_id=state_id,
        issues=issues,
    )
    for field in ("p_finish_mc", "p_no_touch_mc", "best_bid", "best_ask", "midpoint"):
        if field == probability_field:
            continue
        _probability_field(
            payload,
            field,
            row_index=row_index,
            state_id=state_id,
            issues=issues,
        )

    label = _label(payload, row_index=row_index, state_id=state_id, issues=issues)
    tte_seconds = _non_negative_metric(
        payload,
        "tte_seconds",
        row_index=row_index,
        state_id=state_id,
        issues=issues,
    )
    z_path = _finite_metric(
        payload,
        "z_path",
        row_index=row_index,
        state_id=state_id,
        issues=issues,
    )
    distance_to_threshold = _finite_metric(
        payload,
        "distance_to_threshold",
        row_index=row_index,
        state_id=state_id,
        issues=issues,
    )
    spread = _non_negative_metric(
        payload,
        "spread",
        row_index=row_index,
        state_id=state_id,
        issues=issues,
    )
    visible_depth = _non_negative_metric(
        payload,
        "visible_depth",
        row_index=row_index,
        state_id=state_id,
        issues=issues,
    )
    orderbook_imbalance = _finite_metric(
        payload,
        "orderbook_imbalance",
        row_index=row_index,
        state_id=state_id,
        issues=issues,
    )
    for field in ("k", "current_price", "sigma_tau", "quote_age_ms", "source_age_ms"):
        _non_negative_metric(
            payload,
            field,
            row_index=row_index,
            state_id=state_id,
            issues=issues,
        )
    asset = _string_field(payload, "asset")
    side = _string_field(payload, "side")
    volatility_regime = _string_field(payload, "volatility_regime")

    if issues:
        return None, tuple(issues)
    if probability is None or label is None:
        return None, tuple(issues)
    if (
        tte_seconds is None
        or z_path is None
        or distance_to_threshold is None
        or spread is None
        or visible_depth is None
        or orderbook_imbalance is None
    ):
        return None, tuple(issues)
    return (
        _ValidatedRow(
            probability=probability,
            label=label,
            tte_seconds=tte_seconds,
            z_path=z_path,
            distance_to_threshold=distance_to_threshold,
            spread=spread,
            visible_depth=visible_depth,
            orderbook_imbalance=orderbook_imbalance,
            volatility_regime=volatility_regime,
            asset=asset,
            side=side,
        ),
        tuple(issues),
    )


def _probability_field(
    payload: Mapping[str, object],
    field: str,
    *,
    row_index: int,
    state_id: str | None,
    issues: list[CalibrationValidationError],
) -> float | None:
    value = _raw_number(payload, field)
    if value is None:
        issues.append(_issue(row_index, state_id, field, "missing_probability"))
        return None
    if not math.isfinite(value):
        issues.append(_issue(row_index, state_id, field, "probability_non_finite"))
        return None
    if value < 0.0 or value > 1.0:
        issues.append(_issue(row_index, state_id, field, "probability_out_of_range"))
        return None
    return value


def _label(
    payload: Mapping[str, object],
    *,
    row_index: int,
    state_id: str | None,
    issues: list[CalibrationValidationError],
) -> int | None:
    value = payload.get("final_label")
    if value is None:
        issues.append(_issue(row_index, state_id, "final_label", "missing_label"))
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    issues.append(_issue(row_index, state_id, "final_label", "non_binary_label"))
    return None


def _finite_metric(
    payload: Mapping[str, object],
    field: str,
    *,
    row_index: int,
    state_id: str | None,
    issues: list[CalibrationValidationError],
) -> float | None:
    value = _raw_number(payload, field)
    if value is None:
        issues.append(_issue(row_index, state_id, field, "missing_replay_metric"))
        return None
    if not math.isfinite(value):
        issues.append(_issue(row_index, state_id, field, "non_finite_replay_metric"))
        return None
    return value


def _non_negative_metric(
    payload: Mapping[str, object],
    field: str,
    *,
    row_index: int,
    state_id: str | None,
    issues: list[CalibrationValidationError],
) -> float | None:
    value = _finite_metric(
        payload,
        field,
        row_index=row_index,
        state_id=state_id,
        issues=issues,
    )
    if value is None:
        return None
    if value < 0.0:
        issues.append(_issue(row_index, state_id, field, "negative_replay_metric"))
        return None
    return value


def _issue(
    row_index: int,
    state_id: str | None,
    field: str,
    reason: str,
) -> CalibrationValidationError:
    return CalibrationValidationError(
        row_index=row_index,
        state_id=state_id,
        field=field,
        reason=reason,
    )


def _raw_number(payload: Mapping[str, object], field: str) -> float | None:
    value = payload.get(field)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _state_id(payload: Mapping[str, object]) -> str | None:
    value = payload.get("state_id")
    if value is None:
        return None
    return str(value)


def _string_field(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text if text else "unknown"


def _brier_score(rows: list[_ValidatedRow]) -> float:
    if not rows:
        return 0.0
    return sum((row.probability - row.label) ** 2 for row in rows) / len(rows)


def _log_loss(rows: list[_ValidatedRow]) -> float:
    if not rows:
        return 0.0
    return sum(_row_log_loss(row) for row in rows) / len(rows)


def _row_log_loss(row: _ValidatedRow) -> float:
    probability = min(1.0 - _LOG_LOSS_EPSILON, max(_LOG_LOSS_EPSILON, row.probability))
    return -math.log(probability if row.label == 1 else 1.0 - probability)


def _ece_buckets(
    rows: list[_ValidatedRow],
    *,
    bucket_count: int,
) -> tuple[CalibrationEceBucket, ...]:
    buckets: list[list[_ValidatedRow]] = [[] for _ in range(bucket_count)]
    for row in rows:
        index = min(bucket_count - 1, int(row.probability * bucket_count))
        buckets[index].append(row)

    output: list[CalibrationEceBucket] = []
    total_count = len(rows)
    if total_count == 0:
        return ()

    for index, bucket_rows in enumerate(buckets):
        if not bucket_rows:
            continue
        lower = index / bucket_count
        upper = (index + 1) / bucket_count
        count = len(bucket_rows)
        mean_probability = sum(row.probability for row in bucket_rows) / count
        observed_rate = sum(row.label for row in bucket_rows) / count
        ece_contribution = (count / total_count) * abs(observed_rate - mean_probability)
        output.append(
            CalibrationEceBucket(
                bucket_key=f"prob_{lower:.2f}_{upper:.2f}",
                lower=lower,
                upper=upper,
                count=count,
                mean_probability=mean_probability,
                observed_rate=observed_rate,
                ece_contribution=ece_contribution,
            )
        )
    return tuple(output)


def _slice_bucket_counts(rows: list[_ValidatedRow]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        keys = (
            _tte_bucket(row.tte_seconds),
            _z_path_bucket(row.z_path),
            _distance_bucket(row.distance_to_threshold),
            f"volatility_regime_{_key_part(row.volatility_regime.lower())}",
            f"asset_{_key_part(row.asset.upper())}",
            f"side_{_key_part(row.side.upper())}",
            _spread_depth_bucket(row.spread, row.visible_depth),
            _orderbook_imbalance_bucket(row.orderbook_imbalance),
            _final_window_bucket(row.tte_seconds),
            _threshold_congestion_bucket(row.distance_to_threshold),
        )
        counts.update(keys)
    return dict(counts)


def _tte_bucket(tte_seconds: float) -> str:
    if tte_seconds <= 60.0:
        return "tte_0_60"
    if tte_seconds <= 180.0:
        return "tte_60_180"
    return "tte_180_plus"


def _z_path_bucket(z_path: float) -> str:
    if abs(z_path) <= 0.5:
        return "z_path_near"
    if z_path < 0.0:
        return "z_path_below"
    return "z_path_above"


def _distance_bucket(distance_to_threshold: float) -> str:
    absolute_distance = abs(distance_to_threshold)
    if absolute_distance <= 50.0:
        return "distance_near"
    if absolute_distance <= 100.0:
        return "distance_mid"
    return "distance_far"


def _spread_depth_bucket(spread: float, visible_depth: float) -> str:
    spread_part = "tight" if spread <= 0.03 else "wide"
    depth_part = "deep" if visible_depth >= 1000.0 else "thin"
    return f"spread_depth_{spread_part}_{depth_part}"


def _orderbook_imbalance_bucket(orderbook_imbalance: float) -> str:
    if orderbook_imbalance > 0.2:
        return "orderbook_imbalance_buy"
    if orderbook_imbalance < -0.2:
        return "orderbook_imbalance_sell"
    return "orderbook_imbalance_neutral"


def _final_window_bucket(tte_seconds: float) -> str:
    if 30.0 <= tte_seconds <= 60.0:
        return "final_window_30_60"
    return "final_window_other"


def _threshold_congestion_bucket(distance_to_threshold: float) -> str:
    absolute_distance = abs(distance_to_threshold)
    if absolute_distance <= 50.0:
        return "threshold_congestion_near"
    if absolute_distance <= 100.0:
        return "threshold_congestion_mid"
    return "threshold_congestion_far"


def _key_part(value: str) -> str:
    output = []
    for char in value:
        if char.isalnum():
            output.append(char)
        else:
            output.append("_")
    return "".join(output).strip("_") or "unknown"


def _require_positive_int(value: int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")

