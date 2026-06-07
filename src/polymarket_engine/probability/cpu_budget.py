from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CpuBudgetAdjustment:
    next_total_paths: int
    reason: str
    cpu_percent: float | None
    target_percent: float
    soft_max_percent: float


def cycle_cpu_percent(
    start_process_seconds: float,
    end_process_seconds: float,
    start_monotonic_seconds: float,
    end_monotonic_seconds: float,
) -> float | None:
    wall_seconds = end_monotonic_seconds - start_monotonic_seconds
    if wall_seconds <= 0.0:
        return None

    process_seconds = max(0.0, end_process_seconds - start_process_seconds)
    return round((process_seconds / wall_seconds) * 100.0, 3)


def adjust_total_path_budget(
    *,
    current_total_paths: int,
    configured_max_total_paths: int,
    min_total_paths: int,
    cpu_percent: float | None,
    target_percent: float,
    soft_max_percent: float,
) -> CpuBudgetAdjustment:
    _validate_budget_inputs(
        configured_max_total_paths=configured_max_total_paths,
        min_total_paths=min_total_paths,
        target_percent=target_percent,
        soft_max_percent=soft_max_percent,
    )
    current = _bound(current_total_paths, min_total_paths, configured_max_total_paths)
    if cpu_percent is not None and (not math.isfinite(cpu_percent) or cpu_percent < 0.0):
        raise ValueError("cpu_percent must be non-negative and finite")

    if cpu_percent is None:
        return CpuBudgetAdjustment(
            next_total_paths=current,
            reason="cpu_unmeasured",
            cpu_percent=cpu_percent,
            target_percent=target_percent,
            soft_max_percent=soft_max_percent,
        )

    if cpu_percent > soft_max_percent:
        next_total_paths = _bound(
            int(current * 0.70),
            min_total_paths,
            configured_max_total_paths,
        )
        return CpuBudgetAdjustment(
            next_total_paths=next_total_paths,
            reason="cpu_above_soft_max",
            cpu_percent=cpu_percent,
            target_percent=target_percent,
            soft_max_percent=soft_max_percent,
        )

    if cpu_percent < target_percent * 0.80 and current < configured_max_total_paths:
        next_total_paths = _bound(
            int(current * 1.15),
            min_total_paths,
            configured_max_total_paths,
        )
        return CpuBudgetAdjustment(
            next_total_paths=next_total_paths,
            reason="cpu_below_target",
            cpu_percent=cpu_percent,
            target_percent=target_percent,
            soft_max_percent=soft_max_percent,
        )

    return CpuBudgetAdjustment(
        next_total_paths=current,
        reason="cpu_inside_band",
        cpu_percent=cpu_percent,
        target_percent=target_percent,
        soft_max_percent=soft_max_percent,
    )


def _validate_budget_inputs(
    *,
    configured_max_total_paths: int,
    min_total_paths: int,
    target_percent: float,
    soft_max_percent: float,
) -> None:
    if configured_max_total_paths <= 0:
        raise ValueError("configured_max_total_paths must be positive")
    if min_total_paths <= 0:
        raise ValueError("min_total_paths must be positive")
    if min_total_paths > configured_max_total_paths:
        raise ValueError("min_total_paths must be <= configured_max_total_paths")
    if target_percent <= 0.0:
        raise ValueError("target_percent must be positive")
    if soft_max_percent < target_percent:
        raise ValueError("soft_max_percent must be >= target_percent")


def _bound(value: int, minimum: int, maximum: int) -> int:
    return min(max(value, minimum), maximum)
