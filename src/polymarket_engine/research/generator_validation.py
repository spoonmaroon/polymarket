from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from polymarket_engine.probability.generator_contracts import GeneratorId

_LOG_LOSS_EPSILON = 1e-9


@dataclass(frozen=True)
class GeneratorPrediction:
    state_id: str
    asof_ts: datetime
    generator_id: GeneratorId
    p_finish: float
    p_no_touch: float

    def __post_init__(self) -> None:
        _require_nonempty_string(self.state_id, "state_id")
        _require_utc(self.asof_ts, "asof_ts")
        if not isinstance(self.generator_id, GeneratorId):
            raise ValueError("generator_id must be a GeneratorId")
        _require_probability(self.p_finish, "p_finish")
        _require_probability(self.p_no_touch, "p_no_touch")


@dataclass(frozen=True)
class GeneratorLabel:
    state_id: str
    label_ts: datetime
    did_finish_win: bool
    did_no_touch: bool

    def __post_init__(self) -> None:
        _require_nonempty_string(self.state_id, "state_id")
        _require_utc(self.label_ts, "label_ts")
        if not isinstance(self.did_finish_win, bool):
            raise ValueError("did_finish_win must be a bool")
        if not isinstance(self.did_no_touch, bool):
            raise ValueError("did_no_touch must be a bool")


@dataclass(frozen=True)
class WeightCandidate:
    weights: dict[GeneratorId, float]
    label_count: int
    trained_through_ts: datetime | None
    sparse: bool


def build_weight_candidate(
    predictions: tuple[GeneratorPrediction, ...],
    labels: tuple[GeneratorLabel, ...],
    decision_asof_ts: datetime,
    min_labels: int,
    eta: float,
) -> WeightCandidate:
    _require_utc(decision_asof_ts, "decision_asof_ts")
    _require_positive_int(min_labels, "min_labels")
    _require_positive_finite(eta, "eta")

    labels_by_state = _eligible_labels_by_state(labels, decision_asof_ts)
    losses: dict[GeneratorId, list[float]] = {}
    matched_label_timestamps: dict[str, datetime] = {}

    for prediction in predictions:
        if prediction.asof_ts >= decision_asof_ts:
            continue

        label = labels_by_state.get(prediction.state_id)
        if label is None:
            continue
        if prediction.asof_ts >= label.label_ts:
            continue

        loss = (
            0.70 * _log_loss(prediction.p_finish, label.did_finish_win)
            + 0.30 * _log_loss(prediction.p_no_touch, label.did_no_touch)
        )
        losses.setdefault(prediction.generator_id, []).append(loss)
        matched_label_timestamps[prediction.state_id] = label.label_ts

    label_count = len(matched_label_timestamps)
    trained_through_ts = (
        max(matched_label_timestamps.values()) if matched_label_timestamps else None
    )

    if label_count < min_labels or not losses:
        return WeightCandidate(
            weights=_seed_weights(),
            label_count=label_count,
            trained_through_ts=trained_through_ts,
            sparse=True,
        )

    raw_weights: dict[GeneratorId, float] = {}
    for generator_id, generator_losses in losses.items():
        mean_loss = sum(generator_losses) / len(generator_losses)
        raw_weights[generator_id] = math.exp(-eta * mean_loss)

    for generator_id, seed_weight in _seed_weights().items():
        raw_weights.setdefault(generator_id, seed_weight)

    return WeightCandidate(
        weights=_normalize_weights(raw_weights),
        label_count=label_count,
        trained_through_ts=trained_through_ts,
        sparse=False,
    )


def _eligible_labels_by_state(
    labels: tuple[GeneratorLabel, ...],
    decision_asof_ts: datetime,
) -> dict[str, GeneratorLabel]:
    labels_by_state: dict[str, GeneratorLabel] = {}
    for label in labels:
        if label.label_ts >= decision_asof_ts:
            continue
        prior_label = labels_by_state.get(label.state_id)
        if prior_label is None or label.label_ts > prior_label.label_ts:
            labels_by_state[label.state_id] = label
    return labels_by_state


def _normalize_weights(raw_weights: dict[GeneratorId, float]) -> dict[GeneratorId, float]:
    total = sum(raw_weights.values())
    if total <= 0.0 or not math.isfinite(total):
        raise ValueError("weight total must be positive and finite")
    return {
        generator_id: weight / total for generator_id, weight in raw_weights.items()
    }


def _log_loss(probability: float, label: bool) -> float:
    clipped = min(1.0 - _LOG_LOSS_EPSILON, max(_LOG_LOSS_EPSILON, probability))
    return -math.log(clipped if label else 1.0 - clipped)


def _seed_weights() -> dict[GeneratorId, float]:
    return {
        GeneratorId.EMPIRICAL_CONDITIONAL: 0.40,
        GeneratorId.BLOCK_BOOTSTRAP: 0.25,
        GeneratorId.FILTERED_HISTORICAL: 0.25,
        GeneratorId.STRESS_OVERLAY: 0.10,
    }


def _require_probability(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0.0
        or value > 1.0
    ):
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_positive_finite(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0.0
    ):
        raise ValueError(f"{field_name} must be positive and finite")


def _require_nonempty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_utc(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")
