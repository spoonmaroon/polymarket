from __future__ import annotations

import importlib
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from types import ModuleType
from typing import Any

from polymarket_engine.probability.monte_carlo import _require_positive_int
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


class CudaUnavailableError(RuntimeError):
    """Raised when the NVIDIA CUDA Monte Carlo path cannot run."""


TERMINAL_HISTOGRAM_BINS = 16


@dataclass(frozen=True)
class _CudaSimulationKey:
    asset: str
    asof_ts: datetime
    settlement_price: float
    sigma_tau: float
    seconds_left_bucket: int
    steps: int


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
            terminal_wins_mask=terminal_wins_mask,
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
            "prior_sensitivity": preview.get("prior_sensitivity", []),
        },
    )


def run_cuda_monte_carlo_batch(
    probability_inputs: Sequence[ProbabilityInput],
    *,
    paths_per_seed: int,
    steps: int,
    seed: int,
    seed_count: int,
) -> tuple[ProbabilityOutput, ...]:
    """Run CUDA paths once per asset/as-of group and score every contract in that group."""
    if not probability_inputs:
        raise ValueError("probability_inputs must not be empty")
    _require_positive_int(paths_per_seed, "paths_per_seed")
    _require_positive_int(steps, "steps")
    _require_positive_int(seed_count, "seed_count")

    groups: dict[_CudaSimulationKey, list[ProbabilityInput]] = {}
    ordered_state_ids = tuple(item.state_id for item in probability_inputs)
    inputs_by_state_id = {item.state_id: item for item in probability_inputs}
    outputs_by_state_id: dict[str, list[ProbabilityOutput]] = {
        item.state_id: [] for item in probability_inputs
    }
    for item in probability_inputs:
        groups.setdefault(_simulation_key(item, steps=steps), []).append(item)

    for run_seed in _seed_sequence(seed, seed_count):
        for group_inputs in groups.values():
            seed_outputs = _run_cuda_monte_carlo_seed_batch(
                tuple(group_inputs),
                path_count=paths_per_seed,
                steps=steps,
                seed=run_seed,
            )
            for output in seed_outputs:
                outputs_by_state_id[output.state_id].append(output)

    return tuple(
        _aggregate_batch_seed_outputs(
            probability_input=inputs_by_state_id[state_id],
            outputs=tuple(outputs_by_state_id[state_id]),
            paths_per_seed=paths_per_seed,
            steps=steps,
            seed=seed,
            seed_count=seed_count,
        )
        for state_id in ordered_state_ids
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
    prior_sensitivity = _aggregate_prior_sensitivity_rows(
        tuple(tuple(output.diagnostics.get("prior_sensitivity", [])) for output in outputs)
    )
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
        "prior_sensitivity": prior_sensitivity,
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


def _simulation_key(probability_input: ProbabilityInput, *, steps: int) -> _CudaSimulationKey:
    return _CudaSimulationKey(
        asset=probability_input.asset,
        asof_ts=probability_input.asof_ts,
        settlement_price=probability_input.settlement_price,
        sigma_tau=probability_input.sigma_tau,
        seconds_left_bucket=int(round(probability_input.seconds_left)),
        steps=steps,
    )


def _run_cuda_monte_carlo_seed_batch(
    probability_inputs: Sequence[ProbabilityInput],
    *,
    path_count: int,
    steps: int,
    seed: int,
) -> tuple[ProbabilityOutput, ...]:
    if not probability_inputs:
        raise ValueError("probability_inputs must not be empty")
    _require_positive_int(path_count, "path_count")
    _require_positive_int(steps, "steps")
    first = probability_inputs[0]
    reference_key = _simulation_key(first, steps=steps)
    for item in probability_inputs[1:]:
        if _simulation_key(item, steps=steps) != reference_key:
            raise ValueError("probability_inputs in a CUDA seed batch must share simulation inputs")

    cp = _load_cupy()
    per_step_sigma = first.sigma_tau / math.sqrt(steps)
    try:
        rng = cp.random.default_rng(seed)
        log_returns = rng.standard_normal(size=(path_count, steps)) * per_step_sigma
        cumulative_returns = cp.cumsum(log_returns, axis=1)
        simulated_prices = first.settlement_price * cp.exp(cumulative_returns)
        start_column = cp.full((path_count, 1), first.settlement_price)
        full_paths = cp.concatenate((start_column, simulated_prices), axis=1)
        if not bool(cp.all(cp.isfinite(full_paths) & (full_paths > 0)).get()):
            raise ValueError("path prices must be positive and finite")

        terminal_prices = full_paths[:, -1]
        outputs: list[ProbabilityOutput] = []
        for item in probability_inputs:
            terminal_wins_mask = _cuda_satisfies_contract(cp, item, terminal_prices)
            no_touch_wins_mask = cp.all(
                _cuda_satisfies_contract(cp, item, full_paths),
                axis=1,
            )
            terminal_wins = int(cp.sum(terminal_wins_mask).get())
            no_touch_wins = int(cp.sum(no_touch_wins_mask).get())
            preview = _simulation_preview_from_cuda(
                cp,
                item,
                full_paths=full_paths,
                terminal_prices=terminal_prices,
                terminal_wins_mask=terminal_wins_mask,
                terminal_wins=terminal_wins,
                no_touch_wins=no_touch_wins,
            )
            outputs.append(
                ProbabilityOutput(
                    state_id=item.state_id,
                    asof_ts=item.asof_ts,
                    p_finish=terminal_wins / path_count,
                    p_no_touch=no_touch_wins / path_count,
                    z_path=item.z_path,
                    model_version="cuda-lognormal-chainlink-sigma-v1",
                    seed=seed,
                    diagnostics={
                        "path_count": path_count,
                        "steps": steps,
                        "model": "cuda_lognormal_chainlink_sigma",
                        "simulation_preview": preview,
                        "prior_sensitivity": preview.get("prior_sensitivity", []),
                        "batch_group_key": reference_key.asset,
                    },
                )
            )
    except CudaUnavailableError:
        raise
    except ValueError:
        raise
    except Exception as exc:
        raise CudaUnavailableError(f"CuPy/CUDA unavailable: {type(exc).__name__}: {exc}") from exc
    return tuple(outputs)


def _aggregate_batch_seed_outputs(
    *,
    probability_input: ProbabilityInput,
    outputs: tuple[ProbabilityOutput, ...],
    paths_per_seed: int,
    steps: int,
    seed: int,
    seed_count: int,
) -> ProbabilityOutput:
    if len(outputs) != seed_count:
        raise ValueError("seed output count must match seed_count")
    p_finish_values = [output.p_finish for output in outputs]
    p_no_touch_values = [output.p_no_touch for output in outputs]
    p_hat = statistics.fmean(p_finish_values)
    p_no_touch = statistics.fmean(p_no_touch_values)
    p_hat_std = statistics.stdev(p_finish_values) if len(p_finish_values) > 1 else 0.0
    standard_error = p_hat_std / math.sqrt(len(p_finish_values)) if p_finish_values else 0.0
    ci_half_width = 1.96 * standard_error
    total_path_count = paths_per_seed * seed_count
    first_diagnostics = dict(outputs[0].diagnostics)
    prior_sensitivity = _aggregate_prior_sensitivity_rows(
        tuple(tuple(output.diagnostics.get("prior_sensitivity", [])) for output in outputs)
    )
    batch_group_key = first_diagnostics.get("batch_group_key")
    return ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=p_hat,
        p_no_touch=p_no_touch,
        z_path=probability_input.z_path,
        model_version="cuda-lognormal-chainlink-sigma-batch-v1",
        seed=seed,
        diagnostics={
            "path_count": total_path_count,
            "paths_per_seed": paths_per_seed,
            "seed_count": seed_count,
            "steps": steps,
            "model": "cuda_lognormal_chainlink_sigma_batch",
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
            "prior_sensitivity": prior_sensitivity,
            "batch_group_key": batch_group_key,
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
    terminal_wins_mask: Any,
    terminal_wins: int,
    no_touch_wins: int,
) -> dict[str, Any]:
    path_count = int(full_paths.shape[0])
    point_count = int(full_paths.shape[1])
    sampled_path_indices = _evenly_spaced_indices(path_count, min(24, path_count))
    sampled_point_indices = _evenly_spaced_indices(point_count, min(24, point_count))
    sampled_full_paths = cp.asnumpy(full_paths[list(sampled_path_indices), :])
    terminal_prices_cpu = _float_tuple_from_cpu_row(cp.asnumpy(terminal_prices))
    sampled_path_rows = tuple(
        _float_tuple_from_cpu_row(sampled_full_paths[index])
        for index in range(len(sampled_path_indices))
    )
    sensitivity_count = min(2048, path_count)
    terminal_wins_cpu = tuple(
        bool(value) for value in _to_cpu_list(cp.asnumpy(terminal_wins_mask[:sensitivity_count]))
    )
    sensitivity_paths = tuple(
        _float_tuple_from_cpu_row(row) for row in cp.asnumpy(full_paths[:sensitivity_count, :])
    )
    sensitivity_terminal_wins = terminal_wins_cpu[: len(sensitivity_paths)]
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
        "prior_sensitivity": _prior_sensitivity_from_cpu_paths(
            probability_input,
            paths=sensitivity_paths,
            terminal_wins=sensitivity_terminal_wins,
        ),
    }


def _to_cpu_list(values: Any) -> Any:
    if hasattr(values, "tolist"):
        return values.tolist()
    return values


def _float_tuple_from_cpu_row(values: Any) -> tuple[float, ...]:
    return tuple(float(price) for price in _to_cpu_list(values))


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


def _prior_sensitivity_from_cpu_paths(
    probability_input: ProbabilityInput,
    *,
    paths: tuple[tuple[float, ...], ...],
    terminal_wins: tuple[bool, ...],
) -> list[dict[str, Any]]:
    if not paths:
        return []
    rows: list[dict[str, Any]] = []
    time_fractions = (0.25, 0.50, 0.75)
    quantile_bands = ((0.0, 0.25), (0.25, 0.50), (0.50, 0.75), (0.75, 1.0))
    point_count = len(paths[0])
    for time_fraction in time_fractions:
        point_index = min(point_count - 1, max(0, round((point_count - 1) * time_fraction)))
        values = tuple(path[point_index] for path in paths)
        ranked = sorted(enumerate(values), key=lambda item: item[1])
        for quantile_low, quantile_high in quantile_bands:
            start = int(math.floor(len(ranked) * quantile_low))
            end = int(math.ceil(len(ranked) * quantile_high))
            band = ranked[start : max(start + 1, end)]
            indices = tuple(index for index, _ in band)
            wins = sum(1 for index in indices if terminal_wins[index])
            price_values = tuple(value for _, value in band)
            rows.append(
                {
                    "dimension": "prior_price_quantile",
                    "time_fraction": time_fraction,
                    "point_index": point_index,
                    "quantile_low": quantile_low,
                    "quantile_high": quantile_high,
                    "sample_count": len(indices),
                    "price_quantile": statistics.fmean(price_values),
                    "log_return_quantile": math.log(
                        statistics.fmean(price_values) / probability_input.settlement_price
                    ),
                    "p_hat": wins / len(indices),
                }
            )
    return rows


def _aggregate_prior_sensitivity_rows(
    seed_rows: tuple[tuple[dict[str, Any], ...], ...],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for rows in seed_rows:
        seen_groups: set[tuple[Any, ...]] = set()
        for row in rows:
            sample_count = int(row["sample_count"])
            if sample_count <= 0:
                continue
            key = (
                row["dimension"],
                row["time_fraction"],
                row["point_index"],
                row["quantile_low"],
                row["quantile_high"],
            )
            if key not in groups:
                groups[key] = {
                    "dimension": row["dimension"],
                    "time_fraction": row["time_fraction"],
                    "point_index": row["point_index"],
                    "quantile_low": row["quantile_low"],
                    "quantile_high": row["quantile_high"],
                    "sample_count": 0,
                    "p_hat_weighted_sum": 0.0,
                    "price_quantile_weighted_sum": 0.0,
                    "log_return_quantile_weighted_sum": 0.0,
                    "source_seed_count": 0,
                }
            group = groups[key]
            group["sample_count"] += sample_count
            group["p_hat_weighted_sum"] += float(row["p_hat"]) * sample_count
            group["price_quantile_weighted_sum"] += float(row["price_quantile"]) * sample_count
            group["log_return_quantile_weighted_sum"] += (
                float(row["log_return_quantile"]) * sample_count
            )
            if key not in seen_groups:
                group["source_seed_count"] += 1
                seen_groups.add(key)

    aggregated_rows: list[dict[str, Any]] = []
    for key in sorted(groups):
        group = groups[key]
        sample_count = int(group["sample_count"])
        aggregated_rows.append(
            {
                "dimension": group["dimension"],
                "time_fraction": group["time_fraction"],
                "point_index": group["point_index"],
                "quantile_low": group["quantile_low"],
                "quantile_high": group["quantile_high"],
                "sample_count": sample_count,
                "price_quantile": group["price_quantile_weighted_sum"] / sample_count,
                "log_return_quantile": group["log_return_quantile_weighted_sum"] / sample_count,
                "p_hat": group["p_hat_weighted_sum"] / sample_count,
                "source_seed_count": group["source_seed_count"],
            }
        )
    return aggregated_rows
