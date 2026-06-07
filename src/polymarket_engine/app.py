from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from polymarket_engine.runtime_api import (
    build_runtime_router,
    container_status_enabled_from_env,
    runtime_probability_compute_fallback_enabled_from_env,
    runtime_probabilities_enabled_from_env,
)


def create_app(
    *,
    status_path: Path = Path("data/live/status.json"),
    duckdb_path: Path = Path("data/db/polymarket.duckdb"),
    normalized_health_path: Path = Path("data/live/normalized_health.json"),
    probability_status_path: Optional[Path] = None,
    probability_inputs_path: Optional[Path] = None,
    probability_fragments_path: Optional[Path] = None,
    outcome_status_path: Optional[Path] = None,
    target_cache_path: Optional[Path] = None,
    volatility_status_path: Optional[Path] = None,
    data_dir: Path = Path("data"),
    enable_container_status: bool | None = None,
    enable_runtime_probabilities: bool | None = None,
    allow_runtime_probability_compute: bool | None = None,
    ui_dist_path: Optional[Path] = None,
) -> FastAPI:
    probability_status_path = probability_status_path or status_path.with_name(
        "probabilities.json"
    )
    probability_inputs_path = probability_inputs_path or status_path.with_name(
        "probability_inputs.json"
    )
    probability_fragments_path = probability_fragments_path or status_path.with_name(
        "probability_fragments.json"
    )
    outcome_status_path = outcome_status_path or status_path.with_name("outcomes.json")
    target_cache_path = target_cache_path or status_path.with_name("targets.json")
    volatility_status_path = volatility_status_path or status_path.with_name(
        "volatility.json"
    )
    app = FastAPI(title="Polymarket Engine", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(
        build_runtime_router(
            status_path=status_path,
            duckdb_path=duckdb_path,
            normalized_health_path=normalized_health_path,
            probability_status_path=probability_status_path,
            probability_inputs_path=probability_inputs_path,
            probability_fragments_path=probability_fragments_path,
            outcome_status_path=outcome_status_path,
            target_cache_path=target_cache_path,
            volatility_status_path=volatility_status_path,
            data_dir=data_dir,
            enable_container_status=container_status_enabled_from_env()
            if enable_container_status is None
            else enable_container_status,
            enable_runtime_probabilities=runtime_probabilities_enabled_from_env()
            if enable_runtime_probabilities is None
            else enable_runtime_probabilities,
            allow_probability_compute_fallback=runtime_probability_compute_fallback_enabled_from_env()
            if allow_runtime_probability_compute is None
            else allow_runtime_probability_compute,
        )
    )
    resolved_ui_dist_path = _resolve_ui_dist_path(ui_dist_path)
    if resolved_ui_dist_path is not None:
        app.mount(
            "/",
            StaticFiles(directory=resolved_ui_dist_path, html=True),
            name="ui",
        )
    return app


def create_app_from_env() -> FastAPI:
    status_path = _env_path("POLYMARKET_STATUS_PATH", Path("data/live/status.json"))
    return create_app(
        status_path=status_path,
        duckdb_path=_env_path("POLYMARKET_DUCKDB_PATH", Path("data/db/polymarket.duckdb")),
        normalized_health_path=_env_path(
            "POLYMARKET_NORMALIZED_HEALTH_PATH",
            Path("data/live/normalized_health.json"),
        ),
        probability_status_path=_env_path(
            "POLYMARKET_PROBABILITY_STATUS_PATH",
            Path("data/live/probabilities.json"),
        ),
        probability_inputs_path=_env_path(
            "POLYMARKET_PROBABILITY_INPUTS_PATH",
            status_path.with_name("probability_inputs.json"),
        ),
        probability_fragments_path=_env_path(
            "POLYMARKET_PROBABILITY_FRAGMENTS_PATH",
            status_path.with_name("probability_fragments.json"),
        ),
        outcome_status_path=_env_path(
            "POLYMARKET_OUTCOME_STATUS_PATH",
            Path("data/live/outcomes.json"),
        ),
        target_cache_path=_env_path(
            "POLYMARKET_TARGET_STATUS_PATH",
            status_path.with_name("targets.json"),
        ),
        volatility_status_path=_env_path(
            "POLYMARKET_VOLATILITY_STATUS_PATH",
            status_path.with_name("volatility.json"),
        ),
        data_dir=_env_path("POLYMARKET_DATA_DIR", Path("data")),
        ui_dist_path=_env_path_or_none("POLYMARKET_UI_DIST_PATH"),
    )


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return default if value is None or value == "" else Path(value)


def _env_path_or_none(name: str) -> Optional[Path]:
    value = os.getenv(name)
    return None if value is None or value == "" else Path(value)


def _resolve_ui_dist_path(path: Optional[Path]) -> Optional[Path]:
    candidates = [path] if path is not None else _default_ui_dist_candidates()
    for candidate in candidates:
        if candidate is None:
            continue
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    return None


def _default_ui_dist_candidates() -> list[Path]:
    source_root = Path(__file__).resolve().parents[2]
    return [
        Path.cwd() / "ui" / "dist",
        source_root / "ui" / "dist",
    ]


app = create_app_from_env()
