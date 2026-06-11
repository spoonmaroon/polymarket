from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState
from polymarket_engine.probability.generator_fragments import GeneratorFragment
from polymarket_engine.probability.generator_fragments import write_probability_fragments
from polymarket_engine.probability.hot_inputs import write_hot_probability_inputs
from polymarket_engine.probability.runtime import (
    ProbabilityRuntimeCache,
    _compute_and_persist_rows,
    build_probability_payload,
)
from polymarket_engine.probability.runtime_inputs import ProbabilityRuntimeInput
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


def test_compute_and_persist_rows_batches_valid_probability_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ProbabilityInput, ProbabilityOutput], ...]] = []
    runtime_inputs = (
        _runtime_input(_decision_state()),
        _runtime_input(_second_decision_state()),
    )

    class _Store:
        def insert_probability_outputs(
            self,
            rows: list[tuple[str, ProbabilityInput, ProbabilityOutput]],
        ) -> None:
            calls.append(tuple(rows))

        def insert_probability_output(self, **_: object) -> None:
            raise AssertionError("runtime should use the batch persistence path")

    def fake_ensemble(
        probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: tuple[tuple[float, ...], ...] | None = None,
    ) -> ProbabilityOutput:
        del history_fragments
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.58,
            p_no_touch=0.81,
            z_path=probability_input.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "backend": "ensemble",
                "generator_version": "four-generator-ensemble-v1",
                "path_count": path_count,
                "steps": steps,
                "effective_weights": {"empirical_conditional": 0.4},
                "generator_summary": {
                    "empirical_conditional": {
                        "p_finish": 0.58,
                        "p_no_touch": 0.81,
                        "weight": 0.4,
                        "sparse": False,
                    }
                },
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime.run_four_generator_ensemble",
        fake_ensemble,
    )

    rows, errors = _compute_and_persist_rows(store=_Store(), inputs=runtime_inputs)  # type: ignore[arg-type]

    assert errors == []
    assert len(rows) == 2
    assert rows[0]["model_version"] == "ensemble-v1"
    assert rows[0]["backend"] == "ensemble"
    assert rows[0]["generator_version"] == "four-generator-ensemble-v1"
    assert rows[0]["generator_summary"]["empirical_conditional"]["weight"] == 0.4
    assert len(calls) == 1
    assert [row[1].state_id for row in calls[0]] == ["state-btc-up", "state-eth-up"]


def test_compute_and_persist_rows_batches_valid_outputs_after_compute_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ProbabilityInput, ProbabilityOutput], ...]] = []
    runtime_inputs = (
        _runtime_input(_decision_state()),
        _runtime_input(_second_decision_state()),
    )

    class _Store:
        def insert_probability_outputs(
            self,
            rows: list[tuple[str, ProbabilityInput, ProbabilityOutput]],
        ) -> None:
            calls.append(tuple(rows))

    def fake_ensemble(
        probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: tuple[tuple[float, ...], ...] | None = None,
    ) -> ProbabilityOutput:
        del history_fragments
        if probability_input.state_id == "state-btc-up":
            raise RuntimeError("fixture compute failure")
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.58,
            p_no_touch=0.81,
            z_path=probability_input.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "backend": "ensemble",
                "generator_version": "four-generator-ensemble-v1",
                "path_count": path_count,
                "steps": steps,
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime.run_four_generator_ensemble",
        fake_ensemble,
    )

    rows, errors = _compute_and_persist_rows(store=_Store(), inputs=runtime_inputs)  # type: ignore[arg-type]

    assert len(rows) == 1
    assert rows[0]["contract_id"] == "eth-market:UP"
    assert errors == ["state-btc-up: RuntimeError: fixture compute failure"]
    assert len(calls) == 1
    assert [row[1].state_id for row in calls[0]] == ["state-eth-up"]


def test_build_probability_payload_uses_hot_inputs_without_duckdb_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hot_inputs_path = tmp_path / "live" / "probability_inputs.json"
    write_hot_probability_inputs(
        out_path=hot_inputs_path,
        states=(_decision_state(),),
        generated_at=datetime.now(timezone.utc),
    )

    def fail_duckdb_polling(*_: object, **__: object) -> NoReturn:
        raise AssertionError("hot probability inputs should avoid DuckDB input polling")

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime.latest_probability_inputs",
        fail_duckdb_polling,
    )

    payload = build_probability_payload(
        duckdb_path=tmp_path / "missing.duckdb",
        limit=4,
        probability_inputs_path=hot_inputs_path,
    )

    assert payload["ok"] is True
    assert payload["state"] == "OK"
    assert payload["errors"] == []
    assert payload["rows"]
    row = payload["rows"][0]
    assert row["contract"] == "BTC 5m UP"
    assert row["model_version"] == "ensemble-v1"
    assert row["backend"] == "ensemble"
    assert row["generator_version"] == "four-generator-ensemble-v1"
    assert row["generator_summary"]
    assert row["effective_weights"]


def test_build_probability_payload_does_not_compute_blocked_hot_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hot_inputs_path = tmp_path / "live" / "probability_inputs.json"
    blocked_state = replace(_decision_state(), sigma_tau=None)
    write_hot_probability_inputs(
        out_path=hot_inputs_path,
        states=(blocked_state,),
        generated_at=datetime.now(timezone.utc),
    )

    def fail_ensemble(*_: object, **__: object) -> NoReturn:
        raise AssertionError("blocked hot inputs must not run ensemble")

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime.run_four_generator_ensemble",
        fail_ensemble,
    )

    payload = build_probability_payload(
        duckdb_path=tmp_path / "missing.duckdb",
        limit=4,
        probability_inputs_path=hot_inputs_path,
    )

    assert payload["ok"] is True
    assert payload["source"] == "hot_inputs"
    assert payload["model_version"] is None
    assert payload["errors"] == []
    row = payload["rows"][0]
    assert row["contract_id"] == "btc-market:UP"
    assert row["probability_state"] == "BLOCKED_OR_STALE"
    assert row["sigma_valid"] is False
    assert row["offload_allowed"] is False
    assert "sigma_invalid" in row["block_reasons"]
    assert row["model_version"] is None
    assert row["model_version"] != "ensemble-v1"
    assert row["threshold"] == 103_950.0
    assert row["threshold_price"] == 103_950.0
    assert row["settlement_price"] == 104_000.0
    assert row["executable_price"] == 0.64
    assert row["source_age_ms"] == 1000
    assert row["book_age_ms"] == 1000


def test_compute_and_persist_rows_does_not_persist_blocked_inputs() -> None:
    calls: list[tuple[tuple[str, ProbabilityInput, ProbabilityOutput], ...]] = []
    runtime_inputs = (
        replace(
            _runtime_input(_decision_state()),
            probability_state="BLOCKED_OR_STALE",
            sigma_valid=False,
            offload_allowed=False,
            block_reasons=("sigma_invalid",),
        ),
    )

    class _Store:
        def insert_probability_outputs(
            self,
            rows: list[tuple[str, ProbabilityInput, ProbabilityOutput]],
        ) -> None:
            calls.append(tuple(rows))

    rows, errors = _compute_and_persist_rows(store=_Store(), inputs=runtime_inputs)  # type: ignore[arg-type]

    assert errors == []
    assert calls == []
    assert len(rows) == 1
    assert rows[0]["probability_state"] == "BLOCKED_OR_STALE"
    assert rows[0]["sigma_valid"] is False
    assert rows[0]["offload_allowed"] is False
    assert "sigma_invalid" in rows[0]["block_reasons"]
    assert rows[0]["model_version"] is None


def test_hot_input_fallback_uses_probability_fragments_prior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hot_inputs_path = tmp_path / "live" / "probability_inputs.json"
    probability_fragments_path = tmp_path / "live" / "probability_fragments.json"
    decision_state = _decision_state()
    write_hot_probability_inputs(
        out_path=hot_inputs_path,
        states=(decision_state,),
        generated_at=datetime.now(timezone.utc),
    )
    prior_one = (70_000.0, 70_020.0, 70_060.0)
    prior_two = (70_000.0, 70_040.0, 70_090.0)
    future = (70_000.0, 69_980.0, 69_950.0)
    write_probability_fragments(
        out_path=probability_fragments_path,
        generated_at=datetime.now(timezone.utc),
        fragments=(
            GeneratorFragment(
                fragment_id="btc-prior-one",
                asset="BTC",
                asof_ts=decision_state.asof_ts,
                prices=prior_one,
                horizon_seconds=300,
                z_path_bucket="near",
                quality_bucket="OK",
            ),
            GeneratorFragment(
                fragment_id="btc-prior-two",
                asset="BTC",
                asof_ts=decision_state.asof_ts,
                prices=prior_two,
                horizon_seconds=300,
                z_path_bucket="near",
                quality_bucket="OK",
            ),
            GeneratorFragment(
                fragment_id="btc-future",
                asset="BTC",
                asof_ts=datetime.now(timezone.utc),
                prices=future,
                horizon_seconds=300,
                z_path_bucket="near",
                quality_bucket="OK",
            ),
        ),
    )
    seen_history: list[tuple[tuple[float, ...], ...] | None] = []

    def fake_ensemble(
        probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: tuple[tuple[float, ...], ...] | None = None,
    ) -> ProbabilityOutput:
        seen_history.append(history_fragments)
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.58,
            p_no_touch=0.81,
            z_path=probability_input.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "backend": "ensemble",
                "generator_version": "four-generator-ensemble-v1",
                "path_count": path_count,
                "steps": steps,
                "effective_weights": {"empirical_conditional": 0.4},
                "generator_summary": {},
                "generator_runs": [],
                "effective_generator_values": {},
                "u_gen": 0.03,
                "mc_dispersion": 0.08,
                "uncertainty_buffer": 0.055,
                "path_diagnosis": "OK",
                "sparse_scope": False,
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime.run_four_generator_ensemble",
        fake_ensemble,
    )

    payload = build_probability_payload(
        duckdb_path=tmp_path / "missing.duckdb",
        limit=4,
        probability_inputs_path=hot_inputs_path,
        probability_fragments_path=probability_fragments_path,
    )

    assert seen_history == [(prior_one, prior_two)]
    row = payload["rows"][0]
    assert row["prior_fragment_count"] == 2
    assert row["prior_fragment_reason"] == "exact"
    assert row["prior_fragment_sparse"] is False
    assert row["prior_fragment_ids"] == ["btc-prior-one", "btc-prior-two"]


def test_probability_runtime_cache_keys_hot_inputs_by_limit(tmp_path: Path) -> None:
    hot_inputs_path = tmp_path / "live" / "probability_inputs.json"
    write_hot_probability_inputs(
        out_path=hot_inputs_path,
        states=(_decision_state(), _second_decision_state()),
        generated_at=datetime.now(timezone.utc),
    )
    cache = ProbabilityRuntimeCache(min_interval_seconds=60.0)

    first = cache.payload(
        duckdb_path=tmp_path / "missing.duckdb",
        limit=1,
        probability_inputs_path=hot_inputs_path,
    )
    second = cache.payload(
        duckdb_path=tmp_path / "missing.duckdb",
        limit=8,
        probability_inputs_path=hot_inputs_path,
    )

    assert len(first["rows"]) == 1
    assert first["cached"] is False
    assert len(second["rows"]) == 2
    assert second["cached"] is False


def test_probability_runtime_cache_invalidates_when_hot_inputs_change(tmp_path: Path) -> None:
    hot_inputs_path = tmp_path / "live" / "probability_inputs.json"
    cache = ProbabilityRuntimeCache(min_interval_seconds=60.0)
    write_hot_probability_inputs(
        out_path=hot_inputs_path,
        states=(_decision_state(),),
        generated_at=datetime.now(timezone.utc),
    )
    os.utime(hot_inputs_path, ns=(1_000_000_000, 1_000_000_000))

    first = cache.payload(
        duckdb_path=tmp_path / "missing.duckdb",
        limit=4,
        probability_inputs_path=hot_inputs_path,
    )
    write_hot_probability_inputs(
        out_path=hot_inputs_path,
        states=(_second_decision_state(),),
        generated_at=datetime.now(timezone.utc),
    )
    os.utime(hot_inputs_path, ns=(2_000_000_000, 2_000_000_000))
    second = cache.payload(
        duckdb_path=tmp_path / "missing.duckdb",
        limit=4,
        probability_inputs_path=hot_inputs_path,
    )

    assert first["rows"][0]["contract_id"] == "btc-market:UP"
    assert second["cached"] is False
    assert second["rows"][0]["contract_id"] == "eth-market:UP"


def test_build_probability_payload_reports_invalid_hot_inputs_on_fallback(
    tmp_path: Path,
) -> None:
    hot_inputs_path = tmp_path / "live" / "probability_inputs.json"
    hot_inputs_path.parent.mkdir()
    hot_inputs_path.write_text("{not-json", encoding="utf-8")

    payload = build_probability_payload(
        duckdb_path=tmp_path / "missing.duckdb",
        limit=4,
        probability_inputs_path=hot_inputs_path,
    )

    assert payload["state"] == "MISSING"
    assert "malformed hot probability inputs JSON" in payload["hot_input_error"]
    assert payload["hot_input_error"] in payload["warnings"]


def _contract() -> ContractSpec:
    return ContractSpec(
        contract_id="btc-market:UP",
        venue="polymarket",
        market_id="btc-market",
        condition_id="0xbtc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        side="UP",
        token_id="up-token",
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


def _decision_state() -> DecisionState:
    contract = _contract()
    asof_ts = datetime.now(timezone.utc)
    return DecisionState(
        state_id="state-btc-up",
        asof_ts=asof_ts,
        contract=contract,
        threshold=103_950.0,
        threshold_source_key="polymarket_rtds_chainlink",
        threshold_event_ts=contract.start_ts,
        threshold_observed_ts=contract.start_ts,
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


def _second_decision_state() -> DecisionState:
    state = _decision_state()
    return replace(
        state,
        state_id="state-eth-up",
        contract=replace(
            state.contract,
            contract_id="eth-market:UP",
            market_id="eth-market",
            condition_id="0xeth",
            slug="eth-updown-5m-1780264500",
            asset="ETH",
            token_id="eth-up-token",
            settlement_symbol="ETH/USD",
        ),
        threshold=3_500.0,
        settlement_price=3_510.0,
        proxy_prices={"coinbase_advanced_ws": 3_511.0},
    )


def _runtime_input(state: DecisionState) -> ProbabilityRuntimeInput:
    return ProbabilityRuntimeInput(
        probability_input=ProbabilityInput.from_decision_state(state),
        contract_id=state.contract.contract_id,
        contract=f"{state.contract.asset} 5m {state.contract.side}",
        start_ts=state.contract.start_ts,
        expiry_ts=state.contract.expiry_ts,
        flags=("OK",),
    )
