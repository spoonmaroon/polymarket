from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState
from polymarket_engine.probability import runtime
from polymarket_engine.probability.runtime import latest_probability_output_rows
from polymarket_engine.probability.runtime import ProbabilityRuntimeCache
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_runtime_cache_timestamps_after_successful_payload_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monotonic_times = iter((100.0, 102.0, 102.1))
    build_calls: list[Path] = []

    def fake_monotonic() -> float:
        return next(monotonic_times)

    def fake_build_probability_payload(*, duckdb_path: Path, limit: int) -> dict[str, object]:
        build_calls.append(duckdb_path)
        return {
            "ok": True,
            "state": "OK",
            "generated_at": "2026-06-05T14:03:00+00:00",
            "cached": False,
            "model_version": None,
            "rows": [],
            "skipped": 0,
            "errors": [],
            "limit": limit,
        }

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    monkeypatch.setattr(runtime, "build_probability_payload", fake_build_probability_payload)

    cache = ProbabilityRuntimeCache(min_interval_seconds=1.0)

    first = cache.payload(duckdb_path=tmp_path / "probability.duckdb", limit=4)
    second = cache.payload(duckdb_path=tmp_path / "probability.duckdb", limit=4)

    assert first["cached"] is False
    assert second["cached"] is True
    assert len(build_calls) == 1


def test_latest_probability_output_rows_falls_back_to_p_finish_when_p_hat_missing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    probability_input = _persisted_probability_input(store)
    store.insert_probability_output(
        output_id="prob-missing-p-hat",
        probability_input=probability_input,
        output=ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.42,
            p_no_touch=0.57,
            z_path=probability_input.z_path,
            model_version="offline-lognormal-chainlink-sigma-v1",
            seed=20260605,
            diagnostics={"path_count": 10_000, "steps": 60},
        ),
    )

    rows = latest_probability_output_rows(duckdb_path=db_path, limit=1)

    assert rows[0]["p_finish"] == pytest.approx(0.42)
    assert rows[0]["p_hat"] == pytest.approx(0.42)


def test_latest_probability_output_rows_preserves_explicit_zero_p_hat(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    probability_input = _persisted_probability_input(store)
    store.insert_probability_output(
        output_id="prob-zero-p-hat",
        probability_input=probability_input,
        output=ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.42,
            p_no_touch=0.57,
            z_path=probability_input.z_path,
            model_version="offline-lognormal-chainlink-sigma-v1",
            seed=20260605,
            diagnostics={"path_count": 10_000, "steps": 60, "p_hat": 0.0},
        ),
    )

    rows = latest_probability_output_rows(duckdb_path=db_path, limit=1)

    assert rows[0]["p_finish"] == pytest.approx(0.42)
    assert rows[0]["p_hat"] == pytest.approx(0.0)


def test_latest_probability_output_rows_promotes_risk_adjusted_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    probability_input = _persisted_probability_input(store)
    store.insert_probability_output(
        output_id="prob-risk-fields",
        probability_input=probability_input,
        output=ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.62,
            p_no_touch=0.58,
            z_path=probability_input.z_path,
            model_version="ensemble-v1",
            seed=20260607,
            diagnostics={
                "risk_adjusted_p_finish": 0.56,
                "risk_adjusted_p_no_touch": 0.51,
                "risk_adjustment": 0.06,
                "terminal_probability_source": "core_generators_ex_stress_overlay",
            },
        ),
    )

    rows = latest_probability_output_rows(duckdb_path=db_path, limit=1)

    assert rows[0]["p_finish"] == pytest.approx(0.62)
    assert rows[0]["risk_adjusted_p_finish"] == pytest.approx(0.56)
    assert rows[0]["risk_adjustment"] == pytest.approx(0.06)
    assert rows[0]["terminal_probability_source"] == "core_generators_ex_stress_overlay"


def _persisted_probability_input(store: DuckDbIngestStore) -> ProbabilityInput:
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    return ProbabilityInput.from_decision_state(state)


def _contract() -> ContractSpec:
    start_ts = datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc)
    return ContractSpec(
        contract_id="btc-updown-5m:UP",
        venue="polymarket",
        market_id="btc-updown-5m",
        condition_id="0xbtc",
        slug="btc-updown-5m",
        asset="BTC",
        side="UP",
        token_id="up-token",
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=">",
        start_ts=start_ts,
        expiry_ts=start_ts + timedelta(minutes=5),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="fixture",
        rule_hash="hash",
        parser_version="test",
    )


def _decision_state() -> DecisionState:
    contract = _contract()
    asof_ts = contract.start_ts + timedelta(minutes=3)
    return DecisionState(
        state_id="state-btc-up",
        asof_ts=asof_ts,
        contract=contract,
        threshold=100.0,
        threshold_source_key="polymarket_rtds_chainlink",
        threshold_event_ts=contract.start_ts,
        threshold_observed_ts=contract.start_ts + timedelta(seconds=1),
        seconds_left=120.0,
        settlement_price=101.0,
        settlement_source_key="polymarket_rtds_chainlink",
        settlement_event_ts=asof_ts,
        settlement_observed_ts=asof_ts,
        proxy_prices={"coinbase_advanced_ws": 101.0},
        source_disagreement_bps=0.0,
        best_bid=0.52,
        best_ask=0.54,
        executable_price=0.54,
        spread=0.02,
        book_event_ts=asof_ts,
        book_observed_ts=asof_ts,
        quote_age_ms=200,
        source_age_ms=200,
        source_observed_lag_ms=0,
        book_age_ms=200,
        book_observed_lag_ms=0,
        realized_returns=(0.001, -0.0005),
        short_realized_vol=0.01,
        medium_realized_vol=0.012,
        long_realized_vol=0.015,
        sigma_tau=0.01,
        volatility_regime="normal",
        data_quality_flags=(),
    )
