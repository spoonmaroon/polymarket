#!/bin/zsh
emulate -L zsh
set -uo pipefail

PC_HOST="${PC_HOST:-ender@100.72.104.49}"
PC_WSL_DISTRO="${PC_WSL_DISTRO:-Ubuntu}"
REMOTE_SCRIPT="${POLYMARKET_DUCKDB_UI_REMOTE_SCRIPT:-/home/ender/bin/open-polymarket-duckdb-ui.sh}"
REMOTE_PORT="${POLYMARKET_DUCKDB_UI_PORT:-4213}"
LOCAL_PORT="${POLYMARKET_DUCKDB_UI_LOCAL_PORT:-$REMOTE_PORT}"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/polymarket-duckdb-ui-mac-launch.log"
URL="http://127.0.0.1:${LOCAL_PORT}"

mkdir -p "$LOG_DIR"
{
  echo "launch $(date -Iseconds)"
  echo "pc_host=$PC_HOST"
  echo "remote_port=$REMOTE_PORT"
  echo "local_port=$LOCAL_PORT"
} >> "$LOG_FILE"

echo "Starting DuckDB UI on THEPC..."
if ! ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- $REMOTE_SCRIPT --port $REMOTE_PORT" >> "$LOG_FILE" 2>&1; then
  echo
  echo "Could not start DuckDB UI on THEPC."
  echo "Run ./scripts/deploy_pc.sh once so the THEPC DuckDB UI helper is installed."
  echo "Log: $LOG_FILE"
  echo
  read -r "?Press Return to close."
  exit 1
fi

if [[ "${POLYMARKET_DUCKDB_UI_TEST_LAUNCH:-0}" == "1" ]]; then
  echo "Mac DuckDB UI launcher ready."
  exit 0
fi

if ! curl -fsS --max-time 2 "$URL" >/dev/null 2>> "$LOG_FILE"; then
  echo "Opening SSH tunnel to THEPC DuckDB UI..."
  ssh -f -N -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" "$PC_HOST" >> "$LOG_FILE" 2>&1 || {
    echo
    echo "Could not open SSH tunnel to THEPC DuckDB UI."
    echo "Log: $LOG_FILE"
    echo
    read -r "?Press Return to close."
    exit 1
  }
fi

for _ in {1..20}; do
  if curl -fsS --max-time 2 "$URL" >/dev/null 2>> "$LOG_FILE"; then
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
