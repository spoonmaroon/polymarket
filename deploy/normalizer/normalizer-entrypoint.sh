#!/usr/bin/env sh
set -eu

RAW_DIR="${POLYMARKET_RAW_DIR:-/var/lib/polymarket/raw}"
DB_PATH="${POLYMARKET_DUCKDB_PATH:-/var/lib/polymarket/db/polymarket.duckdb}"
LIVE_DIR="${POLYMARKET_LIVE_DIR:-/var/lib/polymarket/live}"
STATUS_PATH="${POLYMARKET_STATUS_PATH:-$LIVE_DIR/status.json}"
NORMALIZED_HEALTH_PATH="${POLYMARKET_NORMALIZED_HEALTH_PATH:-$LIVE_DIR/normalized_health.json}"
PROBABILITY_STATUS_PATH="${POLYMARKET_PROBABILITY_STATUS_PATH:-$LIVE_DIR/probabilities.json}"
OUTCOME_STATUS_PATH="${POLYMARKET_OUTCOME_STATUS_PATH:-$LIVE_DIR/outcomes.json}"
INTERVAL_SECONDS="${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.25}"

if [ ! -f "$RAW_DIR/.polymarket_archive_root" ]; then
  echo "missing archive sentinel: $RAW_DIR/.polymarket_archive_root" >&2
  exit 66
fi

mkdir -p "$(dirname "$DB_PATH")" "$LIVE_DIR"

exec polymarket-engine run-rust-normalizer-sidecar \
  --raw-root "$RAW_DIR" \
  --duckdb-path "$DB_PATH" \
  --status-path "$STATUS_PATH" \
  --normalized-health-path "$NORMALIZED_HEALTH_PATH" \
  --probability-status-path "$PROBABILITY_STATUS_PATH" \
  --outcome-status-path "$OUTCOME_STATUS_PATH" \
  --interval-seconds "$INTERVAL_SECONDS" \
  --include-next
