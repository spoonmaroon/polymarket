#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist/docker}"
DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-HEAD}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"

PC_HOST="${PC_HOST:-ender@100.72.104.49}"
PC_WSL_DISTRO="${PC_WSL_DISTRO:-Ubuntu}"
PC_REPO="${PC_REPO:-/home/ender/polymarket}"
PC_BUNDLE="${PC_BUNDLE:-/home/ender/polymarket.bundle}"
PC_DATA_DIR="${PC_DATA_DIR:-/home/ender/polymarket-data}"
PC_DIST_DIR="${PC_DIST_DIR:-/home/ender/polymarket-image-artifacts}"
PC_NORMALIZER_INTERVAL_SECONDS="${PC_NORMALIZER_INTERVAL_SECONDS:-0.1}"
PC_REST_BACKUP_INTERVAL_MS="${PC_REST_BACKUP_INTERVAL_MS:-1000}"
PC_DEPLOY_BUILD_IMAGES="${PC_DEPLOY_BUILD_IMAGES:-1}"
PC_BRANCH="${PC_BRANCH:-$(git -C "$ROOT" branch --show-current)}"

if [ -z "$PC_BRANCH" ]; then
  echo "could not infer current git branch; set PC_BRANCH explicitly" >&2
  exit 1
fi

if ! git -C "$ROOT" diff --quiet; then
  echo "working tree has unstaged changes; commit or stash before deploying to THEPC" >&2
  exit 1
fi

if ! git -C "$ROOT" diff --cached --quiet; then
  echo "working tree has staged changes; commit or unstage before deploying to THEPC" >&2
  exit 1
fi

if [ -n "$(git -C "$ROOT" ls-files --others --exclude-standard)" ]; then
  echo "working tree has untracked files; commit, remove, or ignore them before deploying to THEPC" >&2
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
LOCAL_BUNDLE="$DIST_DIR/polymarket-${SHORT_SHA}.bundle"

if [ "$PC_DEPLOY_BUILD_IMAGES" = "1" ]; then
  TARGET_PLATFORM="$TARGET_PLATFORM" POLYMARKET_DEPLOY_REF="$DEPLOY_REF" "$ROOT/scripts/build_images_pc.sh"
fi

if [ ! -f "$COLLECTOR_TAR" ]; then
  echo "missing collector image tarball: $COLLECTOR_TAR" >&2
  exit 1
fi

if [ ! -f "$NORMALIZER_TAR" ]; then
  echo "missing normalizer image tarball: $NORMALIZER_TAR" >&2
  exit 1
fi

mkdir -p "$DIST_DIR"
git -C "$ROOT" bundle create "$LOCAL_BUNDLE.tmp" --branches --tags
mv "$LOCAL_BUNDLE.tmp" "$LOCAL_BUNDLE"

shell_quote() {
  printf "%q" "$1"
}

wsl_put_file() {
  local src="$1"
  local dest="$2"
  local dest_dir
  local dest_dir_q
  local dest_q

  dest_dir="$(dirname "$dest")"
  dest_dir_q="$(shell_quote "$dest_dir")"
  dest_q="$(shell_quote "$dest")"

  ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- bash -lc \"mkdir -p $dest_dir_q && cat > $dest_q\"" < "$src"
}

echo "copying git bundle and image tarballs to THEPC WSL"
wsl_put_file "$LOCAL_BUNDLE" "$PC_BUNDLE"
wsl_put_file "$COLLECTOR_TAR" "$PC_DIST_DIR/$(basename "$COLLECTOR_TAR")"
wsl_put_file "$NORMALIZER_TAR" "$PC_DIST_DIR/$(basename "$NORMALIZER_TAR")"

ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- bash -s" <<EOF
set -euo pipefail

FULL_SHA=$(shell_quote "$FULL_SHA")
SHORT_SHA=$(shell_quote "$SHORT_SHA")
PC_BRANCH=$(shell_quote "$PC_BRANCH")
PC_REPO=$(shell_quote "$PC_REPO")
PC_BUNDLE=$(shell_quote "$PC_BUNDLE")
PC_DATA_DIR=$(shell_quote "$PC_DATA_DIR")
PC_DIST_DIR=$(shell_quote "$PC_DIST_DIR")
PC_NORMALIZER_INTERVAL_SECONDS=$(shell_quote "$PC_NORMALIZER_INTERVAL_SECONDS")
PC_REST_BACKUP_INTERVAL_MS=$(shell_quote "$PC_REST_BACKUP_INTERVAL_MS")
COLLECTOR_IMAGE=$(shell_quote "$COLLECTOR_IMAGE")
NORMALIZER_IMAGE=$(shell_quote "$NORMALIZER_IMAGE")
COLLECTOR_TAR=$(shell_quote "$PC_DIST_DIR/$(basename "$COLLECTOR_TAR")")
NORMALIZER_TAR=$(shell_quote "$PC_DIST_DIR/$(basename "$NORMALIZER_TAR")")

set_env() {
  key="\$1"
  value="\$2"
  file="\$3"
  tmp="\$(mktemp)"
  touch "\$file"
  awk -v key="\$key" -v value="\$value" '
    BEGIN { found = 0 }
    \$0 ~ "^" key "=" {
      print key "=" value
      found = 1
      next
    }
    { print }
    END {
      if (!found) {
        print key "=" value
      }
    }
  ' "\$file" > "\$tmp"
  mv "\$tmp" "\$file"
}

mkdir -p "\$PC_DATA_DIR/raw" "\$PC_DATA_DIR/db" "\$PC_DATA_DIR/live" "\$PC_DATA_DIR/logs" "\$PC_DIST_DIR"
touch "\$PC_DATA_DIR/raw/.polymarket_archive_root"

if [ ! -d "\$PC_REPO/.git" ]; then
  git clone "\$PC_BUNDLE" "\$PC_REPO"
fi

cd "\$PC_REPO"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "THEPC repo is dirty; refusing deploy" >&2
  git status --porcelain >&2
  exit 1
fi

git remote set-url origin "\$PC_BUNDLE" 2>/dev/null || git remote add origin "\$PC_BUNDLE"
git fetch --quiet origin
git checkout -B "\$PC_BRANCH" "\$FULL_SHA"

if [ ! -f deploy/collector/.env ]; then
  cp deploy/collector/.env.example deploy/collector/.env
fi

set_env POLYMARKET_UID "\$(id -u)" deploy/collector/.env
set_env POLYMARKET_GID "\$(id -g)" deploy/collector/.env
set_env POLYMARKET_DATA_DIR "\$PC_DATA_DIR" deploy/collector/.env
set_env POLYMARKET_NORMALIZER_INTERVAL_SECONDS "\$PC_NORMALIZER_INTERVAL_SECONDS" deploy/collector/.env
set_env POLYMARKET_REST_BACKUP_INTERVAL_MS "\$PC_REST_BACKUP_INTERVAL_MS" deploy/collector/.env
set_env POLYMARKET_COLLECTOR_IMAGE "\$COLLECTOR_IMAGE" deploy/collector/.env
set_env POLYMARKET_NORMALIZER_IMAGE "\$NORMALIZER_IMAGE" deploy/collector/.env

docker load -i "\$COLLECTOR_TAR"
docker load -i "\$NORMALIZER_TAR"

POLYMARKET_DEPLOY_USE_PREBUILT=1 \\
POLYMARKET_DEPLOY_REF="\$FULL_SHA" \\
POLYMARKET_EXPECTED_DEPLOY_SHA="\$FULL_SHA" \\
POLYMARKET_COLLECTOR_IMAGE="\$COLLECTOR_IMAGE" \\
POLYMARKET_NORMALIZER_IMAGE="\$NORMALIZER_IMAGE" \\
POLYMARKET_DATA_DIR="\$PC_DATA_DIR" \\
POLYMARKET_NORMALIZER_INTERVAL_SECONDS="\$PC_NORMALIZER_INTERVAL_SECONDS" \\
POLYMARKET_REST_BACKUP_INTERVAL_MS="\$PC_REST_BACKUP_INTERVAL_MS" \\
DEPLOY_FORCE=1 \\
./scripts/deploy.sh

python3 scripts/check_collector_status.py \\
  --status-path "\$PC_DATA_DIR/live/status.json" \\
  --max-status-age-seconds 30 \\
  --max-price-age-ms 30000 \\
  --max-orderbook-age-ms 30000 \\
  --max-websocket-event-age-ms 30000 \\
  --raw-root "\$PC_DATA_DIR/raw" \\
  --max-raw-event-age-ms 30000 \\
  --normalized-health-path "\$PC_DATA_DIR/live/normalized_health.json" \\
  --max-normalized-health-age-ms 30000 \\
  --expected-prewarm-windows 2

docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml ps
printf 'THEPC deployed %s\\n' "\$FULL_SHA"
EOF
