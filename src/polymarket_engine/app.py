from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from polymarket_engine.runtime_api import build_runtime_router, container_status_enabled_from_env


def create_app(
    *,
    status_path: Path = Path("data/live/status.json"),
    duckdb_path: Path = Path("data/db/polymarket.duckdb"),
    normalized_health_path: Path = Path("data/live/normalized_health.json"),
    data_dir: Path = Path("data"),
    enable_container_status: bool | None = None,
) -> FastAPI:
    app = FastAPI(title="Polymarket Engine", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(
        build_runtime_router(
            status_path=status_path,
            duckdb_path=duckdb_path,
            normalized_health_path=normalized_health_path,
            data_dir=data_dir,
            enable_container_status=container_status_enabled_from_env()
            if enable_container_status is None
            else enable_container_status,
        )
    )
    return app


app = create_app()
