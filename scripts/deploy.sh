#!/usr/bin/env bash
set -u
set -o pipefail

export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO="${REPO:-$HOME/polymarket}"
DATA_DIR="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}"
COMPOSE_FILE="$REPO/deploy/collector/docker-compose.yml"
STATUS_PATH="$DATA_DIR/live/status.json"
LOCK_DIR="/tmp/polymarket-deploy.lock.d"
LOG_FILE="$REPO/logs/deploy.log"
DEPLOYED_MARKER="$HOME/.polymarket/last-deployed-sha"
DEPLOY_SMOKE_ATTEMPTS="${DEPLOY_SMOKE_ATTEMPTS:-90}"
LOG() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG_FILE"; }

mkdir -p "$REPO/logs" "$DATA_DIR/raw" "$DATA_DIR/db" "$DATA_DIR/live" "$DATA_DIR/logs" "$(dirname "$DEPLOYED_MARKER")"
touch "$DATA_DIR/raw/.polymarket_archive_root"

LOG "Python collector deployment is retired. Use the Rust runtime path; refusing to start legacy collector."
exit 64

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  LOG "deploy already running"
  exit 75
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

cd "$REPO" || exit 1

git fetch --quiet origin main || { LOG "git fetch failed"; exit 1; }
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
if [ "$LOCAL" = "$REMOTE" ] && [ "${DEPLOY_FORCE:-0}" != "1" ]; then
  exit 0
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  LOG "working tree is dirty; refusing deploy"
  git status --porcelain | while read -r line; do LOG "  $line"; done
  exit 1
fi

LOG "deploying $REMOTE from $LOCAL"

if ! git pull --ff-only --quiet origin main; then
  LOG "git pull failed"
  exit 1
fi

COMPOSE_ENV_ARGS=()
if [ -f "$REPO/deploy/collector/.env" ]; then
  COMPOSE_ENV_ARGS=(--env-file "$REPO/deploy/collector/.env")
fi

if ! docker compose "${COMPOSE_ENV_ARGS[@]}" -f "$COMPOSE_FILE" up -d --build collector >> "$LOG_FILE" 2>&1; then
  LOG "docker compose failed"
  exit 1
fi

for _ in $(seq 1 "$DEPLOY_SMOKE_ATTEMPTS"); do
  if python3 "$REPO/scripts/check_collector_status.py" \
    --status-path "$STATUS_PATH" \
    --max-status-age-seconds 30 \
    --max-price-age-ms 30000 \
    --max-orderbook-age-ms 30000 >> "$LOG_FILE" 2>&1; then
    echo "$REMOTE" > "$DEPLOYED_MARKER"
    LOG "deploy OK $REMOTE"
    exit 0
  fi
  sleep 2
done

LOG "collector smoke failed; leaving container logs in docker compose"
docker compose "${COMPOSE_ENV_ARGS[@]}" -f "$COMPOSE_FILE" logs --tail=80 collector >> "$LOG_FILE" 2>&1 || true
exit 1
