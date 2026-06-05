from __future__ import annotations

import importlib
import json
import types
from datetime import datetime, timezone
from typing import Any

import pytest

from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


def _probability_input() -> ProbabilityInput:
    return ProbabilityInput(
        state_id="state-native",
        asof_ts=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
        asset="BTC",
        side="UP",
        comparison_operator=">=",
        seconds_left=90.0,
        settlement_price=100.0,
        threshold=101.0,
        sigma_tau=0.02,
        executable_price=0.55,
        source_age_ms=10,
        book_age_ms=20,
        z_path=-0.5,
    )


def test_run_native_or_python_uses_python_numpy_backend() -> None:
    from polymarket_engine.probability.native import run_native_or_python

    output = run_native_or_python(
        _probability_input(),
        path_count=128,
        steps=8,
        seed=123,
        backend="python_numpy",
    )

    assert isinstance(output, ProbabilityOutput)
    assert 0.0 <= output.p_finish <= 1.0
    assert 0.0 <= output.p_no_touch <= 1.0
    assert output.seed == 123
    assert output.diagnostics["backend_request"] == "python_numpy"
    assert output.diagnostics["backend"] == "python_numpy"
    assert output.diagnostics["native_available"] is False


def test_run_native_or_python_falls_back_when_native_module_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability.native import run_native_or_python

    real_import_module = importlib.import_module

    def missing_native(name: str, package: str | None = None) -> types.ModuleType:
        if name == "polymarket_probability_native":
            raise ImportError("native extension unavailable")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", missing_native)

    output = run_native_or_python(
        _probability_input(),
        path_count=128,
        steps=8,
        seed=456,
        backend="cpu_rayon",
    )

    assert 0.0 <= output.p_finish <= 1.0
    assert 0.0 <= output.p_no_touch <= 1.0
    assert output.seed == 456
    assert output.diagnostics["backend_request"] == "cpu_rayon"
    assert output.diagnostics["backend"] == "python_numpy"
    assert output.diagnostics["native_available"] is False


def test_run_native_or_python_returns_native_output_from_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability.native import run_native_or_python

    captured: dict[str, Any] = {}

    def run_cpu_json(input_json: str, config_json: str) -> str:
        captured["input"] = json.loads(input_json)
        captured["config"] = json.loads(config_json)
        return json.dumps(
            {
                "state_id": "state-native",
                "asof_ts": "2026-06-05T12:00:00Z",
                "p_finish": 0.25,
                "p_no_touch": 0.75,
                "z_path": -0.5,
                "model_version": "offline-lognormal-chainlink-sigma-rust-cpu-v1",
                "seed": 789,
                "backend": "cpu_rayon",
                "diagnostics": {"path_count": 64, "steps": 4},
                "artifacts": {
                    "percentile_paths": [],
                    "sample_paths": [],
                    "terminal_histogram": [],
                },
            }
        )

    fake_module = types.SimpleNamespace(run_cpu_json=run_cpu_json)
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: fake_module if name == "polymarket_probability_native" else importlib.import_module(name),
    )

    output = run_native_or_python(
        _probability_input(),
        path_count=64,
        steps=4,
        seed=789,
        backend="cpu_rayon",
    )

    assert captured["input"]["asset"] == "BTC"
    assert captured["input"]["side"] == "UP"
    assert captured["input"]["comparison_operator"] == ">="
    assert captured["config"] == {
        "path_count": 64,
        "steps": 4,
        "seed": 789,
        "backend": "cpu_rayon",
        "model_version": "offline-lognormal-chainlink-sigma-rust-cpu-v1",
        "emit_artifacts": False,
        "sample_path_limit": 0,
    }
    assert output.p_finish == pytest.approx(0.25)
    assert output.p_no_touch == pytest.approx(0.75)
    assert output.seed == 789
    assert output.diagnostics == {
        "path_count": 64,
        "steps": 4,
        "backend_request": "cpu_rayon",
        "backend": "cpu_rayon",
        "native_available": True,
    }
