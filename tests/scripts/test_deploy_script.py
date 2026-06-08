import shutil
import subprocess
from pathlib import Path

import pytest

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
    assert "POLYMARKET_PROBABILITY_INPUTS_PATH" in compose
    assert "polymarket-engine" in compose
    assert "--normalized-health-path" in compose
    assert "/var/lib/polymarket/live/normalized_health.json" in compose
    assert 'DEPLOY_ROLE="${POLYMARKET_DEPLOY_ROLE:-spoon-cpu-authority}"' in script
    assert 'printf \'%s\\n\' "collector normalizer"' in script
    assert "compose_for_role up -d --build $START_SERVICES" in script
    assert "outcome_refresh_stopped()" in script
    assert "outcome_status_fresh()" in script
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
    assert "OPENBLAS_NUM_THREADS" in compose
    assert "OMP_NUM_THREADS" in compose
    assert "MKL_NUM_THREADS" in compose
    assert "NUMEXPR_NUM_THREADS" in compose
    assert "cpus: \"${POLYMARKET_GPU_WORKER_CPUS:-1.0}\"" in compose
    assert "mem_limit: \"${POLYMARKET_GPU_WORKER_MEM_LIMIT:-1536m}\"" in compose
    assert "${POLYMARKET_API_PORT:-8000}:8000" in compose
    assert "POLYMARKET_API_PORT=8000" in env_example
    assert "POLYMARKET_ENABLE_CONTAINER_STATUS=1" in env_example
    assert "POLYMARKET_ENABLE_RUNTIME_PROBABILITIES=0" in env_example
    assert "POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE=0" in env_example
    assert "set_env POLYMARKET_ENABLE_RUNTIME_PROBABILITIES 1 deploy/collector/.env" in pc_script
    assert 'DEPLOY_ROLE="${POLYMARKET_DEPLOY_ROLE:-spoon-cpu-authority}"' in script
    assert "docker-compose.spoon-cpu-authority.yml" in script
    assert 'printf \'%s\\n\' "collector normalizer api gpu-probability-worker"' in script
    assert "compose_for_role logs --tail=80 $START_SERVICES" in script
    assert "docker compose --env-file deploy/collector/.env" in pc_script
    assert "-f deploy/collector/docker-compose.thepc-gpu-api.yml ps" in pc_script
    assert "def wait_json(path: str, attempts: int = 30)" in pc_script
    assert 'health = wait_json("/health")' in pc_script
    assert 'get_json("/api/runtime/live?limit=8")' in pc_script
    assert "has_orderbooks" in pc_script
    assert 'live = {"error": repr(exc)}' in pc_script
    assert 'get_json("/api/runtime/probabilities?limit=8")' in pc_script
    assert "for _ in range(30):" in pc_script
    assert "except Exception as exc:" in pc_script
    assert 'probabilities = {"error": repr(exc)}' in pc_script
    assert 'for key in ("rows", "last_good_rows"):' in pc_script
    assert "probability_candidate_rows(probabilities)" in pc_script
    assert "row_is_recent(row, now)" in pc_script
    assert 'row.get("model_version") == "ensemble-v1"' in pc_script
    assert 'required_generators.issubset(set(row.get("prior_fragment_generators") or []))' in pc_script
    assert 'probabilities.get("state") in {"OK", "NOWCAST"}' in pc_script
    assert 'parse_ts(row.get("valid_until"))' in pc_script
    assert 'parse_ts(row.get("generated_at"))' in pc_script
    assert "runtime probabilities smoke failed" in pc_script
    assert 'outcomes = wait_json("/api/runtime/outcomes?limit=8")' in pc_script
    assert 'outcomes.get("state") == "LOCKED"' in pc_script
    assert "/api/runtime/live/stream?limit=8&interval_ms=250&max_events=1" in pc_script


def test_spoon_deploy_defaults_to_cpu_authority_overlay() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert 'DEPLOY_ROLE="${POLYMARKET_DEPLOY_ROLE:-spoon-cpu-authority}"' in script
    assert "docker-compose.spoon-cpu-authority.yml" in script
    assert 'printf \'%s\\n\' "collector normalizer"' in script
    assert "stop_services_excluded_by_role" in script
    assert 'compose -f "$COMPOSE_FILE" stop api gpu-probability-worker' in script


def test_spoon_deploy_fast_path_stops_role_excluded_services_before_exit() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    fast_path_index = script.index(
        'if [ "$USE_PREBUILT" != "1" ] && [ "$LOCAL" = "$REMOTE" ]'
    )
    stop_index = script.index("stop_services_excluded_by_role", fast_path_index)
    exit_index = script.index("exit 0", fast_path_index)

    assert stop_index < exit_index


def test_pc_deploy_defaults_to_gpu_api_overlay_and_sync() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert 'PC_DEPLOY_ROLE="${PC_DEPLOY_ROLE:-thepc-gpu-api}"' in script
    assert "docker-compose.thepc-gpu-api.yml" in script
    assert "stop collector normalizer outcome-refresh" in script
    assert "up -d --no-build api gpu-probability-worker" in script
    assert "install_thepc_spoon_artifact_sync.sh" in script
    assert (
        'set_env POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT "\\$PC_PROBABILITY_CPU_TARGET_PERCENT"'
        in script
    )
    assert (
        'set_env POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT '
        '"\\$PC_PROBABILITY_CPU_SOFT_MAX_PERCENT"'
    ) in script


def test_pc_deploy_fetches_exact_main_sha_from_github_ssh() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert 'PC_GIT_REMOTE="${PC_GIT_REMOTE:-git@github.com:AnimeWeeb9000/polymarket.git}"' in script
    assert 'PC_BRANCH="${PC_BRANCH:-main}"' in script
    assert 'git -C "$ROOT" fetch --quiet origin main' in script
    assert 'LOCAL_MAIN_SHA="$(git -C "$ROOT" rev-parse origin/main^{commit})"' in script
    assert 'origin/main is $LOCAL_MAIN_SHA but deploy ref is $FULL_SHA' in script
    assert 'git ls-remote "\\$PC_GIT_REMOTE" HEAD' in script
    assert 'git clone "\\$PC_GIT_REMOTE" "\\$PC_REPO"' in script
    assert 'git fetch --quiet --prune origin "\\$PC_BRANCH"' in script
    assert 'git checkout -B "\\$PC_BRANCH" "\\$FULL_SHA"' in script
    assert "git bundle create" not in script
    assert "PC_BUNDLE" not in script


def test_thepc_spoon_artifact_sync_installer_is_role_safe() -> None:
    script = (ROOT / "scripts" / "install_thepc_spoon_artifact_sync.sh").read_text(
        encoding="utf-8"
    )

    assert (
        "status.json normalized_health.json probability_inputs.json "
        "probability_fragments.json outcomes.json volatility.json"
    ) in script
    assert "Host spoon" in script
    assert 'SPOON_HOSTNAME="${SPOON_HOSTNAME:-100.126.126.1}"' in script
    assert "HostName {hostname}" in script
    assert 'SPOON_USER="${SPOON_USER:-spoon}"' in script
    assert "User {user}" in script
    assert "polymarket-spoon-artifact-sync.service" in script
    assert "systemctl --user enable --now polymarket-spoon-artifact-sync.service" in script
    assert "nohup bash -lc" in script
    assert "artifact sync skipped" in script


def test_compose_and_env_support_prebuilt_image_overrides() -> None:
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )
    entrypoint = (ROOT / "deploy" / "gpu" / "gpu-probability-entrypoint.sh").read_text(
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
    assert (
        "image: ${POLYMARKET_CUDA_PROBABILITY_IMAGE:-polymarket-cuda-probability:latest}"
        in compose
    )
    assert "POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25" in env_example
    assert "POLYMARKET_PROBABILITY_WORKER_MODE=ensemble" in env_example
    assert "POLYMARKET_ENSEMBLE_GENERATOR_POLICY=all_four_every_cycle" in env_example
    assert "POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT=15.0" in env_example
    assert "POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT=20.0" in env_example
    assert "POLYMARKET_PROBABILITY_MAX_RSS_MB=512" in env_example
    assert "POLYMARKET_PROBABILITY_MAX_CYCLE_RUNTIME_MS=750" in env_example
    assert "POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS=500000" in env_example
    assert "POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS=4000" in env_example
    assert "POLYMARKET_PROBABILITY_SUSTAINED_BREACH_CYCLES=3" in env_example
    assert "POLYMARKET_CUDA_PROBABILITY_MAX_INPUT_SNAPSHOT_AGE_SECONDS=30.0" in env_example
    assert (
        "POLYMARKET_PROBABILITY_FRAGMENTS_PATH=/var/lib/polymarket/live/probability_fragments.json"
        in env_example
    )
    assert "POLYMARKET_ENSEMBLE_FRAGMENT_MAX_ROWS=250000" in env_example
    assert "POLYMARKET_ENSEMBLE_CPU_THREADS=1" in env_example
    assert "POLYMARKET_ENSEMBLE_CPU_THREADS:" in compose
    assert "POLYMARKET_GPU_WORKER_CPUS" in env_example
    assert "POLYMARKET_GPU_WORKER_MEM_LIMIT" in env_example
    assert "POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT:" in compose
    assert "POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT:" in compose
    assert "POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS:" in compose
    assert "POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS:" in compose
    assert "POLYMARKET_PROBABILITY_FRAGMENTS_PATH:" in compose
    assert "POLYMARKET_CUDA_PROBABILITY_MAX_INPUT_SNAPSHOT_AGE_SECONDS:-30.0" in compose
    assert '--worker-mode "$WORKER_MODE"' in entrypoint
    assert (
        'PROBABILITY_FRAGMENTS_PATH="${POLYMARKET_PROBABILITY_FRAGMENTS_PATH:-/var/lib/polymarket/live/probability_fragments.json}"'
        in entrypoint
    )
    assert (
        'MAX_INPUT_SNAPSHOT_AGE_SECONDS="${POLYMARKET_CUDA_PROBABILITY_MAX_INPUT_SNAPSHOT_AGE_SECONDS:-30.0}"'
        in entrypoint
    )
    assert '--probability-fragments-path "$PROBABILITY_FRAGMENTS_PATH"' in entrypoint
    assert '--generator-policy "$GENERATOR_POLICY"' in entrypoint
    assert '--cpu-target-percent "$CPU_TARGET_PERCENT"' in entrypoint
    assert '--cpu-soft-max-percent "$CPU_SOFT_MAX_PERCENT"' in entrypoint
    assert '--max-rss-mb "$MAX_RSS_MB"' in entrypoint
    assert '--max-cycle-runtime-ms "$MAX_CYCLE_RUNTIME_MS"' in entrypoint
    assert '--max-total-paths "$MAX_TOTAL_PATHS"' in entrypoint
    assert '--min-total-paths "$MIN_TOTAL_PATHS"' in entrypoint
    assert '--sustained-breach-cycles "$SUSTAINED_BREACH_CYCLES"' in entrypoint
    assert '--fragment-max-rows "$FRAGMENT_MAX_ROWS"' in entrypoint
    assert '--cpu-threads "$CPU_THREADS"' in entrypoint
    assert "POLYMARKET_COLLECTOR_IMAGE=polymarket-rust-collector:latest" in env_example
    assert "POLYMARKET_NORMALIZER_IMAGE=polymarket-normalizer:latest" in env_example
    assert "POLYMARKET_CUDA_PROBABILITY_IMAGE=polymarket-cuda-probability:latest" in env_example


def test_spoon_and_thepc_compose_overlays_render() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI unavailable")
    compose = ROOT / "deploy" / "collector" / "docker-compose.yml"
    for overlay in (
        ROOT / "deploy" / "collector" / "docker-compose.spoon-cpu-authority.yml",
        ROOT / "deploy" / "collector" / "docker-compose.thepc-gpu-api.yml",
    ):
        subprocess.run(
            ["docker", "compose", "-f", str(compose), "-f", str(overlay), "config"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )


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
    assert 'SAVE_TARS="${POLYMARKET_BUILD_SAVE_TARS:-1}"' in script
    assert "DIST_DIR=\"${DIST_DIR:-$ROOT/dist/docker}\"" in script
    assert "polymarket-rust-collector:${SHORT_SHA}" in script
    assert "polymarket-normalizer:${SHORT_SHA}" in script
    assert "polymarket-cuda-probability:${SHORT_SHA}" in script
    assert "docker buildx build" in script
    assert "deploy/gpu/Dockerfile" in script
    assert '--platform "$TARGET_PLATFORM"' in script
    assert "--load" in script
    assert "docker save" in script
    assert 'if [ "$SAVE_TARS" = "1" ]; then' in script
    assert "manifest-${SHORT_SHA}.txt" in script
    assert "full_sha=" in script
    assert "short_sha=" in script
    assert "deploy_ref=" in script
    assert "target_platform=" in script
    assert "collector_image_id=" in script
    assert "normalizer_image_id=" in script
    assert "cuda_probability_image_id=" in script
    assert "cuda_probability_tar=" in script
    assert "saved_tars=" in script


def test_pc_deploy_installs_duckdb_ui_snapshot_launcher() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert "open-polymarket-duckdb-ui.sh" in script
    assert "open-polymarket-duckdb-ui-window.sh" in script
    assert "open-polymarket-duckdb-ui.cmd" in script
    assert "Polymarket DuckDB UI.lnk" in script
    assert "https://install.duckdb.org" in script
    assert "polymarket_duckdb_viewer.py" in script
    assert "ThreadingHTTPServer" in script
    assert 'curl -fsS --max-time 2 "http://127.0.0.1:\\${PORT}/api/tables"' in script
    assert 'SNAPSHOT_TMP="\\$SNAPSHOT_DIR/snapshot.duckdb"' in script
    assert "ATTACH \\$(quote_sql_string \"\\$SOURCE_DB\") AS source_db (READ_ONLY)" in script
    assert "COPY FROM DATABASE source_db TO snapshot" in script
    assert 'subprocess.run(' in script
    assert '"-readonly", self.db_path, "-json", "-c", sql' in script
    assert 'PC_DEPLOY_ROLE="\\${PC_DEPLOY_ROLE:-thepc-gpu-api}"' in script
    assert "stop collector normalizer outcome-refresh" in script
    assert "up -d --no-deps normalizer" in script
    assert "up -d --no-deps api gpu-probability-worker" in script


def test_pc_deploy_duckdb_ui_avoids_js_templates_in_outer_heredoc() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert "${{" not in script
    assert "`" not in script
    assert "'rows ' + (offset + 1) + '-' + (offset + limit)" in script
    assert "'/api/table?schema=' + encodeURIComponent(selected.schema)" in script


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
    assert "POLYMARKET_CUDA_PROBABILITY_IMAGE='$CUDA_PROBABILITY_IMAGE'" in script
    assert "POLYMARKET_DATA_DIR='$POLYMARKET_DATA_DIR'" in script
    assert "check_collector_status.py" in script
    assert "--expected-prewarm-windows 2" in script
    assert "--build" not in script


def test_pc_deploy_script_fetches_github_main_and_streams_image_artifacts_into_wsl() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert 'PC_DEPLOY_MODE="${PC_DEPLOY_MODE:-remote-build}"' in script
    assert 'PC_REMOTE_BUILD_SAVE_TARS="${PC_REMOTE_BUILD_SAVE_TARS:-0}"' in script
    assert "remote-build | image-tar)" in script
    assert 'PC_HOST="${PC_HOST:-ender@100.72.104.49}"' in script
    assert 'PC_WSL_DISTRO="${PC_WSL_DISTRO:-Ubuntu}"' in script
    assert 'PC_REPO="${PC_REPO:-/home/ender/polymarket}"' in script
    assert 'PC_GIT_REMOTE="${PC_GIT_REMOTE:-git@github.com:AnimeWeeb9000/polymarket.git}"' in script
    assert 'PC_DATA_DIR="${PC_DATA_DIR:-/home/ender/polymarket-data}"' in script
    assert "wsl_put_artifact_file()" in script
    assert "cat >" in script
    assert 'polymarket-rust-collector-${SHORT_SHA}.tar' in script
    assert 'polymarket-normalizer-${SHORT_SHA}.tar' in script
    assert 'polymarket-cuda-probability-${SHORT_SHA}.tar' in script
    assert "THEPC WSL will fetch GitHub main and build images locally" in script
    assert "THEPC WSL will fetch GitHub main; copying image tarballs" in script
    assert 'if [ "$PC_DEPLOY_MODE" = "image-tar" ]; then' in script
    assert 'git ls-remote "\\$PC_GIT_REMOTE" HEAD' in script
    assert 'git clone "\\$PC_GIT_REMOTE" "\\$PC_REPO"' in script
    assert 'set_env POLYMARKET_CUDA_PROBABILITY_IMAGE "\\$CUDA_PROBABILITY_IMAGE"' in script
    assert 'DOCKER_CONFIG="\\$PC_DATA_DIR/docker-config"' in script
    assert "printf '%s\\n' '{\"auths\":{}}'" in script
    assert "export DOCKER_CONFIG" in script
    assert 'POLYMARKET_BUILD_SAVE_TARS="\\$PC_REMOTE_BUILD_SAVE_TARS"' in script
    assert 'POLYMARKET_DEPLOY_REF="\\$FULL_SHA"' in script
    assert './scripts/build_images_pc.sh' in script
    assert 'TUI_BIN="\\$PC_REPO/dist/docker/polymarket-cockpit-tui-\\$SHORT_SHA"' in script
    assert 'docker load -i "\\$CUDA_PROBABILITY_TAR"' in script


def test_pc_deploy_script_runs_prebuilt_deploy_gate_with_pc_cadence() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert 'PC_NORMALIZER_INTERVAL_SECONDS="${PC_NORMALIZER_INTERVAL_SECONDS:-0.1}"' in script
    assert 'PC_REST_BACKUP_INTERVAL_MS="${PC_REST_BACKUP_INTERVAL_MS:-1000}"' in script
    assert 'PC_DEPLOY_ROLE="${PC_DEPLOY_ROLE:-thepc-gpu-api}"' in script
    assert 'PC_PROBABILITY_CPU_TARGET_PERCENT="${PC_PROBABILITY_CPU_TARGET_PERCENT:-15.0}"' in script
    assert (
        'PC_PROBABILITY_CPU_SOFT_MAX_PERCENT="${PC_PROBABILITY_CPU_SOFT_MAX_PERCENT:-20.0}"'
        in script
    )
    assert 'PC_PROBABILITY_MAX_TOTAL_PATHS="${PC_PROBABILITY_MAX_TOTAL_PATHS:-500000}"' in script
    assert 'PC_PROBABILITY_MIN_TOTAL_PATHS="${PC_PROBABILITY_MIN_TOTAL_PATHS:-4000}"' in script
    assert 'PC_GPU_WORKER_MEM_LIMIT="${PC_GPU_WORKER_MEM_LIMIT:-1536m}"' in script
    assert 'PC_DEPLOY_ROLE=$(shell_quote "$PC_DEPLOY_ROLE")' in script
    assert (
        'PC_PROBABILITY_CPU_TARGET_PERCENT=$(shell_quote "$PC_PROBABILITY_CPU_TARGET_PERCENT")'
        in script
    )
    assert (
        'PC_PROBABILITY_CPU_SOFT_MAX_PERCENT=$(shell_quote "$PC_PROBABILITY_CPU_SOFT_MAX_PERCENT")'
        in script
    )
    assert 'PC_PROBABILITY_MAX_TOTAL_PATHS=$(shell_quote "$PC_PROBABILITY_MAX_TOTAL_PATHS")' in script
    assert 'PC_PROBABILITY_MIN_TOTAL_PATHS=$(shell_quote "$PC_PROBABILITY_MIN_TOTAL_PATHS")' in script
    assert 'PC_GPU_WORKER_MEM_LIMIT=$(shell_quote "$PC_GPU_WORKER_MEM_LIMIT")' in script
    assert "export POLYMARKET_DEPLOY_USE_PREBUILT=1" in script
    assert 'POLYMARKET_DEPLOY_REF="\\$FULL_SHA"' in script
    assert 'POLYMARKET_EXPECTED_DEPLOY_SHA="\\$FULL_SHA"' in script
    assert 'POLYMARKET_COLLECTOR_IMAGE="\\$COLLECTOR_IMAGE"' in script
    assert 'POLYMARKET_NORMALIZER_IMAGE="\\$NORMALIZER_IMAGE"' in script
    assert 'POLYMARKET_CUDA_PROBABILITY_IMAGE="\\$CUDA_PROBABILITY_IMAGE"' in script
    assert 'POLYMARKET_DATA_DIR="\\$PC_DATA_DIR"' in script
    assert 'DEPLOY_FORCE=1' in script
    assert (
        'POLYMARKET_NORMALIZER_INTERVAL_SECONDS="\\$PC_NORMALIZER_INTERVAL_SECONDS"'
        in script
    )
    assert 'set_env POLYMARKET_REST_BACKUP_INTERVAL_MS "\\$PC_REST_BACKUP_INTERVAL_MS"' in script
    assert (
        'set_env POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS "\\$PC_PROBABILITY_MAX_TOTAL_PATHS"'
        in script
    )
    assert (
        'set_env POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT "\\$PC_PROBABILITY_CPU_TARGET_PERCENT"'
        in script
    )
    assert (
        'set_env POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT "\\$PC_PROBABILITY_CPU_SOFT_MAX_PERCENT"'
        in script
    )
    assert (
        'set_env POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS "\\$PC_PROBABILITY_MIN_TOTAL_PATHS"'
        in script
    )
    assert 'set_env POLYMARKET_GPU_WORKER_MEM_LIMIT "\\$PC_GPU_WORKER_MEM_LIMIT"' in script
    assert "set_env POLYMARKET_ENABLE_RUNTIME_PROBABILITIES 1 deploy/collector/.env" in script
    assert "set_env POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE 0 deploy/collector/.env" in script
    assert 'POLYMARKET_REST_BACKUP_INTERVAL_MS="\\$PC_REST_BACKUP_INTERVAL_MS"' in script
    assert "scripts/check_collector_status.py" in script
    assert "collector_status_ok=0" in script
    assert "install_thepc_spoon_artifact_sync.sh" in script
    assert "docker-compose.thepc-gpu-api.yml" in script
    assert "stop collector normalizer outcome-refresh" in script
    assert "up -d --no-build api gpu-probability-worker" in script
    assert "for _ in \\$(seq 1 30); do" in script
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
        "docker compose --env-file deploy/collector/.env "
        "-f deploy/collector/docker-compose.yml "
        "-f deploy/collector/docker-compose.thepc-gpu-api.yml "
        "up -d --no-recreate api gpu-probability-worker"
    ) in script
    assert "python3 -c %q" in script
    assert 'm=p.get("monitor") or {}' in script
    assert 'len(m.get("orderbooks") or []) > 0' in script
    assert 'p.get("status",{}).get("counts",{})' not in script
    assert "--engine-api-url http://127.0.0.1:%s --poll-interval-ms 250" in script
    assert '"\\$PC_API_PORT"' in script
    assert "\\\\n' \"\\$PC_API_PORT\"" not in script
    assert "polymarket-runtime-api" not in script


def test_pc_tui_desktop_launcher_logs_failures_and_forces_new_terminal_window() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert "open-polymarket-tui.ps1" in script
    assert "open-polymarket-tui-window.sh" in script
    assert "polymarket-tui-launch-{0}.log" in script
    assert "Start-Transcript" in script
    assert "Add-Content -Path \\$logPath" not in script
    assert "Start-Process -FilePath 'wt.exe'" in script
    assert "'-w', 'new'" in script
    assert "Read-Host 'Press Enter to close'" in script
    assert "\\$shortcut.TargetPath = \\$launcherPath" in script
    assert "\\$shortcut.TargetPath = 'powershell.exe'" not in script


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
    assert 'EXPECTED_CUDA_PROBABILITY_IMAGE="polymarket-cuda-probability:$EXPECTED_SHORT_SHA"' in script
    assert 'if [ "$COLLECTOR_IMAGE" != "$EXPECTED_COLLECTOR_IMAGE" ]; then' in script
    assert 'if [ "$NORMALIZER_IMAGE" != "$EXPECTED_NORMALIZER_IMAGE" ]; then' in script
    assert 'if [ "$CUDA_PROBABILITY_IMAGE" != "$EXPECTED_CUDA_PROBABILITY_IMAGE" ]; then' in script
    assert '[ "$USE_PREBUILT" != "1" ] && [ "$LOCAL" = "$REMOTE" ]' in script
    assert 'export POLYMARKET_COLLECTOR_IMAGE="$COLLECTOR_IMAGE"' in script
    assert 'export POLYMARKET_NORMALIZER_IMAGE="$NORMALIZER_IMAGE"' in script
    assert 'export POLYMARKET_CUDA_PROBABILITY_IMAGE="$CUDA_PROBABILITY_IMAGE"' in script
    assert "compose_for_role up -d $START_SERVICES" in script
    assert "compose_for_role up -d --build $START_SERVICES" in script
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
    assert "POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.25" in compose
    assert 'INTERVAL_SECONDS="${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.25}"' in entrypoint
    assert (
        'OUTCOME_STATUS_PATH="${POLYMARKET_OUTCOME_STATUS_PATH:-$LIVE_DIR/outcomes.json}"'
        in entrypoint
    )
    assert (
        "PROBABILITY_INPUTS_PATH="
        '"${POLYMARKET_PROBABILITY_INPUTS_PATH:-$LIVE_DIR/probability_inputs.json}"'
        in entrypoint
    )
    assert (
        "PROBABILITY_FRAGMENTS_PATH="
        '"${POLYMARKET_PROBABILITY_FRAGMENTS_PATH:-$LIVE_DIR/probability_fragments.json}"'
        in entrypoint
    )
    assert 'FRAGMENT_MAX_ROWS="${POLYMARKET_ENSEMBLE_FRAGMENT_MAX_ROWS:-250000}"' in entrypoint
    assert (
        'VOLATILITY_STATUS_PATH="${POLYMARKET_VOLATILITY_STATUS_PATH:-$LIVE_DIR/volatility.json}"'
        in entrypoint
    )
    assert "run-rust-normalizer-sidecar" in entrypoint
    assert "set -- polymarket-engine" in entrypoint
    assert 'exec "$@"' in entrypoint
    assert "while true" not in entrypoint
    assert '--interval-seconds "$INTERVAL_SECONDS"' in entrypoint
    assert '--normalized-health-path "$NORMALIZED_HEALTH_PATH"' in entrypoint
    assert '--probability-inputs-path "$PROBABILITY_INPUTS_PATH"' in entrypoint
    assert '--probability-fragments-path "$PROBABILITY_FRAGMENTS_PATH"' in entrypoint
    assert '--fragment-max-rows "$FRAGMENT_MAX_ROWS"' in entrypoint
    assert '--outcome-status-path "$OUTCOME_STATUS_PATH"' in entrypoint
    assert '--volatility-status-path "$VOLATILITY_STATUS_PATH"' in entrypoint
    assert "--enable-outcome-refresh" in entrypoint
    assert 'ENABLE_OUTCOME_REFRESH="${POLYMARKET_ENABLE_OUTCOME_REFRESH:-0}"' in entrypoint


def test_outcome_refresh_is_owned_by_hot_normalizer_with_sidecar_fallback() -> None:
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert (
        "POLYMARKET_PROBABILITY_INPUTS_PATH="
        "/var/lib/polymarket/live/probability_inputs.json"
        in env_example
    )
    assert "POLYMARKET_ENABLE_OUTCOME_REFRESH=1" in env_example
    assert "POLYMARKET_OUTCOME_REFRESH_INTERVAL_SECONDS=30" in env_example
    assert "POLYMARKET_DUCKDB_THREADS=1" in env_example
    assert "POLYMARKET_DUCKDB_MEMORY_LIMIT=512MiB" in env_example
    assert "POLYMARKET_DUCKDB_PRESERVE_INSERTION_ORDER=false" in env_example
    assert "outcome-refresh:" in compose
    assert "run-outcome-refresh-sidecar" in compose
    assert "polymarket-outcome-runtime-v1" in compose
    assert "/var/lib/polymarket/live/outcomes.json" in compose
    assert "${POLYMARKET_OUTCOME_REFRESH_INTERVAL_SECONDS:-30}" in compose
    assert (
        "POLYMARKET_PROBABILITY_INPUTS_PATH: "
        "/var/lib/polymarket/live/probability_inputs.json"
        in compose
    )
    assert (
        "POLYMARKET_ENABLE_OUTCOME_REFRESH: "
        "${POLYMARKET_ENABLE_OUTCOME_REFRESH:-1}"
        in compose
    )
    assert "--enable-outcome-refresh" not in compose


def test_deploy_script_requires_running_normalizer_before_success() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "normalizer_running()" in script
    assert "outcome_refresh_stopped()" in script
    assert "outcome_status_fresh()" in script
    assert "ps --services --status running normalizer" in script
    assert "ps --services --status running outcome-refresh" in script
    assert "normalizer_running \\" in script
    assert "&& normalizer_uses_sidecar \\" in script
    assert "&& outcome_refresh_stopped \\" in script
    assert "&& outcome_status_fresh \\" in script


def test_deploy_script_rejects_old_normalize_rust_events_normalizer() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "normalizer_uses_sidecar()" in script
    assert "run-rust-normalizer-sidecar" in script
    assert "compose_for_role top normalizer" in script
    assert "ps -eo args" not in script
    assert "&& outcome_refresh_stopped \\" in script
    assert "&& outcome_status_fresh \\" in script
