from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from polymarket_engine.probability.ensemble_weights import DynamicWeightSet
from polymarket_engine.probability.generator_contracts import (
    DynamicWeightScope,
    GeneratorId,
    GeneratorWeight,
)


def generator_weight_snapshot_payload(
    weight_set: DynamicWeightSet,
    *,
    scope: DynamicWeightScope,
    snapshot_id: str | None = None,
    scores: Mapping[GeneratorId, float] | None = None,
    label_counts: Mapping[GeneratorId, int] | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(weight_set, DynamicWeightSet):
        raise ValueError("weight_set must be a DynamicWeightSet")
    if not isinstance(scope, DynamicWeightScope):
        raise ValueError("scope must be a DynamicWeightScope")
    if snapshot_id is not None and (not isinstance(snapshot_id, str) or not snapshot_id):
        raise ValueError("snapshot_id must be a non-empty string")
    if created_at is not None:
        _require_timezone_aware(created_at, "created_at")

    payload = {
        "snapshot_id": snapshot_id,
        "runtime_asof_ts": _isoformat_utc(weight_set.runtime_asof_ts),
        "evaluated_through_ts": _isoformat_utc(
            weight_set.validation_window.evaluated_through_ts
        ),
        "label_window_seconds": weight_set.validation_window.label_window_seconds,
        "source": weight_set.source,
        "scope": scope_json_dict(scope),
        "weights": _generator_float_mapping(weight_set.weights, "weights"),
        "scores": _generator_float_mapping(scores or {}, "scores"),
        "label_counts": _generator_int_mapping(label_counts or {}, "label_counts"),
    }
    if created_at is not None:
        payload["created_at"] = _isoformat_utc(created_at)
    payload["uses_future_labels"] = _uses_future_labels(payload)
    return payload


def generator_weight_snapshot_payload_from_weights(
    generator_weights: Sequence[GeneratorWeight],
    *,
    runtime_asof_ts: datetime,
    snapshot_id: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    _require_timezone_aware(runtime_asof_ts, "runtime_asof_ts")
    if not generator_weights:
        raise ValueError("generator_weights must not be empty")

    first = generator_weights[0]
    if not isinstance(first, GeneratorWeight):
        raise ValueError("generator_weights must contain GeneratorWeight values")
    scope = first.scope
    validation_window = first.validation_window
    source = first.source
    weights: dict[GeneratorId, float] = {}
    scores: dict[GeneratorId, float] = {}
    label_counts: dict[GeneratorId, int] = {}
    for generator_weight in generator_weights:
        if not isinstance(generator_weight, GeneratorWeight):
            raise ValueError("generator_weights must contain GeneratorWeight values")
        if generator_weight.generator_id in weights:
            raise ValueError("duplicate generator_id in generator_weights")
        if generator_weight.scope != scope:
            raise ValueError("generator_weights must share one scope")
        if generator_weight.validation_window != validation_window:
            raise ValueError("generator_weights must share one validation_window")
        if generator_weight.source != source:
            raise ValueError("generator_weights must share one source")
        weights[generator_weight.generator_id] = generator_weight.weight
        label_counts[generator_weight.generator_id] = generator_weight.label_count
        if generator_weight.score is not None:
            scores[generator_weight.generator_id] = generator_weight.score

    return generator_weight_snapshot_payload(
        DynamicWeightSet(
            weights=weights,
            validation_window=validation_window,
            runtime_asof_ts=runtime_asof_ts,
            source=source,
        ),
        scope=scope,
        snapshot_id=snapshot_id,
        scores=scores,
        label_counts=label_counts,
        created_at=created_at,
    )


def generator_weight_snapshot_report(payload: Mapping[str, Any]) -> dict[str, Any]:
    uses_future_labels = _uses_future_labels(payload)
    unsafe_reasons = ("FUTURE_LABELS",) if uses_future_labels else ()
    return {
        "snapshot_id": payload.get("snapshot_id"),
        "source": payload["source"],
        "runtime_asof_ts": payload["runtime_asof_ts"],
        "validation_cutoff": payload["evaluated_through_ts"],
        "label_window_seconds": payload["label_window_seconds"],
        "scope": payload["scope"],
        "effective_weights": payload["weights"],
        "label_counts": payload["label_counts"],
        "scores": payload["scores"],
        "uses_future_labels": uses_future_labels,
        "unsafe_reasons": unsafe_reasons,
    }


def scope_json_dict(scope: DynamicWeightScope) -> dict[str, Any]:
    if not isinstance(scope, DynamicWeightScope):
        raise ValueError("scope must be a DynamicWeightScope")
    return {
        "asset": scope.asset,
        "horizon_seconds": scope.horizon_seconds,
        "seconds_left_bucket": scope.seconds_left_bucket,
        "z_path_bucket": scope.z_path_bucket,
        "vol_regime": scope.vol_regime,
        "vol_trend": scope.vol_trend,
        "wick_regime": scope.wick_regime,
        "source_quality_state": scope.source_quality_state,
    }


def _generator_float_mapping(
    values: Mapping[GeneratorId, float],
    field_name: str,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_generator_id, value in values.items():
        generator_id = _coerce_generator_id(raw_generator_id, field_name)
        if not _is_finite_number(value):
            raise ValueError(f"{field_name} values must be finite")
        result[generator_id.value] = float(value)
    return dict(sorted(result.items()))


def _generator_int_mapping(
    values: Mapping[GeneratorId, int],
    field_name: str,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for raw_generator_id, value in values.items():
        generator_id = _coerce_generator_id(raw_generator_id, field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field_name} values must be nonnegative integers")
        result[generator_id.value] = value
    return dict(sorted(result.items()))


def _coerce_generator_id(value: GeneratorId, field_name: str) -> GeneratorId:
    try:
        return GeneratorId(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} keys must be supported GeneratorId values") from exc


def _uses_future_labels(payload: Mapping[str, Any]) -> bool:
    evaluated_through_ts = _parse_iso_datetime(payload["evaluated_through_ts"])
    runtime_asof_ts = _parse_iso_datetime(payload["runtime_asof_ts"])
    return evaluated_through_ts > runtime_asof_ts


def _parse_iso_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp values must be non-empty ISO strings")
    parsed = datetime.fromisoformat(value)
    _require_timezone_aware(parsed, "timestamp")
    return parsed


def _isoformat_utc(value: datetime) -> str:
    _require_timezone_aware(value, "timestamp")
    return value.astimezone(timezone.utc).isoformat()


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
