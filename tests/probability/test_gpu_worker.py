from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarket_engine.probability.gpu_worker import ProbabilityWorkerBudget
from polymarket_engine.probability.gpu_worker import PROBABILITY_INPUTS_SCHEMA_VERSION
from polymarket_engine.probability.gpu_worker import _budget_diagnostics
from polymarket_engine.probability.gpu_worker import _clamp_path_count
from polymarket_engine.probability.gpu_worker import _event_payload_from_row
from polymarket_engine.probability.gpu_worker import _latest_probability_inputs_from_snapshot
from polymarket_engine.probability.gpu_worker import _path_budget_per_input
from polymarket_engine.probability.gpu_worker import run_cuda_probability_worker_cycle
from polymarket_engine.probability.gpu_worker import run_cuda_probability_worker_loop
from polymarket_engine.probability.hot_inputs import HOT_PROBABILITY_INPUTS_SCHEMA_VERSION
from polymarket_engine.probability.generator_fragments import GeneratorFragment
from polymarket_engine.probability.generator_fragments import write_probability_fragments
from polymarket_engine.probability.runtime_inputs import ProbabilityRuntimeInput
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


def _write_ready_recovery_status(
    path: Path,
    *,
    consecutive_healthy_cycles: int = 3,
) -> None:
    path.write_text(
        json.dumps(
            {
                "runtime_phase": "READY",
                "ready": True,
                "reasons": [],
                "uptime_seconds": 300.0,
                "consecutive_healthy_cycles": consecutive_healthy_cycles,
            }
        ),
        encoding="utf-8",
    )


def _runtime_input_snapshot_row(
    *,
    asof_ts: datetime,
    state_id: str,
    asset: str,
    side: str,
    source_age_ms: int = 100,
    book_age_ms: int = 100,
    seconds_left: float = 300.0,
    expiry_offset_seconds: float = 300.0,
    probability_state: str = "READY",
    offload_allowed: bool = True,
    block_reasons: list[str] | None = None,
) -> dict[str, object]:
    probability_input = ProbabilityInput(
        state_id=state_id,
        asof_ts=asof_ts,
        asset=asset,
        side=side,
        comparison_operator=">=" if side == "UP" else "<",
        seconds_left=seconds_left,
        settlement_price=70_100.0 if asset == "BTC" else 3_600.0,
        threshold=70_000.0 if asset == "BTC" else 3_580.0,
        sigma_tau=0.012,
        executable_price=0.52 if side == "UP" else 0.48,
        source_age_ms=source_age_ms,
        book_age_ms=book_age_ms,
        z_path=0.12,
    )
    return {
        "contract": f"{asset} 5m {side}",
        "contract_id": f"{asset.lower()}-{side.lower()}",
        "market_slug": f"{asset.lower()}-updown-5m",
        "start_ts": asof_ts.isoformat(),
        "expiry_ts": (asof_ts + timedelta(seconds=expiry_offset_seconds)).isoformat(),
        "flags": ["OK"] if probability_state == "READY" else ["BLOCKED"],
        "probability_state": probability_state,
        "offload_allowed": offload_allowed,
        "block_reasons": block_reasons or [],
        "probability_input": probability_input.to_json_dict(),
        "volatility_regime": "normal",
    }


def test_event_payload_includes_simulation_preview_for_mc_rows() -> None:
    asof_ts = datetime(2026, 6, 6, 16, 0, tzinfo=UTC)
    runtime_input = ProbabilityRuntimeInput(
        probability_input=ProbabilityInput(
            state_id="state-btc-up",
            asof_ts=asof_ts,
            asset="BTC",
            side="UP",
            comparison_operator=">=",
            seconds_left=240.0,
            settlement_price=70_100.0,
            threshold=70_000.0,
            sigma_tau=0.012,
            executable_price=0.54,
            source_age_ms=120,
            book_age_ms=80,
            z_path=0.12,
        ),
        contract_id="btc-up",
        contract="BTC 5m UP",
        start_ts=asof_ts,
        expiry_ts=asof_ts + timedelta(minutes=5),
        flags=("OK",),
        market_slug="btc-updown-5m-1780752000",
    )
    preview = {
        "sampled_paths": [
            {
                "index": 0,
                "terminal_win": True,
                "no_touch_win": True,
                "points": [70_100.0, 70_120.0],
            }
        ]
    }

    payload = _event_payload_from_row(
        runtime_input=runtime_input,
        row={
            "probability_kind": "MC",
            "backend": "cuda",
            "p_finish": 0.61,
            "p_no_touch": 0.57,
            "z_path": 0.12,
            "sigma_tau": 0.012,
            "wave_phase": "none",
            "wave_score": 0.0,
            "simulation_preview": preview,
        },
        generated_at=asof_ts,
        output_id="output-btc-up",
    )

    assert payload["simulation_preview"] == preview


def test_worker_budget_caps_total_generator_paths_for_ensemble() -> None:
    budget = ProbabilityWorkerBudget(max_total_paths=320_000, worker_mode="ensemble")

    assert _path_budget_per_input(input_count=4, budget=budget) == 20_000
    assert _clamp_path_count(80_000, path_budget_per_input=20_000) == (
        20_000,
        True,
    )
    assert _clamp_path_count(10_000, path_budget_per_input=20_000) == (
        10_000,
        False,
    )


def test_worker_budget_keeps_single_generator_modes_divided_by_inputs_only() -> None:
    budget = ProbabilityWorkerBudget(max_total_paths=320_000, worker_mode="cuda")

    assert _path_budget_per_input(input_count=4, budget=budget) == 80_000


def test_worker_budget_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="max_total_paths"):
        ProbabilityWorkerBudget(max_total_paths=0)


def test_worker_budget_includes_soft_cpu_limits() -> None:
    budget = ProbabilityWorkerBudget(
        cpu_target_percent=15.0,
        cpu_soft_max_percent=20.0,
        min_total_paths=4_000,
        max_total_paths=40_000,
    )

    assert budget.cpu_target_percent == 15.0
    assert budget.cpu_soft_max_percent == 20.0
    assert budget.min_total_paths == 4_000


def test_worker_publishes_nowcast_rows_for_new_contracts_before_mc_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    offload_status_path = tmp_path / "offload_status.json"
    offload_status_path = tmp_path / "offload_status.json"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))

    def input_row(state_id: str, side: str, start_offset_minutes: int) -> dict[str, object]:
        probability_input = ProbabilityInput(
            state_id=state_id,
            asof_ts=asof_ts,
            asset="BTC",
            side=side,
            comparison_operator=">=" if side == "UP" else "<",
            seconds_left=300.0 + start_offset_minutes * 60.0,
            settlement_price=70_100.0,
            threshold=70_000.0,
            sigma_tau=0.012,
            executable_price=0.52 if side == "UP" else 0.48,
            source_age_ms=100,
            book_age_ms=100,
            z_path=0.12,
        )
        return {
            "contract": f"BTC 5m {side}",
            "contract_id": f"btc-{start_offset_minutes}-{side.lower()}",
            "market_slug": f"btc-updown-5m-{start_offset_minutes}",
            "start_ts": (asof_ts + timedelta(minutes=start_offset_minutes)).isoformat(),
            "expiry_ts": (asof_ts + timedelta(minutes=start_offset_minutes + 5)).isoformat(),
            "flags": ["OK"],
            "probability_input": probability_input.to_json_dict(),
            "volatility_regime": "normal",
        }

    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    input_row("state-current-up", "UP", 0),
                    input_row("state-current-down", "DOWN", 0),
                    input_row("state-next-up", "UP", 5),
                    input_row("state-next-down", "DOWN", 5),
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fail_mc(*_: object, **__: object) -> ProbabilityOutput:
        raise RuntimeError("mc intentionally unavailable")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fail_mc,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        offload_status_path=offload_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    assert payload["state"] == "NOWCAST"
    assert len(payload["rows"]) == 4
    assert {row["probability_kind"] for row in payload["rows"]} == {"NOWCAST"}
    assert {row["market_slug"] for row in payload["rows"]} == {
        "btc-updown-5m-0",
        "btc-updown-5m-5",
    }


def test_worker_runs_mc_for_fresh_input_when_sibling_source_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    asof_ts = now - timedelta(seconds=4)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    offload_status_path = tmp_path / "offload_status.json"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": now.isoformat(),
                "rows": [
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-btc-stale",
                        asset="BTC",
                        side="UP",
                        source_age_ms=2500,
                    ),
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-eth-fresh",
                        asset="ETH",
                        side="UP",
                        source_age_ms=100,
                    ),
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fake_ensemble(
        probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: object | None,
    ) -> ProbabilityOutput:
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.62,
            p_no_touch=0.58,
            z_path=probability_input.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "path_count": path_count,
                "paths_per_generator": path_count,
                "generator_count": 4,
                "simulation_preview": {
                    "sampled_paths": [
                        {
                            "index": 0,
                            "terminal_win": True,
                            "no_touch_win": True,
                            "points": [
                                probability_input.settlement_price,
                                probability_input.threshold,
                            ],
                        }
                    ],
                    "start_price": probability_input.settlement_price,
                    "threshold": probability_input.threshold,
                    "steps": steps,
                    "terminal_win_count": 1,
                },
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fake_ensemble,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        offload_status_path=offload_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    rows_by_state = {row["state_id"]: row for row in payload["rows"]}
    assert rows_by_state["state-eth-fresh"]["probability_kind"] == "MC"
    assert rows_by_state["state-eth-fresh"]["simulation_preview"]["sampled_paths"]
    assert rows_by_state["state-btc-stale"]["probability_kind"] == "NOWCAST"
    assert rows_by_state["state-btc-stale"]["block_reasons"] == ["price_stale"]
    assert payload["offload"]["offload_allowed"] is True
    assert payload["offload"]["mc_eligible_input_count"] == 1
    assert payload["offload"]["blocked_input_count"] == 1
    persisted_offload = json.loads(offload_status_path.read_text(encoding="utf-8"))
    assert persisted_offload["mc_eligible_input_count"] == 1
    assert persisted_offload["blocked_input_count"] == 1


def test_worker_allows_mc_for_probability_input_lag_within_live_cadence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    asof_ts = now - timedelta(seconds=16)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    offload_status_path = tmp_path / "offload_status.json"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": now.isoformat(),
                "rows": [
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-btc-lagged",
                        asset="BTC",
                        side="UP",
                    )
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fake_ensemble(
        probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: object | None,
    ) -> ProbabilityOutput:
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.62,
            p_no_touch=0.58,
            z_path=probability_input.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "path_count": path_count,
                "simulation_preview": {
                    "sampled_paths": [
                        {
                            "index": 0,
                            "terminal_win": True,
                            "no_touch_win": True,
                            "points": [
                                probability_input.settlement_price,
                                probability_input.threshold,
                            ],
                        }
                    ],
                    "start_price": probability_input.settlement_price,
                    "threshold": probability_input.threshold,
                    "steps": steps,
                    "terminal_win_count": 1,
                },
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fake_ensemble,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        offload_status_path=offload_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    assert payload["state"] == "OK"
    assert payload["rows"][0]["probability_kind"] == "MC"
    assert payload["offload"]["offload_allowed"] is True
    assert payload["offload"]["blocked_input_count"] == 0


def test_worker_blocks_probability_input_lag_beyond_live_cadence_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    asof_ts = now - timedelta(seconds=26)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    offload_status_path = tmp_path / "offload_status.json"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": now.isoformat(),
                "rows": [
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-btc-too-lagged",
                        asset="BTC",
                        side="UP",
                    )
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fail_if_mc_runs(*_: object, **__: object) -> ProbabilityOutput:
        raise AssertionError("MC must not run for stale probability input")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fail_if_mc_runs,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        offload_status_path=offload_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    assert payload["state"] == "OFFLOAD_BLOCKED"
    assert payload["rows"][0]["probability_kind"] == "NOWCAST"
    assert payload["offload"]["offload_allowed"] is True
    assert payload["offload"]["blocked_input_count"] == 1
    assert "probability_inputs_stale" in payload["offload"]["reason_codes"]


def test_worker_blocks_expired_probability_input_even_when_snapshot_is_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    asof_ts = now - timedelta(seconds=25)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    offload_status_path = tmp_path / "offload_status.json"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": now.isoformat(),
                "rows": [
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-expired",
                        asset="BTC",
                        side="UP",
                        seconds_left=0.0,
                        expiry_offset_seconds=-1.0,
                    )
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fail_if_mc_runs(*_: object, **__: object) -> ProbabilityOutput:
        raise AssertionError("MC must not run for expired probability input")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fail_if_mc_runs,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        offload_status_path=offload_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    assert payload["state"] == "OFFLOAD_BLOCKED"
    assert payload["rows"][0]["probability_kind"] == "NOWCAST"
    assert "probability_input_expired" in payload["rows"][0]["block_reasons"]
    assert "probability_input_expired" in payload["offload"]["reason_codes"]
    assert payload["offload"]["mc_eligible_input_count"] == 0
    persisted_offload = json.loads(offload_status_path.read_text(encoding="utf-8"))
    assert "probability_input_expired" in persisted_offload["reason_codes"]


def test_worker_blocks_near_expiry_input_using_effective_worker_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    asof_ts = now - timedelta(seconds=4)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    offload_status_path = tmp_path / "offload_status.json"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": now.isoformat(),
                "rows": [
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-near-expiry",
                        asset="BTC",
                        side="UP",
                        seconds_left=21.0,
                        expiry_offset_seconds=17.0,
                    )
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fail_if_mc_runs(*_: object, **__: object) -> ProbabilityOutput:
        raise AssertionError("MC must not run inside the near-expiry window")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fail_if_mc_runs,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        offload_status_path=offload_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    assert payload["state"] == "OFFLOAD_BLOCKED"
    assert payload["rows"][0]["probability_kind"] == "NOWCAST"
    assert "near_expiry" in payload["rows"][0]["block_reasons"]
    assert "near_expiry" in payload["offload"]["reason_codes"]
    assert payload["offload"]["mc_eligible_input_count"] == 0
    persisted_offload = json.loads(offload_status_path.read_text(encoding="utf-8"))
    assert "near_expiry" in persisted_offload["reason_codes"]


def test_worker_blocks_expensive_mc_when_offload_gate_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    offload_status_path = tmp_path / "offload_status.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"

    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    {
                        "contract": "BTC 5m UP",
                        "contract_id": "btc-up",
                        "market_slug": "btc-updown-5m",
                        "start_ts": asof_ts.isoformat(),
                        "expiry_ts": (asof_ts + timedelta(minutes=5)).isoformat(),
                        "flags": ["OK"],
                        "offload_allowed": False,
                        "block_reasons": ["runtime_not_ready"],
                        "probability_input": ProbabilityInput(
                            state_id="state-btc-up",
                            asof_ts=asof_ts,
                            asset="BTC",
                            side="UP",
                            comparison_operator=">=",
                            seconds_left=300.0,
                            settlement_price=70_100.0,
                            threshold=70_000.0,
                            sigma_tau=0.012,
                            executable_price=0.52,
                            source_age_ms=100,
                            book_age_ms=100,
                            z_path=0.12,
                        ).to_json_dict(),
                        "volatility_regime": "normal",
                    }
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fail_if_mc_runs(*_: object, **__: object) -> ProbabilityOutput:
        raise AssertionError("MC should be blocked by offload gate")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fail_if_mc_runs,
    )

    def fail_if_fragments_load(*_: object, **__: object) -> tuple[tuple[object, ...], None]:
        raise AssertionError("fragments should not load when offload gate blocks")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker._load_probability_fragments",
        fail_if_fragments_load,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        offload_status_path=offload_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    assert payload["state"] == "OFFLOAD_BLOCKED"
    assert payload["offload"]["offload_allowed"] is False
    assert "runtime_not_ready" in payload["offload"]["reason_codes"]
    assert "recovery_status_missing" in payload["offload"]["reason_codes"]
    assert payload["offload"]["recommended_worker_mode"] == "nowcast_only"
    assert payload["offload"]["recommended_max_total_paths"] == 0
    assert payload["rows"][0]["probability_kind"] == "NOWCAST"
    persisted_offload = json.loads(offload_status_path.read_text(encoding="utf-8"))
    assert persisted_offload == payload["offload"]


def test_worker_blocks_expensive_mc_until_recovery_is_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    recovery_status_path = tmp_path / "recovery_status.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"

    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    {
                        "contract": "BTC 5m UP",
                        "contract_id": "btc-up",
                        "market_slug": "btc-updown-5m",
                        "start_ts": asof_ts.isoformat(),
                        "expiry_ts": (asof_ts + timedelta(minutes=5)).isoformat(),
                        "flags": ["OK"],
                        "probability_input": ProbabilityInput(
                            state_id="state-btc-up",
                            asof_ts=asof_ts,
                            asset="BTC",
                            side="UP",
                            comparison_operator=">=",
                            seconds_left=300.0,
                            settlement_price=70_100.0,
                            threshold=70_000.0,
                            sigma_tau=0.012,
                            executable_price=0.52,
                            source_age_ms=100,
                            book_age_ms=100,
                            z_path=0.12,
                        ).to_json_dict(),
                        "volatility_regime": "normal",
                    }
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )
    recovery_status_path.write_text(
        json.dumps(
            {
                "runtime_phase": "WARMING",
                "ready": False,
                "reasons": ["warmup_active"],
                "uptime_seconds": 30.0,
                "consecutive_healthy_cycles": 1,
            }
        ),
        encoding="utf-8",
    )

    def fail_if_mc_runs(*_: object, **__: object) -> ProbabilityOutput:
        raise AssertionError("MC should be blocked while recovery is warming")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fail_if_mc_runs,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        recovery_status_path=recovery_status_path,
        probability_inputs_path=probability_inputs_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    assert payload["state"] == "OFFLOAD_BLOCKED"
    assert payload["offload"]["offload_allowed"] is False
    assert "runtime_not_ready" in payload["offload"]["reason_codes"]
    assert "warmup_active" in payload["offload"]["reason_codes"]
    assert payload["rows"][0]["probability_kind"] == "NOWCAST"


def test_worker_trusts_ready_recovery_status_after_one_configured_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    recovery_status_path = tmp_path / "recovery_status.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    offload_status_path = tmp_path / "offload_status.json"
    _write_ready_recovery_status(
        recovery_status_path,
        consecutive_healthy_cycles=1,
    )
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-btc-up",
                        asset="BTC",
                        side="UP",
                    )
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fake_ensemble(
        probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: object | None,
    ) -> ProbabilityOutput:
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.62,
            p_no_touch=0.58,
            z_path=probability_input.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "path_count": path_count,
                "paths_per_generator": path_count,
                "generator_count": 4,
                "steps": steps,
                "simulation_preview": {"sampled_paths": []},
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fake_ensemble,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        recovery_status_path=recovery_status_path,
        offload_status_path=offload_status_path,
        probability_inputs_path=probability_inputs_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    assert payload["rows"][0]["probability_kind"] == "MC"
    assert payload["offload"]["offload_allowed"] is True
    assert "insufficient_healthy_cycles" not in payload["offload"]["reason_codes"]


def test_worker_blocks_expensive_mc_when_recovery_status_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    recovery_status_path = tmp_path / "recovery_status.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    probability_fragments_path = tmp_path / "probability_fragments.json"

    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    {
                        "contract": "BTC 5m UP",
                        "contract_id": "btc-up",
                        "market_slug": "btc-updown-5m",
                        "start_ts": asof_ts.isoformat(),
                        "expiry_ts": (asof_ts + timedelta(minutes=5)).isoformat(),
                        "flags": ["OK"],
                        "probability_input": ProbabilityInput(
                            state_id="state-btc-up",
                            asof_ts=asof_ts,
                            asset="BTC",
                            side="UP",
                            comparison_operator=">=",
                            seconds_left=300.0,
                            settlement_price=70_100.0,
                            threshold=70_000.0,
                            sigma_tau=0.012,
                            executable_price=0.52,
                            source_age_ms=100,
                            book_age_ms=100,
                            z_path=0.12,
                        ).to_json_dict(),
                        "volatility_regime": "normal",
                    }
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )
    recovery_status_path.write_text(
        json.dumps(
            {
                "runtime_phase": "READY",
                "ready": True,
                "reasons": [],
                "uptime_seconds": -1.0,
                "consecutive_healthy_cycles": 3,
            }
        ),
        encoding="utf-8",
    )

    def fail_if_mc_runs(*_: object, **__: object) -> ProbabilityOutput:
        raise AssertionError("MC should be blocked when recovery status is invalid")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fail_if_mc_runs,
    )

    def fail_if_fragments_load(*_: object, **__: object) -> tuple[tuple[object, ...], None]:
        raise AssertionError("fragments should not load when recovery status is invalid")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker._load_probability_fragments",
        fail_if_fragments_load,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        recovery_status_path=recovery_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_fragments_path=probability_fragments_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    assert payload["state"] == "OFFLOAD_BLOCKED"
    assert payload["offload"]["offload_allowed"] is False
    assert "recovery_status_invalid" in payload["offload"]["reason_codes"]
    assert payload["rows"][0]["probability_kind"] == "NOWCAST"


def test_snapshot_adapter_preserves_runtime_safety_fields(tmp_path: Path) -> None:
    asof_ts = datetime.now(UTC)
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    {
                        "contract": "BTC 5m UP",
                        "contract_id": "btc-up",
                        "market_slug": "btc-updown-5m",
                        "start_ts": asof_ts.isoformat(),
                        "expiry_ts": (asof_ts + timedelta(minutes=5)).isoformat(),
                        "flags": ["SIGMA_INVALID"],
                        "sigma_tau": None,
                        "sigma_valid": False,
                        "sigma_age_ms": 2500,
                        "offload_allowed": False,
                        "block_reasons": ["sigma_invalid"],
                        "probability_input": ProbabilityInput(
                            state_id="state-btc-up",
                            asof_ts=asof_ts,
                            asset="BTC",
                            side="UP",
                            comparison_operator=">=",
                            seconds_left=300.0,
                            settlement_price=70_100.0,
                            threshold=70_000.0,
                            sigma_tau=0.012,
                            executable_price=0.52,
                            source_age_ms=100,
                            book_age_ms=100,
                            z_path=0.12,
                        ).to_json_dict(),
                        "probability_state": "BLOCKED",
                        "volatility_regime": "normal",
                    }
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    inputs, skipped = _latest_probability_inputs_from_snapshot(
        path=probability_inputs_path,
        limit=4,
        max_state_age_seconds=600,
        max_snapshot_age_seconds=30,
    )

    assert skipped == 0
    assert len(inputs) == 1
    runtime_input = inputs[0]
    assert runtime_input.sigma_valid is False
    assert runtime_input.sigma_age_ms == 2500
    assert runtime_input.offload_allowed is False
    assert runtime_input.block_reasons == ("sigma_invalid",)
    assert runtime_input.probability_state == "BLOCKED"


def test_worker_preserves_blocked_threshold_rows_without_running_mc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "hot_probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))

    def input_row(
        *,
        state_id: str,
        side: str,
        contract_id: str,
        probability_state: str = "READY",
        k_stable: bool = True,
        flags: list[str] | None = None,
    ) -> dict[str, object]:
        probability_input = ProbabilityInput(
            state_id=state_id,
            asof_ts=asof_ts,
            asset="BTC",
            side=side,
            comparison_operator=">=" if side == "UP" else "<",
            seconds_left=300.0,
            settlement_price=70_100.0,
            threshold=70_000.0 if k_stable else 70_001.0,
            sigma_tau=0.012,
            executable_price=0.52 if side == "UP" else 0.48,
            source_age_ms=100,
            book_age_ms=100,
            z_path=0.12,
        )
        row: dict[str, object] = {
            "contract": f"BTC 5m {side}",
            "contract_id": contract_id,
            "market_slug": "btc-updown-5m-1780752000",
            "start_ts": asof_ts.isoformat(),
            "expiry_ts": (asof_ts + timedelta(minutes=5)).isoformat(),
            "flags": flags or ["OK"],
            "k_stable": k_stable,
            "probability_input": probability_input.to_json_dict(),
            "probability_state": probability_state,
            "threshold_diagnostics": {
                "contract_id": contract_id,
                "market_slug": "btc-updown-5m-1780752000",
                "asset": "BTC",
                "side": side,
                "K": probability_input.threshold,
                "K_source": "polymarket_rtds_chainlink",
                "rule_hash": "hash",
                "timestamp": asof_ts.isoformat(),
                "previous_K": 70_000.0 if not k_stable else None,
                "new_K": probability_input.threshold,
                "reason_for_change": (
                    "threshold_changed_without_rule_hash_change"
                    if not k_stable
                    else "initial_assignment"
                ),
            },
            "volatility_regime": "normal",
        }
        return row

    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": HOT_PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "inputs": [
                    input_row(
                        state_id="state-ready-original",
                        side="UP",
                        contract_id="btc-up",
                    ),
                    input_row(
                        state_id="state-blocked-mutated",
                        side="UP",
                        contract_id="btc-up",
                        probability_state="BLOCKED",
                        k_stable=False,
                        flags=["THRESHOLD_MUTATION_ERROR"],
                    ),
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fake_ensemble_output(
        input_row: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: tuple[tuple[float, ...], ...] | None = None,
    ) -> ProbabilityOutput:
        del path_count, steps, seed, history_fragments
        if input_row.state_id in {"state-ready-original", "state-blocked-mutated"}:
            raise AssertionError("blocked contract_id reached MC")
        return ProbabilityOutput(
            state_id=input_row.state_id,
            asof_ts=input_row.asof_ts,
            p_finish=0.64,
            p_no_touch=0.57,
            z_path=input_row.z_path,
            model_version="ensemble-v1",
            seed=1,
            diagnostics={
                "model": "ensemble-v1",
                "generator_version": "four-generator-ensemble-v1",
                "path_count": 4_000,
                "paths_per_generator": 1_000,
                "generator_count": 4,
                "steps": 300,
                "effective_weights": {},
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
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fake_ensemble_output,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=20_000),
    )

    assert payload["state"] == "OFFLOAD_BLOCKED"
    assert {row["probability_kind"] for row in payload["rows"]} == {"NOWCAST"}
    blocked = next(row for row in payload["rows"] if row["state_id"] == "state-blocked-mutated")
    assert blocked["contract_id"] == "btc-up"
    assert blocked["probability_kind"] == "NOWCAST"
    assert blocked["probability_state"] == "BLOCKED"
    assert blocked["k_stable"] is False
    assert blocked["flags"] == ["THRESHOLD_MUTATION_ERROR"]
    assert blocked["threshold_diagnostics"]["reason_for_change"] == (
        "threshold_changed_without_rule_hash_change"
    )


def test_gpu_snapshot_adapter_accepts_blocked_or_stale_state(tmp_path: Path) -> None:
    asof_ts = datetime.now(UTC)
    probability_inputs_path = tmp_path / "hot_probability_inputs.json"
    probability_input = ProbabilityInput(
        state_id="state-blocked-or-stale",
        asof_ts=asof_ts,
        asset="BTC",
        side="UP",
        comparison_operator=">=",
        seconds_left=300.0,
        settlement_price=70_100.0,
        threshold=70_000.0,
        sigma_tau=0.012,
        executable_price=0.52,
        source_age_ms=100,
        book_age_ms=100,
        z_path=0.12,
    )
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": HOT_PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "inputs": [
                    {
                        "contract": "BTC 5m UP",
                        "contract_id": "btc-up",
                        "market_slug": "btc-updown-5m-1780752000",
                        "start_ts": asof_ts.isoformat(),
                        "expiry_ts": (asof_ts + timedelta(minutes=5)).isoformat(),
                        "flags": ["STALE_INPUT"],
                        "k_stable": True,
                        "probability_input": probability_input.to_json_dict(),
                        "probability_state": "BLOCKED_OR_STALE",
                    }
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    inputs, skipped = _latest_probability_inputs_from_snapshot(
        path=probability_inputs_path,
        limit=10,
        max_state_age_seconds=60 * 60,
        max_snapshot_age_seconds=60,
    )

    assert skipped == 0
    assert inputs[0].probability_state == "BLOCKED_OR_STALE"


def test_worker_budget_rejects_soft_max_below_target() -> None:
    with pytest.raises(ValueError, match="cpu_soft_max_percent"):
        ProbabilityWorkerBudget(
            cpu_target_percent=20.0,
            cpu_soft_max_percent=15.0,
        )


def test_worker_budget_rejects_min_paths_above_max_paths() -> None:
    with pytest.raises(ValueError, match="min_total_paths"):
        ProbabilityWorkerBudget(
            min_total_paths=50_000,
            max_total_paths=40_000,
        )


def test_probability_loop_adapts_next_cycle_path_budget_from_cpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_budgets: list[int] = []

    def fake_cycle(**kwargs: object) -> dict[str, object]:
        budget = kwargs["budget"]
        assert isinstance(budget, ProbabilityWorkerBudget)
        observed_budgets.append(budget.max_total_paths)
        if len(observed_budgets) >= 2:
            raise KeyboardInterrupt
        return {
            "ok": True,
            "schema_version": "polymarket-probability-runtime-v1",
            "rows": [],
            "budget": {
                "cpu_percent": 25.0,
                "allocated_total_paths": 28_000,
                "effective_max_total_paths": budget.max_total_paths,
            },
        }

    sleeps: list[float] = []
    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_cuda_probability_worker_cycle",
        fake_cycle,
    )
    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.time.sleep",
        sleeps.append,
    )

    with pytest.raises(KeyboardInterrupt):
        run_cuda_probability_worker_loop(
            duckdb_path=tmp_path / "unused.duckdb",
            probability_status_path=tmp_path / "probabilities.json",
            interval_seconds=0.01,
            budget=ProbabilityWorkerBudget(
                max_total_paths=40_000,
                min_total_paths=4_000,
                cpu_target_percent=15.0,
                cpu_soft_max_percent=20.0,
            ),
        )

    assert observed_budgets == [40_000, 28_000]
    assert sleeps == [0.01]
    status_payload = json.loads((tmp_path / "probabilities.json").read_text())
    assert status_payload["budget"]["next_max_total_paths"] == 28_000
    assert (
        status_payload["budget"]["cpu_budget_adjustment_reason"]
        == "cpu_above_soft_max"
    )


def test_probability_loop_does_not_adapt_on_zero_allocated_path_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_budgets: list[int] = []

    def fake_cycle(**kwargs: object) -> dict[str, object]:
        budget = kwargs["budget"]
        assert isinstance(budget, ProbabilityWorkerBudget)
        observed_budgets.append(budget.max_total_paths)
        if len(observed_budgets) >= 2:
            raise KeyboardInterrupt
        return {
            "ok": True,
            "schema_version": "polymarket-probability-runtime-v1",
            "rows": [],
            "budget": {
                "cpu_percent": 2.0,
                "allocated_total_paths": 0,
                "effective_max_total_paths": budget.max_total_paths,
            },
        }

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_cuda_probability_worker_cycle",
        fake_cycle,
    )
    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.time.sleep",
        lambda _: None,
    )

    with pytest.raises(KeyboardInterrupt):
        run_cuda_probability_worker_loop(
            duckdb_path=tmp_path / "unused.duckdb",
            probability_status_path=tmp_path / "probabilities.json",
            interval_seconds=0.01,
            budget=ProbabilityWorkerBudget(
                max_total_paths=40_000,
                min_total_paths=4_000,
                cpu_target_percent=15.0,
                cpu_soft_max_percent=20.0,
            ),
        )

    assert observed_budgets == [40_000, 40_000]
    status_payload = json.loads((tmp_path / "probabilities.json").read_text())
    assert status_payload["budget"]["next_max_total_paths"] == 40_000
    assert status_payload["budget"]["cpu_budget_adjustment_reason"] == "cpu_unmeasured"


def test_budget_diagnostics_preserve_configured_and_effective_path_budgets() -> None:
    budget = ProbabilityWorkerBudget(
        max_total_paths=28_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
    )

    diagnostics = _budget_diagnostics(
        budget=budget,
        cycle_started_monotonic=100.0,
        cycle_started_process=10.0,
        requested_total_paths=40_000,
        allocated_total_paths=28_000,
        clamped_inputs=4,
        mc_input_skipped=0,
        path_budget_per_input=7_000,
    )

    assert diagnostics["max_total_paths"] == 40_000
    assert diagnostics["effective_max_total_paths"] == 28_000


def test_worker_serves_retained_mc_rows_when_input_snapshot_is_stale(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    retained_row = {
        "contract": "BTC 5m UP",
        "contract_id": "btc-up",
        "expiry_ts": (now + timedelta(minutes=5)).isoformat(),
        "model_version": "cuda-monte-carlo-v1",
        "output_id": "retained-output",
        "p_finish": 0.61,
        "probability_kind": "MC",
        "state_id": "state-btc-up",
        "valid_until": (now + timedelta(seconds=30)).isoformat(),
    }
    probability_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-probability-runtime-v1",
                "rows": [retained_row],
            }
        ),
        encoding="utf-8",
    )
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": (now - timedelta(seconds=30)).isoformat(),
                "rows": [],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        probability_inputs_path=probability_inputs_path,
        max_input_snapshot_age_seconds=10.0,
    )

    assert payload["ok"] is True
    assert payload["state"] == "STALE_INPUTS"
    assert payload["rows"] == [retained_row]
    assert payload["retained_mc_rows"] == 1
    assert payload["previous_mc_retained"] is True
    assert "probability input snapshot stale" in payload["input_error"]


def test_worker_writes_ensemble_v1_rows_and_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_input = ProbabilityInput(
        state_id="state-btc-up",
        asof_ts=asof_ts,
        asset="BTC",
        side="UP",
        comparison_operator=">=",
        seconds_left=240.0,
        settlement_price=70_100.0,
        threshold=70_000.0,
        sigma_tau=0.012,
        executable_price=0.54,
        source_age_ms=120,
        book_age_ms=80,
        z_path=0.12,
    )
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    {
                        "contract": "BTC 5m UP",
                        "contract_id": "btc-up",
                        "market_slug": "btc-updown-5m-1780752000",
                        "start_ts": asof_ts.isoformat(),
                        "expiry_ts": (asof_ts + timedelta(minutes=5)).isoformat(),
                        "flags": ["OK"],
                        "probability_input": probability_input.to_json_dict(),
                        "volatility_regime": "normal",
                    }
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fake_ensemble_output(
        input_row: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: tuple[tuple[float, ...], ...] | None = None,
    ) -> ProbabilityOutput:
        del history_fragments
        return ProbabilityOutput(
            state_id=input_row.state_id,
            asof_ts=input_row.asof_ts,
            p_finish=0.64,
            p_no_touch=0.57,
            z_path=input_row.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "model": "ensemble-v1",
                "generator_version": "four-generator-ensemble-v1",
                "path_count": path_count * 4,
                "paths_per_generator": path_count,
                "generator_count": 4,
                "steps": steps,
                "simulation_preview": {
                    "path_count": path_count * 4,
                    "paths_per_generator": path_count,
                    "generator_count": 4,
                    "sampled_paths": [
                        {
                            "index": "empirical_conditional:0",
                            "generator_id": "empirical_conditional",
                            "terminal_win": True,
                            "no_touch_win": True,
                            "points": [70_100.0, 70_120.0],
                        }
                    ],
                    "terminal_histogram": [],
                },
                "effective_weights": {
                    "empirical_conditional": 0.4,
                    "block_bootstrap": 0.25,
                    "filtered_historical": 0.25,
                    "stress_overlay": 0.1,
                },
                "generator_summary": {
                    "empirical_conditional": {
                        "p_finish": 0.66,
                        "p_no_touch": 0.60,
                        "weight": 0.4,
                        "sparse": True,
                    }
                },
                "generator_runs": [],
                "effective_generator_values": {},
                "u_gen": 0.03,
                "mc_dispersion": 0.08,
                "uncertainty_buffer": 0.055,
                "risk_adjusted_p_finish": 0.56,
                "risk_adjusted_p_no_touch": 0.53,
                "risk_adjustment": 0.08,
                "terminal_probability_source": "core_generators_ex_stress_overlay",
                "path_diagnosis": "SPARSE",
                "sparse_scope": True,
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fake_ensemble_output,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=30_000),
    )

    row = payload["rows"][0]
    assert row["model_version"] == "ensemble-v1"
    assert row["generator_version"] == "four-generator-ensemble-v1"
    assert row["backend"] == "ensemble"
    assert row["p_finish"] == 0.64
    assert row["path_count"] == 30_000
    assert row["paths_per_generator"] == 7_500
    assert row["generator_count"] == 4
    assert row["simulation_preview"]["sampled_paths"][0]["generator_id"] == "empirical_conditional"
    assert row["effective_weights"]["stress_overlay"] == 0.1
    assert row["generator_summary"]["empirical_conditional"]["sparse"] is True
    assert row["mc_dispersion"] == 0.08
    assert row["uncertainty_buffer"] == 0.055
    assert row["risk_adjusted_p_finish"] == 0.56
    assert row["terminal_probability_source"] == "core_generators_ex_stress_overlay"
    assert row["path_diagnosis"] == "SPARSE"
    assert row["sparse_scope"] is True

    events = [
        json.loads(line)
        for line in probability_event_path.read_text(encoding="utf-8").splitlines()
    ]
    mc_event = next(event for event in events if event["probability_kind"] == "MC")
    assert mc_event["model_version"] == "ensemble-v1"
    assert mc_event["generator_version"] == "four-generator-ensemble-v1"
    assert mc_event["path_count"] == 30_000
    assert mc_event["paths_per_generator"] == 7_500
    assert mc_event["generator_count"] == 4
    assert mc_event["simulation_preview"]["sampled_paths"][0]["generator_id"] == "empirical_conditional"
    assert mc_event["effective_weights"]["empirical_conditional"] == 0.4
    assert mc_event["generator_summary"]["empirical_conditional"]["p_finish"] == 0.66
    assert mc_event["risk_adjusted_p_finish"] == 0.56
    assert mc_event["terminal_probability_source"] == "core_generators_ex_stress_overlay"


def test_worker_does_not_feed_prior_fragments_without_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_fragments_path = tmp_path / "probability_fragments.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-btc-up",
                        asset="BTC",
                        side="UP",
                        seconds_left=240.0,
                    )
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fail_load_probability_fragments(**_: object) -> None:
        raise AssertionError("_load_probability_fragments should not be called")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker._load_probability_fragments",
        fail_load_probability_fragments,
    )
    seen_history: list[tuple[tuple[float, ...], ...] | None] = []

    def fake_ensemble_output(
        input_row: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: tuple[tuple[float, ...], ...] | None = None,
    ) -> ProbabilityOutput:
        seen_history.append(history_fragments)
        return ProbabilityOutput(
            state_id=input_row.state_id,
            asof_ts=input_row.asof_ts,
            p_finish=0.56,
            p_no_touch=0.51,
            z_path=input_row.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "model": "ensemble-v1",
                "generator_version": "four-generator-ensemble-v1",
                "path_count": path_count,
                "steps": steps,
                "effective_weights": {},
                "generator_summary": {},
                "generator_runs": [],
                "effective_generator_values": {},
                "u_gen": 0.01,
                "mc_dispersion": 0.02,
                "uncertainty_buffer": 0.02,
                "path_diagnosis": "CLEAN",
                "sparse_scope": False,
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fake_ensemble_output,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_fragments_path=probability_fragments_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=30_000),
    )

    assert seen_history == [None]
    row = payload["rows"][0]
    assert row["prior_fragment_enabled"] is False
    assert row["prior_fragment_count"] == 0
    assert row["prior_fragment_reason"] == "disabled_uncalibrated_live_prior"
    assert row["prior_fragment_sparse"] is False
    assert row["prior_fragment_ids"] == []
    assert row["path_diagnosis"] == "CLEAN"
    events = [
        json.loads(line)
        for line in probability_event_path.read_text(encoding="utf-8").splitlines()
    ]
    mc_event = next(event for event in events if event["probability_kind"] == "MC")
    assert mc_event["prior_fragment_enabled"] is False


def test_worker_passes_asof_safe_fragments_into_ensemble_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_fragments_path = tmp_path / "probability_fragments.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_input = ProbabilityInput(
        state_id="state-btc-up",
        asof_ts=asof_ts,
        asset="BTC",
        side="UP",
        comparison_operator=">=",
        seconds_left=240.0,
        settlement_price=70_100.0,
        threshold=70_000.0,
        sigma_tau=0.012,
        executable_price=0.54,
        source_age_ms=120,
        book_age_ms=80,
        z_path=0.12,
    )
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    {
                        "contract": "BTC 5m UP",
                        "contract_id": "btc-up",
                        "market_slug": "btc-updown-5m-1780752000",
                        "start_ts": asof_ts.isoformat(),
                        "expiry_ts": (asof_ts + timedelta(minutes=5)).isoformat(),
                        "flags": ["OK"],
                        "probability_input": probability_input.to_json_dict(),
                        "volatility_regime": "normal",
                    }
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )
    prior_one = (70_000.0, 70_050.0, 70_100.0)
    prior_two = (70_000.0, 70_010.0, 70_080.0)
    future = (70_000.0, 69_900.0, 69_800.0)
    write_probability_fragments(
        out_path=probability_fragments_path,
        generated_at=asof_ts,
        fragments=(
            GeneratorFragment(
                fragment_id="btc-prior-one",
                asset="BTC",
                asof_ts=asof_ts - timedelta(seconds=20),
                prices=prior_one,
                horizon_seconds=300,
                z_path_bucket="near",
                quality_bucket="OK",
            ),
            GeneratorFragment(
                fragment_id="btc-prior-two",
                asset="BTC",
                asof_ts=asof_ts - timedelta(seconds=10),
                prices=prior_two,
                horizon_seconds=300,
                z_path_bucket="near",
                quality_bucket="OK",
            ),
            GeneratorFragment(
                fragment_id="btc-future",
                asset="BTC",
                asof_ts=asof_ts + timedelta(seconds=10),
                prices=future,
                horizon_seconds=300,
                z_path_bucket="near",
                quality_bucket="OK",
            ),
        ),
    )
    seen_history: list[tuple[tuple[float, ...], ...] | None] = []

    def fake_ensemble_output(
        input_row: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: tuple[tuple[float, ...], ...] | None = None,
    ) -> ProbabilityOutput:
        seen_history.append(history_fragments)
        return ProbabilityOutput(
            state_id=input_row.state_id,
            asof_ts=input_row.asof_ts,
            p_finish=0.64,
            p_no_touch=0.57,
            z_path=input_row.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "model": "ensemble-v1",
                "generator_version": "four-generator-ensemble-v1",
                "path_count": path_count,
                "steps": steps,
                "effective_weights": {},
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
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fake_ensemble_output,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_fragments_path=probability_fragments_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=30_000, use_prior_fragments=True),
    )

    assert seen_history == [(prior_one, prior_two)]
    row = payload["rows"][0]
    assert row["prior_fragment_enabled"] is True
    assert row["prior_fragment_count"] == 2
    assert row["prior_fragment_reason"] == "exact"
    assert row["prior_fragment_sparse"] is False
    assert row["prior_fragment_ids"] == ["btc-prior-one", "btc-prior-two"]
    events = [
        json.loads(line)
        for line in probability_event_path.read_text(encoding="utf-8").splitlines()
    ]
    mc_event = next(event for event in events if event["probability_kind"] == "MC")
    assert mc_event["prior_fragment_enabled"] is True
