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
from polymarket_engine.probability.gpu_worker import _path_budget_per_input
from polymarket_engine.probability.gpu_worker import run_cuda_probability_worker_cycle
from polymarket_engine.probability.gpu_worker import run_cuda_probability_worker_loop
from polymarket_engine.probability.generator_fragments import GeneratorFragment
from polymarket_engine.probability.generator_fragments import write_probability_fragments
from polymarket_engine.probability.runtime_inputs import ProbabilityRuntimeInput
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


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
    budget = ProbabilityWorkerBudget(max_total_paths=500_000, worker_mode="ensemble")

    assert _path_budget_per_input(input_count=4, budget=budget) == 31_250
    assert _clamp_path_count(80_000, path_budget_per_input=31_250) == (
        31_250,
        True,
    )
    assert _clamp_path_count(10_000, path_budget_per_input=31_250) == (
        10_000,
        False,
    )


def test_worker_budget_keeps_single_generator_modes_divided_by_inputs_only() -> None:
    budget = ProbabilityWorkerBudget(max_total_paths=500_000, worker_mode="cuda")

    assert _path_budget_per_input(input_count=4, budget=budget) == 125_000


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


def test_worker_passes_asof_safe_fragments_into_ensemble(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_fragments_path = tmp_path / "probability_fragments.json"
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
        probability_event_path=tmp_path / "probability-events.jsonl",
        budget=ProbabilityWorkerBudget(max_total_paths=30_000),
    )

    assert seen_history == [(prior_one, prior_two)]
    row = payload["rows"][0]
    assert row["prior_fragment_count"] == 2
    assert row["prior_fragment_reason"] == "exact"
    assert row["prior_fragment_sparse"] is False
    assert row["prior_fragment_ids"] == ["btc-prior-one", "btc-prior-two"]
