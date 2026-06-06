from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from polymarket_engine.app import app, create_app


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_serves_ui_dist_from_default_working_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    ui_dist = tmp_path / "ui" / "dist"
    ui_dist.mkdir(parents=True)
    (ui_dist / "index.html").write_text(
        "<!doctype html><title>Runtime Monitor</title><div id=\"root\"></div>",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    client = TestClient(
        create_app(
            enable_container_status=False,
            enable_runtime_probabilities=False,
            allow_runtime_probability_compute=False,
        )
    )
    response = client.get("/")

    assert response.status_code == 200
    assert "Runtime Monitor" in response.text
