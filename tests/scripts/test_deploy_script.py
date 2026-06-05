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


def test_collector_fast_status_keeps_five_second_snapshot_journal() -> None:
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "collector" / "collector-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "POLYMARKET_STATUS_INTERVAL_MS=100" in env_example
    assert "POLYMARKET_STATUS_INTERVAL_MS:-100" in compose
    assert 'STATUS_INTERVAL_MS="${POLYMARKET_STATUS_INTERVAL_MS:-100}"' in entrypoint
    assert "POLYMARKET_STATE_SNAPSHOT_INTERVAL_MS=5000" in env_example
    assert "POLYMARKET_STATE_SNAPSHOT_INTERVAL_MS:-5000" in compose
    assert (
        'STATE_SNAPSHOT_INTERVAL_MS="${POLYMARKET_STATE_SNAPSHOT_INTERVAL_MS:-5000}"'
        in entrypoint
    )
    assert "--state-snapshot-interval-ms" in entrypoint
    assert '"$STATE_SNAPSHOT_INTERVAL_MS"' in entrypoint


def test_collector_rest_backup_defaults_to_one_second_fast_backup() -> None:
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "collector" / "collector-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "POLYMARKET_REST_BACKUP_INTERVAL_MS=1000" in env_example
    assert "POLYMARKET_REST_BACKUP_INTERVAL_MS:-1000" in compose
    assert 'REST_BACKUP_INTERVAL_MS="${POLYMARKET_REST_BACKUP_INTERVAL_MS:-1000}"' in entrypoint


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


def test_collector_defaults_to_two_prewarm_windows() -> None:
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "collector" / "collector-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "POLYMARKET_PREWARM_WINDOWS=2" in env_example
    assert "POLYMARKET_PREWARM_WINDOWS:-2" in compose
    assert 'PREWARM_WINDOWS="${POLYMARKET_PREWARM_WINDOWS:-2}"' in entrypoint


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


def test_runtime_api_service_is_deployed_with_engine_compose() -> None:
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    pc_script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert "api:" in compose
    assert "uvicorn" in compose
    assert "polymarket_engine.app:app" in compose
    assert "POLYMARKET_STATUS_PATH: /var/lib/polymarket/live/status.json" in compose
    assert "POLYMARKET_DUCKDB_PATH: /var/lib/polymarket/db/polymarket.duckdb" in compose
    assert "POLYMARKET_OUTCOME_STATUS_PATH: /var/lib/polymarket/live/outcomes.json" in compose
    assert "POLYMARKET_VOLATILITY_STATUS_PATH: /var/lib/polymarket/live/volatility.json" in compose
    assert "${POLYMARKET_API_PORT:-8000}:8000" in compose
    assert "POLYMARKET_API_PORT=8000" in env_example
    assert "POLYMARKET_ENABLE_CONTAINER_STATUS=1" in env_example
    assert "up -d collector normalizer api" in script
    assert "up -d --build collector normalizer api" in script
    assert "logs --tail=80 collector normalizer api" in script
    assert "gpu-probability-worker" not in script
    assert "docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml ps" in pc_script
    assert 'get_json("/health")' in pc_script
    assert 'get_json("/api/runtime/live?limit=8")' in pc_script
    assert 'get_json("/api/runtime/outcomes?limit=8")' in pc_script
    assert "/api/runtime/live/stream?limit=8&interval_ms=250&max_events=1" in pc_script


def test_compose_and_env_support_prebuilt_image_overrides() -> None:
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )

    assert (
        "image: ${POLYMARKET_COLLECTOR_IMAGE:-polymarket-rust-collector:latest}"
        in compose
    )
    assert (
        "image: ${POLYMARKET_NORMALIZER_IMAGE:-polymarket-normalizer:latest}"
        in compose
    )
    assert "POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25" in env_example
    assert "POLYMARKET_COLLECTOR_IMAGE=polymarket-rust-collector:latest" in env_example
    assert "POLYMARKET_NORMALIZER_IMAGE=polymarket-normalizer:latest" in env_example
    assert "POLYMARKET_CUDA_PROBABILITY_IMAGE=polymarket-cuda-probability:latest" in env_example


def test_pc_image_build_script_exports_docker_tarballs_and_manifest() -> None:
    script = (ROOT / "scripts" / "build_images_pc.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "DOCKER_BUILDKIT=1" in script
    assert "git -C \"$ROOT\" diff --quiet" in script
    assert "git -C \"$ROOT\" diff --cached --quiet" in script
    assert "git -C \"$ROOT\" ls-files --others --exclude-standard" in script
    assert 'DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-HEAD}"' in script
    assert 'FULL_SHA="$(git -C "$ROOT" rev-parse "$DEPLOY_REF^{commit}")"' in script
    assert 'HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"' in script
    assert 'if [ "$HEAD_SHA" != "$FULL_SHA" ]; then' in script
    assert 'SHORT_SHA="${FULL_SHA:0:12}"' in script
    assert 'TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"' in script
    assert "DIST_DIR=\"${DIST_DIR:-$ROOT/dist/docker}\"" in script
    assert "polymarket-rust-collector:${SHORT_SHA}" in script
    assert "polymarket-normalizer:${SHORT_SHA}" in script
    assert "docker buildx build" in script
    assert '--platform "$TARGET_PLATFORM"' in script
    assert "--load" in script
    assert "docker save" in script
    assert 'npm --prefix "$ROOT/ui" run build' in script
    assert "COPY ui/dist ./ui/dist" in script
    assert "normalizer-ui-${SHORT_SHA}" in script
    assert "manifest-${SHORT_SHA}.txt" in script
    assert "full_sha=" in script
    assert "short_sha=" in script
    assert "deploy_ref=" in script
    assert "target_platform=" in script
    assert "ui_dist=" in script
    assert "collector_image_id=" in script
    assert "normalizer_image_id=" in script


def test_prebuilt_image_deploy_script_loads_images_and_uses_deploy_fast_path() -> None:
    script = (ROOT / "scripts" / "deploy_prebuilt_images.sh").read_text(
        encoding="utf-8"
    )

    assert "set -euo pipefail" in script
    assert "git -C \"$ROOT\" diff --quiet" in script
    assert "git -C \"$ROOT\" diff --cached --quiet" in script
    assert "git -C \"$ROOT\" ls-files --others --exclude-standard" in script
    assert 'DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-HEAD}"' in script
    assert 'FULL_SHA="$(git -C "$ROOT" rev-parse "$DEPLOY_REF^{commit}")"' in script
    assert 'HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"' in script
    assert 'SHORT_SHA="${FULL_SHA:0:12}"' in script
    assert "docker load" in script
    assert "POLYMARKET_DEPLOY_USE_PREBUILT=1" in script
    assert "POLYMARKET_DEPLOY_REF='$FULL_SHA'" in script
    assert "POLYMARKET_EXPECTED_DEPLOY_SHA='$FULL_SHA'" in script
    assert "POLYMARKET_COLLECTOR_IMAGE='$COLLECTOR_IMAGE'" in script
    assert "POLYMARKET_NORMALIZER_IMAGE='$NORMALIZER_IMAGE'" in script
    assert "POLYMARKET_CUDA_PROBABILITY_IMAGE" not in script
    assert "POLYMARKET_DATA_DIR='$POLYMARKET_DATA_DIR'" in script
    assert "check_collector_status.py" in script
    assert "--expected-prewarm-windows 2" in script
    assert "--build" not in script


def test_pc_deploy_script_streams_bundle_and_images_into_wsl() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert 'PC_HOST="${PC_HOST:-ender@100.72.104.49}"' in script
    assert 'PC_WSL_DISTRO="${PC_WSL_DISTRO:-Ubuntu}"' in script
    assert 'PC_REPO="${PC_REPO:-/home/ender/polymarket}"' in script
    assert 'PC_BUNDLE="${PC_BUNDLE:-/home/ender/polymarket.bundle}"' in script
    assert 'PC_DATA_DIR="${PC_DATA_DIR:-/home/ender/polymarket-data}"' in script
    assert 'git -C "$ROOT" bundle create' in script
    assert "wsl_put_file()" in script
    assert "cat >" in script
    assert 'polymarket-rust-collector-${SHORT_SHA}.tar' in script
    assert 'polymarket-normalizer-${SHORT_SHA}.tar' in script
    assert 'polymarket-cuda-probability-${SHORT_SHA}.tar' in script


def test_pc_deploy_script_runs_prebuilt_deploy_gate_with_pc_cadence() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert 'PC_NORMALIZER_INTERVAL_SECONDS="${PC_NORMALIZER_INTERVAL_SECONDS:-1.0}"' in script
    assert 'PC_REST_BACKUP_INTERVAL_MS="${PC_REST_BACKUP_INTERVAL_MS:-1000}"' in script
    assert "export POLYMARKET_DEPLOY_USE_PREBUILT=1" in script
    assert 'POLYMARKET_DEPLOY_REF="\\$FULL_SHA"' in script
    assert 'POLYMARKET_EXPECTED_DEPLOY_SHA="\\$FULL_SHA"' in script
    assert 'POLYMARKET_COLLECTOR_IMAGE="\\$COLLECTOR_IMAGE"' in script
    assert 'POLYMARKET_NORMALIZER_IMAGE="\\$NORMALIZER_IMAGE"' in script
    assert 'POLYMARKET_CUDA_PROBABILITY_IMAGE="\\$CUDA_PROBABILITY_IMAGE"' in script
    assert 'docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d gpu-probability-worker' in script
    assert 'POLYMARKET_DATA_DIR="\\$PC_DATA_DIR"' in script
    assert 'DEPLOY_FORCE=1' in script
    assert (
        'POLYMARKET_NORMALIZER_INTERVAL_SECONDS="\\$PC_NORMALIZER_INTERVAL_SECONDS"'
        in script
    )
    assert 'set_env POLYMARKET_REST_BACKUP_INTERVAL_MS "\\$PC_REST_BACKUP_INTERVAL_MS"' in script
    assert 'POLYMARKET_REST_BACKUP_INTERVAL_MS="\\$PC_REST_BACKUP_INTERVAL_MS"' in script
    assert "scripts/check_collector_status.py" in script
    assert "--expected-prewarm-windows 2" in script


def test_pc_deploy_script_refreshes_tui_desktop_launcher() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert "open-polymarket-tui.sh" in script
    assert "open-polymarket-tui.cmd" in script
    assert "Polymarket TUI.lnk" in script
    assert "CreateShortcut" in script
    assert "Checking Polymarket runtime..." in script
    assert "Runtime already live." in script
    assert "Runtime not live; starting containers..." in script
    assert (
        "docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml "
        "up -d --no-recreate collector normalizer api gpu-probability-worker"
    ) in script
    assert 'docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml ps --services --status running gpu-probability-worker' in script
    assert "python3 -c %q" in script
    assert 'm=p.get("monitor") or {}' in script
    assert 'len(m.get("orderbooks") or []) > 0' in script
    assert 'p.get("status",{}).get("counts",{})' not in script
    assert "--engine-api-url http://127.0.0.1:%s --poll-interval-ms 250" in script
    assert '"\\$PC_API_PORT"' in script
    assert "\\\\n' \"\\$PC_API_PORT\"" not in script
    assert "polymarket-runtime-api" not in script


def test_pc_deploy_script_refreshes_browser_ui_launcher() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert "open-polymarket-ui.sh" in script
    assert "open-polymarket-ui.ps1" in script
    assert "open-polymarket-ui.cmd" in script
    assert "Polymarket Runtime Monitor.lnk" in script
    assert "Checking browser runtime..." in script
    assert "Waiting for browser UI..." in script
    assert "Start-Process 'http://127.0.0.1:__PC_API_PORT__/'" in script
    assert "THEPC browser UI launcher installed" in script


def test_pc_tui_desktop_launcher_logs_failures_and_forces_new_terminal_window() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert "open-polymarket-tui.ps1" in script
    assert "open-polymarket-tui-window.sh" in script
    assert "polymarket-tui-launch.log" in script
    assert "Start-Transcript" in script
    assert "Start-Process -FilePath 'wt.exe'" in script
    assert "'-w', 'new'" in script
    assert "Read-Host 'Press Enter to close'" in script
    assert "\\$shortcut.TargetPath = 'powershell.exe'" in script


def test_pc_deploy_script_prevents_powershell_from_consuming_remote_script() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    powershell_index = script.index("powershell.exe -NoProfile")
    deploy_gate_index = script.index("export POLYMARKET_DEPLOY_USE_PREBUILT=1")

    assert powershell_index < deploy_gate_index
    assert "< /dev/null" in script[powershell_index:deploy_gate_index]


def test_deploy_script_supports_prebuilt_images_with_build_fallback() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert 'USE_PREBUILT="${POLYMARKET_DEPLOY_USE_PREBUILT:-0}"' in script
    assert 'ALLOW_SPOON_BUILD="${POLYMARKET_DEPLOY_ALLOW_SPOON_BUILD:-0}"' in script
    assert 'EXPECTED_DEPLOY_SHA="${POLYMARKET_EXPECTED_DEPLOY_SHA:-}"' in script
    assert "required_image_available()" in script
    assert "docker image inspect" in script
    assert "POLYMARKET_DEPLOY_USE_PREBUILT=1" in script
    assert "requires POLYMARKET_EXPECTED_DEPLOY_SHA" in script
    assert 'EXPECTED_FULL_SHA="$(git rev-parse "$EXPECTED_DEPLOY_SHA^{commit}")"' in script
    assert 'if [ "$REMOTE" != "$EXPECTED_FULL_SHA" ]; then' in script
    assert 'EXPECTED_SHORT_SHA="${EXPECTED_FULL_SHA:0:12}"' in script
    assert 'EXPECTED_COLLECTOR_IMAGE="polymarket-rust-collector:$EXPECTED_SHORT_SHA"' in script
    assert 'EXPECTED_NORMALIZER_IMAGE="polymarket-normalizer:$EXPECTED_SHORT_SHA"' in script
    assert 'if [ "$COLLECTOR_IMAGE" != "$EXPECTED_COLLECTOR_IMAGE" ]; then' in script
    assert 'if [ "$NORMALIZER_IMAGE" != "$EXPECTED_NORMALIZER_IMAGE" ]; then' in script
    assert '[ "$USE_PREBUILT" != "1" ] && [ "$LOCAL" = "$REMOTE" ]' in script
    assert 'export POLYMARKET_COLLECTOR_IMAGE="$COLLECTOR_IMAGE"' in script
    assert 'export POLYMARKET_NORMALIZER_IMAGE="$NORMALIZER_IMAGE"' in script
    assert 'POLYMARKET_CUDA_PROBABILITY_IMAGE' not in script
    assert 'compose -f "$COMPOSE_FILE" up -d collector normalizer api' in script
    assert 'compose -f "$COMPOSE_FILE" up -d --build collector normalizer api' in script
    assert 'export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-1}"' in script


def test_normalizer_hot_loop_omits_state_snapshot_backfill() -> None:
    entrypoint = (
        ROOT / "deploy" / "normalizer" / "normalizer-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "run-rust-normalizer-sidecar" in entrypoint
    assert "--include-state-snapshots" not in entrypoint


def test_normalizer_defaults_to_quarter_second_checkpointed_cadence() -> None:
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "normalizer" / "normalizer-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25" in env_example
    assert "POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES=0" in env_example
    assert "POLYMARKET_ENABLE_RUNTIME_PROBABILITIES=0" in env_example
    assert "POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.25" in compose
    assert "POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES:-0" in compose
    assert "POLYMARKET_ENABLE_RUNTIME_PROBABILITIES:-0" in compose
    assert 'INTERVAL_SECONDS="${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.25}"' in entrypoint
    assert 'ENABLE_PROBABILITIES="${POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES:-0}"' in entrypoint
    assert 'OUTCOME_STATUS_PATH="${POLYMARKET_OUTCOME_STATUS_PATH:-$LIVE_DIR/outcomes.json}"' in entrypoint
    assert 'VOLATILITY_STATUS_PATH="${POLYMARKET_VOLATILITY_STATUS_PATH:-$LIVE_DIR/volatility.json}"' in entrypoint
    assert "run-rust-normalizer-sidecar" in entrypoint
    assert "exec polymarket-engine" in entrypoint
    assert "while true" not in entrypoint
    assert '--interval-seconds "$INTERVAL_SECONDS"' in entrypoint
    assert '--normalized-health-path "$NORMALIZED_HEALTH_PATH"' in entrypoint
    assert '--outcome-status-path "$OUTCOME_STATUS_PATH"' in entrypoint
    assert '--volatility-status-path "$VOLATILITY_STATUS_PATH"' in entrypoint
    assert "--enable-probabilities" in entrypoint


def test_deploy_script_requires_running_normalizer_before_success() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "normalizer_running()" in script
    assert "ps --services --status running normalizer" in script
    assert "normalizer_running && normalizer_uses_sidecar && python3" in script


def test_deploy_script_rejects_old_normalize_rust_events_normalizer() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "normalizer_uses_sidecar()" in script
    assert "run-rust-normalizer-sidecar" in script
    assert 'compose -f "$COMPOSE_FILE" top normalizer' in script
    assert "ps -eo args" not in script
    assert "normalizer_running && normalizer_uses_sidecar && python3" in script
