import builtins
import inspect
import importlib
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from types import ModuleType

import pytest

from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


def _probability_input() -> ProbabilityInput:
    return ProbabilityInput(
        state_id="state-btc-up",
        asof_ts=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
        asset="BTC",
        side="UP",
        comparison_operator=">",
        seconds_left=120.0,
        settlement_price=70_000.0,
        threshold=70_500.0,
        sigma_tau=0.02,
        executable_price=0.48,
        source_age_ms=100,
        book_age_ms=200,
        z_path=-0.35,
    )


def test_cuda_module_import_does_not_import_cupy(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.modules.pop("polymarket_engine.probability.cuda_monte_carlo", None)
    real_import = builtins.__import__

    def reject_cupy_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "cupy" or name.startswith("cupy."):
            raise AssertionError("cuda_monte_carlo must not import CuPy at module import time")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", reject_cupy_import)

    importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")


def test_cuda_monte_carlo_raises_clear_error_when_cupy_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")
    real_import = builtins.__import__

    def missing_cupy(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "cupy" or name.startswith("cupy."):
            raise ModuleNotFoundError("No module named 'cupy'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", missing_cupy)

    with pytest.raises(module.CudaUnavailableError, match="CuPy.*CUDA.*unavailable"):
        module.run_cuda_monte_carlo(
            _probability_input(),
            path_count=16,
            steps=4,
            seed=20260605,
        )


def test_cuda_monte_carlo_uses_cupy_generator_standard_normal() -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")

    source = inspect.getsource(module.run_cuda_monte_carlo)

    assert ".standard_normal(" in source
    assert ".normal(" not in source


def test_cuda_multi_seed_aggregates_p_hat_and_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")
    probability_input = _probability_input()
    outputs: Iterator[ProbabilityOutput] = iter(
        (
            module.ProbabilityOutput(
                state_id=probability_input.state_id,
                asof_ts=probability_input.asof_ts,
                p_finish=0.40,
                p_no_touch=0.70,
                z_path=probability_input.z_path,
                model_version="cuda-lognormal-chainlink-sigma-v1",
                seed=11,
                diagnostics={
                    "path_count": 10_000,
                    "steps": 120,
                    "model": "cuda_lognormal_chainlink_sigma",
                    "simulation_preview": {"path_count": 10_000, "sampled_paths": []},
                    "prior_sensitivity": [],
                },
            ),
            module.ProbabilityOutput(
                state_id=probability_input.state_id,
                asof_ts=probability_input.asof_ts,
                p_finish=0.50,
                p_no_touch=0.80,
                z_path=probability_input.z_path,
                model_version="cuda-lognormal-chainlink-sigma-v1",
                seed=22,
                diagnostics={
                    "path_count": 10_000,
                    "steps": 120,
                    "model": "cuda_lognormal_chainlink_sigma",
                    "simulation_preview": {"path_count": 10_000, "sampled_paths": []},
                    "prior_sensitivity": [],
                },
            ),
            module.ProbabilityOutput(
                state_id=probability_input.state_id,
                asof_ts=probability_input.asof_ts,
                p_finish=0.60,
                p_no_touch=0.90,
                z_path=probability_input.z_path,
                model_version="cuda-lognormal-chainlink-sigma-v1",
                seed=33,
                diagnostics={
                    "path_count": 10_000,
                    "steps": 120,
                    "model": "cuda_lognormal_chainlink_sigma",
                    "simulation_preview": {"path_count": 10_000, "sampled_paths": []},
                    "prior_sensitivity": [],
                },
            ),
        )
    )
    calls = []

    def fake_single_seed(probability_input_arg: object, **kwargs: object) -> ProbabilityOutput:
        calls.append((probability_input_arg, kwargs))
        return next(outputs)

    monkeypatch.setattr(module, "run_cuda_monte_carlo", fake_single_seed)

    result = module.run_cuda_monte_carlo_multi_seed(
        probability_input,
        paths_per_seed=10_000,
        steps=120,
        seed=11,
        seed_count=3,
    )

    assert result.p_finish == pytest.approx(0.50)
    assert result.p_no_touch == pytest.approx(0.80)
    assert result.diagnostics["p_hat"] == pytest.approx(0.50)
    assert result.diagnostics["p_hat_std"] == pytest.approx(0.10)
    assert result.diagnostics["p_hat_ci_low"] == pytest.approx(0.3868, rel=1e-3)
    assert result.diagnostics["p_hat_ci_high"] == pytest.approx(0.6132, rel=1e-3)
    assert result.diagnostics["seed_count"] == 3
    assert result.diagnostics["paths_per_seed"] == 10_000
    assert result.diagnostics["path_count"] == 30_000
    assert [row["seed"] for row in result.diagnostics["seed_runs"]] == [11, 22, 33]
    assert calls == [
        (
            probability_input,
            {
                "path_count": 10_000,
                "steps": 120,
                "seed": 11,
            },
        ),
        (
            probability_input,
            {
                "path_count": 10_000,
                "steps": 120,
                "seed": 22,
            },
        ),
        (
            probability_input,
            {
                "path_count": 10_000,
                "steps": 120,
                "seed": 33,
            },
        ),
    ]


def test_prior_sensitivity_rows_are_distribution_based() -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")
    paths = (
        (100.0, 101.0, 102.0, 103.0),
        (100.0, 99.0, 98.0, 97.0),
        (100.0, 100.5, 101.0, 101.5),
        (100.0, 98.5, 99.0, 100.0),
    )
    probability_input = module.ProbabilityInput(
        state_id="state-btc-up",
        asof_ts=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
        asset="BTC",
        side="UP",
        comparison_operator=">",
        seconds_left=120.0,
        settlement_price=100.0,
        threshold=100.0,
        sigma_tau=0.02,
        executable_price=0.50,
        source_age_ms=100,
        book_age_ms=100,
        z_path=0.0,
    )

    rows = module._prior_sensitivity_from_cpu_paths(
        probability_input,
        paths=paths,
        terminal_wins=(True, False, True, False),
    )

    assert rows
    assert len(rows) == 12
    assert {row["dimension"] for row in rows} == {"prior_price_quantile"}
    assert {row["time_fraction"] for row in rows} == {0.25, 0.50, 0.75}
    assert {
        (row["quantile_low"], row["quantile_high"]) for row in rows
    } == {
        (0.0, 0.25),
        (0.25, 0.50),
        (0.50, 0.75),
        (0.75, 1.0),
    }
    assert all("price_delta" not in row for row in rows)
    assert all("dollar_move" not in row for row in rows)
    assert all(0.0 <= row["p_hat"] <= 1.0 for row in rows)
    assert all(row["sample_count"] > 0 for row in rows)
    assert all("quantile_low" in row and "quantile_high" in row for row in rows)
    assert all("point_index" in row for row in rows)
    assert all("price_quantile" in row for row in rows)
    assert all("log_return_quantile" in row for row in rows)


def test_cuda_cpu_row_conversion_accepts_nested_python_lists() -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")
    rows = [[100.0, 101.25, 102.5], [100.0, 99.75, 98.5]]

    converted = tuple(module._float_tuple_from_cpu_row(row) for row in rows)

    assert converted == ((100.0, 101.25, 102.5), (100.0, 99.75, 98.5))


def test_prior_sensitivity_aggregation_weights_by_sample_count() -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")

    rows = module._aggregate_prior_sensitivity_rows(
        (
            (
                {
                    "dimension": "prior_price_quantile",
                    "time_fraction": 0.25,
                    "point_index": 1,
                    "quantile_low": 0.0,
                    "quantile_high": 0.25,
                    "sample_count": 2,
                    "price_quantile": 100.0,
                    "log_return_quantile": 0.01,
                    "p_hat": 0.25,
                },
                {
                    "dimension": "prior_price_quantile",
                    "time_fraction": 0.25,
                    "point_index": 1,
                    "quantile_low": 0.25,
                    "quantile_high": 0.50,
                    "sample_count": 4,
                    "price_quantile": 102.0,
                    "log_return_quantile": 0.02,
                    "p_hat": 0.50,
                },
            ),
            (
                {
                    "dimension": "prior_price_quantile",
                    "time_fraction": 0.25,
                    "point_index": 1,
                    "quantile_low": 0.0,
                    "quantile_high": 0.25,
                    "sample_count": 6,
                    "price_quantile": 104.0,
                    "log_return_quantile": 0.03,
                    "p_hat": 0.75,
                },
            ),
        )
    )

    first_band = rows[0]
    second_band = rows[1]
    assert first_band["sample_count"] == 8
    assert first_band["source_seed_count"] == 2
    assert first_band["p_hat"] == pytest.approx(0.625)
    assert first_band["price_quantile"] == pytest.approx(103.0)
    assert first_band["log_return_quantile"] == pytest.approx(0.025)
    assert second_band["sample_count"] == 4
    assert second_band["source_seed_count"] == 1
    assert second_band["p_hat"] == pytest.approx(0.50)


def test_cuda_multi_seed_aggregates_prior_sensitivity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")
    probability_input = _probability_input()
    outputs: Iterator[ProbabilityOutput] = iter(
        (
            module.ProbabilityOutput(
                state_id=probability_input.state_id,
                asof_ts=probability_input.asof_ts,
                p_finish=0.40,
                p_no_touch=0.70,
                z_path=probability_input.z_path,
                model_version="cuda-lognormal-chainlink-sigma-v1",
                seed=11,
                diagnostics={
                    "path_count": 10_000,
                    "steps": 120,
                    "model": "cuda_lognormal_chainlink_sigma",
                    "simulation_preview": {"path_count": 10_000, "sampled_paths": []},
                    "prior_sensitivity": [
                        {
                            "dimension": "prior_price_quantile",
                            "time_fraction": 0.25,
                            "point_index": 30,
                            "quantile_low": 0.0,
                            "quantile_high": 0.25,
                            "sample_count": 2,
                            "price_quantile": 100.0,
                            "log_return_quantile": 0.01,
                            "p_hat": 0.25,
                        }
                    ],
                },
            ),
            module.ProbabilityOutput(
                state_id=probability_input.state_id,
                asof_ts=probability_input.asof_ts,
                p_finish=0.60,
                p_no_touch=0.90,
                z_path=probability_input.z_path,
                model_version="cuda-lognormal-chainlink-sigma-v1",
                seed=22,
                diagnostics={
                    "path_count": 10_000,
                    "steps": 120,
                    "model": "cuda_lognormal_chainlink_sigma",
                    "simulation_preview": {"path_count": 10_000, "sampled_paths": []},
                    "prior_sensitivity": [
                        {
                            "dimension": "prior_price_quantile",
                            "time_fraction": 0.25,
                            "point_index": 30,
                            "quantile_low": 0.0,
                            "quantile_high": 0.25,
                            "sample_count": 6,
                            "price_quantile": 104.0,
                            "log_return_quantile": 0.03,
                            "p_hat": 0.75,
                        }
                    ],
                },
            ),
        )
    )

    monkeypatch.setattr(
        module,
        "run_cuda_monte_carlo",
        lambda probability_input_arg, **kwargs: next(outputs),
    )

    result = module.run_cuda_monte_carlo_multi_seed(
        probability_input,
        paths_per_seed=10_000,
        steps=120,
        seed=11,
        seed_count=2,
    )

    rows = result.diagnostics["prior_sensitivity"]
    assert len(rows) == 1
    assert rows[0]["sample_count"] == 8
    assert rows[0]["source_seed_count"] == 2
    assert rows[0]["p_hat"] == pytest.approx(0.625)
    assert rows[0]["price_quantile"] == pytest.approx(103.0)
    assert rows[0]["log_return_quantile"] == pytest.approx(0.025)
    assert "price_delta" not in rows[0]
    assert "dollar_move" not in rows[0]


def test_cuda_multi_seed_rejects_nonpositive_paths_per_seed() -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")

    with pytest.raises(ValueError, match="paths_per_seed"):
        module.run_cuda_monte_carlo_multi_seed(
            _probability_input(),
            paths_per_seed=0,
            steps=120,
            seed=11,
            seed_count=3,
        )


def test_cuda_multi_seed_rejects_nonpositive_seed_count() -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")

    with pytest.raises(ValueError, match="seed_count"):
        module.run_cuda_monte_carlo_multi_seed(
            _probability_input(),
            paths_per_seed=10_000,
            steps=120,
            seed=11,
            seed_count=0,
        )
