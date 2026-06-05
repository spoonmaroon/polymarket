from __future__ import annotations

import importlib
import math
from types import ModuleType
from typing import Any

from polymarket_engine.probability.monte_carlo import _evenly_spaced_indices
from polymarket_engine.probability.monte_carlo import _require_positive_int
from polymarket_engine.probability.monte_carlo import _sampled_path_payload
from polymarket_engine.probability.monte_carlo import _terminal_histogram
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


class CudaUnavailableError(RuntimeError):
    """Raised when the NVIDIA CUDA Monte Carlo path cannot run."""


def run_cuda_monte_carlo(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
) -> ProbabilityOutput:
    """Run the lognormal Monte Carlo baseline on NVIDIA CUDA through CuPy."""
    _require_positive_int(path_count, "path_count")
    _require_positive_int(steps, "steps")
    cp = _load_cupy()

    per_step_sigma = probability_input.sigma_tau / math.sqrt(steps)
    try:
        rng = cp.random.default_rng(seed)
        log_returns = rng.standard_normal(size=(path_count, steps)) * per_step_sigma
        cumulative_returns = cp.cumsum(log_returns, axis=1)
        simulated_prices = probability_input.settlement_price * cp.exp(cumulative_returns)
        start_column = cp.full((path_count, 1), probability_input.settlement_price)
        full_paths = cp.concatenate((start_column, simulated_prices), axis=1)
        if not bool(cp.all(cp.isfinite(full_paths) & (full_paths > 0)).get()):
            raise ValueError("path prices must be positive and finite")

        terminal_prices = full_paths[:, -1]
        terminal_wins_mask = _cuda_satisfies_contract(cp, probability_input, terminal_prices)
        no_touch_wins_mask = cp.all(
            _cuda_satisfies_contract(cp, probability_input, full_paths),
            axis=1,
        )
        terminal_wins = int(cp.sum(terminal_wins_mask).get())
        no_touch_wins = int(cp.sum(no_touch_wins_mask).get())
        preview = _simulation_preview_from_cuda(
            cp,
            probability_input,
            full_paths=full_paths,
            terminal_prices=terminal_prices,
            terminal_wins=terminal_wins,
            no_touch_wins=no_touch_wins,
        )
    except CudaUnavailableError:
        raise
    except ValueError:
        raise
    except Exception as exc:
        raise CudaUnavailableError(f"CuPy/CUDA unavailable: {type(exc).__name__}: {exc}") from exc

    return ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=terminal_wins / path_count,
        p_no_touch=no_touch_wins / path_count,
        z_path=probability_input.z_path,
        model_version="cuda-lognormal-chainlink-sigma-v1",
        seed=seed,
        diagnostics={
            "path_count": path_count,
            "steps": steps,
            "model": "cuda_lognormal_chainlink_sigma",
            "simulation_preview": preview,
        },
    )


def _load_cupy() -> ModuleType:
    try:
        cp = importlib.import_module("cupy")
    except ModuleNotFoundError as exc:
        raise CudaUnavailableError(
            "CuPy/CUDA unavailable: install cupy-cuda13x in an NVIDIA CUDA container"
        ) from exc
    try:
        if int(cp.cuda.runtime.getDeviceCount()) <= 0:
            raise CudaUnavailableError("CuPy/CUDA unavailable: no CUDA devices visible")
    except CudaUnavailableError:
        raise
    except Exception as exc:
        raise CudaUnavailableError(f"CuPy/CUDA unavailable: {type(exc).__name__}: {exc}") from exc
    return cp


def _cuda_satisfies_contract(
    cp: ModuleType,
    probability_input: ProbabilityInput,
    prices: Any,
) -> Any:
    threshold = probability_input.threshold
    if probability_input.comparison_operator == ">":
        return prices > threshold
    if probability_input.comparison_operator == ">=":
        return prices >= threshold
    if probability_input.comparison_operator == "<":
        return prices < threshold
    if probability_input.comparison_operator == "<=":
        return prices <= threshold
    raise ValueError("unsupported comparison_operator")


def _simulation_preview_from_cuda(
    cp: ModuleType,
    probability_input: ProbabilityInput,
    *,
    full_paths: Any,
    terminal_prices: Any,
    terminal_wins: int,
    no_touch_wins: int,
) -> dict[str, Any]:
    path_count = int(full_paths.shape[0])
    point_count = int(full_paths.shape[1])
    sampled_path_indices = _evenly_spaced_indices(path_count, min(24, path_count))
    sampled_point_indices = _evenly_spaced_indices(point_count, min(24, point_count))
    sampled_full_paths = cp.asnumpy(full_paths[list(sampled_path_indices), :])
    terminal_prices_cpu = tuple(float(price) for price in cp.asnumpy(terminal_prices).tolist())
    sampled_path_rows = tuple(
        tuple(float(price) for price in sampled_full_paths[index].tolist())
        for index in range(len(sampled_path_indices))
    )
    return {
        "path_count": path_count,
        "steps": point_count - 1,
        "start_price": probability_input.settlement_price,
        "threshold": probability_input.threshold,
        "comparison_operator": probability_input.comparison_operator,
        "terminal_win_count": terminal_wins,
        "no_touch_win_count": no_touch_wins,
        "sampled_paths": [
            _sampled_path_payload(
                probability_input,
                sampled_path_rows[index],
                path_index=path_index,
                sampled_point_indices=sampled_point_indices,
            )
            for index, path_index in enumerate(sampled_path_indices)
        ],
        "terminal_histogram": _terminal_histogram(terminal_prices_cpu),
    }
