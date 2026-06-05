import builtins
import inspect
import importlib
import sys
from datetime import datetime, timezone

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

    def reject_cupy_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "cupy" or name.startswith("cupy."):
            raise AssertionError("cuda_monte_carlo must not import CuPy at module import time")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_cupy_import)

    importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")


def test_cuda_monte_carlo_raises_clear_error_when_cupy_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")
    real_import = builtins.__import__

    def missing_cupy(name: str, *args: object, **kwargs: object) -> object:
        if name == "cupy" or name.startswith("cupy."):
            raise ModuleNotFoundError("No module named 'cupy'")
        return real_import(name, *args, **kwargs)

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
