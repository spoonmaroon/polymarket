from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from polymarket_engine.probability.ensemble_outputs import (
    GeneratorWeight,
    reduce_ensemble,
)
from polymarket_engine.probability.generator_contracts import (
    GeneratorId,
    GeneratorRun,
    generator_runs_to_json,
)
from polymarket_engine.probability.path_generators import (
    PathSimulationResult,
    path_result_to_generator_run,
    run_generator_suite,
)
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput

ENSEMBLE_MODEL_VERSION = "ensemble-v1"
ENSEMBLE_GENERATOR_VERSION = "four-generator-ensemble-v1"
ENSEMBLE_PREVIEW_PATH_LIMIT = 64
ENSEMBLE_PREVIEW_POINT_LIMIT = 48

SEED_ENSEMBLE_WEIGHTS = (
    GeneratorWeight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.40),
    GeneratorWeight(GeneratorId.BLOCK_BOOTSTRAP, 0.25),
    GeneratorWeight(GeneratorId.FILTERED_HISTORICAL, 0.25),
    GeneratorWeight(GeneratorId.STRESS_OVERLAY, 0.10),
)

_GENERATOR_SEED_OFFSETS = {
    GeneratorId.EMPIRICAL_CONDITIONAL: 1,
    GeneratorId.BLOCK_BOOTSTRAP: 2,
    GeneratorId.FILTERED_HISTORICAL: 3,
    GeneratorId.STRESS_OVERLAY: 4,
}


def run_four_generator_ensemble(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    history_fragments: Sequence[Sequence[float]] | None = None,
    base_model_buffer: float = 0.01,
) -> ProbabilityOutput:
    sparse_scope = not bool(history_fragments)
    results = run_generator_suite(
        probability_input,
        path_count=path_count,
        steps=steps,
        seed=seed,
        history_fragments=history_fragments,
    )
    _validate_generator_results(results, path_count=path_count, steps=steps)
    runs = _generator_runs_from_results(
        results,
        probability_input=probability_input,
        seed=seed,
        sparse_scope=sparse_scope,
        path_count=path_count,
        steps=steps,
    )
    ensemble = reduce_ensemble(
        runs=runs,
        weights=SEED_ENSEMBLE_WEIGHTS,
        base_model_buffer=base_model_buffer,
    )
    effective_weights = _effective_weights(ensemble.effective_generator_values)
    diagnostics: dict[str, Any] = {
        "model": ENSEMBLE_MODEL_VERSION,
        "backend": "ensemble",
        "generator_version": ENSEMBLE_GENERATOR_VERSION,
        "path_count": int(path_count * len(results)),
        "paths_per_generator": int(path_count),
        "generator_count": len(results),
        "steps": int(steps),
        "generator_runs": generator_runs_to_json(runs),
        "effective_generator_values": ensemble.effective_generator_values,
        "effective_weights": effective_weights,
        "u_gen": float(ensemble.u_gen),
        "mc_dispersion": float(ensemble.mc_dispersion),
        "uncertainty_buffer": float(ensemble.uncertainty_buffer),
        "terminal_probability_source": "core_generators_ex_stress_overlay",
        "risk_adjusted_p_finish": float(ensemble.risk_adjusted_p_finish),
        "risk_adjusted_p_no_touch": float(ensemble.risk_adjusted_p_no_touch),
        "risk_adjustment": float(ensemble.risk_adjustment),
        "path_diagnosis": ensemble.path_diagnosis.value,
        "sparse_scope": sparse_scope,
        "prior_fragment_generators": [
            weight.generator_id.value for weight in SEED_ENSEMBLE_WEIGHTS
        ]
        if history_fragments
        else [],
        "generator_summary": _generator_summary(runs, effective_weights),
        "simulation_preview": _ensemble_simulation_preview(probability_input, results),
    }

    return ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=float(ensemble.p_finish),
        p_no_touch=float(ensemble.p_no_touch),
        z_path=float(probability_input.z_path),
        model_version=ENSEMBLE_MODEL_VERSION,
        seed=seed,
        diagnostics=diagnostics,
    )


def _validate_generator_results(
    results: Sequence[PathSimulationResult],
    *,
    path_count: int,
    steps: int,
) -> None:
    expected_ids = {weight.generator_id for weight in SEED_ENSEMBLE_WEIGHTS}
    observed_ids: list[GeneratorId] = []
    for result in results:
        try:
            generator_id = GeneratorId(result.generator_id)
        except ValueError as exc:
            raise ValueError(f"unexpected generator_id: {result.generator_id}") from exc
        observed_ids.append(generator_id)
        if len(result.paths) != path_count:
            raise ValueError(
                f"{generator_id.value} path_count mismatch: "
                f"expected {path_count}, got {len(result.paths)}"
            )
        expected_points = steps + 1
        for path in result.paths:
            if len(path) != expected_points:
                raise ValueError(
                    f"{generator_id.value} steps mismatch: "
                    f"expected {steps}, got {len(path) - 1}"
                )
    observed_set = set(observed_ids)
    if len(observed_ids) != len(observed_set):
        raise ValueError("generator suite returned duplicate generator_id values")
    if observed_set != expected_ids:
        missing = ",".join(sorted(generator_id.value for generator_id in expected_ids - observed_set))
        extra = ",".join(sorted(generator_id.value for generator_id in observed_set - expected_ids))
        raise ValueError(f"generator suite mismatch: missing={missing} extra={extra}")


def _generator_runs_from_results(
    results: Sequence[PathSimulationResult],
    *,
    probability_input: ProbabilityInput,
    seed: int,
    sparse_scope: bool,
    path_count: int,
    steps: int,
) -> tuple[GeneratorRun, ...]:
    runs: list[GeneratorRun] = []
    for result in results:
        run = path_result_to_generator_run(
            result,
            asof_ts=probability_input.asof_ts,
            diagnostics={
                "model": ENSEMBLE_MODEL_VERSION,
                "generator_version": ENSEMBLE_GENERATOR_VERSION,
                "path_count": int(path_count),
                "steps": int(steps),
                "sparse_scope": sparse_scope,
            },
        )
        runs.append(
            replace(
                run,
                seed=seed + _GENERATOR_SEED_OFFSETS[run.generator_id],
                sparse=sparse_scope,
            )
        )
    return tuple(runs)


def _effective_weights(
    effective_generator_values: dict[str, dict[str, float]],
) -> dict[str, float]:
    return {
        generator_id: float(values["weight"])
        for generator_id, values in effective_generator_values.items()
    }


def _generator_summary(
    runs: Sequence[GeneratorRun],
    effective_weights: dict[str, float],
) -> dict[str, dict[str, float | bool]]:
    return {
        run.generator_id.value: {
            "p_finish": float(run.p_finish),
            "p_no_touch": float(run.p_no_touch),
            "weight": float(effective_weights[run.generator_id.value]),
            "sparse": run.sparse,
        }
        for run in runs
    }


def _ensemble_simulation_preview(
    probability_input: ProbabilityInput,
    results: Sequence[PathSimulationResult],
    *,
    path_limit: int = ENSEMBLE_PREVIEW_PATH_LIMIT,
    point_limit: int = ENSEMBLE_PREVIEW_POINT_LIMIT,
) -> dict[str, Any]:
    generator_count = len(results)
    if generator_count <= 0:
        raise ValueError("results must be non-empty")
    paths_per_generator = len(results[0].paths)
    per_generator_limit = max(1, path_limit // generator_count)
    sampled_paths: list[dict[str, Any]] = []
    terminal_win_count = 0
    no_touch_win_count = 0
    terminal_prices: list[float] = []

    for result in results:
        terminal_win_count += sum(1 for win in result.terminal_wins if win)
        no_touch_win_count += sum(1 for win in result.no_touch_survivals if win)
        terminal_prices.extend(float(price) for price in result.terminal_prices)
        path_indices = _evenly_spaced_indices(
            len(result.paths),
            min(per_generator_limit, len(result.paths)),
        )
        for path_index in path_indices:
            sampled_paths.append(
                {
                    "index": f"{result.generator_id}:{path_index}",
                    "generator_id": result.generator_id,
                    "terminal_win": bool(result.terminal_wins[path_index]),
                    "no_touch_win": bool(result.no_touch_survivals[path_index]),
                    "points": _sampled_points(
                        result.paths[path_index],
                        point_limit=point_limit,
                    ),
                }
            )

    return {
        "path_count": sum(len(result.paths) for result in results),
        "paths_per_generator": paths_per_generator,
        "generator_count": generator_count,
        "steps": len(results[0].paths[0]) - 1,
        "start_price": probability_input.settlement_price,
        "threshold": probability_input.threshold,
        "comparison_operator": probability_input.comparison_operator,
        "terminal_win_count": terminal_win_count,
        "no_touch_win_count": no_touch_win_count,
        "sampled_paths": sampled_paths[:path_limit],
        "terminal_histogram": _terminal_histogram(tuple(terminal_prices)),
    }


def _evenly_spaced_indices(length: int, count: int) -> tuple[int, ...]:
    if count <= 0:
        return ()
    if count >= length:
        return tuple(range(length))
    if count == 1:
        return (0,)
    return tuple(round(index * (length - 1) / (count - 1)) for index in range(count))


def _sampled_points(path: Sequence[float], *, point_limit: int) -> list[float]:
    indices = _evenly_spaced_indices(len(path), min(point_limit, len(path)))
    return [float(path[index]) for index in indices]


def _terminal_histogram(terminal_prices: tuple[float, ...]) -> list[dict[str, Any]]:
    lower_bound = min(terminal_prices)
    upper_bound = max(terminal_prices)
    if lower_bound == upper_bound:
        return [{"lower": lower_bound, "upper": upper_bound, "count": len(terminal_prices)}]

    bin_count = min(16, len(terminal_prices))
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


__all__ = [
    "ENSEMBLE_GENERATOR_VERSION",
    "ENSEMBLE_MODEL_VERSION",
    "ENSEMBLE_PREVIEW_PATH_LIMIT",
    "ENSEMBLE_PREVIEW_POINT_LIMIT",
    "SEED_ENSEMBLE_WEIGHTS",
    "run_four_generator_ensemble",
]
