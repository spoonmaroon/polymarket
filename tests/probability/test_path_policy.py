from __future__ import annotations

import pytest

from polymarket_engine.probability.path_policy import runtime_path_count_for_seconds_left


@pytest.mark.parametrize(
    ("seconds_left", "expected_path_count"),
    (
        (1_200.0, 10_000),
        (901.0, 10_000),
        (900.0, 10_000),
        (750.0, 10_000),
        (601.0, 10_000),
        (600.0, 10_000),
        (450.0, 10_000),
        (301.0, 10_000),
        (300.0, 20_000),
        (121.0, 20_000),
        (120.0, 30_000),
        (31.0, 30_000),
        (30.0, 50_000),
        (1.0, 50_000),
    ),
)
def test_runtime_path_count_policy_scales_by_seconds_left(
    seconds_left: float,
    expected_path_count: int,
) -> None:
    assert runtime_path_count_for_seconds_left(seconds_left) == expected_path_count


@pytest.mark.parametrize("seconds_left", (1.0, 30.0, 120.0, 300.0, 600.0, 900.0, 1_200.0))
def test_runtime_path_count_policy_never_drops_below_ten_thousand(
    seconds_left: float,
) -> None:
    assert runtime_path_count_for_seconds_left(seconds_left) >= 10_000
