from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from polymarket_engine.domain.contracts import ContractSide, ContractSpec
from polymarket_engine.domain.market_state import DecisionState
from polymarket_engine.probability.hot_inputs import (
    HOT_PROBABILITY_INPUTS_SCHEMA_VERSION,
    read_hot_probability_inputs,
    write_hot_probability_inputs,
)
from polymarket_engine.probability.gpu_worker import _latest_probability_inputs_from_snapshot


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


def test_hot_probability_inputs_round_trips_one_ready_state(tmp_path: Path) -> None:
    out_path = tmp_path / "hot" / "inputs.json"
    generated_at = datetime(2026, 5, 31, 20, 3, 30, tzinfo=timezone(timedelta(hours=-5)))

    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state(),),
        generated_at=generated_at,
    )
    raw_text = out_path.read_text()
    raw = json.loads(raw_text)
    payload = read_hot_probability_inputs(
        out_path=out_path,
        limit=10,
        max_age_seconds=60 * 60 * 24 * 365,
    )

    assert raw_text.endswith("\n")
    assert "\n" not in raw_text[:-1]
    assert raw["schema_version"] == HOT_PROBABILITY_INPUTS_SCHEMA_VERSION
    assert raw["generated_at"] == "2026-06-01T01:03:30+00:00"
    assert raw["inputs"][0]["market_slug"] == "btc-updown-5m-1780264500"
    assert raw["inputs"][0]["volatility_regime"] == "normal"
    assert payload.schema_version == HOT_PROBABILITY_INPUTS_SCHEMA_VERSION
    assert payload.generated_at == datetime(2026, 6, 1, 1, 3, 30, tzinfo=timezone.utc)
    assert payload.skipped == 0
    assert len(payload.inputs) == 1

    runtime_input = payload.inputs[0]
    assert runtime_input.probability_input.state_id == "state-UP"
    assert runtime_input.contract_id == "btc-market:UP"
    assert runtime_input.contract == "BTC 5m UP"
    assert runtime_input.start_ts == datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)
    assert runtime_input.expiry_ts == datetime(2026, 5, 31, 20, 5, tzinfo=timezone.utc)
    assert runtime_input.flags == ("OK",)
    assert runtime_input.market_slug == "btc-updown-5m-1780264500"
    assert runtime_input.volatility_regime == "normal"


def test_cuda_worker_loads_hot_probability_inputs_snapshot(tmp_path: Path) -> None:
    out_path = tmp_path / "hot" / "inputs.json"
    generated_at = datetime.now(timezone.utc)

    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state("UP"), _state("DOWN")),
        generated_at=generated_at,
    )

    inputs, skipped = _latest_probability_inputs_from_snapshot(
        path=out_path,
        limit=10,
        max_state_age_seconds=60 * 60 * 24 * 365,
        max_snapshot_age_seconds=60,
    )

    assert skipped == 0
    assert [row.contract_id for row in inputs] == ["btc-market:UP", "btc-market:DOWN"]
    assert [row.probability_input.side for row in inputs] == ["UP", "DOWN"]
    assert [row.market_slug for row in inputs] == [
        "btc-updown-5m-1780264500",
        "btc-updown-5m-1780264500",
    ]


def test_cuda_worker_derives_market_slug_from_legacy_hot_snapshot(tmp_path: Path) -> None:
    out_path = tmp_path / "hot" / "inputs.json"
    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state("UP"),),
        generated_at=datetime.now(timezone.utc),
    )
    raw = json.loads(out_path.read_text())
    del raw["inputs"][0]["market_slug"]
    out_path.write_text(json.dumps(raw))

    inputs, skipped = _latest_probability_inputs_from_snapshot(
        path=out_path,
        limit=10,
        max_state_age_seconds=60 * 60 * 24 * 365,
        max_snapshot_age_seconds=60,
    )

    assert skipped == 0
    assert [row.market_slug for row in inputs] == ["btc-market"]


def test_hot_probability_inputs_skip_quality_blocked_states(tmp_path: Path) -> None:
    out_path = tmp_path / "inputs.json"
    blocked = replace(_state("DOWN"), data_quality_flags=("stale_source",))

    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state("UP"), blocked),
        generated_at=datetime.now(timezone.utc),
    )

    payload = read_hot_probability_inputs(out_path=out_path, limit=10, max_age_seconds=60)

    assert payload.skipped == 1
    assert [row.contract_id for row in payload.inputs] == ["btc-market:UP"]


def test_hot_probability_inputs_keep_incomplete_orderbook_when_sigma_valid(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "inputs.json"
    incomplete_orderbook = replace(
        _state("UP"),
        data_quality_flags=("incomplete_orderbook",),
    )

    write_hot_probability_inputs(
        out_path=out_path,
        states=(incomplete_orderbook,),
        generated_at=datetime.now(timezone.utc),
    )

    payload = read_hot_probability_inputs(out_path=out_path, limit=10, max_age_seconds=60)

    assert payload.skipped == 0
    assert [row.contract_id for row in payload.inputs] == ["btc-market:UP"]
    assert payload.inputs[0].sigma_valid is True


@pytest.mark.parametrize(
    ("invalid_case", "expected_sigma_tau", "expected_failure_reason"),
    [
        ("missing", None, "sigma_missing"),
        ("nan", None, "sigma_nonfinite"),
        ("zero", 0.0, "sigma_non_positive"),
        ("stale", 0.002, "stale_reference_source"),
    ],
)
def test_hot_probability_inputs_blocks_invalid_sigma_with_diagnostics(
    tmp_path: Path,
    invalid_case: str,
    expected_sigma_tau: float | None,
    expected_failure_reason: str,
) -> None:
    out_path = tmp_path / "inputs.json"
    base_state = _state("UP")
    if invalid_case == "missing":
        invalid_sigma_state = replace(base_state, sigma_tau=None)
    elif invalid_case == "nan":
        invalid_sigma_state = replace(base_state, sigma_tau=float("nan"))
    elif invalid_case == "zero":
        invalid_sigma_state = replace(base_state, sigma_tau=0.0)
    elif invalid_case == "stale":
        invalid_sigma_state = replace(
            base_state,
            volatility_regime="stale_reference_source",
        )
    else:
        raise AssertionError(f"unknown invalid sigma case: {invalid_case}")

    write_hot_probability_inputs(
        out_path=out_path,
        states=(invalid_sigma_state,),
        generated_at=datetime.now(timezone.utc),
    )

    raw_text = out_path.read_text()
    raw = json.loads(raw_text)
    row = raw["inputs"][0]

    assert "NaN" not in raw_text
    assert row["sigma_tau"] == expected_sigma_tau
    assert row["sigma_valid"] is False
    assert row["sigma_age_ms"] == 0
    if invalid_case == "stale":
        assert row["last_sigma_update_ts"] is None
    else:
        assert row["last_sigma_update_ts"] == "2026-05-31T20:03:00+00:00"
    assert row["short_vol"] == 0.01
    assert row["medium_vol"] == 0.012
    assert row["long_vol"] == 0.015
    assert row["volatility_floor_applied"] is False
    assert row["regime_multiplier_applied"] is False
    assert row["failure_reason"] == expected_failure_reason
    assert row["input_sample_count"] == 2
    assert row["probability_state"] == "BLOCKED_OR_STALE"
    assert row["offload_allowed"] is False
    assert "sigma_invalid" in row["block_reasons"]

    payload = read_hot_probability_inputs(out_path=out_path, limit=10, max_age_seconds=60)

    assert payload.skipped == 0
    assert payload.inputs[0].probability_state == "BLOCKED_OR_STALE"
    assert payload.inputs[0].sigma_valid is False
    assert payload.inputs[0].offload_allowed is False
    assert "sigma_invalid" in payload.inputs[0].block_reasons


def test_hot_probability_inputs_keeps_missing_volatility_as_blocked_diagnostic(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "inputs.json"
    missing_volatility = replace(
        _state("UP"),
        sigma_tau=None,
        short_realized_vol=float("nan"),
        medium_realized_vol=float("inf"),
        long_realized_vol=float("-inf"),
        volatility_regime="missing_reference_source",
        data_quality_flags=("missing_volatility",),
    )

    write_hot_probability_inputs(
        out_path=out_path,
        states=(missing_volatility,),
        generated_at=datetime.now(timezone.utc),
    )

    raw_text = out_path.read_text()
    raw = json.loads(raw_text)
    row = raw["inputs"][0]

    assert raw["skipped"] == 0
    assert "NaN" not in raw_text
    assert "Infinity" not in raw_text
    assert row["probability_state"] == "BLOCKED_OR_STALE"
    assert row["offload_allowed"] is False
    assert row["sigma_tau"] is None
    assert row["sigma_valid"] is False
    assert row["short_vol"] is None
    assert row["medium_vol"] is None
    assert row["long_vol"] is None
    assert row["failure_reason"] == "sigma_missing"
    assert "sigma_invalid" in row["block_reasons"]


def test_hot_probability_inputs_blocks_threshold_mutation_under_same_rule_hash(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "inputs.json"
    mutated = replace(_state("UP"), state_id="state-UP-mutated", threshold=103_951.0)

    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state("UP"), mutated),
        generated_at=datetime.now(timezone.utc),
    )

    raw = json.loads(out_path.read_text())
    row = raw["inputs"][1]

    assert raw["skipped"] == 0
    assert row["contract_id"] == "btc-market:UP"
    assert row["probability_state"] == "BLOCKED"
    assert "THRESHOLD_MUTATION_ERROR" in row["flags"]
    assert row["k_stable"] is False
    assert row["threshold_diagnostics"]["previous_K"] == 103_950.0
    assert row["threshold_diagnostics"]["new_K"] == 103_951.0
    assert row["threshold_diagnostics"]["rule_hash"] == "hash"
    assert row["threshold_diagnostics"]["reason_for_change"] == (
        "threshold_changed_without_rule_hash_change"
    )


def test_hot_probability_inputs_latches_original_threshold_within_batch(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "inputs.json"
    first_mutated = replace(_state("UP"), state_id="state-UP-mutated", threshold=103_951.0)
    second_mutated = replace(
        _state("UP"),
        state_id="state-UP-mutated-repeat",
        threshold=103_951.0,
    )

    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state("UP"), first_mutated, second_mutated),
        generated_at=datetime.now(timezone.utc),
    )

    raw = json.loads(out_path.read_text())
    first_bad = raw["inputs"][1]
    second_bad = raw["inputs"][2]

    assert first_bad["probability_state"] == "BLOCKED"
    assert second_bad["probability_state"] == "BLOCKED"
    assert "THRESHOLD_MUTATION_ERROR" in first_bad["flags"]
    assert "THRESHOLD_MUTATION_ERROR" in second_bad["flags"]
    assert first_bad["threshold_diagnostics"]["previous_K"] == 103_950.0
    assert second_bad["threshold_diagnostics"]["previous_K"] == 103_950.0
    assert second_bad["threshold_diagnostics"]["new_K"] == 103_951.0


def test_hot_probability_inputs_blocks_threshold_mutation_across_writes(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "inputs.json"
    mutated = replace(_state("UP"), state_id="state-UP-next", threshold=103_951.0)

    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state("UP"),),
        generated_at=datetime.now(timezone.utc),
    )
    write_hot_probability_inputs(
        out_path=out_path,
        states=(mutated,),
        generated_at=datetime.now(timezone.utc),
    )
    write_hot_probability_inputs(
        out_path=out_path,
        states=(replace(mutated, state_id="state-UP-next-repeat"),),
        generated_at=datetime.now(timezone.utc),
    )

    raw = json.loads(out_path.read_text())
    row = raw["inputs"][0]

    assert row["probability_state"] == "BLOCKED"
    assert "THRESHOLD_MUTATION_ERROR" in row["flags"]
    assert row["k_stable"] is False
    assert row["threshold_diagnostics"]["previous_K"] == 103_950.0
    assert row["threshold_diagnostics"]["new_K"] == 103_951.0
    assert row["threshold_diagnostics"]["reason_for_change"] == (
        "threshold_changed_without_rule_hash_change"
    )


def test_hot_probability_inputs_allows_threshold_change_when_rule_hash_changes(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "inputs.json"
    changed_rule_contract = replace(_contract("UP"), rule_hash="hash-v2")
    changed_rule_state = replace(
        _state("UP"),
        contract=changed_rule_contract,
        state_id="state-UP-rule-v2",
        threshold=103_951.0,
    )

    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state("UP"),),
        generated_at=datetime.now(timezone.utc),
    )
    write_hot_probability_inputs(
        out_path=out_path,
        states=(changed_rule_state,),
        generated_at=datetime.now(timezone.utc),
    )

    raw = json.loads(out_path.read_text())
    row = raw["inputs"][0]

    assert row["probability_state"] == "READY"
    assert row["flags"] == ["OK"]
    assert row["k_stable"] is True
    assert row["threshold_diagnostics"]["previous_K"] == 103_950.0
    assert row["threshold_diagnostics"]["new_K"] == 103_951.0
    assert row["threshold_diagnostics"]["rule_hash"] == "hash-v2"
    assert row["threshold_diagnostics"]["reason_for_change"] == "rule_hash_changed"


def test_read_hot_probability_inputs_accepts_blocked_or_stale_state(tmp_path: Path) -> None:
    out_path = tmp_path / "inputs.json"
    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state("UP"),),
        generated_at=datetime.now(timezone.utc),
    )
    raw = json.loads(out_path.read_text())
    raw["inputs"][0]["probability_state"] = "BLOCKED_OR_STALE"
    out_path.write_text(json.dumps(raw))

    payload = read_hot_probability_inputs(out_path=out_path, limit=10, max_age_seconds=60)

    assert payload.inputs[0].probability_state == "BLOCKED_OR_STALE"


def test_read_hot_probability_inputs_enforces_limit(tmp_path: Path) -> None:
    out_path = tmp_path / "inputs.json"

    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state("UP"), _state("DOWN")),
        generated_at=datetime.now(timezone.utc),
    )

    payload = read_hot_probability_inputs(out_path=out_path, limit=1, max_age_seconds=60)

    assert len(payload.inputs) == 1
    with pytest.raises(ValueError, match="limit must be positive"):
        read_hot_probability_inputs(out_path=out_path, limit=0, max_age_seconds=60)


def test_read_hot_probability_inputs_rejects_stale_snapshot(tmp_path: Path) -> None:
    out_path = tmp_path / "inputs.json"

    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state(),),
        generated_at=datetime.now(timezone.utc) - timedelta(seconds=120),
    )

    with pytest.raises(ValueError, match="stale"):
        read_hot_probability_inputs(out_path=out_path, limit=10, max_age_seconds=1)


def test_read_hot_probability_inputs_rejects_future_snapshot(tmp_path: Path) -> None:
    out_path = tmp_path / "inputs.json"

    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state(),),
        generated_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )

    with pytest.raises(ValueError, match="future"):
        read_hot_probability_inputs(out_path=out_path, limit=10, max_age_seconds=60)


def test_read_hot_probability_inputs_rejects_wrong_schema_or_malformed_row(tmp_path: Path) -> None:
    out_path = tmp_path / "inputs.json"
    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state(),),
        generated_at=datetime.now(timezone.utc),
    )
    raw = json.loads(out_path.read_text())
    wrong_schema = {**raw, "schema_version": "wrong"}
    out_path.write_text(json.dumps(wrong_schema))

    with pytest.raises(ValueError, match="schema"):
        read_hot_probability_inputs(out_path=out_path, limit=10, max_age_seconds=60)

    malformed = dict(raw)
    malformed["inputs"] = [dict(cast(dict[str, Any], raw["inputs"][0]), probability_input={})]
    out_path.write_text(json.dumps(malformed))

    with pytest.raises(ValueError, match="invalid input row"):
        read_hot_probability_inputs(out_path=out_path, limit=10, max_age_seconds=60)


def test_read_hot_probability_inputs_validates_malformed_rows_after_limit(tmp_path: Path) -> None:
    out_path = tmp_path / "inputs.json"
    write_hot_probability_inputs(
        out_path=out_path,
        states=(_state("UP"), _state("DOWN")),
        generated_at=datetime.now(timezone.utc),
    )
    raw = json.loads(out_path.read_text())
    raw["inputs"][1]["probability_input"] = {}
    out_path.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="invalid input row"):
        read_hot_probability_inputs(out_path=out_path, limit=1, max_age_seconds=60)


def test_read_hot_probability_inputs_rejects_nonfinite_json_constants(tmp_path: Path) -> None:
    out_path = tmp_path / "inputs.json"
    out_path.write_text(
        (
            '{"generated_at":"2026-05-31T20:03:30+00:00",'
            '"inputs":[],"schema_version":"polymarket-hot-probability-inputs-v1",'
            '"skipped":NaN}'
        )
    )

    with pytest.raises(ValueError, match="nonfinite JSON constant"):
        read_hot_probability_inputs(
            out_path=out_path,
            limit=10,
            max_age_seconds=60 * 60 * 24 * 365,
        )


def test_write_hot_probability_inputs_rejects_non_datetime_generated_at(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="generated_at must be a datetime"):
        write_hot_probability_inputs(
            out_path=tmp_path / "inputs.json",
            states=(_state(),),
            generated_at=cast(Any, "2026-05-31T20:03:30+00:00"),
        )


def test_importing_hot_inputs_does_not_import_duckdb() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import polymarket_engine.probability.hot_inputs; "
                "raise SystemExit(int('duckdb' in sys.modules))"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
