from datetime import datetime, timezone
from pathlib import Path

import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import OrderBookObservation, PriceObservation
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
