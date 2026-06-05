from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.probability.empirical_prior import (
    CHAINLINK_SOURCE_KEY,
    EmpiricalPriorConfig,
    run_empirical_conditional_monte_carlo,
)
from polymarket_engine.probability.schema import ProbabilityInput


def _probability_input() -> ProbabilityInput:
    return ProbabilityInput(
        state_id="state-btc-up",
        asof_ts=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
        asset="BTC",
        side="UP",
        comparison_operator=">=",
        seconds_left=120.0,
        settlement_price=100.0,
        threshold=100.5,
        sigma_tau=0.02,
        executable_price=0.52,
        source_age_ms=20,
        book_age_ms=30,
        z_path=-0.25,
    )


def _tick(
    seconds_before_asof: int,
    price: float,
    *,
    source_key: str = CHAINLINK_SOURCE_KEY,
    symbol: str = "BTC/USD",
    observed_offset_seconds: int = 0,
) -> PriceObservation:
    asof_ts = _probability_input().asof_ts
    event_ts = asof_ts - timedelta(seconds=seconds_before_asof)
    observed_ts = event_ts + timedelta(seconds=observed_offset_seconds)
    return PriceObservation(
        source_key=source_key,
        symbol=symbol,
        event_ts=event_ts,
        observed_ts=observed_ts,
        price=price,
    )


def test_empirical_prior_uses_only_chainlink_ticks_observed_by_asof() -> None:
    probability_input = _probability_input()
    future_observed_tick = _tick(
        1,
        500.0,
        observed_offset_seconds=120,
    )
    non_chainlink_tick = _tick(3, 300.0, source_key="coinbase_advanced_ws")
    ticks = (
        _tick(6, 100.0),
        _tick(5, 101.0),
        _tick(4, 102.0),
        _tick(3, 103.0),
        future_observed_tick,
        non_chainlink_tick,
    )

    output = run_empirical_conditional_monte_carlo(
        probability_input,
        price_ticks=ticks,
        path_count=8,
        steps=2,
        seed=7,
        config=EmpiricalPriorConfig(min_bucket_size=1),
    )

    assert output.diagnostics["generator"] == "empirical_conditional_prior"
    assert output.diagnostics["asof_safe"] is True
    assert output.diagnostics["eligible_tick_count"] == 4
    assert output.diagnostics["excluded_future_tick_count"] == 1
    assert output.diagnostics["ignored_non_chainlink_tick_count"] == 1
    assert output.diagnostics["latest_fragment_end_ts"] <= probability_input.asof_ts.isoformat()
    assert 0.0 <= output.p_finish <= 1.0
    assert 0.0 <= output.p_no_touch <= 1.0


def test_empirical_prior_falls_back_to_lognormal_when_bucket_is_sparse() -> None:
    probability_input = _probability_input()

    output = run_empirical_conditional_monte_carlo(
        probability_input,
        price_ticks=(_tick(3, 100.0), _tick(2, 100.1)),
        path_count=16,
        steps=4,
        seed=11,
        config=EmpiricalPriorConfig(min_bucket_size=3),
    )

    assert output.diagnostics["generator"] == "lognormal_fallback"
    assert output.diagnostics["prior_fallback_level"] == "lognormal"
    assert output.diagnostics["prior_bucket_size"] == 0
    assert output.diagnostics["asof_safe"] is True
    assert output.model_version == "empirical-conditional-prior-fallback-lognormal-v1"


def test_empirical_prior_rescales_historical_residuals_by_current_sigma_tau() -> None:
    probability_input = _probability_input()
    ticks = tuple(
        _tick(seconds_before_asof=10 - index, price=100.0 + index)
        for index in range(8)
    )

    output = run_empirical_conditional_monte_carlo(
        probability_input,
        price_ticks=ticks,
        path_count=12,
        steps=3,
        seed=13,
        config=EmpiricalPriorConfig(min_bucket_size=2),
    )

    assert output.diagnostics["generator"] == "empirical_conditional_prior"
    assert output.diagnostics["sigma_scaled"] is True
    assert output.diagnostics["prior_bucket_size"] >= 2
    assert output.diagnostics["historical_sigma_floor_applied_count"] == 0
    assert output.model_version == "empirical-conditional-chainlink-prior-v1"


def test_empirical_prior_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="min_bucket_size"):
        EmpiricalPriorConfig(min_bucket_size=0)
