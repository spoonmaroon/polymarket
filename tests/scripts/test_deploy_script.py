from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_deploy_script_has_configurable_long_smoke_window() -> None:
    script = ROOT / "scripts" / "deploy.sh"
    text = script.read_text(encoding="utf-8")

    assert 'DEPLOY_SMOKE_ATTEMPTS="${DEPLOY_SMOKE_ATTEMPTS:-90}"' in text
    assert 'seq 1 "$DEPLOY_SMOKE_ATTEMPTS"' in text


def test_collector_container_healthcheck_uses_status_validator() -> None:
    dockerfile = (ROOT / "deploy" / "collector" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert "python3" in dockerfile
    assert "scripts/check_collector_status.py" in dockerfile
    assert "check_collector_status.py" in compose
    assert "--max-price-age-ms" in compose
    assert "--max-orderbook-age-ms" in compose
    assert "--max-websocket-event-age-ms" in compose
    assert "30000" in compose


def test_collector_entrypoint_enables_state_snapshot_journal() -> None:
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "collector" / "collector-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "POLYMARKET_STATE_SNAPSHOT_DIR" in compose
    assert "STATE_SNAPSHOT_DIR=" in entrypoint
    assert "--state-snapshot-dir" in entrypoint
    assert '"$STATE_SNAPSHOT_DIR"' in entrypoint
