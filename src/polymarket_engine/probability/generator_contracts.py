from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Sequence


class GeneratorId(StrEnum):
    EMPIRICAL_CONDITIONAL = "empirical_conditional"
    BLOCK_BOOTSTRAP = "block_bootstrap"
    FILTERED_HISTORICAL = "filtered_historical"
    STRESS_OVERLAY = "stress_overlay"
    LOGNORMAL_CONTROL = "lognormal_control"


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

    def __post_init__(self) -> None:
        if self.asset not in {"BTC", "ETH"}:
            raise ValueError("asset must be BTC or ETH")
        _require_positive_int(self.horizon_seconds, "horizon_seconds")
        for field_name in (
            "seconds_left_bucket",
            "z_path_bucket",
            "vol_regime",
            "vol_trend",
            "wick_regime",
            "source_quality_state",
        ):
            _require_nonempty_string(getattr(self, field_name), field_name)


@dataclass(frozen=True)
class GeneratorRun:
    generator_id: GeneratorId
    p_finish: float
    p_no_touch: float
    path_count: int
    effective_path_count: int
    seed: int | None
    asof_ts: datetime
    runtime_ms: float
    sparse: bool
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.generator_id, GeneratorId):
            raise ValueError("generator_id must be a GeneratorId")
        _require_probability(self.p_finish, "p_finish")
        _require_probability(self.p_no_touch, "p_no_touch")
        _require_positive_int(self.path_count, "path_count")
        _require_nonnegative_int(self.effective_path_count, "effective_path_count")
        if self.effective_path_count > self.path_count:
            raise ValueError("effective_path_count must not exceed path_count")
        if self.seed is not None and (
            isinstance(self.seed, bool) or not isinstance(self.seed, int)
        ):
            raise ValueError("seed must be int or None")
        _require_utc(self.asof_ts, "asof_ts")
        _require_nonnegative_finite(self.runtime_ms, "runtime_ms")
        if not isinstance(self.sparse, bool):
            raise ValueError("sparse must be a bool")
        _validate_json_object(self.diagnostics, "diagnostics")


def generator_runs_to_json(runs: Sequence[GeneratorRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in runs:
        row = asdict(run)
        row["generator_id"] = run.generator_id.value
        row["asof_ts"] = run.asof_ts.isoformat()
        json.dumps(row, allow_nan=False, sort_keys=True)
        rows.append(row)
    return rows


def _require_probability(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or value > 1
    ):
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_nonnegative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")


def _require_nonnegative_finite(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field_name} must be nonnegative and finite")


def _require_nonempty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_utc(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")


def _validate_json_object(value: dict[str, Any], field_name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    _require_json_native_value(value, field_name)
    try:
        json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON serializable") from exc


def _require_json_native_value(value: Any, field_name: str) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} object keys must be strings")
            _require_json_native_value(nested_value, field_name)
    elif isinstance(value, list):
        for nested_value in value:
            _require_json_native_value(nested_value, field_name)
    elif isinstance(value, str) or value is None:
        return
    elif isinstance(value, bool):
        return
    elif isinstance(value, (int, float)) and math.isfinite(value):
        return
    else:
        raise ValueError(f"{field_name} must contain only JSON-native values")
