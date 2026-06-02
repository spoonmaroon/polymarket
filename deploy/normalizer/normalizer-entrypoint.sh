#!/usr/bin/env sh
set -eu

RAW_DIR="${POLYMARKET_RAW_DIR:-/var/lib/polymarket/raw}"
DB_PATH="${POLYMARKET_DUCKDB_PATH:-/var/lib/polymarket/db/polymarket.duckdb}"
LIVE_DIR="${POLYMARKET_LIVE_DIR:-/var/lib/polymarket/live}"
STATUS_PATH="${POLYMARKET_STATUS_PATH:-$LIVE_DIR/status.json}"
NORMALIZED_HEALTH_PATH="${POLYMARKET_NORMALIZED_HEALTH_PATH:-$LIVE_DIR/normalized_health.json}"
INTERVAL_SECONDS="${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-1}"

if [ ! -f "$RAW_DIR/.polymarket_archive_root" ]; then
  echo "missing archive sentinel: $RAW_DIR/.polymarket_archive_root" >&2
  exit 66
fi

mkdir -p "$(dirname "$DB_PATH")" "$LIVE_DIR"

while true; do
  cycle_start_ms="$(date +%s%3N)"
  polymarket-engine normalize-rust-events \
    --raw-root "$RAW_DIR" \
    --duckdb-path "$DB_PATH"
  normalized_ms="$(date +%s%3N)"

  if [ -f "$STATUS_PATH" ]; then
    polymarket-engine build-current-decision-states \
      --duckdb-path "$DB_PATH" \
      --status-path "$STATUS_PATH" \
      --include-next
  fi
  state_ms="$(date +%s%3N)"

  polymarket-engine write-normalized-health \
    --duckdb-path "$DB_PATH" \
    --out "$NORMALIZED_HEALTH_PATH"
  health_ms="$(date +%s%3N)"

  echo "normalizer_cycle elapsed_ms=$((health_ms - cycle_start_ms)) normalize_ms=$((normalized_ms - cycle_start_ms)) state_ms=$((state_ms - normalized_ms)) health_ms=$((health_ms - state_ms))"

  sleep "$INTERVAL_SECONDS"
done
