from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from polymarket_engine.domain.market_state import PriceObservation, VolatilitySnapshot

VOLATILITY_REFERENCE_SOURCE_KEY = "polymarket_rtds_chainlink"
VOLATILITY_FAILURE_REGIMES = frozenset(
    {
        "missing_reference_source",
        "missing_continuous_reference_source",
        "stale_reference_source",
    }
)


def is_volatility_failure_regime(regime: str | None) -> bool:
    return regime in VOLATILITY_FAILURE_REGIMES


@dataclass(frozen=True)
class VolatilityConfig:
    short_window: int = 20
    medium_window: int = 60
    long_window: int = 180
    weights: tuple[float, float, float] = (0.50, 0.30, 0.20)
    sigma_floor: float = 0.00005
    expansion_threshold: float = 1.25
    contraction_threshold: float = 0.80
    expansion_multiplier: float = 1.15
    contraction_multiplier: float = 0.95
    max_price_gap_seconds: float = 12.0
    max_reference_age_seconds: float = 10.0

    def __post_init__(self) -> None:
        _validate_windows(self.short_window, self.medium_window, self.long_window)
        _validate_weights(self.weights)
        _validate_positive(self.sigma_floor, "sigma_floor")
        _validate_positive(self.expansion_threshold, "expansion_threshold")
        _validate_positive(self.contraction_threshold, "contraction_threshold")
        if self.expansion_threshold <= self.contraction_threshold:
            raise ValueError("expansion_threshold must be greater than contraction_threshold")
        _validate_positive(self.expansion_multiplier, "expansion_multiplier")
        _validate_positive(self.contraction_multiplier, "contraction_multiplier")
        _validate_positive(self.max_price_gap_seconds, "max_price_gap_seconds")
        _validate_positive(self.max_reference_age_seconds, "max_reference_age_seconds")


def log_returns_from_prices(prices: Sequence[PriceObservation]) -> tuple[float, ...]:
    ordered = sorted(prices, key=lambda price: (price.event_ts, price.observed_ts))
    returns: list[float] = []
    for previous, current in zip(ordered, ordered[1:]):
        returns.append(math.log(current.price / previous.price))
    return tuple(returns)


def interval_normalized_log_returns_from_prices(
    prices: Sequence[PriceObservation],
) -> tuple[float, ...]:
    ordered = sorted(prices, key=lambda price: (price.event_ts, price.observed_ts))
    returns: list[float] = []
    for previous, current in zip(ordered, ordered[1:]):
        event_interval_seconds = (current.event_ts - previous.event_ts).total_seconds()
        interval_seconds = event_interval_seconds if event_interval_seconds > 0 else 1.0
        returns.append(math.log(current.price / previous.price) / math.sqrt(interval_seconds))
    return tuple(returns)


def realized_volatility(returns: Sequence[float], *, window: int) -> float | None:
    if window <= 0:
        raise ValueError("window must be positive")
    if not returns:
        return None

    recent_returns = tuple(returns[-window:])
    mean_square = sum(value * value for value in recent_returns) / len(recent_returns)
    return math.sqrt(mean_square)


def estimate_sigma_tau(
    returns: Sequence[float],
    seconds_left: float,
    short_window: int = 20,
    medium_window: int = 60,
    long_window: int = 180,
    weights: tuple[float, float, float] = (0.50, 0.30, 0.20),
    sigma_floor: float = 0.00005,
    regime_multiplier: float = 1.0,
) -> float:
    if seconds_left < 0:
        raise ValueError("seconds_left must be nonnegative")
    _validate_windows(short_window, medium_window, long_window)
    _validate_weights(weights)
    _validate_positive(sigma_floor, "sigma_floor")
    _validate_positive(regime_multiplier, "regime_multiplier")

    short_vol = realized_volatility(returns, window=short_window)
    medium_vol = realized_volatility(returns, window=medium_window)
    long_vol = realized_volatility(returns, window=long_window)
    blended_vol = (
        weights[0] * _or_zero(short_vol)
        + weights[1] * _or_zero(medium_vol)
        + weights[2] * _or_zero(long_vol)
    )
    scaled_sigma = blended_vol * math.sqrt(seconds_left) * regime_multiplier
    return max(sigma_floor, scaled_sigma)


def classify_volatility_regime(
    short_vol: float | None,
    medium_vol: float | None,
    *,
    expansion_threshold: float,
    contraction_threshold: float,
) -> str:
    _validate_positive(expansion_threshold, "expansion_threshold")
    _validate_positive(contraction_threshold, "contraction_threshold")
    if expansion_threshold <= contraction_threshold:
        raise ValueError("expansion_threshold must be greater than contraction_threshold")
    if short_vol is None or medium_vol is None or medium_vol <= 0:
        return "unknown"

    ratio = short_vol / medium_vol
    if ratio >= expansion_threshold:
        return "expanding"
    if ratio <= contraction_threshold:
        return "contracting"
    return "normal"


def regime_multiplier(regime: str, config: VolatilityConfig) -> float:
    if regime == "expanding":
        return config.expansion_multiplier
    if regime == "contracting":
        return config.contraction_multiplier
    return 1.0


def build_volatility_snapshot(
    *,
    prices: Sequence[PriceObservation],
    asof_ts: datetime,
    seconds_left: float,
    config: VolatilityConfig | None = None,
    symbol: str | None = None,
) -> VolatilitySnapshot:
    _require_utc(asof_ts, "asof_ts")
    active_config = VolatilityConfig() if config is None else config
    allowed_prices = sorted(
        (
            price
            for price in prices
            if price.source_key == VOLATILITY_REFERENCE_SOURCE_KEY
            if symbol is None or price.symbol == symbol
            if price.event_ts <= asof_ts and price.observed_ts <= asof_ts
        ),
        key=lambda price: (price.event_ts, price.observed_ts),
    )

    if not allowed_prices:
        return VolatilitySnapshot(
            event_ts=asof_ts,
            observed_ts=asof_ts,
            realized_returns=(),
            short_realized_vol=None,
            medium_realized_vol=None,
            long_realized_vol=None,
            sigma_tau=None,
            regime="missing_reference_source",
        )

    continuous_prices = _continuous_tail(
        allowed_prices,
        max_gap_seconds=active_config.max_price_gap_seconds,
    )
    if len(continuous_prices) < 2:
        latest = continuous_prices[-1] if continuous_prices else allowed_prices[-1]
        return VolatilitySnapshot(
            event_ts=latest.event_ts,
            observed_ts=latest.observed_ts,
            realized_returns=(),
            short_realized_vol=None,
            medium_realized_vol=None,
            long_realized_vol=None,
            sigma_tau=None,
            regime="missing_continuous_reference_source",
        )

    latest = continuous_prices[-1]
    latest_age_seconds = max(
        (asof_ts - latest.event_ts).total_seconds(),
        (asof_ts - latest.observed_ts).total_seconds(),
    )
    if latest_age_seconds > active_config.max_reference_age_seconds:
        return VolatilitySnapshot(
            event_ts=latest.event_ts,
            observed_ts=latest.observed_ts,
            realized_returns=(),
            short_realized_vol=None,
            medium_realized_vol=None,
            long_realized_vol=None,
            sigma_tau=None,
            regime="stale_reference_source",
        )

    returns = log_returns_from_prices(continuous_prices)
    normalized_returns = interval_normalized_log_returns_from_prices(continuous_prices)
    short_vol = realized_volatility(normalized_returns, window=active_config.short_window)
    medium_vol = realized_volatility(normalized_returns, window=active_config.medium_window)
    long_vol = realized_volatility(normalized_returns, window=active_config.long_window)
    regime = classify_volatility_regime(
        short_vol,
        medium_vol,
        expansion_threshold=active_config.expansion_threshold,
        contraction_threshold=active_config.contraction_threshold,
    )
    sigma_tau = estimate_sigma_tau(
        normalized_returns,
        seconds_left,
        short_window=active_config.short_window,
        medium_window=active_config.medium_window,
        long_window=active_config.long_window,
        weights=active_config.weights,
        sigma_floor=active_config.sigma_floor,
        regime_multiplier=regime_multiplier(regime, active_config),
    )

    event_ts = latest.event_ts
    observed_ts = max(price.observed_ts for price in continuous_prices)

    return VolatilitySnapshot(
        event_ts=event_ts,
        observed_ts=observed_ts,
        realized_returns=returns,
        short_realized_vol=short_vol,
        medium_realized_vol=medium_vol,
        long_realized_vol=long_vol,
        sigma_tau=sigma_tau,
        regime=regime,
    )


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")


def _continuous_tail(
    prices: Sequence[PriceObservation],
    *,
    max_gap_seconds: float,
) -> tuple[PriceObservation, ...]:
    ordered = tuple(sorted(prices, key=lambda price: (price.event_ts, price.observed_ts)))
    if len(ordered) < 2:
        return ordered

    start_index = len(ordered) - 1
    for index in range(len(ordered) - 1, 0, -1):
        previous = ordered[index - 1]
        current = ordered[index]
        event_gap = (current.event_ts - previous.event_ts).total_seconds()
        if event_gap > max_gap_seconds:
            break
        start_index = index - 1
    return ordered[start_index:]


def _validate_windows(short_window: int, medium_window: int, long_window: int) -> None:
    for field_name, value in (
        ("short_window", short_window),
        ("medium_window", medium_window),
        ("long_window", long_window),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{field_name} must be a positive integer")


def _validate_weights(weights: tuple[float, float, float]) -> None:
    if len(weights) != 3:
        raise ValueError("weights must contain exactly 3 values")
    if not math.isclose(sum(weights), 1.0, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError("weights must sum to 1")
    for weight in weights:
        if weight < 0:
            raise ValueError("weights must be nonnegative")


def _validate_positive(value: float, field_name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field_name} must be finite and positive")


def _or_zero(value: float | None) -> float:
    return 0.0 if value is None else value
