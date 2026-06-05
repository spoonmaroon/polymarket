#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist/docker}"
DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-HEAD}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"

if ! git -C "$ROOT" diff --quiet; then
  echo "working tree has unstaged changes; commit or stash before building images" >&2
  exit 1
fi

if ! git -C "$ROOT" diff --cached --quiet; then
  echo "working tree has staged changes; commit or unstage before building images" >&2
  exit 1
fi

if [ -n "$(git -C "$ROOT" ls-files --others --exclude-standard)" ]; then
  echo "working tree has untracked files; commit, remove, or ignore them before building images" >&2
  exit 1
fi

FULL_SHA="$(git -C "$ROOT" rev-parse "$DEPLOY_REF^{commit}")"
HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"
if [ "$HEAD_SHA" != "$FULL_SHA" ]; then
  echo "deploy ref $DEPLOY_REF resolves to $FULL_SHA but HEAD is $HEAD_SHA; checkout the deploy ref first" >&2
  exit 1
fi
SHORT_SHA="${FULL_SHA:0:12}"

COLLECTOR_IMAGE="polymarket-rust-collector"
NORMALIZER_IMAGE="polymarket-normalizer"
CUDA_PROBABILITY_IMAGE="polymarket-cuda-probability"
COLLECTOR_SHA_TAG="polymarket-rust-collector:${SHORT_SHA}"
NORMALIZER_SHA_TAG="polymarket-normalizer:${SHORT_SHA}"
CUDA_PROBABILITY_SHA_TAG="polymarket-cuda-probability:${SHORT_SHA}"
COLLECTOR_LATEST_TAG="polymarket-rust-collector:latest"
NORMALIZER_LATEST_TAG="polymarket-normalizer:latest"
CUDA_PROBABILITY_LATEST_TAG="polymarket-cuda-probability:latest"
COLLECTOR_TAR="$DIST_DIR/${COLLECTOR_IMAGE}-${SHORT_SHA}.tar"
NORMALIZER_TAR="$DIST_DIR/${NORMALIZER_IMAGE}-${SHORT_SHA}.tar"
CUDA_PROBABILITY_TAR="$DIST_DIR/${CUDA_PROBABILITY_IMAGE}-${SHORT_SHA}.tar"
TUI_BIN="$DIST_DIR/polymarket-cockpit-tui-${SHORT_SHA}"
MANIFEST="$DIST_DIR/manifest-${SHORT_SHA}.txt"
UI_DIST="$ROOT/ui/dist"
NORMALIZER_DOCKERFILE="$DIST_DIR/normalizer-ui-${SHORT_SHA}.Dockerfile"

export DOCKER_BUILDKIT=1

mkdir -p "$DIST_DIR"

npm --prefix "$ROOT/ui" run build
if [ ! -f "$UI_DIST/index.html" ]; then
  echo "missing built UI index: $UI_DIST/index.html" >&2
  exit 1
fi

awk '
  { print }
  $0 == "COPY src ./src" {
    print "COPY ui/dist ./ui/dist"
  }
' "$ROOT/deploy/normalizer/Dockerfile" > "$NORMALIZER_DOCKERFILE.tmp"
mv "$NORMALIZER_DOCKERFILE.tmp" "$NORMALIZER_DOCKERFILE"

docker buildx build \
  --platform "$TARGET_PLATFORM" \
  --load \
  -f "$ROOT/deploy/collector/Dockerfile" \
  -t "$COLLECTOR_SHA_TAG" \
  -t "$COLLECTOR_LATEST_TAG" \
  "$ROOT"

docker buildx build \
  --platform "$TARGET_PLATFORM" \
  --load \
  -f "$NORMALIZER_DOCKERFILE" \
  -t "$NORMALIZER_SHA_TAG" \
  -t "$NORMALIZER_LATEST_TAG" \
  "$ROOT"

docker buildx build \
  --platform "$TARGET_PLATFORM" \
  --load \
  -f "$ROOT/deploy/gpu/Dockerfile" \
  -t "$CUDA_PROBABILITY_SHA_TAG" \
  -t "$CUDA_PROBABILITY_LATEST_TAG" \
  "$ROOT"

docker save \
  -o "$COLLECTOR_TAR" \
  "$COLLECTOR_SHA_TAG" \
  "$COLLECTOR_LATEST_TAG"

docker save \
  -o "$NORMALIZER_TAR" \
  "$NORMALIZER_SHA_TAG" \
  "$NORMALIZER_LATEST_TAG"

docker save \
  -o "$CUDA_PROBABILITY_TAR" \
  "$CUDA_PROBABILITY_SHA_TAG" \
  "$CUDA_PROBABILITY_LATEST_TAG"

CONTAINER_ID="$(docker create --platform "$TARGET_PLATFORM" "$COLLECTOR_SHA_TAG")"
trap 'docker rm -f "$CONTAINER_ID" >/dev/null 2>&1 || true' EXIT
docker cp "$CONTAINER_ID:/usr/local/bin/polymarket-cockpit-tui" "$TUI_BIN.tmp"
mv "$TUI_BIN.tmp" "$TUI_BIN"
chmod 755 "$TUI_BIN"
docker rm -f "$CONTAINER_ID" >/dev/null
trap - EXIT

COLLECTOR_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$COLLECTOR_SHA_TAG")"
NORMALIZER_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$NORMALIZER_SHA_TAG")"
CUDA_PROBABILITY_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$CUDA_PROBABILITY_SHA_TAG")"

cat > "$MANIFEST" <<EOF
full_sha=${FULL_SHA}
short_sha=${SHORT_SHA}
deploy_ref=${DEPLOY_REF}
target_platform=${TARGET_PLATFORM}
ui_dist=${UI_DIST}
collector_image=${COLLECTOR_SHA_TAG}
collector_latest=${COLLECTOR_LATEST_TAG}
collector_tar=${COLLECTOR_TAR}
collector_image_id=${COLLECTOR_IMAGE_ID}
normalizer_image=${NORMALIZER_SHA_TAG}
normalizer_latest=${NORMALIZER_LATEST_TAG}
normalizer_tar=${NORMALIZER_TAR}
normalizer_image_id=${NORMALIZER_IMAGE_ID}
cuda_probability_image=${CUDA_PROBABILITY_SHA_TAG}
cuda_probability_latest=${CUDA_PROBABILITY_LATEST_TAG}
cuda_probability_tar=${CUDA_PROBABILITY_TAR}
cuda_probability_image_id=${CUDA_PROBABILITY_IMAGE_ID}
tui_bin=${TUI_BIN}
EOF

echo "$MANIFEST"
