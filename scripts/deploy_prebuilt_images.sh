#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist/docker}"
DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-HEAD}"

REMOTE_HOST="${REMOTE_HOST:-spoon}"
REMOTE_REPO="${REMOTE_REPO:-/home/spoon/polymarket}"
REMOTE_DIST_DIR="${REMOTE_DIST_DIR:-/home/spoon/polymarket-image-artifacts}"
POLYMARKET_DATA_DIR="${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}"

if ! git -C "$ROOT" diff --quiet; then
  echo "working tree has unstaged changes; commit or stash before deploying images" >&2
  exit 1
fi

if ! git -C "$ROOT" diff --cached --quiet; then
  echo "working tree has staged changes; commit or unstage before deploying images" >&2
  exit 1
fi

if [ -n "$(git -C "$ROOT" ls-files --others --exclude-standard)" ]; then
  echo "working tree has untracked files; commit, remove, or ignore them before deploying images" >&2
  exit 1
fi

FULL_SHA="$(git -C "$ROOT" rev-parse "$DEPLOY_REF^{commit}")"
HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"
if [ "$HEAD_SHA" != "$FULL_SHA" ]; then
  echo "deploy ref $DEPLOY_REF resolves to $FULL_SHA but HEAD is $HEAD_SHA; checkout the deploy ref first" >&2
  exit 1
fi
SHORT_SHA="${FULL_SHA:0:12}"

COLLECTOR_IMAGE="polymarket-rust-collector:${SHORT_SHA}"
NORMALIZER_IMAGE="polymarket-normalizer:${SHORT_SHA}"
COLLECTOR_TAR="$DIST_DIR/polymarket-rust-collector-${SHORT_SHA}.tar"
NORMALIZER_TAR="$DIST_DIR/polymarket-normalizer-${SHORT_SHA}.tar"
REMOTE_COLLECTOR_TAR="$REMOTE_DIST_DIR/$(basename "$COLLECTOR_TAR")"
REMOTE_NORMALIZER_TAR="$REMOTE_DIST_DIR/$(basename "$NORMALIZER_TAR")"

if [ ! -f "$COLLECTOR_TAR" ]; then
  echo "missing collector image tarball: $COLLECTOR_TAR" >&2
  exit 1
fi

if [ ! -f "$NORMALIZER_TAR" ]; then
  echo "missing normalizer image tarball: $NORMALIZER_TAR" >&2
  exit 1
fi

ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_DIST_DIR'"
scp "$COLLECTOR_TAR" "$NORMALIZER_TAR" "$REMOTE_HOST:$REMOTE_DIST_DIR/"

ssh "$REMOTE_HOST" "docker load -i '$REMOTE_COLLECTOR_TAR' && docker load -i '$REMOTE_NORMALIZER_TAR'"

ssh "$REMOTE_HOST" "cd '$REMOTE_REPO' && POLYMARKET_DEPLOY_USE_PREBUILT=1 POLYMARKET_DEPLOY_REF='$FULL_SHA' POLYMARKET_EXPECTED_DEPLOY_SHA='$FULL_SHA' POLYMARKET_COLLECTOR_IMAGE='$COLLECTOR_IMAGE' POLYMARKET_NORMALIZER_IMAGE='$NORMALIZER_IMAGE' POLYMARKET_DATA_DIR='$POLYMARKET_DATA_DIR' DEPLOY_FORCE=1 ./scripts/deploy.sh"

ssh "$REMOTE_HOST" "cd '$REMOTE_REPO' && python3 scripts/check_collector_status.py --status-path '$POLYMARKET_DATA_DIR/live/status.json' --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000 --max-websocket-event-age-ms 30000 --raw-root '$POLYMARKET_DATA_DIR/raw' --max-raw-event-age-ms 30000 --normalized-health-path '$POLYMARKET_DATA_DIR/live/normalized_health.json' --max-normalized-health-age-ms 30000 --expected-prewarm-windows 2"
