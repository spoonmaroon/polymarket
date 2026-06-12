from __future__ import annotations

from typing import TypedDict

import pytest

from polymarket_engine.probability.cpu_budget import adjust_total_path_budget
from polymarket_engine.probability.cpu_budget import cycle_cpu_percent


class InvalidBudgetKwargs(TypedDict):
    current_total_paths: int
    configured_max_total_paths: int
    min_total_paths: int
    cpu_percent: float
    target_percent: float
    soft_max_percent: float


def test_cycle_cpu_percent_measures_process_time_over_wall_time() -> None:
    assert cycle_cpu_percent(10.0, 10.15, 100.0, 101.0) == 15.0


def test_cycle_cpu_percent_returns_none_for_zero_wall_time() -> None:
    assert cycle_cpu_percent(10.0, 10.15, 100.0, 100.0) is None


def test_cycle_cpu_percent_clamps_negative_process_delta() -> None:
    assert cycle_cpu_percent(10.15, 10.0, 100.0, 101.0) == 0.0


def test_adjust_total_path_budget_reduces_when_cpu_above_soft_max() -> None:
    adjustment = adjust_total_path_budget(
        current_total_paths=40_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=25.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )

    assert adjustment.next_total_paths == 28_000
    assert adjustment.reason == "cpu_above_soft_max"
    assert adjustment.cpu_percent == 25.0
    assert adjustment.target_percent == 15.0
    assert adjustment.soft_max_percent == 20.0


def test_adjust_total_path_budget_increases_when_cpu_below_target() -> None:
    adjustment = adjust_total_path_budget(
        current_total_paths=10_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=9.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )

    assert adjustment.next_total_paths == 11_500
    assert adjustment.reason == "cpu_below_target"


def test_adjust_total_path_budget_recovers_when_runtime_is_inside_budget() -> None:
    adjustment = adjust_total_path_budget(
        current_total_paths=4_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=99.0,
        target_percent=15.0,
        soft_max_percent=20.0,
        cycle_runtime_breached=False,
    )

    assert adjustment.next_total_paths == 4_600
    assert adjustment.reason == "cycle_runtime_inside_budget"


def test_adjust_total_path_budget_stays_when_cpu_inside_band() -> None:
    adjustment = adjust_total_path_budget(
        current_total_paths=10_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=16.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )

    assert adjustment.next_total_paths == 10_000
    assert adjustment.reason == "cpu_inside_band"


def test_adjust_total_path_budget_stays_when_cpu_unmeasured() -> None:
    adjustment = adjust_total_path_budget(
        current_total_paths=10_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=None,
        target_percent=15.0,
        soft_max_percent=20.0,
    )

    assert adjustment.next_total_paths == 10_000
    assert adjustment.reason == "cpu_unmeasured"
    assert adjustment.cpu_percent is None


@pytest.mark.parametrize("cpu_percent", (-1.0, float("nan"), float("inf")))
def test_adjust_total_path_budget_rejects_invalid_cpu_percent(cpu_percent: float) -> None:
    with pytest.raises(ValueError, match="cpu_percent"):
        adjust_total_path_budget(
            current_total_paths=10_000,
            configured_max_total_paths=40_000,
            min_total_paths=4_000,
            cpu_percent=cpu_percent,
            target_percent=15.0,
            soft_max_percent=20.0,
        )


def test_adjust_total_path_budget_respects_minimum() -> None:
    adjustment = adjust_total_path_budget(
        current_total_paths=4_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=25.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )

    assert adjustment.next_total_paths == 4_000
    assert adjustment.reason == "cpu_above_soft_max"


def test_adjust_total_path_budget_respects_ceiling() -> None:
    adjustment = adjust_total_path_budget(
        current_total_paths=40_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=9.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )

    assert adjustment.next_total_paths == 40_000
    assert adjustment.reason == "cpu_inside_band"


@pytest.mark.parametrize(
    "kwargs",
    (
        {
            "current_total_paths": 10_000,
            "configured_max_total_paths": 0,
            "min_total_paths": 4_000,
            "cpu_percent": 16.0,
            "target_percent": 15.0,
            "soft_max_percent": 20.0,
        },
        {
            "current_total_paths": 10_000,
            "configured_max_total_paths": 40_000,
            "min_total_paths": 0,
            "cpu_percent": 16.0,
            "target_percent": 15.0,
            "soft_max_percent": 20.0,
        },
        {
            "current_total_paths": 10_000,
            "configured_max_total_paths": 40_000,
            "min_total_paths": 50_000,
            "cpu_percent": 16.0,
            "target_percent": 15.0,
            "soft_max_percent": 20.0,
        },
        {
            "current_total_paths": 10_000,
            "configured_max_total_paths": 40_000,
            "min_total_paths": 4_000,
            "cpu_percent": 16.0,
            "target_percent": 0.0,
            "soft_max_percent": 20.0,
        },
        {
            "current_total_paths": 10_000,
            "configured_max_total_paths": 40_000,
            "min_total_paths": 4_000,
            "cpu_percent": 16.0,
            "target_percent": 15.0,
            "soft_max_percent": 10.0,
        },
    ),
)
def test_adjust_total_path_budget_validates_inputs(kwargs: InvalidBudgetKwargs) -> None:
    with pytest.raises(ValueError):
        adjust_total_path_budget(**kwargs)
