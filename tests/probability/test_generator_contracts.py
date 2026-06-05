import math
from datetime import datetime, timezone

import pytest

from polymarket_engine.probability.generator_contracts import (
    DynamicWeightScope,
    GeneratorId,
    GeneratorResult,
    GeneratorRun,
    GeneratorWeight,
    HistoricalValidationWindow,
)


def _asof() -> datetime:
    return datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc)


def _scope() -> DynamicWeightScope:
    return DynamicWeightScope(
        asset="BTC",
        horizon_seconds=300,
        seconds_left_bucket="60-120",
        z_path_bucket="near",
        vol_regime="normal",
        vol_trend="rising",
        wick_regime="quiet",
        source_quality_state="ready",
    )


def _result() -> GeneratorResult:
    return GeneratorResult(
        p_finish=0.62,
        p_no_touch=0.81,
        z_path=0.42,
        diagnostics={"source": "fixture"},
    )


def _validation_window() -> HistoricalValidationWindow:
    return HistoricalValidationWindow(
        asof_ts=datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc),
        evaluated_through_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
        label_window_seconds=3600,
    )


def test_generator_id_values_match_contract() -> None:
    assert [generator.value for generator in GeneratorId] == [
        "empirical_conditional",
        "block_bootstrap",
        "filtered_historical",
        "stress_overlay",
        "lognormal_baseline",
    ]


def test_generator_result_validates_probability_and_path_outputs() -> None:
    result = _result()

    assert result.p_finish == 0.62
    assert result.p_no_touch == 0.81
    assert result.z_path == 0.42


def test_generator_result_defensively_freezes_diagnostics() -> None:
    diagnostics = {
        "source": {"name": "fixture"},
        "lags": [1, 2],
    }

    result = GeneratorResult(
        p_finish=0.62,
        p_no_touch=0.81,
        z_path=0.42,
        diagnostics=diagnostics,
    )
    diagnostics["source"]["name"] = "mutated"
    diagnostics["lags"].append(3)
    diagnostics["new"] = "leak"

    assert result.diagnostics_json_dict() == {
        "source": {"name": "fixture"},
        "lags": [1, 2],
    }
    with pytest.raises(TypeError):
        result.diagnostics["new"] = "blocked"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("p_finish", -0.01),
        ("p_finish", 1.01),
        ("p_finish", math.nan),
        ("p_no_touch", -0.01),
        ("p_no_touch", math.inf),
        ("z_path", math.nan),
        ("diagnostics", (("not", "a dict"),)),
    ),
)
def test_generator_result_rejects_invalid_fields(field_name: str, invalid_value: object) -> None:
    values = {
        "p_finish": 0.54,
        "p_no_touch": 0.72,
        "z_path": 0.30,
        "diagnostics": {},
    }

    with pytest.raises(ValueError, match=field_name):
        GeneratorResult(**{**values, field_name: invalid_value})


def test_generator_run_carries_metadata_scope_conditioning_and_result() -> None:
    run = GeneratorRun(
        generator_id=GeneratorId.EMPIRICAL_CONDITIONAL,
        generator_name="Empirical conditional",
        generator_version="empirical-v1",
        scope=_scope(),
        conditioning={"seconds_left_bucket": "60-120", "z_path_bucket": "near"},
        result=_result(),
        path_count=10_000,
        steps=60,
        seed=17,
        asof_ts=_asof(),
        diagnostics={"runtime": "offline"},
        weight_seed=0.40,
    )

    assert run.generator_name == "Empirical conditional"
    assert run.generator_version == "empirical-v1"
    assert run.scope == _scope()
    assert run.conditioning == {"seconds_left_bucket": "60-120", "z_path_bucket": "near"}
    assert run.result == _result()
    assert run.p_finish == 0.62
    assert run.p_no_touch == 0.81
    assert run.z_path == 0.42
    assert run.steps == 60
    assert run.weight_seed == 0.40
    assert run.sparse is False
    assert run.fallback_level == "none"


def test_generator_run_defensively_freezes_conditioning_and_diagnostics() -> None:
    conditioning = {"bucket": {"z_path": "near"}, "lags": [1, 2]}
    diagnostics = {"inputs": {"rows": 20}, "warnings": []}

    run = GeneratorRun(
        generator_id=GeneratorId.EMPIRICAL_CONDITIONAL,
        generator_name="Empirical conditional",
        generator_version="empirical-v1",
        scope=_scope(),
        conditioning=conditioning,
        result=_result(),
        path_count=10_000,
        steps=60,
        seed=17,
        asof_ts=_asof(),
        diagnostics=diagnostics,
    )
    conditioning["bucket"]["z_path"] = "mutated"
    conditioning["lags"].append(3)
    diagnostics["inputs"]["rows"] = 99
    diagnostics["warnings"].append("leak")

    assert run.conditioning_json_dict() == {"bucket": {"z_path": "near"}, "lags": [1, 2]}
    assert run.diagnostics_json_dict() == {"inputs": {"rows": 20}, "warnings": []}
    with pytest.raises(TypeError):
        run.conditioning["new"] = "blocked"
    with pytest.raises(TypeError):
        run.diagnostics["new"] = "blocked"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("generator_name", ""),
        ("generator_version", ""),
        ("scope", "BTC"),
        ("conditioning", (("not", "a dict"),)),
        ("result", "not-a-result"),
        ("path_count", 0),
        ("path_count", True),
        ("steps", 0),
        ("steps", True),
        ("seed", 1.5),
        ("seed", True),
        ("diagnostics", (("not", "a dict"),)),
        ("weight_seed", -0.01),
        ("weight_seed", 1.01),
        ("weight_seed", math.nan),
    ),
)
def test_generator_run_rejects_invalid_fields(field_name: str, invalid_value: object) -> None:
    values = {
        "generator_id": GeneratorId.BLOCK_BOOTSTRAP,
        "generator_name": "Block bootstrap",
        "generator_version": "block-v1",
        "scope": _scope(),
        "conditioning": {},
        "result": _result(),
        "path_count": 5000,
        "steps": 60,
        "seed": 23,
        "asof_ts": _asof(),
        "diagnostics": {},
    }

    with pytest.raises(ValueError, match=field_name):
        GeneratorRun(**{**values, field_name: invalid_value})


def test_generator_run_requires_timezone_aware_asof() -> None:
    with pytest.raises(ValueError, match="asof_ts"):
        GeneratorRun(
            generator_id=GeneratorId.FILTERED_HISTORICAL,
            generator_name="Filtered historical",
            generator_version="filtered-v1",
            scope=_scope(),
            conditioning={},
            result=_result(),
            path_count=5000,
            steps=60,
            seed=23,
            asof_ts=datetime(2026, 6, 5, 16, 0),
            diagnostics={},
        )


def test_dynamic_weight_scope_is_hashable() -> None:
    scope = _scope()

    assert {scope: "weights"}[scope] == "weights"


def test_historical_validation_window_can_describe_post_asof_label_period() -> None:
    window = _validation_window()

    assert window.asof_ts == datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc)
    assert window.evaluated_through_ts == datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)
    assert window.label_window_seconds == 3600


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("asof_ts", datetime(2026, 6, 5, 16, 0)),
        ("evaluated_through_ts", datetime(2026, 6, 5, 17, 0)),
        ("evaluated_through_ts", datetime(2026, 6, 5, 15, 59, tzinfo=timezone.utc)),
        ("label_window_seconds", 0),
        ("label_window_seconds", True),
    ),
)
def test_historical_validation_window_rejects_invalid_fields(
    field_name: str,
    invalid_value: object,
) -> None:
    values = {
        "asof_ts": datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc),
        "evaluated_through_ts": datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
        "label_window_seconds": 3600,
    }

    with pytest.raises(ValueError, match=field_name):
        HistoricalValidationWindow(**{**values, field_name: invalid_value})


def test_generator_weight_accepts_optional_score() -> None:
    weight = GeneratorWeight(
        generator_id=GeneratorId.STRESS_OVERLAY,
        weight=0.10,
        scope=_scope(),
        label_count=40,
        source="seed",
        validation_window=_validation_window(),
        score=0.21,
    )

    assert weight.score == 0.21
    assert weight.validation_window == _validation_window()


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("weight", -0.01),
        ("weight", 1.01),
        ("weight", math.nan),
        ("label_count", -1),
        ("label_count", True),
        ("score", math.inf),
    ),
)
def test_generator_weight_rejects_invalid_fields(field_name: str, invalid_value: object) -> None:
    values = {
        "generator_id": GeneratorId.LOGNORMAL_BASELINE,
        "weight": 0.20,
        "scope": _scope(),
        "label_count": 12,
        "source": "fixture",
        "validation_window": _validation_window(),
        "score": 0.33,
    }

    with pytest.raises(ValueError, match=field_name):
        GeneratorWeight(**{**values, field_name: invalid_value})
