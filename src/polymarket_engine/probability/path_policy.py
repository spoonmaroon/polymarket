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


def runtime_path_count_for_state(
    *,
    seconds_left: float,
    z_path: float,
    executable_price: float | None,
    wave_phase: str,
) -> int:
    seconds = _finite_seconds_left(seconds_left)
    price = 0.5 if executable_price is None else float(executable_price)
    if not math.isfinite(price):
        raise ValueError("executable_price must be finite")
    phase = wave_phase.lower()

    if phase == "missed" and price >= 0.96:
        return 30_000
    if phase in {"breaking", "late"} and 0.90 <= price < 0.96:
        return max(runtime_path_count_for_seconds_left(seconds), 250_000)
    if phase == "forming" or abs(float(z_path)) <= 0.35:
        return max(runtime_path_count_for_seconds_left(seconds), 80_000)
    if abs(float(z_path)) >= 1.5 and price <= 0.25 and seconds > 120.0:
        return 30_000
    return runtime_path_count_for_seconds_left(seconds)


def _finite_seconds_left(seconds_left: float) -> float:
    if isinstance(seconds_left, bool):
        raise ValueError("seconds_left must be finite")
    value = float(seconds_left)
    if not math.isfinite(value):
        raise ValueError("seconds_left must be finite")
    return value
