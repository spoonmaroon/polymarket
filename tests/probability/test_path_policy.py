from __future__ import annotations

import math

import pytest

from polymarket_engine.probability.path_policy import runtime_path_count_for_seconds_left
from polymarket_engine.probability.path_policy import runtime_paths_per_seed_for_seconds_left
from polymarket_engine.probability.path_policy import runtime_seed_count_for_seconds_left
from polymarket_engine.probability.path_policy import runtime_total_path_count_for_seconds_left


@pytest.mark.parametrize(
    ("seconds_left", "expected_path_count"),
    (
        (1_200.0, 30_000),
        (901.0, 30_000),
        (900.0, 30_000),
        (750.0, 30_000),
        (601.0, 30_000),
        (600.0, 30_000),
        (450.0, 30_000),
        (301.0, 30_000),
        (300.0, 80_000),
        (121.0, 80_000),
        (120.0, 120_000),
        (31.0, 120_000),
        (30.0, 250_000),
        (1.0, 250_000),
    ),
)
def test_runtime_path_count_policy_reports_total_paths(
    seconds_left: float,
    expected_path_count: int,
) -> None:
    assert runtime_path_count_for_seconds_left(seconds_left) == expected_path_count


@pytest.mark.parametrize("seconds_left", (1.0, 30.0, 120.0, 300.0, 600.0, 900.0, 1_200.0))
def test_runtime_path_count_policy_never_drops_below_ten_thousand(
    seconds_left: float,
) -> None:
    assert runtime_path_count_for_seconds_left(seconds_left) >= 10_000


@pytest.mark.parametrize(
    ("seconds_left", "expected_paths_per_seed", "expected_seed_count", "expected_total"),
    (
        (1_200.0, 10_000, 3, 30_000),
        (600.0, 10_000, 3, 30_000),
        (300.0, 20_000, 4, 80_000),
        (120.0, 30_000, 4, 120_000),
        (30.0, 50_000, 5, 250_000),
    ),
)
def test_runtime_seed_policy_reports_total_paths(
    seconds_left: float,
    expected_paths_per_seed: int,
    expected_seed_count: int,
    expected_total: int,
) -> None:
    assert runtime_paths_per_seed_for_seconds_left(seconds_left) == expected_paths_per_seed
    assert runtime_seed_count_for_seconds_left(seconds_left) == expected_seed_count
    assert runtime_total_path_count_for_seconds_left(seconds_left) == expected_total


@pytest.mark.parametrize("seconds_left", (True, False, math.nan, math.inf, -math.inf))
def test_runtime_total_path_count_policy_rejects_non_finite_inputs(
    seconds_left: float,
) -> None:
    with pytest.raises(ValueError, match="seconds_left must be finite"):
        runtime_total_path_count_for_seconds_left(seconds_left)
