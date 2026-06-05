from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class GeneratorId(str, Enum):
    EMPIRICAL_CONDITIONAL = "empirical_conditional"
    BLOCK_BOOTSTRAP = "block_bootstrap"
    FILTERED_HISTORICAL = "filtered_historical"
    STRESS_OVERLAY = "stress_overlay"
    LOGNORMAL_BASELINE = "lognormal_baseline"


@dataclass(frozen=True)
class GeneratorRun:
    generator_id: GeneratorId
    p_finish: float
    p_no_touch: float
    path_count: int
    seed: int
    asof_ts: datetime
    diagnostics: dict[str, Any]
    sparse: bool = False
    fallback_level: str = "none"

    def __post_init__(self) -> None:
        object.__setattr__(self, "generator_id", _coerce_generator_id(self.generator_id))
        _require_probability(self.p_finish, "p_finish")
        _require_probability(self.p_no_touch, "p_no_touch")
        _require_positive_int(self.path_count, "path_count")
        _require_int(self.seed, "seed")
        _require_timezone_aware(self.asof_ts, "asof_ts")
        if not isinstance(self.diagnostics, dict):
            raise ValueError("diagnostics must be a dict")
        if not isinstance(self.sparse, bool):
            raise ValueError("sparse must be bool")
        if not isinstance(self.fallback_level, str) or not self.fallback_level:
            raise ValueError("fallback_level must be a non-empty string")


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
    score: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "generator_id", _coerce_generator_id(self.generator_id))
        _require_probability(self.weight, "weight")
        if not isinstance(self.scope, DynamicWeightScope):
            raise ValueError("scope must be a DynamicWeightScope")
        _require_nonnegative_int(self.label_count, "label_count")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source must be a non-empty string")
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
