from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any


class GeneratorId(str, Enum):
    EMPIRICAL_CONDITIONAL = "empirical_conditional"
    BLOCK_BOOTSTRAP = "block_bootstrap"
    FILTERED_HISTORICAL = "filtered_historical"
    STRESS_OVERLAY = "stress_overlay"
    LOGNORMAL_BASELINE = "lognormal_baseline"


@dataclass(frozen=True)
class GeneratorResult:
    p_finish: float
    p_no_touch: float
    z_path: float
    diagnostics: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_probability(self.p_finish, "p_finish")
        _require_probability(self.p_no_touch, "p_no_touch")
        _require_finite(self.z_path, "z_path")
        object.__setattr__(self, "diagnostics", _freeze_mapping(self.diagnostics, "diagnostics"))

    def diagnostics_json_dict(self) -> dict[str, Any]:
        return _thaw_json_mapping(self.diagnostics)


@dataclass(frozen=True)
class HistoricalValidationWindow:
    asof_ts: datetime
    evaluated_through_ts: datetime
    label_window_seconds: int

    def __post_init__(self) -> None:
        _require_timezone_aware(self.asof_ts, "asof_ts")
        _require_timezone_aware(self.evaluated_through_ts, "evaluated_through_ts")
        if self.evaluated_through_ts < self.asof_ts:
            raise ValueError("evaluated_through_ts must not be before asof_ts")
        _require_positive_int(self.label_window_seconds, "label_window_seconds")


@dataclass(frozen=True)
class GeneratorRun:
    generator_id: GeneratorId
    generator_name: str
    generator_version: str
    scope: DynamicWeightScope
    conditioning: Mapping[str, Any]
    result: GeneratorResult
    path_count: int
    steps: int
    seed: int
    asof_ts: datetime
    diagnostics: Mapping[str, Any]
    sparse: bool = False
    fallback_level: str = "none"
    weight_seed: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "generator_id", _coerce_generator_id(self.generator_id))
        _require_nonempty_string(self.generator_name, "generator_name")
        _require_nonempty_string(self.generator_version, "generator_version")
        if not isinstance(self.scope, DynamicWeightScope):
            raise ValueError("scope must be a DynamicWeightScope")
        object.__setattr__(self, "conditioning", _freeze_mapping(self.conditioning, "conditioning"))
        if not isinstance(self.result, GeneratorResult):
            raise ValueError("result must be a GeneratorResult")
        _require_positive_int(self.path_count, "path_count")
        _require_positive_int(self.steps, "steps")
        _require_int(self.seed, "seed")
        _require_timezone_aware(self.asof_ts, "asof_ts")
        object.__setattr__(self, "diagnostics", _freeze_mapping(self.diagnostics, "diagnostics"))
        if not isinstance(self.sparse, bool):
            raise ValueError("sparse must be bool")
        _require_nonempty_string(self.fallback_level, "fallback_level")
        if self.weight_seed is not None:
            _require_probability(self.weight_seed, "weight_seed")

    @property
    def p_finish(self) -> float:
        return self.result.p_finish

    @property
    def p_no_touch(self) -> float:
        return self.result.p_no_touch

    @property
    def z_path(self) -> float:
        return self.result.z_path

    def conditioning_json_dict(self) -> dict[str, Any]:
        return _thaw_json_mapping(self.conditioning)

    def diagnostics_json_dict(self) -> dict[str, Any]:
        return _thaw_json_mapping(self.diagnostics)


@dataclass(frozen=True)
class DynamicWeightScope:
    asset: str
    horizon_seconds: int
    seconds_left_bucket: str
    z_path_bucket: str
    vol_regime: str
    vol_trend: str
    wick_regime: str
    source_quality_state: str


@dataclass(frozen=True)
class GeneratorWeight:
    generator_id: GeneratorId
    weight: float
    scope: DynamicWeightScope
    label_count: int
    source: str
    validation_window: HistoricalValidationWindow
    score: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "generator_id", _coerce_generator_id(self.generator_id))
        _require_probability(self.weight, "weight")
        if not isinstance(self.scope, DynamicWeightScope):
            raise ValueError("scope must be a DynamicWeightScope")
        _require_nonnegative_int(self.label_count, "label_count")
        _require_nonempty_string(self.source, "source")
        if not isinstance(self.validation_window, HistoricalValidationWindow):
            raise ValueError("validation_window must be a HistoricalValidationWindow")
        if self.score is not None:
            _require_finite(self.score, "score")


def _coerce_generator_id(value: GeneratorId) -> GeneratorId:
    try:
        return GeneratorId(value)
    except ValueError as exc:
        raise ValueError("generator_id must be a supported GeneratorId") from exc


def _require_probability(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_finite(value: float, field_name: str) -> None:
    if not _is_finite_number(value):
        raise ValueError(f"{field_name} must be finite")


def _require_nonempty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _freeze_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a dict")
    frozen: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be strings")
        frozen[key] = _freeze_json_value(item)
    return MappingProxyType(frozen)


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value, "mapping")
    if isinstance(value, list | tuple):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _thaw_json_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _thaw_json_value(item) for key, item in value.items()}


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _thaw_json_mapping(value)
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _require_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_nonnegative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")


def _require_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
