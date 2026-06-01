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


def test_build_decision_state_from_store_raises_when_only_future_price_exists(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    after = datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", after, after, 105_000.0)
    )

    with pytest.raises(DecisionStateUnavailable, match="no settlement price"):
        build_decision_state_from_store(
            store=store,
            contract=contract,
            asof_ts=asof_ts,
            resolved_threshold_price=103_950.0,
            settlement_source_key="polymarket_rtds_chainlink",
            proxy_source_keys=(),
            volatility=None,
        )
