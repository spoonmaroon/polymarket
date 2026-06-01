from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from polymarket_engine.domain.contracts import ContractSpec

DataQualityFlag = Literal[
    "stale_source",
    "stale_orderbook",
    "missing_orderbook",
    "source_disagreement",
    "missing_volatility",
]


@dataclass(frozen=True)
class PriceObservation:
    source_key: str
    symbol: str
    event_ts: datetime
    observed_ts: datetime
    price: float
    bid: float | None = None
    ask: float | None = None
    sequence: str | None = None

    def __post_init__(self) -> None:
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.observed_ts, "observed_ts")
        if self.price <= 0:
            raise ValueError("price must be positive")


@dataclass(frozen=True)
class OrderBookObservation:
    venue: str
    contract_id: str
    token_id: str
    event_ts: datetime
    observed_ts: datetime
    best_bid: float | None
    best_ask: float | None
    bid_size_top: float | None
    ask_size_top: float | None
    spread: float | None
    depth_json: str

    def __post_init__(self) -> None:
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.observed_ts, "observed_ts")


@dataclass(frozen=True)
class VolatilitySnapshot:
    event_ts: datetime
    observed_ts: datetime
    realized_returns: tuple[float, ...]
    short_realized_vol: float | None
    medium_realized_vol: float | None
    long_realized_vol: float | None
    sigma_tau: float | None
    regime: str | None

    def __post_init__(self) -> None:
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.observed_ts, "observed_ts")


@dataclass(frozen=True)
class DecisionState:
    state_id: str
    asof_ts: datetime
    contract: ContractSpec
    threshold: float
    seconds_left: float
    settlement_price: float
    settlement_source_key: str
    proxy_prices: dict[str, float]
    source_disagreement_bps: float | None
    best_bid: float | None
    best_ask: float | None
    executable_price: float | None
    spread: float | None
    quote_age_ms: int | None
    source_age_ms: int | None
    book_age_ms: int | None
    realized_returns: tuple[float, ...]
    short_realized_vol: float | None
    medium_realized_vol: float | None
    long_realized_vol: float | None
    sigma_tau: float | None
    volatility_regime: str | None
    data_quality_flags: tuple[DataQualityFlag, ...]

    def __post_init__(self) -> None:
        _require_utc(self.asof_ts, "asof_ts")
        if self.threshold <= 0:
            raise ValueError("threshold must be positive")
        if self.seconds_left < 0:
            raise ValueError("seconds_left must be nonnegative")
        if self.settlement_price <= 0:
            raise ValueError("settlement_price must be positive")

    def to_json_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        return _json_ready(raw)


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


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")
