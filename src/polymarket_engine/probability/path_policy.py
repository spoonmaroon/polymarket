from __future__ import annotations

import math


def runtime_paths_per_seed_for_seconds_left(seconds_left: float) -> int:
    value = _finite_seconds_left(seconds_left)
    if value <= 30:
        return 50_000
    if value <= 120:
        return 30_000
    if value <= 300:
        return 20_000
    return 10_000


def runtime_seed_count_for_seconds_left(seconds_left: float) -> int:
    value = _finite_seconds_left(seconds_left)
    if value <= 30:
        return 5
    if value <= 300:
        return 4
    return 3


def runtime_total_path_count_for_seconds_left(seconds_left: float) -> int:
    return (
        runtime_paths_per_seed_for_seconds_left(seconds_left)
        * runtime_seed_count_for_seconds_left(seconds_left)
    )


def runtime_path_count_for_seconds_left(seconds_left: float) -> int:
    return runtime_total_path_count_for_seconds_left(seconds_left)


def _finite_seconds_left(seconds_left: float) -> float:
    if isinstance(seconds_left, bool):
        raise ValueError("seconds_left must be finite")
    value = float(seconds_left)
    if not math.isfinite(value):
        raise ValueError("seconds_left must be finite")
    return value
