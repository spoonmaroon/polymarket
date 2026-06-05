import math
from datetime import datetime, timezone

import pytest

from polymarket_engine.probability.generator_contracts import (
    DynamicWeightScope,
    GeneratorId,
    GeneratorRun,
    GeneratorWeight,
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


def test_generator_id_values_match_contract() -> None:
    assert [generator.value for generator in GeneratorId] == [
        "empirical_conditional",
        "block_bootstrap",
        "filtered_historical",
        "stress_overlay",
        "lognormal_baseline",
    ]


def test_generator_run_accepts_valid_probabilities_and_defaults() -> None:
    run = GeneratorRun(
        generator_id=GeneratorId.EMPIRICAL_CONDITIONAL,
        p_finish=0.62,
        p_no_touch=0.81,
        path_count=10_000,
        seed=17,
        asof_ts=_asof(),
        diagnostics={"source": "fixture"},
    )

    assert run.sparse is False
    assert run.fallback_level == "none"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("p_finish", -0.01),
        ("p_finish", 1.01),
        ("p_finish", math.nan),
        ("p_no_touch", -0.01),
        ("p_no_touch", math.inf),
        ("path_count", 0),
        ("path_count", True),
        ("seed", 1.5),
        ("seed", True),
        ("diagnostics", (("not", "a dict"),)),
    ),
)
def test_generator_run_rejects_invalid_fields(field_name: str, invalid_value: object) -> None:
    values = {
        "generator_id": GeneratorId.BLOCK_BOOTSTRAP,
        "p_finish": 0.54,
        "p_no_touch": 0.72,
        "path_count": 5000,
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
            p_finish=0.54,
            p_no_touch=0.72,
            path_count=5000,
            seed=23,
            asof_ts=datetime(2026, 6, 5, 16, 0),
            diagnostics={},
        )


def test_dynamic_weight_scope_is_hashable() -> None:
    scope = _scope()

    assert {scope: "weights"}[scope] == "weights"


def test_generator_weight_accepts_optional_score() -> None:
    weight = GeneratorWeight(
        generator_id=GeneratorId.STRESS_OVERLAY,
        weight=0.10,
        scope=_scope(),
        label_count=40,
        source="seed",
        score=0.21,
    )

    assert weight.score == 0.21


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
        "score": 0.33,
    }

    with pytest.raises(ValueError, match=field_name):
        GeneratorWeight(**{**values, field_name: invalid_value})
