from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState, PriceObservation
from polymarket_engine.probability.runtime import (
    compute_and_persist_probability_outputs,
    latest_probability_output_rows,
)
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_runtime_persists_empirical_prior_output_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLYMARKET_PROBABILITY_GENERATOR", "empirical_conditional")
    monkeypatch.setenv("POLYMARKET_EMPIRICAL_PRIOR_MIN_BUCKET_SIZE", "1")
    monkeypatch.setenv("POLYMARKET_EMPIRICAL_PRIOR_HISTORY_LIMIT", "64")
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    store.insert_price_ticks(_chainlink_ticks(state.asof_ts))

    written, skipped, errors = compute_and_persist_probability_outputs(store=store, limit=4)

    assert written == 1
    assert skipped == 0
    assert errors == ()
    with duckdb.connect(str(db_path), read_only=True) as conn:
        output_json_row = conn.execute(
            "select output_json from features.probability_outputs"
        ).fetchone()
    assert output_json_row is not None
    (output_json,) = output_json_row
    payload = json.loads(output_json)
    diagnostics = payload["diagnostics"]
    assert payload["model_version"] == "empirical-conditional-chainlink-prior-v1"
    assert diagnostics["generator"] == "empirical_conditional_prior"
    assert diagnostics["asof_safe"] is True
    assert diagnostics["prior_bucket_size"] >= 1
    assert diagnostics["eligible_tick_count"] >= 3
    rows = latest_probability_output_rows(duckdb_path=db_path, limit=4)
    assert rows[0]["generator"] == "empirical_conditional_prior"
    assert rows[0]["prior_bucket_size"] >= 1
    assert rows[0]["prior_fallback_level"] == "none"


def _contract() -> ContractSpec:
    return ContractSpec(
        contract_id="btc-market:UP",
        venue="polymarket",
        market_id="btc-market",
        condition_id="0xbtc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        side="UP",
        token_id="btc-up-token",
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=">=",
        start_ts=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 6, 5, 12, 5, tzinfo=timezone.utc),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="fixture",
        rule_hash="hash",
        parser_version="test",
    )


def _decision_state() -> DecisionState:
    contract = _contract()
    asof_ts = datetime(2026, 6, 5, 12, 3, tzinfo=timezone.utc)
    return DecisionState(
        state_id="state-btc-up",
        asof_ts=asof_ts,
        contract=contract,
        threshold=100.5,
        threshold_source_key="polymarket_rtds_chainlink",
        threshold_event_ts=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
        threshold_observed_ts=datetime(2026, 6, 5, 12, 0, 1, tzinfo=timezone.utc),
        seconds_left=2.0,
        settlement_price=101.0,
        settlement_source_key="polymarket_rtds_chainlink",
        settlement_event_ts=asof_ts,
        settlement_observed_ts=asof_ts,
        proxy_prices={"coinbase_advanced_ws": 101.01},
        source_disagreement_bps=0.1,
        best_bid=0.61,
        best_ask=0.64,
        executable_price=0.64,
        spread=0.03,
        book_event_ts=asof_ts,
        book_observed_ts=asof_ts,
        quote_age_ms=100,
        source_age_ms=100,
        source_observed_lag_ms=0,
        book_age_ms=120,
        book_observed_lag_ms=0,
        realized_returns=(0.001, -0.0005),
        short_realized_vol=0.01,
        medium_realized_vol=0.01,
        long_realized_vol=0.01,
        sigma_tau=0.01,
        volatility_regime="normal",
        data_quality_flags=(),
    )


def _chainlink_ticks(asof_ts: datetime) -> tuple[PriceObservation, ...]:
    ticks: list[PriceObservation] = []
    for index in range(8):
        event_ts = asof_ts - timedelta(seconds=10 - index)
        ticks.append(
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="BTC/USD",
                event_ts=event_ts,
                observed_ts=event_ts,
                price=100.0 + index,
            )
        )
    return tuple(ticks)
