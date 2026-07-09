#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-HEAD}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"

GPU_NODE_HOST="${GPU_NODE_HOST:-server2}"
GPU_NODE_REPO="${GPU_NODE_REPO:-/home/enoch/polymarket}"
GPU_NODE_GIT_REMOTE="${GPU_NODE_GIT_REMOTE:-git@github.com:AnimeWeeb9000/polymarket.git}"
GPU_NODE_DATA_DIR="${GPU_NODE_DATA_DIR:-/home/enoch/polymarket-data}"
GPU_NODE_BIN_DIR="${GPU_NODE_BIN_DIR:-/home/enoch/bin}"
GPU_NODE_DIST_DIR="${GPU_NODE_DIST_DIR:-/home/enoch/polymarket-image-artifacts}"
GPU_NODE_DEPLOY_ROLE="${GPU_NODE_DEPLOY_ROLE:-server2-gpu-api}"
GPU_NODE_PROBABILITY_CPU_TARGET_PERCENT="${GPU_NODE_PROBABILITY_CPU_TARGET_PERCENT:-15.0}"
GPU_NODE_PROBABILITY_CPU_SOFT_MAX_PERCENT="${GPU_NODE_PROBABILITY_CPU_SOFT_MAX_PERCENT:-20.0}"
GPU_NODE_PROBABILITY_MAX_CYCLE_RUNTIME_MS="${GPU_NODE_PROBABILITY_MAX_CYCLE_RUNTIME_MS:-10000}"
GPU_NODE_PROBABILITY_MAX_TOTAL_PATHS="${GPU_NODE_PROBABILITY_MAX_TOTAL_PATHS:-10000}"
GPU_NODE_PROBABILITY_MIN_TOTAL_PATHS="${GPU_NODE_PROBABILITY_MIN_TOTAL_PATHS:-2000}"
GPU_NODE_ENABLE_LIVE_PRIOR_FRAGMENTS="${GPU_NODE_ENABLE_LIVE_PRIOR_FRAGMENTS:-0}"
GPU_NODE_GPU_WORKER_MEM_LIMIT="${GPU_NODE_GPU_WORKER_MEM_LIMIT:-1536m}"
GPU_NODE_API_PORT="${GPU_NODE_API_PORT:-8000}"
GPU_NODE_REMOTE_BUILD_SAVE_TARS="${GPU_NODE_REMOTE_BUILD_SAVE_TARS:-0}"
GPU_NODE_OLD_WRITER_HOST="${GPU_NODE_OLD_WRITER_HOST:-spoon@100.100.109.27}"

if ! git -C "$ROOT" diff --quiet; then
  echo "working tree has unstaged changes; commit or stash before deploying to server2" >&2
  exit 1
fi

if ! git -C "$ROOT" diff --cached --quiet; then
  echo "working tree has staged changes; commit or unstage before deploying to server2" >&2
  exit 1
fi

if [ -n "$(git -C "$ROOT" ls-files --others --exclude-standard)" ]; then
  echo "working tree has untracked files; commit, remove, or ignore them before deploying to server2" >&2
  exit 1
fi

FULL_SHA="$(git -C "$ROOT" rev-parse "$DEPLOY_REF^{commit}")"
HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"
if [ "$HEAD_SHA" != "$FULL_SHA" ]; then
  echo "deploy ref $DEPLOY_REF resolves to $FULL_SHA but HEAD is $HEAD_SHA; checkout the deploy ref first" >&2
  exit 1
fi

git -C "$ROOT" fetch --quiet origin main
LOCAL_MAIN_SHA="$(git -C "$ROOT" rev-parse origin/main^{commit})"
if [ "$LOCAL_MAIN_SHA" != "$FULL_SHA" ]; then
  echo "origin/main is $LOCAL_MAIN_SHA but deploy ref is $FULL_SHA; push main before deploying" >&2
  exit 1
fi

SHORT_SHA="${FULL_SHA:0:12}"
COLLECTOR_IMAGE="polymarket-rust-collector:${SHORT_SHA}"
NORMALIZER_IMAGE="polymarket-normalizer:${SHORT_SHA}"
CUDA_PROBABILITY_IMAGE="polymarket-cuda-probability:${SHORT_SHA}"

echo "server2 native Linux will fetch main and build images locally"

shell_quote() {
  printf "%q" "$1"
}

ssh "$GPU_NODE_HOST" "bash -s" <<EOF
set -euo pipefail

FULL_SHA=$(shell_quote "$FULL_SHA")
SHORT_SHA=$(shell_quote "$SHORT_SHA")
TARGET_PLATFORM=$(shell_quote "$TARGET_PLATFORM")
GPU_NODE_HOST=$(shell_quote "$GPU_NODE_HOST")
GPU_NODE_REPO=$(shell_quote "$GPU_NODE_REPO")
GPU_NODE_GIT_REMOTE=$(shell_quote "$GPU_NODE_GIT_REMOTE")
GPU_NODE_DATA_DIR=$(shell_quote "$GPU_NODE_DATA_DIR")
GPU_NODE_BIN_DIR=$(shell_quote "$GPU_NODE_BIN_DIR")
GPU_NODE_DIST_DIR=$(shell_quote "$GPU_NODE_DIST_DIR")
GPU_NODE_DEPLOY_ROLE=$(shell_quote "$GPU_NODE_DEPLOY_ROLE")
GPU_NODE_PROBABILITY_CPU_TARGET_PERCENT=$(shell_quote "$GPU_NODE_PROBABILITY_CPU_TARGET_PERCENT")
GPU_NODE_PROBABILITY_CPU_SOFT_MAX_PERCENT=$(shell_quote "$GPU_NODE_PROBABILITY_CPU_SOFT_MAX_PERCENT")
GPU_NODE_PROBABILITY_MAX_CYCLE_RUNTIME_MS=$(shell_quote "$GPU_NODE_PROBABILITY_MAX_CYCLE_RUNTIME_MS")
GPU_NODE_PROBABILITY_MAX_TOTAL_PATHS=$(shell_quote "$GPU_NODE_PROBABILITY_MAX_TOTAL_PATHS")
GPU_NODE_PROBABILITY_MIN_TOTAL_PATHS=$(shell_quote "$GPU_NODE_PROBABILITY_MIN_TOTAL_PATHS")
GPU_NODE_ENABLE_LIVE_PRIOR_FRAGMENTS=$(shell_quote "$GPU_NODE_ENABLE_LIVE_PRIOR_FRAGMENTS")
GPU_NODE_GPU_WORKER_MEM_LIMIT=$(shell_quote "$GPU_NODE_GPU_WORKER_MEM_LIMIT")
GPU_NODE_API_PORT=$(shell_quote "$GPU_NODE_API_PORT")
GPU_NODE_REMOTE_BUILD_SAVE_TARS=$(shell_quote "$GPU_NODE_REMOTE_BUILD_SAVE_TARS")
GPU_NODE_OLD_WRITER_HOST=$(shell_quote "$GPU_NODE_OLD_WRITER_HOST")

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

case "$GPU_NODE_DEPLOY_ROLE" in
  server2-gpu-api)
    ;;
  *)
    echo "unsupported GPU_NODE_DEPLOY_ROLE: $GPU_NODE_DEPLOY_ROLE" >&2
    exit 2
    ;;
esac

mkdir -p "$GPU_NODE_DATA_DIR/raw" "$GPU_NODE_DATA_DIR/db" "$GPU_NODE_DATA_DIR/live" "$GPU_NODE_DATA_DIR/live/bug-reports" "$GPU_NODE_DATA_DIR/logs" "$GPU_NODE_DIST_DIR" "$GPU_NODE_BIN_DIR"
touch "$GPU_NODE_DATA_DIR/raw/.polymarket_archive_root"

if ! git ls-remote "$GPU_NODE_GIT_REMOTE" HEAD >/dev/null 2>&1; then
  echo "remote repo is unreachable: $GPU_NODE_GIT_REMOTE" >&2
  exit 1
fi

if [ -d "$GPU_NODE_REPO/.git" ]; then
  if ! git -C "$GPU_NODE_REPO" diff --quiet; then
    echo "remote repo has unstaged changes; clean the worktree before deploying to server2" >&2
    exit 1
  fi
  if ! git -C "$GPU_NODE_REPO" diff --cached --quiet; then
    echo "remote repo has staged changes; clean the index before deploying to server2" >&2
    exit 1
  fi
  if [ -n "$(git -C "$GPU_NODE_REPO" ls-files --others --exclude-standard)" ]; then
    echo "remote repo has untracked files; clean them before deploying to server2" >&2
    exit 1
  fi
else
  git clone "$GPU_NODE_GIT_REMOTE" "$GPU_NODE_REPO"
fi

cd "$GPU_NODE_REPO"
git remote set-url origin "$GPU_NODE_GIT_REMOTE" 2>/dev/null || git remote add origin "$GPU_NODE_GIT_REMOTE"
git fetch --quiet origin main
git checkout --quiet "$FULL_SHA"

cp deploy/collector/.env.example deploy/collector/.env
set_env POLYMARKET_UID "\$(id -u)" deploy/collector/.env
set_env POLYMARKET_GID "\$(id -g)" deploy/collector/.env
set_env POLYMARKET_DATA_DIR "$GPU_NODE_DATA_DIR" deploy/collector/.env
set_env POLYMARKET_CUDA_PROBABILITY_IMAGE "$CUDA_PROBABILITY_IMAGE" deploy/collector/.env
set_env POLYMARKET_ENABLE_RUNTIME_PROBABILITIES 1 deploy/collector/.env
set_env POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE 0 deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MAX_CYCLE_RUNTIME_MS "$GPU_NODE_PROBABILITY_MAX_CYCLE_RUNTIME_MS" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS "$GPU_NODE_PROBABILITY_MAX_TOTAL_PATHS" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS "$GPU_NODE_PROBABILITY_MIN_TOTAL_PATHS" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT "$GPU_NODE_PROBABILITY_CPU_TARGET_PERCENT" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT "$GPU_NODE_PROBABILITY_CPU_SOFT_MAX_PERCENT" deploy/collector/.env
set_env POLYMARKET_GPU_WORKER_MEM_LIMIT "$GPU_NODE_GPU_WORKER_MEM_LIMIT" deploy/collector/.env
set_env POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS "$GPU_NODE_ENABLE_LIVE_PRIOR_FRAGMENTS" deploy/collector/.env
set_env POLYMARKET_API_PORT "$GPU_NODE_API_PORT" deploy/collector/.env

if OLD_RUNTIME_STATUS="$(
  ssh "$GPU_NODE_OLD_WRITER_HOST" bash -lc "set -euo pipefail
docker info >/dev/null
for container in polymarket-rust-collector-gpu-probability-worker-1 polymarket-rust-collector-api-1; do
  status=\"\$(docker inspect -f '{{.State.Status}}' \"\$container\" 2>/dev/null || printf absent)\"
  case \"\$status\" in
    running|restarting|paused|created)
      printf '%s:%s\n' \"\$container\" \"\$status\"
      exit 3
      ;;
    absent|exited|dead|removing)
      ;;
    *)
      printf '%s:%s\n' \"\$container\" \"\$status\"
      exit 4
      ;;
  esac
done"
)"; then
  :
else
  if [ -n "$OLD_RUNTIME_STATUS" ]; then
    echo "old Polymarket GPU/API runtime is still active on $GPU_NODE_OLD_WRITER_HOST ($OLD_RUNTIME_STATUS)" >&2
  else
    echo "unable to verify old Polymarket GPU/API runtime state on $GPU_NODE_OLD_WRITER_HOST" >&2
  fi
  exit 1
fi
TARGET_PLATFORM="$TARGET_PLATFORM" POLYMARKET_BUILD_SAVE_TARS="$GPU_NODE_REMOTE_BUILD_SAVE_TARS" POLYMARKET_DEPLOY_REF="$FULL_SHA" ./scripts/build_images_pc.sh

POLYMARKET_DATA_DIR="$GPU_NODE_DATA_DIR" POLYMARKET_BIN_DIR="$GPU_NODE_BIN_DIR" ./scripts/install_gpu_node_spoon_artifact_sync.sh


POLYMARKET_REPO="$GPU_NODE_REPO" POLYMARKET_DATA_DIR="$GPU_NODE_DATA_DIR" POLYMARKET_BIN_DIR="$GPU_NODE_BIN_DIR" POLYMARKET_API_BASE_URL="http://127.0.0.1:$GPU_NODE_API_PORT" ./scripts/install_gpu_node_runtime_keeper.sh

docker compose --env-file deploy/collector/.env \
  -f deploy/collector/docker-compose.yml \
  -f deploy/collector/docker-compose.thepc-gpu-api.yml \
  stop collector normalizer outcome-refresh >/dev/null 2>&1 || true

docker compose --env-file deploy/collector/.env \
  -f deploy/collector/docker-compose.yml \
  -f deploy/collector/docker-compose.thepc-gpu-api.yml \
  up -d --no-build api gpu-probability-worker
EOF

ssh "$GPU_NODE_HOST" "curl -fsS http://127.0.0.1:$GPU_NODE_API_PORT/health >/dev/null"
if ! ssh "$GPU_NODE_HOST" "cd \"$GPU_NODE_REPO\" && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml -f deploy/collector/docker-compose.thepc-gpu-api.yml ps --services --filter \"status=running\" | grep -q '^gpu-probability-worker$'" >/dev/null; then
  echo "gpu-probability-worker did not remain running after startup" >&2
  ssh "$GPU_NODE_HOST" "cd \"$GPU_NODE_REPO\" && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml -f deploy/collector/docker-compose.thepc-gpu-api.yml ps"
  exit 1
fi
