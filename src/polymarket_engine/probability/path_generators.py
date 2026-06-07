from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any, Iterable, Mapping, Sequence
import random

from polymarket_engine.probability.generator_contracts import (
    GeneratorId,
    GeneratorRun,
)
from polymarket_engine.probability.schema import ProbabilityInput


@dataclass(frozen=True)
class PathSimulationResult:
    generator_id: str
    paths: tuple[tuple[float, ...], ...]
    terminal_prices: tuple[float, ...]
    terminal_wins: tuple[bool, ...]
    no_touch_survivals: tuple[bool, ...]
    max_adverse_excursions: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.paths:
            raise ValueError("paths must contain at least one path")
        if len(self.paths) != len(self.terminal_prices):
            raise ValueError("terminal_prices length must match path count")
        if len(self.paths) != len(self.terminal_wins):
            raise ValueError("terminal_wins length must match path count")
        if len(self.paths) != len(self.no_touch_survivals):
            raise ValueError("no_touch_survivals length must match path count")
        if len(self.paths) != len(self.max_adverse_excursions):
            raise ValueError("max_adverse_excursions length must match path count")

        path_length = len(self.paths[0])
        if path_length < 2:
            raise ValueError("each path must contain at least two points")
        for path in self.paths:
            if len(path) != path_length:
                raise ValueError("all paths must have the same length")
            for price in path:
                if not isinstance(price, (int, float)) or not math.isfinite(float(price)):
                    raise ValueError("path prices must be finite")
                if float(price) <= 0:
                    raise ValueError("path prices must be strictly positive")


def _require_probability_input(probability_input: ProbabilityInput) -> None:
    if probability_input.asset not in {"BTC", "ETH"}:
        raise ValueError("asset must be BTC or ETH")
    if probability_input.side not in {"UP", "DOWN"}:
        raise ValueError("side must be UP or DOWN")
    if probability_input.seconds_left <= 0:
        raise ValueError("seconds_left must be positive")
    if probability_input.sigma_tau <= 0:
        raise ValueError("sigma_tau must be positive")
    if probability_input.settlement_price <= 0:
        raise ValueError("settlement_price must be positive")
    if probability_input.threshold <= 0:
        raise ValueError("threshold must be positive")
    if probability_input.comparison_operator not in {">", ">=", "<", "<="}:
        raise ValueError("unsupported comparison_operator")


def _price_satisfies_contract(probability_input: ProbabilityInput, price: float) -> bool:
    threshold = probability_input.threshold
    if probability_input.comparison_operator == ">":
        return price > threshold
    if probability_input.comparison_operator == ">=":
        return price >= threshold
    if probability_input.comparison_operator == "<":
        return price < threshold
    return price <= threshold


def _analyze_paths(
    probability_input: ProbabilityInput,
    paths: Sequence[Sequence[float]],
) -> tuple[tuple[bool, ...], tuple[bool, ...], tuple[float, ...]]:
    terminal_wins: list[bool] = []
    no_touch_survivals: list[bool] = []
    max_adverse_excursions: list[float] = []
    start_price = float(probability_input.settlement_price)
    side = probability_input.side

    for path in paths:
        prices = tuple(float(value) for value in path)
        terminal_wins.append(_price_satisfies_contract(probability_input, prices[-1]))
        no_touch_survivals.append(
            all(_price_satisfies_contract(probability_input, price) for price in prices)
        )
        adverse_candidates = []
        for price in prices:
            if side == "UP":
                adverse_candidates.append(max(0.0, (start_price - float(price)) / start_price))
            else:
                adverse_candidates.append(max(0.0, (float(price) - start_price) / start_price))
        max_adverse_excursions.append(max(adverse_candidates))
    return tuple(terminal_wins), tuple(no_touch_survivals), tuple(max_adverse_excursions)


def _resample_path(path: Sequence[float], target_len: int) -> tuple[float, ...]:
    if target_len <= 0:
        raise ValueError("target_len must be positive")
    if not path:
        raise ValueError("path must contain at least one point")
    if len(path) == target_len:
        return tuple(float(value) for value in path)

    if len(path) == 1:
        value = float(path[0])
        return tuple(value for _ in range(target_len))

    indices = [
        round(i * (len(path) - 1) / (target_len - 1))
        for i in range(target_len)
    ]
    return tuple(float(path[index]) for index in indices)


def _fit_fragment(
    fragment: Sequence[float],
    *,
    settlement_price: float,
    target_len: int,
) -> tuple[float, ...]:
    if len(fragment) < 2:
        raise ValueError("fragment must contain at least two points")
    fragment_values = tuple(float(value) for value in fragment)
    if min(fragment_values) <= 0:
        raise ValueError("fragment prices must be positive")
    if settlement_price <= 0:
        raise ValueError("settlement_price must be positive")
    scale = settlement_price / fragment_values[0]
    scaled = tuple(value * scale for value in fragment_values)
    return _resample_path(scaled, target_len=target_len)


def _log_returns(values: Sequence[float]) -> tuple[float, ...]:
    if len(values) < 2:
        return ()
    returns: list[float] = []
    previous = float(values[0])
    if previous <= 0:
        raise ValueError("fragment prices must be positive")
    for value in values[1:]:
        current = float(value)
        if current <= 0:
            raise ValueError("fragment prices must be positive")
        returns.append(math.log(current / previous))
        previous = current
    return tuple(returns)


def _simulate_baseline_paths(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
) -> tuple[tuple[float, ...], ...]:
    if path_count <= 0:
        raise ValueError("path_count must be positive")
    if steps <= 0:
        raise ValueError("steps must be positive")

    rng = random.Random(seed)
    start = float(probability_input.settlement_price)
    per_step_sigma = float(probability_input.sigma_tau) / math.sqrt(steps)
    paths: list[tuple[float, ...]] = []

    for _ in range(path_count):
        cumulative = 0.0
        points = [start]
        for _step in range(steps):
            cumulative += rng.gauss(0.0, per_step_sigma)
            points.append(start * math.exp(cumulative))
        paths.append(tuple(points))
    return tuple(paths)


def _bootstrap_fragment_paths(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    history_fragments: Sequence[Sequence[float]],
) -> tuple[tuple[float, ...], ...]:
    if path_count <= 0:
        raise ValueError("path_count must be positive")
    if steps <= 0:
        raise ValueError("steps must be positive")
    returns = [
        log_return
        for fragment in history_fragments
        for log_return in _log_returns(fragment)
    ]
    if not returns:
        raise ValueError("history_fragments must contain at least one return")

    rng = random.Random(seed)
    start = float(probability_input.settlement_price)
    paths: list[tuple[float, ...]] = []
    for _ in range(path_count):
        points = [start]
        for _step in range(steps):
            points.append(points[-1] * math.exp(rng.choice(returns)))
        paths.append(tuple(points))
    return tuple(paths)


def _apply_stress_overlay(
    paths: Sequence[Sequence[float]],
    *,
    side: str,
    stress_scale: float,
) -> tuple[tuple[float, ...], ...]:
    stressed: list[tuple[float, ...]] = []
    if stress_scale < 0 or stress_scale >= 1:
        raise ValueError("stress_scale must be in [0,1)")

    for path in paths:
        casted = tuple(float(value) for value in path)
        if not casted:
            raise ValueError("path cannot be empty")
        if side == "UP":
            stressed.append(
                tuple(
                    point * (1.0 - stress_scale * ((index + 1) / (len(path) - 1)))
                    if index else point
                    for index, point in enumerate(casted)
                )
            )
        else:
            stressed.append(
                tuple(
                    point * (1.0 + stress_scale * ((index + 1) / (len(path) - 1)))
                    if index else point
                    for index, point in enumerate(casted)
                )
            )
    return tuple(stressed)


def _build_result(
    generator_id: GeneratorId,
    probability_input: ProbabilityInput,
    paths: Sequence[Sequence[float]],
) -> PathSimulationResult:
    normalized_paths = tuple(tuple(float(value) for value in path) for path in paths)
    terminal_wins, no_touch_survivals, max_adverse_excursions = _analyze_paths(
        probability_input,
        normalized_paths,
    )
    terminal_prices = tuple(path[-1] for path in normalized_paths)
    return PathSimulationResult(
        generator_id=generator_id.value,
        paths=normalized_paths,
        terminal_prices=terminal_prices,
        terminal_wins=terminal_wins,
        no_touch_survivals=no_touch_survivals,
        max_adverse_excursions=max_adverse_excursions,
    )


def generate_empirical_conditional_paths(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    history_fragments: Sequence[Sequence[float]] | None = None,
) -> PathSimulationResult:
    _require_probability_input(probability_input)
    target_len = steps + 1
    if history_fragments:
        normalized_fragments = [
            _fit_fragment(
                fragment,
                settlement_price=float(probability_input.settlement_price),
                target_len=target_len,
            )
            for fragment in history_fragments
        ]
        selected = []
        cursor = 0
        for _ in range(path_count):
            selected.append(normalized_fragments[cursor % len(normalized_fragments)])
            cursor += 1
        paths = tuple(selected)
    else:
        paths = _simulate_baseline_paths(
            probability_input,
            path_count=path_count,
            steps=steps,
            seed=seed,
        )
    return _build_result(
        GeneratorId.EMPIRICAL_CONDITIONAL,
        probability_input,
        paths,
    )


def generate_block_bootstrap_paths(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    history_fragments: Sequence[Sequence[float]] | None = None,
) -> PathSimulationResult:
    _require_probability_input(probability_input)
    paths = (
        _bootstrap_fragment_paths(
            probability_input,
            path_count=path_count,
            steps=steps,
            seed=seed,
            history_fragments=history_fragments,
        )
        if history_fragments
        else _simulate_baseline_paths(
            probability_input,
            path_count=path_count,
            steps=steps,
            seed=seed,
        )
    )
    return _build_result(GeneratorId.BLOCK_BOOTSTRAP, probability_input, paths)


def generate_filtered_historical_paths(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    history_fragments: Sequence[Sequence[float]] | None = None,
) -> PathSimulationResult:
    _require_probability_input(probability_input)
    primary = generate_empirical_conditional_paths(
        probability_input,
        path_count=path_count,
        steps=steps,
        seed=seed,
        history_fragments=history_fragments,
    )
    return PathSimulationResult(
        generator_id=GeneratorId.FILTERED_HISTORICAL.value,
        paths=primary.paths,
        terminal_prices=primary.terminal_prices,
        terminal_wins=primary.terminal_wins,
        no_touch_survivals=primary.no_touch_survivals,
        max_adverse_excursions=primary.max_adverse_excursions,
    )


def generate_stress_overlay_paths(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    history_fragments: Sequence[Sequence[float]] | None = None,
    stress_scale: float = 0.03,
) -> PathSimulationResult:
    _require_probability_input(probability_input)
    baseline = (
        generate_empirical_conditional_paths(
            probability_input,
            path_count=path_count,
            steps=steps,
            seed=seed,
            history_fragments=history_fragments,
        ).paths
        if history_fragments
        else _simulate_baseline_paths(
            probability_input,
            path_count=path_count,
            steps=steps,
            seed=seed,
        )
    )
    stressed = _apply_stress_overlay(
        baseline,
        side=probability_input.side,
        stress_scale=stress_scale,
    )
    return _build_result(GeneratorId.STRESS_OVERLAY, probability_input, stressed)


def run_generator_suite(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    history_fragments: Sequence[Sequence[float]] | None = None,
) -> tuple[PathSimulationResult, PathSimulationResult, PathSimulationResult, PathSimulationResult]:
    _require_probability_input(probability_input)
    empirical = generate_empirical_conditional_paths(
        probability_input,
        path_count=path_count,
        steps=steps,
        seed=seed + 1,
        history_fragments=history_fragments,
    )
    bootstrap = generate_block_bootstrap_paths(
        probability_input,
        path_count=path_count,
        steps=steps,
        seed=seed + 2,
        history_fragments=history_fragments,
    )
    filtered = generate_filtered_historical_paths(
        probability_input,
        path_count=path_count,
        steps=steps,
        seed=seed + 3,
        history_fragments=history_fragments,
    )
    stress_overlay = generate_stress_overlay_paths(
        probability_input,
        path_count=path_count,
        steps=steps,
        seed=seed + 4,
        history_fragments=history_fragments,
    )
    return (empirical, bootstrap, filtered, stress_overlay)


def run_path_generators(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    history_fragments: Sequence[Sequence[float]] | None = None,
) -> tuple[PathSimulationResult, PathSimulationResult, PathSimulationResult, PathSimulationResult]:
    return run_generator_suite(
        probability_input,
        path_count=path_count,
        steps=steps,
        seed=seed,
        history_fragments=history_fragments,
    )


def simulate_paths(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    history_fragments: Sequence[Sequence[float]] | None = None,
) -> tuple[PathSimulationResult, PathSimulationResult, PathSimulationResult, PathSimulationResult]:
    return run_path_generators(
        probability_input,
        path_count=path_count,
        steps=steps,
        seed=seed,
        history_fragments=history_fragments,
    )


def path_result_to_generator_run(
    result: PathSimulationResult,
    *,
    asof_ts: datetime,
    runtime_ms: float = 0.0,
    diagnostics: Mapping[str, Any] | None = None,
) -> GeneratorRun:
    try:
        generator_id = GeneratorId(result.generator_id)
    except ValueError as exc:
        raise ValueError("unknown generator_id") from exc
    if not isinstance(asof_ts, datetime):
        raise ValueError("asof_ts must be a datetime")
    payload = dict(diagnostics or {})
    payload["path_count"] = len(result.paths)
    payload["steps"] = len(result.paths[0]) - 1 if result.paths else 0
    if "model" not in payload:
        payload["model"] = "path_generator"
    terminal_wins = len([win for win in result.terminal_wins if win]) / max(1, len(result.paths))
    no_touch = len([win for win in result.no_touch_survivals if win]) / max(
        1,
        len(result.paths),
    )
    return GeneratorRun(
        generator_id=generator_id,
        p_finish=float(terminal_wins),
        p_no_touch=float(no_touch),
        path_count=len(result.paths),
        effective_path_count=len(result.paths),
        seed=None,
        asof_ts=asof_ts,
        runtime_ms=float(runtime_ms),
        sparse=False,
        diagnostics=payload,
    )


def build_generator_runs_from_results(
    results: Sequence[PathSimulationResult],
    *,
    asof_ts: datetime,
) -> tuple[GeneratorRun, ...]:
    return tuple(
        path_result_to_generator_run(result, asof_ts=asof_ts)
        for result in results
    )


def _build_fragment_path(
    fragment: Mapping[str, object],
) -> tuple[float, ...]:
    raw = fragment.get("prices")
    if not isinstance(raw, Iterable):
        raise ValueError("path fragment must include iterable prices")
    return tuple(float(value) for value in raw)


__all__ = [
    "PathSimulationResult",
    "generate_empirical_conditional_paths",
    "generate_block_bootstrap_paths",
    "generate_filtered_historical_paths",
    "generate_stress_overlay_paths",
    "run_generator_suite",
    "run_path_generators",
    "simulate_paths",
    "path_result_to_generator_run",
    "build_generator_runs_from_results",
]
