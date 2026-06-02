from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


def score_paths(
    probability_input: ProbabilityInput,
    *,
    paths: Iterable[Sequence[float]],
    model_version: str,
    seed: int | None,
) -> ProbabilityOutput:
    """Score explicit offline Monte Carlo paths against a probability input."""
    path_rows = _validate_paths(paths)
    counts = _score_validated_paths(probability_input, path_rows)
    path_count = len(path_rows)
    return ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=counts["terminal_wins"] / path_count,
        p_no_touch=counts["no_touch_wins"] / path_count,
        z_path=probability_input.z_path,
        model_version=model_version,
        seed=seed,
        diagnostics={"path_count": path_count, "model": "explicit_paths"},
    )


def run_seeded_monte_carlo(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
) -> ProbabilityOutput:
    """Run a deterministic offline lognormal baseline from as-of state."""
    _require_positive_int(path_count, "path_count")
    _require_positive_int(steps, "steps")

    rng = np.random.default_rng(seed)
    per_step_sigma = probability_input.sigma_tau / math.sqrt(steps)
    log_returns = rng.normal(0.0, per_step_sigma, size=(path_count, steps))
    cumulative_returns = np.cumsum(log_returns, axis=1)
    simulated_prices = probability_input.settlement_price * np.exp(cumulative_returns)
    start_column = np.full((path_count, 1), probability_input.settlement_price)
    full_paths = np.concatenate((start_column, simulated_prices), axis=1)
    path_rows = tuple(tuple(float(price) for price in row) for row in full_paths)

    counts = _score_validated_paths(probability_input, path_rows)
    return ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=counts["terminal_wins"] / path_count,
        p_no_touch=counts["no_touch_wins"] / path_count,
        z_path=probability_input.z_path,
        model_version="offline-lognormal-chainlink-sigma-v1",
        seed=seed,
        diagnostics={
            "path_count": path_count,
            "steps": steps,
            "model": "offline_lognormal_chainlink_sigma",
        },
    )


def _validate_paths(paths: Iterable[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    path_rows = tuple(tuple(row) for row in paths)
    if not path_rows:
        raise ValueError("paths must contain at least one path")

    for path in path_rows:
        if not path:
            raise ValueError("path rows must contain at least one price")
        for price in path:
            if not _is_positive_finite_number(price):
                raise ValueError("path prices must be positive and finite")
    return path_rows


def _score_validated_paths(
    probability_input: ProbabilityInput,
    paths: tuple[tuple[float, ...], ...],
) -> dict[str, int]:
    terminal_wins = 0
    no_touch_wins = 0
    for path in paths:
        final_price = path[-1]
        remaining_prices = path[1:]
        if probability_input.side == "UP":
            terminal_wins += int(final_price >= probability_input.threshold)
            no_touch_wins += int(all(price >= probability_input.threshold for price in remaining_prices))
        else:
            terminal_wins += int(final_price < probability_input.threshold)
            no_touch_wins += int(all(price < probability_input.threshold for price in remaining_prices))
    return {"terminal_wins": terminal_wins, "no_touch_wins": no_touch_wins}


def _require_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _is_positive_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )
