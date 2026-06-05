import builtins
import inspect
import importlib
import sys
from datetime import datetime, timezone
from types import ModuleType

import pytest

from polymarket_engine.probability.schema import ProbabilityInput


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
    outputs = iter(
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

    def fake_single_seed(*_: object, **__: object) -> module.ProbabilityOutput:
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
