from datetime import datetime, timezone
from pathlib import Path

import duckdb

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState, OrderBookObservation, PriceObservation
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


def _state() -> DecisionState:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    return DecisionState(
        state_id="state-1",
        asof_ts=asof_ts,
        contract=contract,
        threshold=103_950.0,
        seconds_left=120.0,
        settlement_price=104_000.0,
        settlement_source_key="polymarket_rtds_chainlink",
        proxy_prices={"coinbase_advanced_ws": 104_010.0},
        source_disagreement_bps=0.96,
        best_bid=0.61,
        best_ask=0.64,
        executable_price=0.64,
        spread=0.03,
        quote_age_ms=1000,
        source_age_ms=1000,
        book_age_ms=1000,
        realized_returns=(0.001, -0.0005),
        short_realized_vol=0.01,
        medium_realized_vol=0.012,
        long_realized_vol=0.015,
        sigma_tau=0.002,
        volatility_regime="normal",
        data_quality_flags=(),
    )


def test_store_writes_contract_price_book_state_decision_and_label(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)

    store.upsert_contract_spec(contract)
    store.insert_price_tick(
        PriceObservation(
            source_key="polymarket_rtds_chainlink",
            symbol="BTC/USD",
            event_ts=asof_ts,
            observed_ts=asof_ts,
            price=104_000.0,
        )
    )
    store.insert_orderbook_snapshot(
        OrderBookObservation(
            venue="polymarket",
            contract_id=contract.contract_id,
            token_id=contract.token_id,
            event_ts=asof_ts,
            observed_ts=asof_ts,
            best_bid=0.61,
            best_ask=0.64,
            bid_size_top=50.0,
            ask_size_top=40.0,
            spread=0.03,
            depth_json='{"bids":[],"asks":[]}',
        )
    )
    state = _state()
    store.upsert_asof_state_input(state)
    store.insert_decision_snapshot(
        decision_id="decision-1",
        state=state,
        model={"model_version": "none"},
        decision="WAIT",
        block_reason="probability_model_not_built",
    )
    store.insert_decision_label(
        decision_id="decision-1",
        contract_id=contract.contract_id,
        expiry_ts=contract.expiry_ts,
        settlement_price=104_100.0,
        did_finish_win=True,
        did_no_touch=True,
        realized_edge=None,
        label_source="fixture",
    )

    with duckdb.connect(str(db_path)) as conn:
        assert conn.sql("select count(*) from core.contracts").fetchone() == (1,)
        assert conn.sql("select count(*) from core.price_ticks").fetchone() == (1,)
        assert conn.sql("select count(*) from core.orderbook_snapshots").fetchone() == (1,)
        assert conn.sql("select count(*) from features.asof_state_inputs").fetchone() == (1,)
        assert conn.sql(
            "select decision, block_reason from features.decision_snapshots"
        ).fetchall() == [("WAIT", "probability_model_not_built")]
        assert conn.sql(
            "select did_finish_win, did_no_touch from validation.decision_labels"
        ).fetchall() == [(True, True)]


def test_register_ingest_file_records_retention_manifest(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.duckdb"
    raw_path = tmp_path / "file.parquet"
    raw_path.write_bytes(b"abc")
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    store.register_ingest_file(
        file_id="file-1",
        source_key="coinbase_advanced_ws",
        stream_key="ticker",
        partition_date="2026-05-31",
        partition_hour=21,
        path=str(raw_path),
        sha256="abc123",
        row_count=2,
        first_event_ts=datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc),
        last_event_ts=datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc),
    )

    with duckdb.connect(str(db_path)) as conn:
        rows = conn.sql(
            "select source_key, stream_key, retention_class, archive_after_days "
            "from ops.retention_manifests"
        ).fetchall()

    assert rows == [("coinbase_advanced_ws", "ticker", "raw_hot_90d", 90)]
