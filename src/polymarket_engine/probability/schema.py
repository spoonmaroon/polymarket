from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, cast

from polymarket_engine.domain.market_state import DecisionState


@dataclass(frozen=True)
class ProbabilityInput:
    state_id: str
    asof_ts: datetime
    asset: str
    side: str
    comparison_operator: str
    seconds_left: float
    settlement_price: float
    threshold: float
    sigma_tau: float
    executable_price: float
    source_age_ms: int
    book_age_ms: int
    z_path: float

    @classmethod
    def from_decision_state(cls, state: DecisionState) -> ProbabilityInput:
        if state.data_quality_flags:
            raise ValueError(f"quality-blocked: {','.join(state.data_quality_flags)}")
        _require_not_after_asof(state.threshold_event_ts, "threshold_event_ts", state.asof_ts)
        _require_not_after_asof(
            state.threshold_observed_ts,
            "threshold_observed_ts",
            state.asof_ts,
        )
        _require_not_after_asof(state.settlement_event_ts, "settlement_event_ts", state.asof_ts)
        _require_not_after_asof(
            state.settlement_observed_ts,
            "settlement_observed_ts",
            state.asof_ts,
        )
        _require_not_after_asof(state.book_event_ts, "book_event_ts", state.asof_ts)
        _require_not_after_asof(state.book_observed_ts, "book_observed_ts", state.asof_ts)
        if state.sigma_tau is None or state.sigma_tau <= 0 or not math.isfinite(state.sigma_tau):
            raise ValueError("sigma_tau must be positive and finite")
        if state.executable_price is None:
            raise ValueError("executable_price is required")
        if state.source_age_ms is None:
            raise ValueError("source_age_ms is required")
        if state.book_age_ms is None:
            raise ValueError("book_age_ms is required")

        signed_log_distance = math.log(state.settlement_price / state.threshold)
        if state.contract.side == "DOWN":
            signed_log_distance *= -1
        z_path = signed_log_distance / state.sigma_tau

        return cls(
            state_id=state.state_id,
            asof_ts=state.asof_ts,
            asset=state.contract.asset,
            side=state.contract.side,
            comparison_operator=state.contract.comparison_operator,
            seconds_left=state.seconds_left,
            settlement_price=state.settlement_price,
            threshold=state.threshold,
            sigma_tau=state.sigma_tau,
            executable_price=state.executable_price,
            source_age_ms=state.source_age_ms,
            book_age_ms=state.book_age_ms,
            z_path=z_path,
        )

    def __post_init__(self) -> None:
        _require_utc(self.asof_ts, "asof_ts")
        _require_supported(self.asset, "asset", {"BTC", "ETH"})
        _require_supported(self.side, "side", {"UP", "DOWN"})
        _require_comparison_operator(self.comparison_operator, self.side)
        _require_nonnegative(self.seconds_left, "seconds_left")
        _require_positive(self.settlement_price, "settlement_price")
        _require_positive(self.threshold, "threshold")
        _require_positive(self.sigma_tau, "sigma_tau")
        _require_probability(self.executable_price, "executable_price")
        _require_nonnegative_int(self.source_age_ms, "source_age_ms")
        _require_nonnegative_int(self.book_age_ms, "book_age_ms")
        _require_finite(self.z_path, "z_path")

    def to_json_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _json_ready(asdict(self)))


@dataclass(frozen=True)
class ProbabilityOutput:
    state_id: str
    asof_ts: datetime
    p_finish: float
    p_no_touch: float
    z_path: float
    model_version: str
    seed: int | None
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        _require_utc(self.asof_ts, "asof_ts")
        _require_probability(self.p_finish, "p_finish")
        _require_probability(self.p_no_touch, "p_no_touch")
        _require_finite(self.z_path, "z_path")
        if not self.model_version:
            raise ValueError("model_version must be non-empty")
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ValueError("seed must be int or None")
        if not isinstance(self.diagnostics, dict):
            raise ValueError("diagnostics must be strict JSON object")
        try:
            _validate_strict_json(self.diagnostics)
            json.dumps(self.diagnostics, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("diagnostics must be strict JSON") from exc

    def to_json_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _json_ready(asdict(self)))


def _require_probability(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_supported(value: str, field_name: str, supported: set[str]) -> None:
    if not isinstance(value, str) or value not in supported:
        raise ValueError(f"{field_name} must be one of {', '.join(sorted(supported))}")


def _require_comparison_operator(value: str, side: str) -> None:
    if side == "UP":
        supported = {">", ">="}
    elif side == "DOWN":
        supported = {"<", "<="}
    else:
        supported = set()
    if not isinstance(value, str) or value not in supported:
        raise ValueError("comparison_operator is incompatible with side")


def _require_finite(value: float, field_name: str) -> None:
    if not _is_finite_number(value):
        raise ValueError(f"{field_name} must be finite")


def _require_positive(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or value <= 0:
        raise ValueError(f"{field_name} must be positive and finite")


def _require_nonnegative(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or value < 0:
        raise ValueError(f"{field_name} must be nonnegative and finite")


def _require_nonnegative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")


def _require_not_after_asof(value: datetime | None, field_name: str, asof_ts: datetime) -> None:
    if value is not None and value > asof_ts:
        raise ValueError(f"{field_name} must not be after asof_ts")


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _validate_strict_json(value: Any) -> None:
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float")
        return
    if isinstance(value, list):
        for item in value:
            _validate_strict_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("object keys must be strings")
            _validate_strict_json(item)
        return
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value
