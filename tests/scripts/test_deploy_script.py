from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_deploy_script_has_configurable_long_smoke_window() -> None:
    script = ROOT / "scripts" / "deploy.sh"
    text = script.read_text(encoding="utf-8")

    assert 'DEPLOY_SMOKE_ATTEMPTS="${DEPLOY_SMOKE_ATTEMPTS:-90}"' in text
    assert 'seq 1 "$DEPLOY_SMOKE_ATTEMPTS"' in text


def test_deploy_skip_requires_deployed_marker_to_match_target_commit() -> None:
    script = ROOT / "scripts" / "deploy.sh"
    text = script.read_text(encoding="utf-8")

    assert 'DEPLOYED_SHA="$(cat "$DEPLOYED_MARKER" 2>/dev/null || true)"' in text
    assert '[ "$DEPLOYED_SHA" = "$REMOTE" ]' in text
    assert "deployed marker differs from target commit; redeploying" in text


def test_collector_container_healthcheck_uses_status_validator() -> None:
    dockerfile = (ROOT / "deploy" / "collector" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "python3" in dockerfile
    assert "scripts/check_collector_status.py" in dockerfile
    assert "check_collector_status.py" in compose
    assert "--max-price-age-ms" in compose
    assert "--max-orderbook-age-ms" in compose
    assert "--max-websocket-event-age-ms" in compose
    assert "--raw-root" in compose
    assert "/var/lib/polymarket/raw" in compose
    assert "--max-raw-event-age-ms" in compose
    assert '--raw-root "$DATA_DIR/raw"' in script
    assert "--max-raw-event-age-ms 30000" in script
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


def test_collector_entrypoint_enables_raw_event_journal() -> None:
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "collector" / "collector-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "POLYMARKET_RAW_EVENT_DIR" in compose
    assert "RAW_EVENT_DIR=" in entrypoint
    assert "--raw-event-dir" in entrypoint
    assert '"$RAW_EVENT_DIR"' in entrypoint


def test_collector_entrypoint_enables_hot_decision_journal() -> None:
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "collector" / "collector-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "POLYMARKET_DECISION_SNAPSHOT_DIR" in compose
    assert "DECISION_SNAPSHOT_DIR=" in entrypoint
    assert "--decision-snapshot-dir" in entrypoint
    assert '"$DECISION_SNAPSHOT_DIR"' in entrypoint


def test_collector_defaults_to_three_prewarm_windows() -> None:
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "collector" / "collector-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "POLYMARKET_PREWARM_WINDOWS=3" in env_example
    assert "POLYMARKET_PREWARM_WINDOWS:-3" in compose
    assert 'PREWARM_WINDOWS="${POLYMARKET_PREWARM_WINDOWS:-3}"' in entrypoint


def test_normalizer_sidecar_is_deployed_and_health_checked() -> None:
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "normalizer:" in compose
    assert "deploy/normalizer/Dockerfile" in compose
    assert "NORMALIZER_INTERVAL_SECONDS" in compose
    assert "polymarket-engine" in compose
    assert "--normalized-health-path" in compose
    assert "/var/lib/polymarket/live/normalized_health.json" in compose
    assert "up -d --build collector normalizer" in script
    assert "--normalized-health-path" in script
    assert "$DATA_DIR/live/normalized_health.json" in script


def test_normalizer_hot_loop_omits_state_snapshot_backfill() -> None:
    entrypoint = (
        ROOT / "deploy" / "normalizer" / "normalizer-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "polymarket-engine normalize-rust-events" in entrypoint
    assert "--include-state-snapshots" not in entrypoint


def test_deploy_script_requires_running_normalizer_before_success() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "normalizer_running()" in script
    assert "ps --services --status running normalizer" in script
    assert "normalizer_running && python3" in script
