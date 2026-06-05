from __future__ import annotations

import math


def runtime_path_count_for_seconds_left(seconds_left: float) -> int:
    if isinstance(seconds_left, bool):
        raise ValueError("seconds_left must be finite")
    value = float(seconds_left)
    if not math.isfinite(value):
        raise ValueError("seconds_left must be finite")
    if value <= 30:
        return 50_000
    if value <= 120:
        return 30_000
    if value <= 300:
        return 20_000
    return 10_000
