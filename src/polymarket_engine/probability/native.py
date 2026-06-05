from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from typing import Any

from polymarket_engine.probability.monte_carlo import run_seeded_monte_carlo
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


RUST_CPU_MODEL_VERSION = "offline-lognormal-chainlink-sigma-rust-cpu-v1"


def run_native_or_python(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    backend: str = "cpu_rayon",
) -> ProbabilityOutput:
    if backend == "python_numpy":
        return _run_python_numpy(
            probability_input,
            path_count=path_count,
            steps=steps,
            seed=seed,
            backend_request=backend,
        )
    if backend != "cpu_rayon":
        raise ValueError(f"unsupported probability backend: {backend}")

    try:
        native_module = importlib.import_module("polymarket_probability_native")
    except ImportError:
        return _run_python_numpy(
            probability_input,
            path_count=path_count,
            steps=steps,
            seed=seed,
            backend_request=backend,
        )

    config = {
        "path_count": path_count,
        "steps": steps,
        "seed": seed,
        "backend": "cpu_rayon",
        "model_version": RUST_CPU_MODEL_VERSION,
        "emit_artifacts": False,
        "sample_path_limit": 0,
    }
    run_json = native_module.run_cpu_json(
        json.dumps(probability_input.to_json_dict(), sort_keys=True, allow_nan=False),
        json.dumps(config, sort_keys=True, allow_nan=False),
    )
    run = json.loads(run_json)
    diagnostics = _diagnostics(run.get("diagnostics"))
    diagnostics.update(
        {
            "backend_request": backend,
            "backend": "cpu_rayon",
            "native_available": True,
        }
    )
    return ProbabilityOutput(
        state_id=str(run["state_id"]),
        asof_ts=_parse_utc(run["asof_ts"]),
        p_finish=float(run["p_finish"]),
        p_no_touch=float(run["p_no_touch"]),
        z_path=float(run["z_path"]),
        model_version=str(run["model_version"]),
        seed=int(run["seed"]),
        diagnostics=diagnostics,
    )


def _run_python_numpy(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    backend_request: str,
) -> ProbabilityOutput:
    output = run_seeded_monte_carlo(
        probability_input,
        path_count=path_count,
        steps=steps,
        seed=seed,
    )
    diagnostics = dict(output.diagnostics)
    diagnostics.update(
        {
            "backend_request": backend_request,
            "backend": "python_numpy",
            "native_available": False,
        }
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


def _diagnostics(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _parse_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("native probability timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)
