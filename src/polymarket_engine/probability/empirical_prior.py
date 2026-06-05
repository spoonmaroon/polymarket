from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.probability.monte_carlo import run_seeded_monte_carlo, score_paths
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


CHAINLINK_SOURCE_KEY = "polymarket_rtds_chainlink"
EMPIRICAL_PRIOR_MODEL_VERSION = "empirical-conditional-chainlink-prior-v1"
EMPIRICAL_PRIOR_FALLBACK_MODEL_VERSION = "empirical-conditional-prior-fallback-lognormal-v1"


@dataclass(frozen=True)
class EmpiricalPriorConfig:
    min_bucket_size: int = 8
    history_limit: int = 2_000
    sigma_floor: float = 1e-9

    def __post_init__(self) -> None:
        if isinstance(self.min_bucket_size, bool) or self.min_bucket_size <= 0:
            raise ValueError("min_bucket_size must be positive")
        if isinstance(self.history_limit, bool) or self.history_limit <= 0:
            raise ValueError("history_limit must be positive")
        if not math.isfinite(self.sigma_floor) or self.sigma_floor <= 0:
            raise ValueError("sigma_floor must be positive and finite")


@dataclass(frozen=True)
class _FilteredTicks:
    eligible: tuple[PriceObservation, ...]
    excluded_future_tick_count: int
    ignored_non_chainlink_tick_count: int
    ignored_wrong_symbol_tick_count: int
    ignored_invalid_price_tick_count: int


@dataclass(frozen=True)
class _PriorFragments:
    paths: tuple[tuple[float, ...], ...]
    latest_fragment_end_ts: str | None
    historical_sigma_floor_applied_count: int


def run_empirical_conditional_monte_carlo(
    probability_input: ProbabilityInput,
    *,
    price_ticks: Sequence[PriceObservation],
    path_count: int,
    steps: int,
    seed: int,
    config: EmpiricalPriorConfig | None = None,
) -> ProbabilityOutput:
    """Run an as-of-safe empirical Chainlink path-fragment Monte Carlo."""
    if config is None:
        config = EmpiricalPriorConfig()
    _require_positive_int(path_count, "path_count")
    _require_positive_int(steps, "steps")

    filtered = _filter_asof_chainlink_ticks(probability_input, price_ticks)
    fragments = _build_fragments(probability_input, filtered.eligible, steps=steps, config=config)
    if len(fragments.paths) < config.min_bucket_size:
        return _fallback_lognormal_output(
            probability_input,
            path_count=path_count,
            steps=steps,
            seed=seed,
            filtered=filtered,
            fragments=fragments,
            min_bucket_size=config.min_bucket_size,
        )

    rng = np.random.default_rng(seed)
    sampled_indexes = rng.integers(0, len(fragments.paths), size=path_count)
    sampled_paths = tuple(fragments.paths[int(index)] for index in sampled_indexes)
    output = score_paths(
        probability_input,
        paths=sampled_paths,
        model_version=EMPIRICAL_PRIOR_MODEL_VERSION,
        seed=seed,
    )
    diagnostics = dict(output.diagnostics)
    diagnostics.update(
        _diagnostics(
            filtered=filtered,
            fragments=fragments,
            generator="empirical_conditional_prior",
            prior_fallback_level="none",
            min_bucket_size=config.min_bucket_size,
            sigma_scaled=True,
        )
    )
    return ProbabilityOutput(
        state_id=output.state_id,
        asof_ts=output.asof_ts,
        p_finish=output.p_finish,
        p_no_touch=output.p_no_touch,
        z_path=output.z_path,
        model_version=output.model_version,
        seed=output.seed,
        diagnostics=diagnostics,
    )


def _filter_asof_chainlink_ticks(
    probability_input: ProbabilityInput,
    price_ticks: Sequence[PriceObservation],
) -> _FilteredTicks:
    symbol = _chainlink_symbol(probability_input.asset)
    eligible: list[PriceObservation] = []
    excluded_future_tick_count = 0
    ignored_non_chainlink_tick_count = 0
    ignored_wrong_symbol_tick_count = 0
    ignored_invalid_price_tick_count = 0

    for tick in price_ticks:
        if tick.source_key != CHAINLINK_SOURCE_KEY:
            ignored_non_chainlink_tick_count += 1
            continue
        if tick.symbol != symbol:
            ignored_wrong_symbol_tick_count += 1
            continue
        if tick.event_ts > probability_input.asof_ts or tick.observed_ts > probability_input.asof_ts:
            excluded_future_tick_count += 1
            continue
        if not math.isfinite(tick.price) or tick.price <= 0:
            ignored_invalid_price_tick_count += 1
            continue
        eligible.append(tick)

    eligible.sort(key=lambda tick: (tick.event_ts, tick.observed_ts))
    return _FilteredTicks(
        eligible=tuple(eligible),
        excluded_future_tick_count=excluded_future_tick_count,
        ignored_non_chainlink_tick_count=ignored_non_chainlink_tick_count,
        ignored_wrong_symbol_tick_count=ignored_wrong_symbol_tick_count,
        ignored_invalid_price_tick_count=ignored_invalid_price_tick_count,
    )


def _build_fragments(
    probability_input: ProbabilityInput,
    ticks: tuple[PriceObservation, ...],
    *,
    steps: int,
    config: EmpiricalPriorConfig,
) -> _PriorFragments:
    paths: list[tuple[float, ...]] = []
    latest_fragment_end_ts: str | None = None
    floor_count = 0

    for start_index in range(max(0, len(ticks) - steps)):
        fragment = ticks[start_index : start_index + steps + 1]
        if len(fragment) != steps + 1:
            continue
        returns = _step_returns(fragment)
        historical_sigma = math.sqrt(sum(step_return * step_return for step_return in returns))
        if historical_sigma < config.sigma_floor:
            historical_sigma = config.sigma_floor
            floor_count += 1
        cumulative = [0.0]
        for step_return in returns:
            cumulative.append(cumulative[-1] + step_return)
        path = tuple(
            probability_input.settlement_price
            * math.exp((cumulative_return / historical_sigma) * probability_input.sigma_tau)
            for cumulative_return in cumulative
        )
        paths.append(path)
        latest_fragment_end_ts = fragment[-1].event_ts.isoformat()

    return _PriorFragments(
        paths=tuple(paths),
        latest_fragment_end_ts=latest_fragment_end_ts,
        historical_sigma_floor_applied_count=floor_count,
    )


def _step_returns(fragment: tuple[PriceObservation, ...]) -> tuple[float, ...]:
    returns: list[float] = []
    for previous, current in zip(fragment[:-1], fragment[1:], strict=True):
        returns.append(math.log(current.price / previous.price))
    return tuple(returns)


def _fallback_lognormal_output(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    filtered: _FilteredTicks,
    fragments: _PriorFragments,
    min_bucket_size: int,
) -> ProbabilityOutput:
    output = run_seeded_monte_carlo(
        probability_input,
        path_count=path_count,
        steps=steps,
        seed=seed,
    )
    diagnostics = dict(output.diagnostics)
    diagnostics.update(
        _diagnostics(
            filtered=filtered,
            fragments=fragments,
            generator="lognormal_fallback",
            prior_fallback_level="lognormal",
            min_bucket_size=min_bucket_size,
            sigma_scaled=False,
        )
    )
    return ProbabilityOutput(
        state_id=output.state_id,
        asof_ts=output.asof_ts,
        p_finish=output.p_finish,
        p_no_touch=output.p_no_touch,
        z_path=output.z_path,
        model_version=EMPIRICAL_PRIOR_FALLBACK_MODEL_VERSION,
        seed=output.seed,
        diagnostics=diagnostics,
    )


def _diagnostics(
    *,
    filtered: _FilteredTicks,
    fragments: _PriorFragments,
    generator: str,
    prior_fallback_level: str,
    min_bucket_size: int,
    sigma_scaled: bool,
) -> dict[str, object]:
    return {
        "model": "empirical_conditional_chainlink_prior",
        "generator": generator,
        "asof_safe": True,
        "sigma_scaled": sigma_scaled,
        "prior_bucket_size": len(fragments.paths),
        "prior_fallback_level": prior_fallback_level,
        "min_bucket_size": min_bucket_size,
        "eligible_tick_count": len(filtered.eligible),
        "excluded_future_tick_count": filtered.excluded_future_tick_count,
        "ignored_non_chainlink_tick_count": filtered.ignored_non_chainlink_tick_count,
        "ignored_wrong_symbol_tick_count": filtered.ignored_wrong_symbol_tick_count,
        "ignored_invalid_price_tick_count": filtered.ignored_invalid_price_tick_count,
        "latest_fragment_end_ts": fragments.latest_fragment_end_ts,
        "historical_sigma_floor_applied_count": fragments.historical_sigma_floor_applied_count,
    }


def _chainlink_symbol(asset: str) -> str:
    if asset == "BTC":
        return "BTC/USD"
    if asset == "ETH":
        return "ETH/USD"
    raise ValueError("asset must be BTC or ETH")


def _require_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
