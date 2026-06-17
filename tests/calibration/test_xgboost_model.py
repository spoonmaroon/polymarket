from __future__ import annotations

import builtins

import pytest

from polymarket_engine.calibration import xgboost_model
from polymarket_engine.calibration.xgboost_model import fit_xgboost_calibrator


def test_xgboost_import_failure_raises_research_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "xgboost":
            raise ImportError("dlopen(libxgboost.dylib): Library not loaded: libomp.dylib")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="native libraries") as excinfo:
        xgboost_model._xgboost()

    message = str(excinfo.value)
    assert "uv sync --group research" in message
    assert "libomp" in message


def test_fit_xgboost_calibrator_predicts_probabilities() -> None:
    pytest.importorskip("xgboost")
    matrix = [[-2.0], [-1.0], [1.0], [2.0]]
    labels = [0, 0, 1, 1]

    model = fit_xgboost_calibrator(
        matrix,
        labels,
        feature_names=("signal",),
        max_depth=2,
        eta=0.3,
        rounds=8,
    )

    probabilities = model.predict_proba([[-2.0], [2.0]])
    assert len(probabilities) == 2
    assert all(0.0 <= value <= 1.0 for value in probabilities)
    assert model.model_version == "MC_Calibrator_GBDT_v1"
