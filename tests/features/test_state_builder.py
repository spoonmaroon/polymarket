from datetime import datetime, timezone

import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import (
    DecisionState,
    OrderBookObservation,
    PriceObservation,
    VolatilitySnapshot,
)
from polymarket_engine.features.state_builder import (
    DecisionStateUnavailable,
    build_decision_state,
    validate_observation_asof,
)


def _contract() -> ContractSpec:
    return ContractSpec(
        contract_id="btc-market:UP",
        venue="polymarket",
        market_id="btc-market",
        condition_id="0xbtc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        side="UP",
        token_id="111",
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=">=",
        start_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 5, 31, 20, 5, tzinfo=timezone.utc),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="fixture",
        rule_hash="hash",
        parser_version="test",
    )


def _threshold_observation(price: float = 103_950.0) -> PriceObservation:
    return PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc),
        price=price,
    )


def test_decision_state_model_holds_contract_price_book_and_volatility_state() -> None:
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    price = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=asof_ts,
        observed_ts=asof_ts,
        price=104_000.0,
    )
    book = OrderBookObservation(
        venue="polymarket",
        contract_id="btc-market:UP",
        token_id="111",
        event_ts=asof_ts,
        observed_ts=asof_ts,
        best_bid=0.61,
        best_ask=0.64,
        bid_size_top=50.0,
        ask_size_top=40.0,
        spread=0.03,
        depth_json='{"bids":[],"asks":[]}',
    )
    volatility = VolatilitySnapshot(
        event_ts=asof_ts,
        observed_ts=asof_ts,
        realized_returns=(0.001, -0.0005),
        short_realized_vol=0.01,
        medium_realized_vol=0.012,
        long_realized_vol=0.015,
        sigma_tau=0.002,
        regime="normal",
    )

    state = DecisionState(
        state_id="btc-market:UP:2026-05-31T20:03:00+00:00",
        asof_ts=asof_ts,
        contract=_contract(),
        threshold=103_950.0,
        threshold_source_key="polymarket_rtds_chainlink",
        threshold_event_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        threshold_observed_ts=datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc),
        seconds_left=120.0,
        settlement_price=price.price,
        settlement_source_key=price.source_key,
        settlement_event_ts=price.event_ts,
        settlement_observed_ts=price.observed_ts,
        proxy_prices={"coinbase_advanced_ws": 104_010.0},
        source_disagreement_bps=0.9615384615384616,
        best_bid=book.best_bid,
        best_ask=book.best_ask,
        executable_price=book.best_ask,
        spread=book.spread,
        book_event_ts=book.event_ts,
        book_observed_ts=book.observed_ts,
        quote_age_ms=0,
        source_age_ms=0,
        source_observed_lag_ms=0,
        book_age_ms=0,
        book_observed_lag_ms=0,
        realized_returns=volatility.realized_returns,
        short_realized_vol=volatility.short_realized_vol,
        medium_realized_vol=volatility.medium_realized_vol,
        long_realized_vol=volatility.long_realized_vol,
        sigma_tau=volatility.sigma_tau,
        volatility_regime=volatility.regime,
        data_quality_flags=(),
    )

    assert state.contract.asset == "BTC"
    assert state.executable_price == 0.64
    assert state.seconds_left == 120.0


def test_build_decision_state_uses_latest_price_and_book_at_or_before_asof() -> None:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    price_before = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=datetime(2026, 5, 31, 20, 2, 58, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
        price=104_000.0,
    )
    price_after = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        price=105_000.0,
    )
    book_before = OrderBookObservation(
        venue="polymarket",
        contract_id=contract.contract_id,
        token_id=contract.token_id,
        event_ts=datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
        best_bid=0.61,
        best_ask=0.64,
        bid_size_top=50.0,
        ask_size_top=40.0,
        spread=0.03,
        depth_json='{"bids":[],"asks":[]}',
    )

    state = build_decision_state(
        contract=contract,
        asof_ts=asof_ts,
        settlement_prices=(price_before, price_after),
        proxy_prices=(),
        orderbooks=(book_before,),
        volatility=None,
        threshold_observation=_threshold_observation(),
    )

    assert state.settlement_price == 104_000.0
    assert state.best_ask == 0.64
    assert state.source_age_ms == 2000
    assert state.source_observed_lag_ms == 1000
    assert state.book_age_ms == 1000


def test_build_decision_state_rejects_selected_future_observation() -> None:
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    future_price = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=asof_ts,
        observed_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        price=105_000.0,
    )

    with pytest.raises(ValueError, match="future_price observed_ts timestamp is after asof_ts"):
        validate_observation_asof(future_price, asof_ts, "future_price")


def test_build_decision_state_rejects_naive_asof_ts_before_comparison() -> None:
    contract = _contract()
    naive_asof_ts = datetime(2026, 5, 31, 20, 3)
    aware_price_ts = datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc)
    price = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=aware_price_ts,
        observed_ts=aware_price_ts,
        price=104_000.0,
    )

    with pytest.raises(ValueError, match="asof_ts must be timezone-aware"):
        build_decision_state(
            contract=contract,
            asof_ts=naive_asof_ts,
            settlement_prices=(price,),
            proxy_prices=(),
            orderbooks=(),
            volatility=None,
            threshold_observation=_threshold_observation(),
        )


def test_build_decision_state_rejects_start_price_threshold_from_future() -> None:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    settlement = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=asof_ts,
        observed_ts=asof_ts,
        price=104_000.0,
    )
    future_threshold = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        price=103_950.0,
    )

    with pytest.raises(
        ValueError,
        match="threshold_observation observed_ts timestamp is after asof_ts",
    ):
        build_decision_state(
            contract=contract,
            asof_ts=asof_ts,
            settlement_prices=(settlement,),
            proxy_prices=(),
            orderbooks=(),
            volatility=None,
            threshold_observation=future_threshold,
        )


def test_build_decision_state_rejects_decision_before_start_price_exists() -> None:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 19, 59, 59, tzinfo=timezone.utc)
    settlement = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=asof_ts,
        observed_ts=asof_ts,
        price=104_000.0,
    )

    with pytest.raises(DecisionStateUnavailable, match="asof_ts before contract start"):
        build_decision_state(
            contract=contract,
            asof_ts=asof_ts,
            settlement_prices=(settlement,),
            proxy_prices=(),
            orderbooks=(),
            volatility=None,
            threshold_observation=_threshold_observation(),
        )


def test_build_decision_state_raises_when_no_settlement_price_exists_before_asof() -> None:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    future_price = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        price=105_000.0,
    )

    with pytest.raises(DecisionStateUnavailable, match="no settlement price"):
        build_decision_state(
            contract=contract,
            asof_ts=asof_ts,
            settlement_prices=(future_price,),
            proxy_prices=(),
            orderbooks=(),
            volatility=None,
            threshold_observation=_threshold_observation(),
        )


def test_build_decision_state_flags_stale_source_missing_book_and_source_disagreement() -> None:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    stale_price = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=datetime(2026, 5, 31, 20, 2, 45, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 2, 45, tzinfo=timezone.utc),
        price=104_000.0,
    )
    proxy = PriceObservation(
        source_key="coinbase_advanced_ws",
        symbol="BTC-USD",
        event_ts=datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
        price=104_500.0,
    )

    state = build_decision_state(
        contract=contract,
        asof_ts=asof_ts,
        settlement_prices=(stale_price,),
        proxy_prices=(proxy,),
        orderbooks=(),
        volatility=None,
        threshold_observation=_threshold_observation(),
        stale_source_after_ms=5_000,
        source_disagreement_block_bps=10.0,
    )

    assert "stale_source" in state.data_quality_flags
    assert "missing_orderbook" in state.data_quality_flags
    assert "source_disagreement" in state.data_quality_flags
    assert state.best_bid is None
    assert state.executable_price is None


def test_build_decision_state_ignores_wrong_asset_proxy_prices() -> None:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    settlement = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=asof_ts,
        observed_ts=asof_ts,
        price=104_000.0,
    )
    eth_proxy = PriceObservation(
        source_key="coinbase_advanced_ws",
        symbol="ETH-USD",
        event_ts=asof_ts,
        observed_ts=asof_ts,
        price=3_500.0,
    )

    state = build_decision_state(
        contract=contract,
        asof_ts=asof_ts,
        settlement_prices=(settlement,),
        proxy_prices=(eth_proxy,),
        orderbooks=(),
        volatility=None,
        threshold_observation=_threshold_observation(),
    )

    assert state.proxy_prices == {}
    assert state.source_disagreement_bps is None


def test_orderbook_observation_rejects_impossible_polymarket_quote() -> None:
    ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="best_bid must be less than or equal to best_ask"):
        OrderBookObservation(
            venue="polymarket",
            contract_id="btc-market:UP",
            token_id="111",
            event_ts=ts,
            observed_ts=ts,
            best_bid=0.70,
            best_ask=0.64,
            bid_size_top=50.0,
            ask_size_top=40.0,
            spread=0.03,
            depth_json='{"bids":[],"asks":[]}',
        )
