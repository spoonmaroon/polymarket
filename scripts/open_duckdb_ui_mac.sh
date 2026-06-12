#!/bin/zsh
emulate -L zsh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SPOON_HOST="${SPOON_HOST:-spoon@100.126.126.1}"
REMOTE_SCRIPT="${POLYMARKET_DUCKDB_UI_REMOTE_SCRIPT:-/home/spoon/bin/open-polymarket-duckdb-ui.sh}"
REMOTE_PORT="${POLYMARKET_DUCKDB_UI_PORT:-4213}"
LOCAL_PORT="${POLYMARKET_DUCKDB_UI_LOCAL_PORT:-$REMOTE_PORT}"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/polymarket-duckdb-ui-mac-launch.log"
URL="http://127.0.0.1:${LOCAL_PORT}"
META_URL="${URL}/api/meta"

mkdir -p "$LOG_DIR"
{
  echo "launch $(date -Iseconds)"
  echo "spoon_host=$SPOON_HOST"
  echo "remote_port=$REMOTE_PORT"
  echo "local_port=$LOCAL_PORT"
} >> "$LOG_FILE"

if [[ "${POLYMARKET_DUCKDB_UI_TEST_LAUNCH:-0}" == "1" ]]; then
  echo "Mac DuckDB UI launcher ready."
  exit 0
fi

is_spoon_viewer() {
  curl -fsS --max-time 2 "$META_URL" 2>> "$LOG_FILE" \
    | python3 -c 'import json,sys; meta=json.load(sys.stdin); sys.exit(0 if meta.get("source_host") == "spoon" else 1)' \
      >/dev/null 2>> "$LOG_FILE"
}

clear_stale_local_endpoint() {
  local pids pid command
  pids="$(lsof -tiTCP:"$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null || true)"
  for pid in ${(f)pids}; do
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    case "$command" in
      *ssh*"-L ${LOCAL_PORT}:127.0.0.1:"*|*ssh*"-L ${LOCAL_PORT}:localhost:"*)
        echo "Clearing stale DuckDB UI tunnel pid=$pid command=$command" >> "$LOG_FILE"
        kill "$pid" >/dev/null 2>&1 || true
        ;;
    esac
  done
}

STARTED_BY_INSTALL=0
if [[ "${POLYMARKET_DUCKDB_UI_INSTALL:-0}" == "1" ]] || ! ssh -n "$SPOON_HOST" "test -x $REMOTE_SCRIPT" >> "$LOG_FILE" 2>&1; then
  echo "Installing DuckDB UI helper on spoon..."
  SPOON_HOST="$SPOON_HOST" POLYMARKET_DUCKDB_UI_REMOTE_SCRIPT="$REMOTE_SCRIPT" POLYMARKET_DUCKDB_UI_PORT="$REMOTE_PORT" "$ROOT/scripts/install_spoon_duckdb_ui.sh" >> "$LOG_FILE" 2>&1 || {
    echo
    echo "Could not install DuckDB UI helper on spoon."
    echo "Log: $LOG_FILE"
    echo
    read -r "?Press Return to close."
    exit 1
  }
  STARTED_BY_INSTALL=1
fi

if [[ "$STARTED_BY_INSTALL" != "1" ]]; then
  echo "Starting DuckDB UI on spoon..."
  if ! ssh -n "$SPOON_HOST" "$REMOTE_SCRIPT --port $REMOTE_PORT" >> "$LOG_FILE" 2>&1; then
    echo
    echo "Could not start DuckDB UI on spoon."
    echo "Run ./scripts/install_spoon_duckdb_ui.sh once so the spoon DuckDB UI helper is installed."
    echo "Log: $LOG_FILE"
    echo
    read -r "?Press Return to close."
    exit 1
  fi
fi

if ! is_spoon_viewer; then
  clear_stale_local_endpoint
  echo "Opening SSH tunnel to spoon DuckDB UI..."
  ssh -o ExitOnForwardFailure=yes -f -N -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" "$SPOON_HOST" >> "$LOG_FILE" 2>&1 || {
    echo
    echo "Could not open SSH tunnel to spoon DuckDB UI."
    echo "Log: $LOG_FILE"
    echo
    read -r "?Press Return to close."
    exit 1
  }
fi

for _ in {1..20}; do
  if is_spoon_viewer; then
    echo "Opening $URL"
    open "$URL"
    exit 0
  fi
  sleep 0.5
done

echo
echo "DuckDB UI tunnel opened, but the UI did not answer at $URL."
echo "Log: $LOG_FILE"
echo
read -r "?Press Return to close."
exit 1
