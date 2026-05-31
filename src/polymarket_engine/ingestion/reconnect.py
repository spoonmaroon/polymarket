from __future__ import annotations

import random


def compute_reconnect_delay(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    jitter_pct: float = 0.25,
    random_value: float | None = None,
) -> float:
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    if base <= 0:
        raise ValueError("base must be > 0")
    if cap <= 0:
        raise ValueError("cap must be > 0")
    if jitter_pct < 0:
        raise ValueError("jitter_pct must be >= 0")

    unclipped = base * (2**attempt)
    delay = min(cap, unclipped)
    u = float(random.random() if random_value is None else random_value)
    jitter = delay * jitter_pct * ((u * 2.0) - 1.0)
    return float(max(0.0, delay + jitter))
