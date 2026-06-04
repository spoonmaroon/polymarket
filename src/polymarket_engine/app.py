from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI

from polymarket_engine.runtime_api import build_runtime_router, container_status_enabled_from_env


def create_app(
    *,
    status_path: Path = Path("data/live/status.json"),
    duckdb_path: Path = Path("data/db/polymarket.duckdb"),
    normalized_health_path: Path = Path("data/live/normalized_health.json"),
    probability_status_path: Optional[Path] = None,
    outcome_status_path: Optional[Path] = None,
    data_dir: Path = Path("data"),
    enable_container_status: bool | None = None,
) -> FastAPI:
    probability_status_path = probability_status_path or status_path.with_name(
        "probabilities.json"
    )
    outcome_status_path = outcome_status_path or status_path.with_name("outcomes.json")
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
            outcome_status_path=outcome_status_path,
            data_dir=data_dir,
            enable_container_status=container_status_enabled_from_env()
            if enable_container_status is None
            else enable_container_status,
        )
    )
    return app


def create_app_from_env() -> FastAPI:
    return create_app(
        status_path=_env_path("POLYMARKET_STATUS_PATH", Path("data/live/status.json")),
        duckdb_path=_env_path("POLYMARKET_DUCKDB_PATH", Path("data/db/polymarket.duckdb")),
        normalized_health_path=_env_path(
            "POLYMARKET_NORMALIZED_HEALTH_PATH",
            Path("data/live/normalized_health.json"),
        ),
        probability_status_path=_env_path(
            "POLYMARKET_PROBABILITY_STATUS_PATH",
            Path("data/live/probabilities.json"),
        ),
        outcome_status_path=_env_path(
            "POLYMARKET_OUTCOME_STATUS_PATH",
            Path("data/live/outcomes.json"),
        ),
        data_dir=_env_path("POLYMARKET_DATA_DIR", Path("data")),
    )


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return default if value is None or value == "" else Path(value)


app = create_app_from_env()
