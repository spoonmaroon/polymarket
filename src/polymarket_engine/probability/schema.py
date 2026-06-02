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
        _require_nonnegative(self.seconds_left, "seconds_left")
        _require_positive(self.settlement_price, "settlement_price")
        _require_positive(self.threshold, "threshold")
        _require_positive(self.sigma_tau, "sigma_tau")
        _require_probability(self.executable_price, "executable_price")
        _require_nonnegative(self.source_age_ms, "source_age_ms")
        _require_nonnegative(self.book_age_ms, "book_age_ms")
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
        try:
            _validate_strict_json(self.diagnostics)
            json.dumps(self.diagnostics, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("diagnostics must be strict JSON") from exc

    def to_json_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _json_ready(asdict(self)))


def _require_probability(value: float, field_name: str) -> None:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_supported(value: str, field_name: str, supported: set[str]) -> None:
    if value not in supported:
        raise ValueError(f"{field_name} must be one of {', '.join(sorted(supported))}")


def _require_finite(value: float, field_name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")


def _require_positive(value: float, field_name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field_name} must be positive and finite")


def _require_nonnegative(value: float, field_name: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field_name} must be nonnegative and finite")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")


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
