#!/usr/bin/env sh
set -eu

RAW_DIR="${POLYMARKET_RAW_DIR:-/var/lib/polymarket/raw}"
LIVE_DIR="${POLYMARKET_LIVE_DIR:-/var/lib/polymarket/live}"
STATUS_PATH="${POLYMARKET_STATUS_PATH:-$LIVE_DIR/status.json}"
STATE_SNAPSHOT_DIR="${POLYMARKET_STATE_SNAPSHOT_DIR:-$RAW_DIR/polymarket_state_manager/state_snapshot}"
RAW_EVENT_DIR="${POLYMARKET_RAW_EVENT_DIR:-$RAW_DIR}"
DECISION_SNAPSHOT_DIR="${POLYMARKET_DECISION_SNAPSHOT_DIR:-$RAW_DIR}"

if [ ! -f "$RAW_DIR/.polymarket_archive_root" ]; then
  echo "missing archive sentinel: $RAW_DIR/.polymarket_archive_root" >&2
  exit 66
fi

mkdir -p "$LIVE_DIR"
rm -f "$STATUS_PATH.tmp" "$STATUS_PATH.json.tmp" 2>/dev/null || true

ASSETS="${POLYMARKET_ASSETS:-BTC,ETH}"
INTERVAL="${POLYMARKET_INTERVAL:-5m}"
PREWARM_WINDOWS="${POLYMARKET_PREWARM_WINDOWS:-3}"
STATUS_INTERVAL_MS="${POLYMARKET_STATUS_INTERVAL_MS:-1000}"
PREWARM_BEFORE_EXPIRY_MS="${POLYMARKET_PREWARM_BEFORE_EXPIRY_MS:-30000}"
STALE_CHAINLINK_AFTER_MS="${POLYMARKET_STALE_CHAINLINK_AFTER_MS:-5000}"
STALE_ORDERBOOK_AFTER_MS="${POLYMARKET_STALE_ORDERBOOK_AFTER_MS:-30000}"
REST_BACKUP_INTERVAL_MS="${POLYMARKET_REST_BACKUP_INTERVAL_MS:-15000}"
RAW_EVENT_BUFFER_SIZE="${POLYMARKET_RAW_EVENT_BUFFER_SIZE:-16384}"
DECISION_EVENT_BUFFER_SIZE="${POLYMARKET_DECISION_EVENT_BUFFER_SIZE:-16384}"

exec /usr/local/bin/polymarket-live-probe \
  --mode state-manager \
  --assets "$ASSETS" \
  --interval "$INTERVAL" \
  --prewarm-windows "$PREWARM_WINDOWS" \
  --forever \
  --status-interval-ms "$STATUS_INTERVAL_MS" \
  --prewarm-before-expiry-ms "$PREWARM_BEFORE_EXPIRY_MS" \
  --stale-chainlink-after-ms "$STALE_CHAINLINK_AFTER_MS" \
  --stale-orderbook-after-ms "$STALE_ORDERBOOK_AFTER_MS" \
  --rest-backup-interval-ms "$REST_BACKUP_INTERVAL_MS" \
  --state-snapshot-dir "$STATE_SNAPSHOT_DIR" \
  --raw-event-dir "$RAW_EVENT_DIR" \
  --raw-event-buffer-size "$RAW_EVENT_BUFFER_SIZE" \
  --decision-snapshot-dir "$DECISION_SNAPSHOT_DIR" \
  --decision-event-buffer-size "$DECISION_EVENT_BUFFER_SIZE" \
  --out "$STATUS_PATH"
