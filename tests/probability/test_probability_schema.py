import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest

from polymarket_engine.domain.contracts import ContractSide, ContractSpec
from polymarket_engine.domain.market_state import DecisionState
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


def _contract(side: ContractSide = "UP") -> ContractSpec:
    return ContractSpec(
        contract_id=f"btc-market:{side}",
        venue="polymarket",
        market_id="btc-market",
        condition_id="0xbtc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        side=side,
        token_id="111",
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=">=" if side == "UP" else "<",
        start_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 5, 31, 20, 5, tzinfo=timezone.utc),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="fixture",
        rule_hash="hash",
        parser_version="test",
    )


def _state(side: ContractSide = "UP") -> DecisionState:
    contract = _contract(side)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    return DecisionState(
        state_id=f"state-{side}",
        asof_ts=asof_ts,
        contract=contract,
        threshold=103_950.0,
        threshold_source_key="polymarket_rtds_chainlink",
        threshold_event_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        threshold_observed_ts=datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc),
        seconds_left=120.0,
        settlement_price=104_000.0,
        settlement_source_key="polymarket_rtds_chainlink",
        settlement_event_ts=asof_ts,
        settlement_observed_ts=asof_ts,
        proxy_prices={"coinbase_advanced_ws": 104_010.0},
        source_disagreement_bps=0.96,
        best_bid=0.61,
        best_ask=0.64,
        executable_price=0.64,
        spread=0.03,
        book_event_ts=asof_ts,
        book_observed_ts=asof_ts,
        quote_age_ms=1000,
        source_age_ms=1000,
        source_observed_lag_ms=0,
        book_age_ms=1000,
        book_observed_lag_ms=0,
        realized_returns=(0.001, -0.0005),
        short_realized_vol=0.01,
        medium_realized_vol=0.012,
        long_realized_vol=0.015,
        sigma_tau=0.002,
        volatility_regime="normal",
        data_quality_flags=(),
    )


def test_probability_input_from_ready_decision_state_calculates_z_path() -> None:
    state = _state()

    probability_input = ProbabilityInput.from_decision_state(state)

    assert state.sigma_tau is not None
    expected_z_path = math.log(state.settlement_price / state.threshold) / state.sigma_tau
    assert probability_input.state_id == state.state_id
    assert probability_input.asof_ts == state.asof_ts
    assert probability_input.asset == "BTC"
    assert probability_input.side == "UP"
    assert probability_input.seconds_left == 120.0
    assert probability_input.settlement_price == 104_000.0
    assert probability_input.threshold == 103_950.0
    assert probability_input.sigma_tau == 0.002
    assert probability_input.executable_price == 0.64
    assert probability_input.source_age_ms == 1000
    assert probability_input.book_age_ms == 1000
    assert probability_input.z_path == pytest.approx(expected_z_path)


def test_probability_input_rejects_quality_blocked_state() -> None:
    state = replace(_state(), data_quality_flags=("stale_source",))

    with pytest.raises(ValueError, match="quality-blocked"):
        ProbabilityInput.from_decision_state(state)


def test_probability_input_rejects_missing_sigma_tau() -> None:
    missing = replace(_state(), sigma_tau=None)
    nonpositive = replace(_state(), sigma_tau=0.0)

    with pytest.raises(ValueError, match="sigma_tau"):
        ProbabilityInput.from_decision_state(missing)
    with pytest.raises(ValueError, match="sigma_tau"):
        ProbabilityInput.from_decision_state(nonpositive)


@pytest.mark.parametrize(
    "field_name",
    (
        "threshold_event_ts",
        "threshold_observed_ts",
        "settlement_event_ts",
        "settlement_observed_ts",
        "book_event_ts",
        "book_observed_ts",
    ),
)
def test_probability_input_from_decision_state_rejects_future_timestamps(
    field_name: str,
) -> None:
    state = _state()
    future_state = replace(
        state,
        **cast(Any, {field_name: state.asof_ts + timedelta(milliseconds=1)}),
    )

    with pytest.raises(ValueError, match=field_name):
        ProbabilityInput.from_decision_state(future_state)


def test_probability_input_constructor_rejects_invalid_domain_values() -> None:
    base: dict[str, Any] = {
        "state_id": "state-1",
        "asof_ts": datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc),
        "asset": "BTC",
        "side": "UP",
        "seconds_left": 120.0,
        "settlement_price": 104_000.0,
        "threshold": 103_950.0,
        "sigma_tau": 0.002,
        "executable_price": 0.64,
        "source_age_ms": 1000,
        "book_age_ms": 1000,
        "z_path": 0.24,
    }

    invalid_cases = (
        ("seconds_left", True),
        ("seconds_left", -1.0),
        ("settlement_price", True),
        ("settlement_price", 0.0),
        ("threshold", -1.0),
        ("sigma_tau", 0.0),
        ("executable_price", True),
        ("executable_price", 1.01),
        ("source_age_ms", True),
        ("source_age_ms", 1.5),
        ("source_age_ms", -1),
        ("book_age_ms", 1.5),
        ("book_age_ms", -1),
        ("z_path", True),
    )
    for field_name, invalid_value in invalid_cases:
        with pytest.raises(ValueError, match=field_name):
            ProbabilityInput(**{**base, field_name: invalid_value})


def test_probability_input_constructor_rejects_unsupported_side() -> None:
    base: dict[str, Any] = {
        "state_id": "state-1",
        "asof_ts": datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc),
        "asset": "BTC",
        "side": "SIDEWAYS",
        "seconds_left": 120.0,
        "settlement_price": 104_000.0,
        "threshold": 103_950.0,
        "sigma_tau": 0.002,
        "executable_price": 0.64,
        "source_age_ms": 1000,
        "book_age_ms": 1000,
        "z_path": 0.24,
    }

    with pytest.raises(ValueError, match="side"):
        ProbabilityInput(**base)


def test_probability_input_constructor_rejects_unsupported_asset() -> None:
    base: dict[str, Any] = {
        "state_id": "state-1",
        "asof_ts": datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc),
        "asset": "DOGE",
        "side": "UP",
        "seconds_left": 120.0,
        "settlement_price": 104_000.0,
        "threshold": 103_950.0,
        "sigma_tau": 0.002,
        "executable_price": 0.64,
        "source_age_ms": 1000,
        "book_age_ms": 1000,
        "z_path": 0.24,
    }

    with pytest.raises(ValueError, match="asset"):
        ProbabilityInput(**base)


def test_probability_output_rejects_invalid_probabilities() -> None:
    base: dict[str, Any] = {
        "state_id": "state-1",
        "asof_ts": datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc),
        "p_finish": 0.55,
        "p_no_touch": 0.82,
        "z_path": 0.12,
        "model_version": "offline-replay-v1",
        "seed": None,
        "diagnostics": {"paths": 1000},
    }

    with pytest.raises(ValueError, match="p_finish"):
        ProbabilityOutput(**{**base, "p_finish": 1.01})
    with pytest.raises(ValueError, match="p_finish"):
        ProbabilityOutput(**{**base, "p_finish": True})
    with pytest.raises(ValueError, match="p_no_touch"):
        ProbabilityOutput(**{**base, "p_no_touch": math.nan})
    with pytest.raises(ValueError, match="z_path"):
        ProbabilityOutput(**{**base, "z_path": math.inf})
    with pytest.raises(ValueError, match="z_path"):
        ProbabilityOutput(**{**base, "z_path": True})
    with pytest.raises(ValueError, match="model_version"):
        ProbabilityOutput(**{**base, "model_version": ""})
    with pytest.raises(ValueError, match="diagnostics"):
        ProbabilityOutput(**{**base, "diagnostics": {"bad": object()}})
    with pytest.raises(ValueError, match="diagnostics"):
        ProbabilityOutput(**{**base, "diagnostics": []})


def test_probability_output_rejects_nonstandard_diagnostic_floats() -> None:
    base: dict[str, Any] = {
        "state_id": "state-1",
        "asof_ts": datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc),
        "p_finish": 0.55,
        "p_no_touch": 0.82,
        "z_path": 0.12,
        "model_version": "offline-replay-v1",
        "seed": None,
    }

    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="diagnostics"):
            ProbabilityOutput(**{**base, "diagnostics": {"x": value}})


def test_probability_output_rejects_non_json_native_diagnostics() -> None:
    base: dict[str, Any] = {
        "state_id": "state-1",
        "asof_ts": datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc),
        "p_finish": 0.55,
        "p_no_touch": 0.82,
        "z_path": 0.12,
        "model_version": "offline-replay-v1",
        "seed": None,
    }

    with pytest.raises(ValueError, match="diagnostics must be strict JSON"):
        ProbabilityOutput(
            **{
                **base,
                "diagnostics": {"not_json": datetime(2026, 5, 31, tzinfo=timezone.utc)},
            }
        )
    with pytest.raises(ValueError, match="diagnostics must be strict JSON"):
        ProbabilityOutput(**{**base, "diagnostics": {1: "not a JSON object key"}})
