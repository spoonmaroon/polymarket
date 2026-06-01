#!/usr/bin/env bash
set -euo pipefail

LOCAL_REPO="${LOCAL_REPO:-/Users/goon/polymarket}"
REMOTE_HOST="${REMOTE_HOST:-spoon}"
REMOTE_DATA_DIR="${REMOTE_DATA_DIR:-/home/spoon/polymarket-data}"
PID_FILE="$LOCAL_REPO/logs/live-collector.pid"

if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE")"
  if ps -p "$pid" >/dev/null 2>&1; then
    echo "Stopping local collector pid=$pid"
    kill "$pid"
    for _ in $(seq 1 30); do
      if ! ps -p "$pid" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    if ps -p "$pid" >/dev/null 2>&1; then
      echo "Local collector did not stop within 30 seconds" >&2
      exit 1
    fi
  fi
fi

ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_DATA_DIR/raw' '$REMOTE_DATA_DIR/db' '$REMOTE_DATA_DIR/live' '$REMOTE_DATA_DIR/logs' && touch '$REMOTE_DATA_DIR/raw/.polymarket_archive_root'"

RSYNC_PROGRESS_ARGS=(--progress)
if rsync --help 2>&1 | grep -q -- "--info="; then
  RSYNC_PROGRESS_ARGS=(--info=progress2)
fi

rsync -a "${RSYNC_PROGRESS_ARGS[@]}" "$LOCAL_REPO/data/raw/" "$REMOTE_HOST:$REMOTE_DATA_DIR/raw/"
rsync -a "${RSYNC_PROGRESS_ARGS[@]}" "$LOCAL_REPO/data/db/" "$REMOTE_HOST:$REMOTE_DATA_DIR/db/"
rsync -a "${RSYNC_PROGRESS_ARGS[@]}" "$LOCAL_REPO/data/live/" "$REMOTE_HOST:$REMOTE_DATA_DIR/live/"
rsync -a "${RSYNC_PROGRESS_ARGS[@]}" "$LOCAL_REPO/logs/" "$REMOTE_HOST:$REMOTE_DATA_DIR/logs/"

echo "Migration copied data to $REMOTE_HOST:$REMOTE_DATA_DIR"
echo "Next: run /home/spoon/polymarket/scripts/deploy.sh on spoon and verify status freshness."
