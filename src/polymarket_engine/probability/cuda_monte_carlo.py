from __future__ import annotations

import importlib
import math
import statistics
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


def run_cuda_monte_carlo_multi_seed(
    probability_input: ProbabilityInput,
    *,
    paths_per_seed: int,
    steps: int,
    seed: int,
    seed_count: int,
) -> ProbabilityOutput:
    _require_positive_int(paths_per_seed, "paths_per_seed")
    _require_positive_int(seed_count, "seed_count")
    outputs = [
        run_cuda_monte_carlo(
            probability_input,
            path_count=paths_per_seed,
            steps=steps,
            seed=run_seed,
        )
        for run_seed in _seed_sequence(seed, seed_count)
    ]
    p_finish_values = [output.p_finish for output in outputs]
    p_no_touch_values = [output.p_no_touch for output in outputs]
    p_hat = statistics.fmean(p_finish_values)
    p_no_touch = statistics.fmean(p_no_touch_values)
    p_hat_std = statistics.stdev(p_finish_values) if len(p_finish_values) > 1 else 0.0
    standard_error = p_hat_std / math.sqrt(len(p_finish_values)) if p_finish_values else 0.0
    ci_half_width = 1.96 * standard_error
    total_path_count = paths_per_seed * seed_count
    first_diagnostics = dict(outputs[0].diagnostics)
    diagnostics = {
        "path_count": total_path_count,
        "paths_per_seed": paths_per_seed,
        "seed_count": seed_count,
        "steps": steps,
        "model": "cuda_lognormal_chainlink_sigma_multi_seed",
        "p_hat": p_hat,
        "p_hat_std": p_hat_std,
        "p_hat_ci_low": max(0.0, p_hat - ci_half_width),
        "p_hat_ci_high": min(1.0, p_hat + ci_half_width),
        "p_no_touch_mean": p_no_touch,
        "seed_runs": [
            {
                "seed": output.seed,
                "p_hat": output.p_finish,
                "p_no_touch": output.p_no_touch,
                "path_count": int(output.diagnostics["path_count"]),
            }
            for output in outputs
        ],
        "simulation_preview": first_diagnostics.get("simulation_preview"),
        "prior_sensitivity": first_diagnostics.get("prior_sensitivity", []),
    }
    return ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=p_hat,
        p_no_touch=p_no_touch,
        z_path=probability_input.z_path,
        model_version="cuda-lognormal-chainlink-sigma-multiseed-v1",
        seed=seed,
        diagnostics=diagnostics,
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


def _seed_sequence(seed: int, seed_count: int) -> tuple[int, ...]:
    _require_positive_int(seed_count, "seed_count")
    return tuple(seed + index * 11 for index in range(seed_count))


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
