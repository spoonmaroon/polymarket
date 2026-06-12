#!/usr/bin/env bash
set -u
set -o pipefail

export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO="${REPO:-$HOME/polymarket}"
DATA_DIR="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}"
COMPOSE_FILE="$REPO/deploy/collector/docker-compose.yml"
SPOON_OVERLAY="$REPO/deploy/collector/docker-compose.spoon-cpu-authority.yml"
STATUS_PATH="$DATA_DIR/live/status.json"
OUTCOME_STATUS_PATH="$DATA_DIR/live/outcomes.json"
LOCK_DIR="/tmp/polymarket-deploy.lock.d"
LOG_FILE="$REPO/logs/deploy.log"
DEPLOYED_MARKER="$HOME/.polymarket/last-deployed-sha"
DEPLOY_SMOKE_ATTEMPTS="${DEPLOY_SMOKE_ATTEMPTS:-90}"
NORMALIZER_SIDECAR_COMMAND="run-rust-normalizer-sidecar"
USE_PREBUILT="${POLYMARKET_DEPLOY_USE_PREBUILT:-0}"
ALLOW_SPOON_BUILD="${POLYMARKET_DEPLOY_ALLOW_SPOON_BUILD:-0}"
EXPECTED_DEPLOY_SHA="${POLYMARKET_EXPECTED_DEPLOY_SHA:-}"
COLLECTOR_IMAGE="${POLYMARKET_COLLECTOR_IMAGE:-polymarket-rust-collector:latest}"
NORMALIZER_IMAGE="${POLYMARKET_NORMALIZER_IMAGE:-polymarket-normalizer:latest}"
CUDA_PROBABILITY_IMAGE="${POLYMARKET_CUDA_PROBABILITY_IMAGE:-polymarket-cuda-probability:latest}"
DEPLOY_ROLE="${POLYMARKET_DEPLOY_ROLE:-spoon-cpu-authority}"
LOG() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG_FILE"; }

mkdir -p "$REPO/logs" "$DATA_DIR/raw" "$DATA_DIR/db" "$DATA_DIR/live" "$DATA_DIR/live/bug-reports" "$DATA_DIR/logs" "$(dirname "$DEPLOYED_MARKER")"
touch "$DATA_DIR/raw/.polymarket_archive_root"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  LOG "deploy already running"
  exit 75
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

cd "$REPO" || exit 1

compose() {
  if [ -f "$REPO/deploy/collector/.env" ]; then
    docker compose --env-file "$REPO/deploy/collector/.env" "$@"
  else
    docker compose "$@"
  fi
}

compose_for_role() {
  case "$DEPLOY_ROLE" in
    spoon-cpu-authority)
      compose -f "$COMPOSE_FILE" -f "$SPOON_OVERLAY" "$@"
      ;;
    full)
      compose -f "$COMPOSE_FILE" "$@"
      ;;
    *)
      LOG "unsupported POLYMARKET_DEPLOY_ROLE=$DEPLOY_ROLE"
      exit 2
      ;;
  esac
}

deploy_start_services() {
  case "$DEPLOY_ROLE" in
    spoon-cpu-authority)
      printf '%s\n' "collector normalizer outcome-refresh"
      ;;
    full)
      printf '%s\n' "collector normalizer outcome-refresh api gpu-probability-worker"
      ;;
    *)
      LOG "unsupported POLYMARKET_DEPLOY_ROLE=$DEPLOY_ROLE"
      exit 2
      ;;
  esac
}

normalizer_running() {
  compose_for_role ps --services --status running normalizer 2>> "$LOG_FILE" \
    | grep -qx normalizer
}

normalizer_uses_sidecar() {
  compose_for_role top normalizer 2>> "$LOG_FILE" \
    | grep "$NORMALIZER_SIDECAR_COMMAND" >> "$LOG_FILE" 2>&1
}

outcome_refresh_running() {
  compose_for_role ps --services --status running outcome-refresh 2>> "$LOG_FILE" \
    | grep -qx outcome-refresh
}

stop_services_excluded_by_role() {
  case "$DEPLOY_ROLE" in
    spoon-cpu-authority)
      LOG "stopping Spoon-excluded API/GPU services if present"
      compose -f "$COMPOSE_FILE" stop api gpu-probability-worker >> "$LOG_FILE" 2>&1 || true
      ;;
    full)
      ;;
  esac
}

outcome_status_fresh() {
  python3 - "$OUTCOME_STATUS_PATH" <<'PY' >> "$LOG_FILE" 2>&1
import json
import os
import sys
import time

path = sys.argv[1]
payload = json.load(open(path, encoding="utf-8"))
if payload.get("schema_version") != "polymarket-outcome-runtime-v1":
    raise SystemExit(1)
if time.time() - os.stat(path).st_mtime > 120:
    raise SystemExit(1)
PY
}

required_image_available() {
  docker image inspect "$1" > /dev/null 2>&1
}

collector_prewarm_windows() {
  if [ -n "${POLYMARKET_PREWARM_WINDOWS:-}" ]; then
    echo "$POLYMARKET_PREWARM_WINDOWS"
    return
  fi
  if [ -f "$REPO/deploy/collector/.env" ]; then
    awk -F= '
      $1 == "POLYMARKET_PREWARM_WINDOWS" {
        print $2
        found = 1
        exit
      }
      END {
        if (!found) {
          print "2"
        }
      }
    ' "$REPO/deploy/collector/.env"
    return
  fi
  echo "2"
}

COLLECTOR_PREWARM_WINDOWS="$(collector_prewarm_windows)"

DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-origin/main}"
git fetch --quiet origin || { LOG "git fetch failed"; exit 1; }
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "$DEPLOY_REF^{commit}")"
DEPLOY_SHORT_SHA="${REMOTE:0:12}"
if [ "$USE_PREBUILT" != "1" ]; then
  export POLYMARKET_COLLECTOR_IMAGE="${POLYMARKET_COLLECTOR_IMAGE:-polymarket-rust-collector:$DEPLOY_SHORT_SHA}"
  export POLYMARKET_NORMALIZER_IMAGE="${POLYMARKET_NORMALIZER_IMAGE:-polymarket-normalizer:$DEPLOY_SHORT_SHA}"
  export POLYMARKET_CUDA_PROBABILITY_IMAGE="${POLYMARKET_CUDA_PROBABILITY_IMAGE:-polymarket-cuda-probability:$DEPLOY_SHORT_SHA}"
  COLLECTOR_IMAGE="$POLYMARKET_COLLECTOR_IMAGE"
  NORMALIZER_IMAGE="$POLYMARKET_NORMALIZER_IMAGE"
  CUDA_PROBABILITY_IMAGE="$POLYMARKET_CUDA_PROBABILITY_IMAGE"
fi
if [ "$USE_PREBUILT" = "1" ]; then
  if [ -z "$EXPECTED_DEPLOY_SHA" ]; then
    LOG "POLYMARKET_DEPLOY_USE_PREBUILT=1 requires POLYMARKET_EXPECTED_DEPLOY_SHA"
    exit 1
  fi
  EXPECTED_FULL_SHA="$(git rev-parse "$EXPECTED_DEPLOY_SHA^{commit}")" || {
    LOG "could not resolve POLYMARKET_EXPECTED_DEPLOY_SHA=$EXPECTED_DEPLOY_SHA"
    exit 1
  }
  if [ "$REMOTE" != "$EXPECTED_FULL_SHA" ]; then
    LOG "deploy ref $DEPLOY_REF resolves to $REMOTE but expected prebuilt sha is $EXPECTED_FULL_SHA"
    exit 1
  fi
  EXPECTED_SHORT_SHA="${EXPECTED_FULL_SHA:0:12}"
  EXPECTED_COLLECTOR_IMAGE="polymarket-rust-collector:$EXPECTED_SHORT_SHA"
  EXPECTED_NORMALIZER_IMAGE="polymarket-normalizer:$EXPECTED_SHORT_SHA"
  EXPECTED_CUDA_PROBABILITY_IMAGE="polymarket-cuda-probability:$EXPECTED_SHORT_SHA"
  if [ "$COLLECTOR_IMAGE" != "$EXPECTED_COLLECTOR_IMAGE" ]; then
    LOG "collector image $COLLECTOR_IMAGE does not match expected $EXPECTED_COLLECTOR_IMAGE"
    exit 1
  fi
  if [ "$NORMALIZER_IMAGE" != "$EXPECTED_NORMALIZER_IMAGE" ]; then
    LOG "normalizer image $NORMALIZER_IMAGE does not match expected $EXPECTED_NORMALIZER_IMAGE"
    exit 1
  fi
  if [ "$CUDA_PROBABILITY_IMAGE" != "$EXPECTED_CUDA_PROBABILITY_IMAGE" ]; then
    LOG "CUDA probability image $CUDA_PROBABILITY_IMAGE does not match expected $EXPECTED_CUDA_PROBABILITY_IMAGE"
    exit 1
  fi
fi
DEPLOYED_SHA="$(cat "$DEPLOYED_MARKER" 2>/dev/null || true)"
if [ "$USE_PREBUILT" != "1" ] && [ "$LOCAL" = "$REMOTE" ] && [ "$DEPLOYED_SHA" = "$REMOTE" ] && [ "${DEPLOY_FORCE:-0}" != "1" ]; then
  stop_services_excluded_by_role
  if normalizer_running \
    && normalizer_uses_sidecar \
    && outcome_refresh_running \
    && outcome_status_fresh \
    && python3 "$REPO/scripts/check_collector_status.py" \
    --status-path "$STATUS_PATH" \
    --max-status-age-seconds 30 \
    --max-price-age-ms 30000 \
    --max-orderbook-age-ms 30000 \
    --max-websocket-event-age-ms 30000 \
    --raw-root "$DATA_DIR/raw" \
    --max-raw-event-age-ms 30000 \
    --normalized-health-path "$DATA_DIR/live/normalized_health.json" \
    --max-normalized-health-age-ms 30000 \
    --expected-prewarm-windows "$COLLECTOR_PREWARM_WINDOWS" >> "$LOG_FILE" 2>&1; then
    exit 0
  fi
  LOG "target commit already checked out but collector is unhealthy; redeploying"
elif [ "$LOCAL" = "$REMOTE" ] && [ "$DEPLOYED_SHA" != "$REMOTE" ] && [ "${DEPLOY_FORCE:-0}" != "1" ]; then
  LOG "deployed marker differs from target commit; redeploying"
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  LOG "working tree is dirty; refusing deploy"
  git status --porcelain | while read -r line; do LOG "  $line"; done
  exit 1
fi

LOG "deploying $REMOTE from $LOCAL"

if ! git merge --ff-only --quiet "$REMOTE"; then
  LOG "git fast-forward failed for $DEPLOY_REF"
  exit 1
fi

LOG "stopping legacy Python collector containers if present"
docker rm -f polymarket-collector-collector-1 polymarket-python-collector-retired-retired-python-collector-1 >> "$LOG_FILE" 2>&1 || true
LOG "stopping retired outcome-refresh sidecar if present"
compose_for_role stop outcome-refresh >> "$LOG_FILE" 2>&1 || true
stop_services_excluded_by_role

START_SERVICES="$(deploy_start_services)"

if [ "$USE_PREBUILT" = "1" ]; then
  if ! required_image_available "$COLLECTOR_IMAGE"; then
    LOG "POLYMARKET_DEPLOY_USE_PREBUILT=1 but required collector image is missing: $COLLECTOR_IMAGE"
    exit 1
  fi
  if ! required_image_available "$NORMALIZER_IMAGE"; then
    LOG "POLYMARKET_DEPLOY_USE_PREBUILT=1 but required normalizer image is missing: $NORMALIZER_IMAGE"
    exit 1
  fi
  if ! required_image_available "$CUDA_PROBABILITY_IMAGE"; then
    LOG "POLYMARKET_DEPLOY_USE_PREBUILT=1 but required CUDA probability image is missing: $CUDA_PROBABILITY_IMAGE"
    exit 1
  fi
  LOG "starting prebuilt images collector=$COLLECTOR_IMAGE normalizer=$NORMALIZER_IMAGE cuda_probability=$CUDA_PROBABILITY_IMAGE"
  if ! (
    export POLYMARKET_COLLECTOR_IMAGE="$COLLECTOR_IMAGE"
    export POLYMARKET_NORMALIZER_IMAGE="$NORMALIZER_IMAGE"
    export POLYMARKET_CUDA_PROBABILITY_IMAGE="$CUDA_PROBABILITY_IMAGE"
    compose_for_role up -d $START_SERVICES
  ) >> "$LOG_FILE" 2>&1; then
    LOG "docker compose failed"
    exit 1
  fi
else
  if [ "$ALLOW_SPOON_BUILD" != "1" ]; then
    LOG "spoon-side Rust image build disabled; set POLYMARKET_DEPLOY_USE_PREBUILT=1 or POLYMARKET_DEPLOY_ALLOW_SPOON_BUILD=1"
    exit 1
  fi
  export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-1}"
  if ! compose_for_role up -d --build $START_SERVICES >> "$LOG_FILE" 2>&1; then
    LOG "docker compose failed"
    exit 1
  fi
fi

for _ in $(seq 1 "$DEPLOY_SMOKE_ATTEMPTS"); do
  if normalizer_running \
    && normalizer_uses_sidecar \
    && outcome_refresh_running \
    && outcome_status_fresh \
    && python3 "$REPO/scripts/check_collector_status.py" \
    --status-path "$STATUS_PATH" \
    --max-status-age-seconds 30 \
    --max-price-age-ms 30000 \
    --max-orderbook-age-ms 30000 \
    --max-websocket-event-age-ms 30000 \
    --raw-root "$DATA_DIR/raw" \
    --max-raw-event-age-ms 30000 \
    --normalized-health-path "$DATA_DIR/live/normalized_health.json" \
    --max-normalized-health-age-ms 30000 \
    --expected-prewarm-windows "$COLLECTOR_PREWARM_WINDOWS" >> "$LOG_FILE" 2>&1; then
    echo "$REMOTE" > "$DEPLOYED_MARKER"
    LOG "deploy OK $REMOTE"
    exit 0
  fi
  sleep 2
done

LOG "collector smoke failed; leaving container logs in docker compose"
compose_for_role logs --tail=80 $START_SERVICES >> "$LOG_FILE" 2>&1 || true
exit 1
