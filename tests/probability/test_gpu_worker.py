import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_cuda_probability_worker_cycle_writes_status_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability import gpu_worker

    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "probabilities.json"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)

    calls: list[tuple[int, int, int]] = []

    def fake_cuda(
        probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
    ) -> ProbabilityOutput:
        calls.append((path_count, steps, seed))
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.61,
            p_no_touch=0.58,
            z_path=probability_input.z_path,
            model_version="cuda-lognormal-chainlink-sigma-v1",
            seed=seed,
            diagnostics={
                "path_count": path_count,
                "steps": steps,
                "model": "cuda_lognormal_chainlink_sigma",
                "simulation_preview": {"path_count": path_count, "sampled_paths": []},
            },
        )

    monkeypatch.setattr(gpu_worker, "run_cuda_monte_carlo", fake_cuda)

    result = gpu_worker.run_cuda_probability_worker_cycle(
        duckdb_path=db_path,
        probability_status_path=status_path,
        limit=24,
        valid_seconds=30,
    )

    assert result["ok"] is True
    assert result["schema_version"] == "polymarket-probability-runtime-v1"
    assert result["rows_written"] == 1
    assert result["rows"][0]["p_finish"] == pytest.approx(0.61)
    assert result["rows"][0]["cache_status"] == "REFRESH"
    assert result["rows"][0]["generator_version"] == "cuda-lognormal-chainlink-sigma-v1"
    assert result["rows"][0]["simulation_preview"]["path_count"] == calls[0][0]
    assert calls[0][0] == 20_000

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "polymarket-probability-runtime-v1"
    assert payload["rows"][0]["model_version"] == "cached-grid-v1"
    assert payload["rows"][0]["path_count"] == 20_000


def test_cuda_probability_worker_cycle_reads_input_snapshot_without_duckdb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability import gpu_worker

    db_path = tmp_path / "locked.duckdb"
    status_path = tmp_path / "live" / "probabilities.json"
    inputs_path = tmp_path / "live" / "probability_inputs.json"
    state = _decision_state()
    probability_input = ProbabilityInput.from_decision_state(state)
    inputs_path.parent.mkdir(parents=True)
    inputs_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-probability-inputs-v1",
                "ok": True,
                "state": "OK",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "rows": [
                    {
                        "contract": "BTC 5m UP",
                        "contract_id": state.contract.contract_id,
                        "market_slug": state.contract.slug,
                        "start_ts": state.contract.start_ts.isoformat(),
                        "expiry_ts": state.contract.expiry_ts.isoformat(),
                        "volatility_regime": state.volatility_regime,
                        "flags": ["OK"],
                        "probability_input": probability_input.to_json_dict(),
                    }
                ],
                "skipped": 0,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )

    def duckdb_inputs_unavailable(**_: object) -> object:
        raise AssertionError("worker should read probability_inputs.json")

    def fake_cuda(
        probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
    ) -> ProbabilityOutput:
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.62,
            p_no_touch=0.51,
            z_path=probability_input.z_path,
            model_version="cuda-lognormal-chainlink-sigma-v1",
            seed=seed,
            diagnostics={
                "path_count": path_count,
                "steps": steps,
                "simulation_preview": {"path_count": path_count, "sampled_paths": []},
            },
        )

    monkeypatch.setattr(gpu_worker, "latest_probability_inputs", duckdb_inputs_unavailable)
    monkeypatch.setattr(gpu_worker, "run_cuda_monte_carlo", fake_cuda)

    result = gpu_worker.run_cuda_probability_worker_cycle(
        duckdb_path=db_path,
        probability_status_path=status_path,
        probability_inputs_path=inputs_path,
    )

    assert result["ok"] is True
    assert result["rows_seen"] == 1
    assert result["rows_written"] == 1
    assert result["rows"][0]["generator_version"] == "cuda-lognormal-chainlink-sigma-v1"
    assert result["rows"][0]["path_count"] == 20_000


def test_cuda_probability_worker_cycle_clears_active_rows_when_inputs_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability import gpu_worker

    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "probabilities.json"
    previous_row = {"contract_id": "btc-updown-5m:UP", "model_version": "cached-grid-v1"}
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"rows": [previous_row]}), encoding="utf-8")

    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    def locked_inputs(**_: object) -> object:
        raise duckdb.IOException("conflicting lock")

    monkeypatch.setattr(gpu_worker, "latest_probability_inputs", locked_inputs)

    result = gpu_worker.run_cuda_probability_worker_cycle(
        duckdb_path=db_path,
        probability_status_path=status_path,
    )

    assert result["ok"] is False
    assert result["rows"] == []
    assert result["last_good_rows"] == [previous_row]
    assert "conflicting lock" in result["error"]
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["rows"] == []
    assert payload["last_good_rows"] == [previous_row]


def test_cuda_probability_worker_loop_clears_active_rows_on_duckdb_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability import gpu_worker

    class StopLoop(Exception):
        pass

    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "probabilities.json"
    previous_row = {"contract_id": "eth-updown-5m:DOWN", "model_version": "cached-grid-v1"}
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"rows": [previous_row]}), encoding="utf-8")

    def locked_cycle(**_: object) -> object:
        raise duckdb.IOException("conflicting lock")

    def stop_after_sleep(seconds: float) -> None:
        assert seconds == pytest.approx(0.25)
        raise StopLoop

    monkeypatch.setattr(gpu_worker, "run_cuda_probability_worker_cycle", locked_cycle)
    monkeypatch.setattr(gpu_worker.time, "sleep", stop_after_sleep)

    with pytest.raises(StopLoop):
        gpu_worker.run_cuda_probability_worker_loop(
            duckdb_path=db_path,
            probability_status_path=status_path,
            interval_seconds=0.25,
        )

    result = json.loads(status_path.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert result["rows"] == []
    assert result["last_good_rows"] == [previous_row]
    assert "probability worker duckdb unavailable" in result["error"]


def test_cuda_probability_worker_cycle_clears_active_rows_when_all_cuda_runs_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability import gpu_worker

    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "probabilities.json"
    previous_row = {"contract_id": "btc-updown-5m:UP", "model_version": "cached-grid-v1"}
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"rows": [previous_row]}), encoding="utf-8")

    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)

    def failing_cuda(*_: object, **__: object) -> object:
        raise RuntimeError("cuda device lost")

    monkeypatch.setattr(gpu_worker, "run_cuda_monte_carlo", failing_cuda)

    result = gpu_worker.run_cuda_probability_worker_cycle(
        duckdb_path=db_path,
        probability_status_path=status_path,
    )

    assert result["ok"] is False
    assert result["rows"] == []
    assert result["last_good_rows"] == [previous_row]
    assert result["rows_seen"] == 1
    assert result["rows_written"] == 0
    assert "cuda device lost" in result["error"]


def test_cuda_probability_worker_reads_only_non_expired_probability_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability import gpu_worker

    captured: list[bool | None] = []

    def fake_inputs(**kwargs: object) -> tuple[tuple[object, ...], int]:
        captured.append(kwargs.get("active_only"))
        return (), 0

    monkeypatch.setattr(gpu_worker, "latest_probability_inputs", fake_inputs)

    result = gpu_worker.run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "state.duckdb",
        probability_status_path=tmp_path / "live" / "probabilities.json",
    )

    assert result["ok"] is True
    assert captured == [True]


def test_cuda_probability_worker_status_write_uses_durable_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability import gpu_worker

    status_path = tmp_path / "live" / "probabilities.json"
    calls: list[tuple[Path, Path]] = []

    def fake_durable_replace(tmp: Path, final: Path) -> None:
        calls.append((tmp, final))
        tmp.replace(final)

    monkeypatch.setattr(gpu_worker, "durable_replace", fake_durable_replace)

    gpu_worker._write_status(
        status_path,
        {
            "schema_version": "polymarket-probability-runtime-v1",
            "rows": [],
        },
    )

    assert calls == [(status_path.with_suffix(".json.tmp"), status_path)]
    assert json.loads(status_path.read_text(encoding="utf-8"))["rows"] == []


def _contract() -> ContractSpec:
    start_ts = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=2)
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
    asof_ts = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=10)
    return DecisionState(
        state_id="state-btc-up",
        asof_ts=asof_ts,
        contract=contract,
        threshold=100.0,
        threshold_source_key="polymarket_rtds_chainlink",
        threshold_event_ts=contract.start_ts,
        threshold_observed_ts=contract.start_ts + timedelta(seconds=1),
        seconds_left=228.0,
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
