from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput

MAX_PREVIEW_PATHS = 24
MAX_PREVIEW_POINTS = 24
TERMINAL_HISTOGRAM_BINS = 16


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
    steps = len(path_rows[0]) - 1
    return ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=counts["terminal_wins"] / path_count,
        p_no_touch=counts["no_touch_wins"] / path_count,
        z_path=probability_input.z_path,
        model_version=model_version,
        seed=seed,
        diagnostics={"path_count": path_count, "steps": steps, "model": "explicit_paths"},
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
    with np.errstate(over="ignore", invalid="ignore"):
        simulated_prices = probability_input.settlement_price * np.exp(cumulative_returns)
    start_column = np.full((path_count, 1), probability_input.settlement_price)
    full_paths = np.concatenate((start_column, simulated_prices), axis=1)
    path_rows = _validate_paths(tuple(tuple(float(price) for price in row) for row in full_paths))

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
            "simulation_preview": _simulation_preview(
                probability_input,
                path_rows=path_rows,
                counts=counts,
            ),
        },
    )


def _validate_paths(paths: Iterable[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    path_rows = tuple(tuple(row) for row in paths)
    if not path_rows:
        raise ValueError("paths must contain at least one path")

    expected_length = len(path_rows[0])
    for path in path_rows:
        if not path:
            raise ValueError("path rows must contain at least one price")
        if len(path) != expected_length:
            raise ValueError("path rows must have the same length")
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
        terminal_wins += int(_price_satisfies_contract(probability_input, final_price))
        no_touch_wins += int(
            all(_price_satisfies_contract(probability_input, price) for price in path)
        )
    return {"terminal_wins": terminal_wins, "no_touch_wins": no_touch_wins}


def _simulation_preview(
    probability_input: ProbabilityInput,
    *,
    path_rows: tuple[tuple[float, ...], ...],
    counts: dict[str, int],
) -> dict[str, Any]:
    sampled_path_indices = _evenly_spaced_indices(
        len(path_rows),
        min(MAX_PREVIEW_PATHS, len(path_rows)),
    )
    sampled_point_indices = _evenly_spaced_indices(
        len(path_rows[0]),
        min(MAX_PREVIEW_POINTS, len(path_rows[0])),
    )
    terminal_prices = tuple(path[-1] for path in path_rows)
    return {
        "path_count": len(path_rows),
        "steps": len(path_rows[0]) - 1,
        "start_price": probability_input.settlement_price,
        "threshold": probability_input.threshold,
        "comparison_operator": probability_input.comparison_operator,
        "terminal_win_count": counts["terminal_wins"],
        "no_touch_win_count": counts["no_touch_wins"],
        "sampled_paths": [
            _sampled_path_payload(
                probability_input,
                path_rows[path_index],
                path_index=path_index,
                sampled_point_indices=sampled_point_indices,
            )
            for path_index in sampled_path_indices
        ],
        "terminal_histogram": _terminal_histogram(terminal_prices),
    }


def _sampled_path_payload(
    probability_input: ProbabilityInput,
    path: tuple[float, ...],
    *,
    path_index: int,
    sampled_point_indices: tuple[int, ...],
) -> dict[str, Any]:
    return {
        "index": path_index,
        "terminal_win": _price_satisfies_contract(probability_input, path[-1]),
        "no_touch_win": all(_price_satisfies_contract(probability_input, price) for price in path),
        "points": [path[index] for index in sampled_point_indices],
    }


def _terminal_histogram(terminal_prices: tuple[float, ...]) -> list[dict[str, Any]]:
    lower_bound = min(terminal_prices)
    upper_bound = max(terminal_prices)
    if lower_bound == upper_bound:
        return [{"lower": lower_bound, "upper": upper_bound, "count": len(terminal_prices)}]

    bin_count = min(TERMINAL_HISTOGRAM_BINS, len(terminal_prices))
    width = (upper_bound - lower_bound) / bin_count
    counts = [0] * bin_count
    for price in terminal_prices:
        index = min(bin_count - 1, int((price - lower_bound) / width))
        counts[index] += 1
    return [
        {
            "lower": lower_bound + width * index,
            "upper": lower_bound + width * (index + 1),
            "count": count,
        }
        for index, count in enumerate(counts)
    ]


def _evenly_spaced_indices(length: int, count: int) -> tuple[int, ...]:
    if count <= 0:
        return ()
    if count >= length:
        return tuple(range(length))
    if count == 1:
        return (0,)
    return tuple(round(index * (length - 1) / (count - 1)) for index in range(count))


def _price_satisfies_contract(probability_input: ProbabilityInput, price: float) -> bool:
    threshold = probability_input.threshold
    if probability_input.comparison_operator == ">":
        return price > threshold
    if probability_input.comparison_operator == ">=":
        return price >= threshold
    if probability_input.comparison_operator == "<":
        return price < threshold
    if probability_input.comparison_operator == "<=":
        return price <= threshold
    raise ValueError("unsupported comparison_operator")


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
