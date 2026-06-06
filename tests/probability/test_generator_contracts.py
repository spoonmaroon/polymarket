from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from polymarket_engine.probability.generator_contracts import (
    DynamicWeightScope,
    GeneratorId,
    GeneratorRun,
    generator_runs_to_json,
)


def test_generator_run_requires_probability_values() -> None:
    with pytest.raises(ValueError, match="p_finish"):
        GeneratorRun(
            generator_id=GeneratorId.EMPIRICAL_CONDITIONAL,
            p_finish=1.2,
            p_no_touch=0.4,
            path_count=1024,
            effective_path_count=900,
            seed=1,
            asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
            runtime_ms=12.5,
            sparse=False,
            diagnostics={},
        )


def test_generator_scope_is_hashable() -> None:
    scope = DynamicWeightScope(
        asset="BTC",
        horizon_seconds=300,
        seconds_left_bucket="120-180",
        z_path_bucket="0.50-1.00",
        vol_regime="normal",
        vol_trend="flat",
        wick_regime="quiet",
        source_quality_state="ok",
    )

    assert {scope: "weights"}[scope] == "weights"


def test_generator_runs_to_json_is_stable_and_strict() -> None:
    run = GeneratorRun(
        generator_id=GeneratorId.BLOCK_BOOTSTRAP,
        p_finish=0.61,
        p_no_touch=0.58,
        path_count=2048,
        effective_path_count=1900,
        seed=17,
        asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        runtime_ms=8.25,
        sparse=False,
        diagnostics={"bucket": "btc-5m"},
    )

    payload = generator_runs_to_json((run,))

    assert payload[0]["generator_id"] == "block_bootstrap"
    assert payload[0]["asof_ts"] == "2026-06-06T00:00:00+00:00"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    (
        ({"generator_id": "block_bootstrap"}, "generator_id"),
        ({"path_count": 0}, "path_count"),
        ({"effective_path_count": -1}, "effective_path_count"),
        ({"runtime_ms": float("inf")}, "runtime_ms"),
        ({"asof_ts": "bad"}, "asof_ts"),
        ({"asof_ts": datetime(2026, 6, 6)}, "asof_ts"),
        ({"diagnostics": {1: "bad"}}, "diagnostics"),
        ({"diagnostics": {"nested": {1: "bad"}}}, "diagnostics"),
        ({"diagnostics": {"nested": ({1: "bad"},)}}, "diagnostics"),
        ({"diagnostics": {"bad": float("nan")}}, "diagnostics"),
    ),
)
def test_generator_run_validates_contract_fields(
    kwargs: dict[str, Any],
    match: str,
) -> None:
    values = {
        "generator_id": GeneratorId.LOGNORMAL_CONTROL,
        "p_finish": 0.5,
        "p_no_touch": 0.5,
        "path_count": 1024,
        "effective_path_count": 1024,
        "seed": None,
        "asof_ts": datetime(2026, 6, 6, tzinfo=UTC),
        "runtime_ms": 10.0,
        "sparse": False,
        "diagnostics": {},
    }

    with pytest.raises(ValueError, match=match):
        GeneratorRun(**{**values, **kwargs})


@pytest.mark.parametrize(
    ("kwargs", "match"),
    (
        ({"asset": "SOL"}, "asset"),
        ({"horizon_seconds": 0}, "horizon_seconds"),
        ({"seconds_left_bucket": ""}, "seconds_left_bucket"),
        ({"seconds_left_bucket": ["120-180"]}, "seconds_left_bucket"),
        ({"vol_regime": ""}, "vol_regime"),
    ),
)
def test_dynamic_weight_scope_validates_bucket_fields(
    kwargs: dict[str, Any],
    match: str,
) -> None:
    values = {
        "asset": "ETH",
        "horizon_seconds": 900,
        "seconds_left_bucket": "300-600",
        "z_path_bucket": "0.00-0.50",
        "vol_regime": "normal",
        "vol_trend": "flat",
        "wick_regime": "quiet",
        "source_quality_state": "ok",
    }

    with pytest.raises(ValueError, match=match):
        DynamicWeightScope(**{**values, **kwargs})


def test_generator_runs_to_json_rejects_non_finite_nested_values() -> None:
    run = GeneratorRun(
        generator_id=GeneratorId.FILTERED_HISTORICAL,
        p_finish=0.50,
        p_no_touch=0.48,
        path_count=1024,
        effective_path_count=1000,
        seed=3,
        asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        runtime_ms=5.0,
        sparse=False,
        diagnostics={"nested": {"ok": 1.0}},
    )
    object.__setattr__(run, "diagnostics", {"nested": {"bad": float("nan")}})

    with pytest.raises(ValueError):
        generator_runs_to_json((run,))
