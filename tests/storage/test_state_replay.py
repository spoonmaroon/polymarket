from datetime import datetime, timezone
from pathlib import Path

import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import (
    OrderBookObservation,
    PriceObservation,
    VolatilitySnapshot,
)
from polymarket_engine.features.state_builder import DecisionStateUnavailable
from polymarket_engine.features.state_replay import build_decision_state_from_store
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


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


def test_store_latest_price_tick_uses_latest_row_at_or_before_asof(tmp_path: Path) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    before = datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc)
    after = datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", before, before, 104_000.0)
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", after, after, 105_000.0)
    )

    tick = store.latest_price_tick(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        asof_ts=asof_ts,
    )

    assert tick is not None
    assert tick.price == 104_000.0
    assert tick.event_ts == before


def test_build_decision_state_from_store_never_uses_future_price_or_book(tmp_path: Path) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    store.upsert_contract_spec(contract)
    before = datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc)
    after = datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            contract.start_ts,
            datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc),
            103_950.0,
        )
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", before, before, 104_000.0)
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", after, after, 105_000.0)
    )
    store.insert_orderbook_snapshot(
        OrderBookObservation(
            venue="polymarket",
            contract_id=contract.contract_id,
            token_id=contract.token_id,
            event_ts=before,
            observed_ts=before,
            best_bid=0.61,
            best_ask=0.64,
            bid_size_top=50.0,
            ask_size_top=40.0,
            spread=0.03,
            depth_json='{"bids":[],"asks":[]}',
        )
    )

    state = build_decision_state_from_store(
        store=store,
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=103_950.0,
        settlement_source_key="polymarket_rtds_chainlink",
        proxy_source_keys=(),
        volatility=None,
    )

    assert state.settlement_price == 104_000.0
    assert state.best_ask == 0.64


def test_build_decision_state_from_store_ignores_future_price_after_threshold(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    after = datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            contract.start_ts,
            datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc),
            103_950.0,
        )
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", after, after, 105_000.0)
    )

    state = build_decision_state_from_store(
        store=store,
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=103_950.0,
        settlement_source_key="polymarket_rtds_chainlink",
        proxy_source_keys=(),
        volatility=None,
    )

    assert state.settlement_price == 103_950.0


def test_build_decision_state_from_store_requires_asof_threshold_observation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            event_ts=datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc),
            observed_ts=datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc),
            price=103_950.0,
        )
    )
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            event_ts=asof_ts,
            observed_ts=asof_ts,
            price=104_000.0,
        )
    )

    with pytest.raises(DecisionStateUnavailable, match="start-price contract requires"):
        build_decision_state_from_store(
            store=store,
            contract=contract,
            asof_ts=asof_ts,
            resolved_threshold_price=103_950.0,
            settlement_source_key="polymarket_rtds_chainlink",
            proxy_source_keys=(),
            volatility=None,
        )


def test_store_latest_price_tick_prefers_newer_event_over_late_old_tick(tmp_path: Path) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    newer_event = datetime(2026, 5, 31, 20, 2, 50, tzinfo=timezone.utc)
    late_old_event = datetime(2026, 5, 31, 20, 2, 40, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            newer_event,
            datetime(2026, 5, 31, 20, 2, 51, tzinfo=timezone.utc),
            104_000.0,
        )
    )
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            late_old_event,
            datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
            103_000.0,
        )
    )

    tick = store.latest_price_tick(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        asof_ts=asof_ts,
    )

    assert tick is not None
    assert tick.price == 104_000.0


def test_build_decision_state_from_store_maps_coinbase_proxy_symbol(tmp_path: Path) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            contract.start_ts,
            datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc),
            103_950.0,
        )
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", asof_ts, asof_ts, 104_000.0)
    )
    store.insert_price_tick(
        PriceObservation("coinbase_advanced_ws", "BTC-USD", asof_ts, asof_ts, 104_100.0)
    )

    state = build_decision_state_from_store(
        store=store,
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=103_950.0,
        settlement_source_key="polymarket_rtds_chainlink",
        proxy_source_keys=("coinbase_advanced_ws",),
        volatility=None,
    )

    assert state.proxy_prices == {"coinbase_advanced_ws": 104_100.0}
    assert state.source_disagreement_bps is not None


def test_build_decision_state_from_store_builds_asof_volatility(tmp_path: Path) -> None:
    db_path = tmp_path / "replay-volatility.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    store.upsert_contract_spec(contract)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    threshold_ts = datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)
    prices = (103_950.0, 103_980.0, 104_000.0, 104_050.0, 104_090.0)
    for index, price in enumerate(prices):
        ts = threshold_ts.replace(second=index)
        store.insert_price_tick(
            PriceObservation("polymarket_rtds_chainlink", "BTC/USD", ts, ts, price)
        )
    store.insert_price_tick(
        PriceObservation("coinbase_advanced_ws", "BTC-USD", asof_ts, asof_ts, 104_100.0)
    )

    state = build_decision_state_from_store(
        store=store,
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=103_950.0,
        settlement_source_key="polymarket_rtds_chainlink",
        proxy_source_keys=("coinbase_advanced_ws",),
        volatility=None,
        volatility_source_key="polymarket_rtds_chainlink",
        volatility_lookback_limit=10,
    )

    assert state.sigma_tau is not None
    assert state.sigma_tau > 0
    assert "missing_volatility" not in state.data_quality_flags


def test_build_decision_state_from_store_rejects_coinbase_volatility_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "replay-coinbase-volatility.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    threshold_ts = datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            threshold_ts,
            threshold_ts,
            103_950.0,
        )
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", asof_ts, asof_ts, 104_100.0)
    )
    prices = (104_000.0, 104_120.0, 104_050.0, 104_260.0, 104_190.0)
    for index, price in enumerate(prices):
        ts = threshold_ts.replace(second=index)
        store.insert_price_tick(PriceObservation("coinbase_advanced_ws", "BTC-USD", ts, ts, price))

    with pytest.raises(ValueError, match="volatility_source_key must be polymarket_rtds_chainlink"):
        build_decision_state_from_store(
            store=store,
            contract=contract,
            asof_ts=asof_ts,
            resolved_threshold_price=103_950.0,
            settlement_source_key="polymarket_rtds_chainlink",
            proxy_source_keys=(),
            volatility=None,
            volatility_source_key="coinbase_advanced_ws",
            volatility_lookback_limit=10,
        )


def test_build_decision_state_from_store_rejects_binance_volatility_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "replay-binance-volatility.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    threshold_ts = datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            threshold_ts,
            threshold_ts,
            103_950.0,
        )
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", asof_ts, asof_ts, 104_100.0)
    )
    prices = (104_000.0, 104_180.0, 104_020.0, 104_300.0, 104_120.0)
    for index, price in enumerate(prices):
        ts = threshold_ts.replace(second=index)
        store.insert_price_tick(PriceObservation("binance_spot_ws", "BTCUSDT", ts, ts, price))

    with pytest.raises(ValueError, match="volatility_source_key must be polymarket_rtds_chainlink"):
        build_decision_state_from_store(
            store=store,
            contract=contract,
            asof_ts=asof_ts,
            resolved_threshold_price=103_950.0,
            settlement_source_key="polymarket_rtds_chainlink",
            proxy_source_keys=(),
            volatility=None,
            volatility_source_key="binance_spot_ws",
            volatility_lookback_limit=10,
        )


def test_build_decision_state_from_store_without_volatility_source_keeps_missing_flag(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "replay-missing-volatility.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            contract.start_ts,
            contract.start_ts,
            103_950.0,
        )
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", asof_ts, asof_ts, 104_000.0)
    )

    state = build_decision_state_from_store(
        store=store,
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=103_950.0,
        settlement_source_key="polymarket_rtds_chainlink",
        proxy_source_keys=(),
        volatility=None,
    )

    assert "missing_volatility" in state.data_quality_flags


def test_build_decision_state_from_store_rejects_nonpositive_volatility_lookback_limit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "replay-invalid-volatility-limit.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()

    with pytest.raises(ValueError, match="limit must be positive"):
        build_decision_state_from_store(
            store=store,
            contract=contract,
            asof_ts=datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc),
            resolved_threshold_price=103_950.0,
            settlement_source_key="polymarket_rtds_chainlink",
            proxy_source_keys=(),
            volatility=None,
            volatility_source_key="polymarket_rtds_chainlink",
            volatility_lookback_limit=0,
        )


def test_build_decision_state_from_store_prefers_explicit_volatility(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "replay-explicit-volatility.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    explicit_volatility = VolatilitySnapshot(
        event_ts=asof_ts,
        observed_ts=asof_ts,
        realized_returns=(0.001,),
        short_realized_vol=0.001,
        medium_realized_vol=0.001,
        long_realized_vol=0.001,
        sigma_tau=0.123,
        regime="normal",
    )
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            contract.start_ts,
            contract.start_ts,
            103_950.0,
        )
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", asof_ts, asof_ts, 104_000.0)
    )

    state = build_decision_state_from_store(
        store=store,
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=103_950.0,
        settlement_source_key="polymarket_rtds_chainlink",
        proxy_source_keys=(),
        volatility=explicit_volatility,
        volatility_source_key="polymarket_rtds_chainlink",
        volatility_lookback_limit=10,
    )

    assert state.sigma_tau == 0.123
    assert state.realized_returns == (0.001,)


def test_store_price_ticks_before_returns_asof_ordered_history(tmp_path: Path) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    before_1 = datetime(2026, 5, 31, 20, 2, 58, tzinfo=timezone.utc)
    before_2 = datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc)
    after = datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", before_1, before_1, 104000.0)
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", before_2, before_2, 104010.0)
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", after, after, 105000.0)
    )

    ticks = store.price_ticks_before(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        asof_ts=asof_ts,
        limit=10,
    )

    assert [tick.price for tick in ticks] == [104000.0, 104010.0]


def test_store_price_ticks_before_limits_latest_rows_and_returns_ascending(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    ticks = (
        (datetime(2026, 5, 31, 20, 2, 56, tzinfo=timezone.utc), 103970.0),
        (datetime(2026, 5, 31, 20, 2, 57, tzinfo=timezone.utc), 103980.0),
        (datetime(2026, 5, 31, 20, 2, 58, tzinfo=timezone.utc), 104000.0),
        (datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc), 104010.0),
    )
    for event_ts, price in ticks:
        store.insert_price_tick(
            PriceObservation("polymarket_rtds_chainlink", "BTC/USD", event_ts, event_ts, price)
        )

    history = store.price_ticks_before(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        asof_ts=asof_ts,
        limit=2,
    )

    assert [tick.price for tick in history] == [104000.0, 104010.0]
    assert [tick.event_ts for tick in history] == [ticks[2][0], ticks[3][0]]


def test_store_price_ticks_before_filters_observed_source_and_symbol(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    eligible_ts = datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc)
    late_observed_ts = datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", eligible_ts, eligible_ts, 104000.0)
    )
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            eligible_ts,
            late_observed_ts,
            104010.0,
        )
    )
    store.insert_price_tick(
        PriceObservation("coinbase_advanced_ws", "BTC/USD", eligible_ts, eligible_ts, 104020.0)
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "ETH/USD", eligible_ts, eligible_ts, 2600.0)
    )

    history = store.price_ticks_before(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        asof_ts=asof_ts,
        limit=10,
    )

    assert [tick.price for tick in history] == [104000.0]


def test_store_price_ticks_before_preserves_bid_ask_and_sequence(tmp_path: Path) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    event_ts = datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation(
            source_key="polymarket_rtds_chainlink",
            symbol="BTC/USD",
            event_ts=event_ts,
            observed_ts=event_ts,
            price=104000.0,
            bid=103990.0,
            ask=104010.0,
            sequence="seq-104",
        )
    )

    history = store.price_ticks_before(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        asof_ts=datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc),
        limit=10,
    )

    assert len(history) == 1
    assert history[0].bid == 103990.0
    assert history[0].ask == 104010.0
    assert history[0].sequence == "seq-104"


@pytest.mark.parametrize("limit", [0, -1])
def test_store_price_ticks_before_rejects_nonpositive_limit(
    tmp_path: Path,
    limit: int,
) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    with pytest.raises(ValueError, match="limit must be positive"):
        store.price_ticks_before(
            source_key="polymarket_rtds_chainlink",
            symbol="BTC/USD",
            asof_ts=datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc),
            limit=limit,
        )
