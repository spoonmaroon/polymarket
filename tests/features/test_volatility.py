from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.features.volatility import (
    VolatilityConfig,
    build_volatility_snapshot,
    classify_volatility_regime,
    estimate_sigma_tau,
    log_returns_from_prices,
    regime_multiplier,
    realized_volatility,
)


BASE_TS = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _price(
    price: float,
    *,
    event_seconds: int,
    observed_seconds: int | None = None,
) -> PriceObservation:
    observed_seconds = event_seconds if observed_seconds is None else observed_seconds
    return PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=BASE_TS + timedelta(seconds=event_seconds),
        observed_ts=BASE_TS + timedelta(seconds=observed_seconds),
        price=price,
    )


def test_log_returns_from_prices_sorts_by_event_and_observed_ts() -> None:
    returns = log_returns_from_prices(
        (
            _price(102.0, event_seconds=2, observed_seconds=4),
            _price(100.0, event_seconds=0, observed_seconds=0),
            _price(101.0, event_seconds=1, observed_seconds=3),
        )
    )

    assert returns == pytest.approx(
        (
            math.log(101.0 / 100.0),
            math.log(102.0 / 101.0),
        )
    )


def test_realized_volatility_computes_rms_of_most_recent_window() -> None:
    returns = (0.01, -0.02, 0.04)

    realized = realized_volatility(returns, window=2)

    assert realized == pytest.approx(math.sqrt(((-0.02) ** 2 + 0.04**2) / 2))


def test_realized_volatility_returns_none_for_empty_returns() -> None:
    assert realized_volatility((), window=20) is None


def test_estimate_sigma_tau_increases_with_larger_recent_returns() -> None:
    small_sigma = estimate_sigma_tau((0.001,) * 200, seconds_left=60.0)
    large_sigma = estimate_sigma_tau((0.002,) * 200, seconds_left=60.0)

    assert large_sigma > small_sigma


def test_estimate_sigma_tau_applies_sigma_floor_for_flat_tape() -> None:
    sigma = estimate_sigma_tau((0.0,) * 200, seconds_left=60.0, sigma_floor=0.00005)

    assert sigma == pytest.approx(0.00005)


def test_estimate_sigma_tau_rejects_weights_that_do_not_sum_to_one() -> None:
    with pytest.raises(ValueError, match="weights"):
        estimate_sigma_tau((0.001,) * 20, seconds_left=60.0, weights=(0.5, 0.5, 0.5))


def test_estimate_sigma_tau_rejects_negative_seconds_left() -> None:
    with pytest.raises(ValueError, match="seconds_left"):
        estimate_sigma_tau((0.001,) * 20, seconds_left=-1.0)


@pytest.mark.parametrize("sigma_floor", (math.nan, math.inf, -math.inf, 0.0, -0.0001))
def test_estimate_sigma_tau_rejects_invalid_sigma_floor(sigma_floor: float) -> None:
    with pytest.raises(ValueError, match="sigma_floor"):
        estimate_sigma_tau((0.001,) * 20, seconds_left=60.0, sigma_floor=sigma_floor)


@pytest.mark.parametrize(
    ("field_name", "make_config"),
    (
        ("sigma_floor", lambda: VolatilityConfig(sigma_floor=math.nan)),
        ("sigma_floor", lambda: VolatilityConfig(sigma_floor=math.inf)),
        ("expansion_threshold", lambda: VolatilityConfig(expansion_threshold=math.nan)),
        ("expansion_threshold", lambda: VolatilityConfig(expansion_threshold=math.inf)),
        ("contraction_threshold", lambda: VolatilityConfig(contraction_threshold=math.nan)),
        ("contraction_threshold", lambda: VolatilityConfig(contraction_threshold=math.inf)),
        ("expansion_multiplier", lambda: VolatilityConfig(expansion_multiplier=math.nan)),
        ("expansion_multiplier", lambda: VolatilityConfig(expansion_multiplier=math.inf)),
        ("contraction_multiplier", lambda: VolatilityConfig(contraction_multiplier=math.nan)),
        ("contraction_multiplier", lambda: VolatilityConfig(contraction_multiplier=math.inf)),
    ),
)
def test_volatility_config_rejects_non_finite_positive_values(
    field_name: str,
    make_config: Callable[[], VolatilityConfig],
) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_config()


@pytest.mark.parametrize(
    ("field_name", "make_config"),
    (
        ("short_window", lambda: VolatilityConfig(short_window=cast(int, 1.5))),
        ("medium_window", lambda: VolatilityConfig(medium_window=cast(int, math.nan))),
        ("long_window", lambda: VolatilityConfig(long_window=cast(int, math.inf))),
    ),
)
def test_volatility_config_rejects_non_integer_or_non_finite_windows(
    field_name: str,
    make_config: Callable[[], VolatilityConfig],
) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_config()


@pytest.mark.parametrize(
    ("field_name", "estimate"),
    (
        (
            "short_window",
            lambda: estimate_sigma_tau(
                (0.001,) * 20,
                seconds_left=60.0,
                short_window=cast(int, math.nan),
            ),
        ),
        (
            "medium_window",
            lambda: estimate_sigma_tau(
                (0.001,) * 20,
                seconds_left=60.0,
                medium_window=cast(int, math.inf),
            ),
        ),
        (
            "long_window",
            lambda: estimate_sigma_tau(
                (0.001,) * 20,
                seconds_left=60.0,
                long_window=cast(int, 1.5),
            ),
        ),
    ),
)
def test_estimate_sigma_tau_rejects_non_integer_or_non_finite_windows(
    field_name: str,
    estimate: Callable[[], float],
) -> None:
    with pytest.raises(ValueError, match=field_name):
        estimate()


@pytest.mark.parametrize(
    ("short_vol", "medium_vol", "expected"),
    (
        (0.015, 0.010, "expanding"),
        (0.007, 0.010, "contracting"),
        (0.010, 0.010, "normal"),
        (None, 0.010, "unknown"),
        (0.010, None, "unknown"),
        (0.010, 0.0, "unknown"),
    ),
)
def test_classify_volatility_regime_labels_direct_cases(
    short_vol: float | None,
    medium_vol: float | None,
    expected: str,
) -> None:
    assert (
        classify_volatility_regime(
            short_vol,
            medium_vol,
            expansion_threshold=1.25,
            contraction_threshold=0.80,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("regime", "expected"),
    (
        ("expanding", 1.15),
        ("contracting", 0.95),
        ("normal", 1.0),
        ("unknown", 1.0),
    ),
)
def test_regime_multiplier_maps_regime_to_configured_multiplier(
    regime: str,
    expected: float,
) -> None:
    assert regime_multiplier(regime, VolatilityConfig()) == pytest.approx(expected)


def test_build_volatility_snapshot_is_asof_safe_and_labels_regime() -> None:
    asof_ts = BASE_TS + timedelta(seconds=10)
    prices = (
        _price(103.0, event_seconds=2),
        _price(999.0, event_seconds=11, observed_seconds=9),
        _price(100.0, event_seconds=0),
        _price(888.0, event_seconds=9, observed_seconds=11),
        _price(101.0, event_seconds=1),
    )

    snapshot = build_volatility_snapshot(
        prices=prices,
        asof_ts=asof_ts,
        seconds_left=60.0,
        config=VolatilityConfig(short_window=1, medium_window=2, long_window=3),
    )

    assert snapshot.event_ts == BASE_TS + timedelta(seconds=2)
    assert snapshot.observed_ts == BASE_TS + timedelta(seconds=2)
    assert snapshot.realized_returns == pytest.approx(
        (
            math.log(101.0 / 100.0),
            math.log(103.0 / 101.0),
        )
    )
    assert snapshot.sigma_tau is not None
    assert snapshot.sigma_tau > 0
    assert snapshot.regime in {"expanding", "normal", "contracting", "unknown"}


def test_build_volatility_snapshot_observed_ts_is_latest_observed_allowed_price() -> None:
    asof_ts = BASE_TS + timedelta(seconds=10)
    snapshot = build_volatility_snapshot(
        prices=(
            _price(100.0, event_seconds=0, observed_seconds=0),
            _price(101.0, event_seconds=4, observed_seconds=9),
            _price(102.0, event_seconds=5, observed_seconds=5),
        ),
        asof_ts=asof_ts,
        seconds_left=60.0,
        config=VolatilityConfig(short_window=1, medium_window=2, long_window=3),
    )

    assert snapshot.event_ts == BASE_TS + timedelta(seconds=5)
    assert snapshot.observed_ts == BASE_TS + timedelta(seconds=9)
    assert snapshot.realized_returns == pytest.approx(
        (
            math.log(101.0 / 100.0),
            math.log(102.0 / 101.0),
        )
    )


def test_build_volatility_snapshot_uses_chainlink_prices_only() -> None:
    asof_ts = BASE_TS + timedelta(seconds=10)
    prices = (
        _price(100.0, event_seconds=0),
        _price(101.0, event_seconds=1),
        _price(102.0, event_seconds=2),
        PriceObservation(
            source_key="coinbase_advanced_ws",
            symbol="BTC-USD",
            event_ts=BASE_TS + timedelta(seconds=3),
            observed_ts=BASE_TS + timedelta(seconds=3),
            price=120.0,
        ),
    )

    snapshot = build_volatility_snapshot(
        prices=prices,
        asof_ts=asof_ts,
        seconds_left=60.0,
        config=VolatilityConfig(short_window=1, medium_window=2, long_window=3),
    )

    assert snapshot.event_ts == BASE_TS + timedelta(seconds=2)
    assert snapshot.realized_returns == pytest.approx(
        (
            math.log(101.0 / 100.0),
            math.log(102.0 / 101.0),
        )
    )


@pytest.mark.parametrize(
    "asof_ts",
    (
        datetime(2026, 6, 1, 12, 0),
        datetime(2026, 6, 1, 7, 0, tzinfo=timezone(timedelta(hours=-5))),
    ),
)
def test_build_volatility_snapshot_rejects_non_utc_asof_ts(asof_ts: datetime) -> None:
    with pytest.raises(ValueError, match="asof_ts"):
        build_volatility_snapshot(prices=(), asof_ts=asof_ts, seconds_left=60.0)


def test_build_volatility_snapshot_returns_missing_snapshot_for_empty_prices() -> None:
    asof_ts = BASE_TS + timedelta(seconds=10)

    snapshot = build_volatility_snapshot(prices=(), asof_ts=asof_ts, seconds_left=60.0)

    assert snapshot.event_ts == asof_ts
    assert snapshot.observed_ts == asof_ts
    assert snapshot.realized_returns == ()
    assert snapshot.short_realized_vol is None
    assert snapshot.medium_realized_vol is None
    assert snapshot.long_realized_vol is None
    assert snapshot.sigma_tau is None
    assert snapshot.regime == "missing_reference_source"
