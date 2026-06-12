#!/bin/zsh
emulate -L zsh
set -uo pipefail

REPO="${POLYMARKET_REPO:-/Users/goon/polymarket}"
RUST_DIR="$REPO/rust"
TUI_BIN="$RUST_DIR/target/release/polymarket-cockpit-tui"
BUILD_MARKER="$RUST_DIR/target/release/.polymarket-cockpit-tui.git-head"
API_URL="${POLYMARKET_ENGINE_API_URL:-http://127.0.0.1:8000}"
TUNNEL_CHECK="${POLYMARKET_TUNNEL_CHECK:-$REPO/scripts/check_mac_polymarket_tunnel.sh}"
POLL_INTERVAL_MS="${POLYMARKET_TUI_POLL_INTERVAL_MS:-250}"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/polymarket-tui-mac-launch.log"

mkdir -p "$LOG_DIR"
current_head="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || printf 'unknown')"
last_built_head="$(cat "$BUILD_MARKER" 2>/dev/null || true)"

{
  echo "launch $(date -Iseconds)"
  echo "api=$API_URL"
  echo "repo=$REPO"
  echo "head=$current_head"
  echo "built_head=${last_built_head:-missing}"
} >> "$LOG_FILE"

if [[ "$API_URL" == "http://127.0.0.1:8000" && -x "$TUNNEL_CHECK" ]]; then
  echo "Starting THEPC API tunnel..."
  if ! "$TUNNEL_CHECK" >> "$LOG_FILE" 2>&1; then
    echo
    echo "Could not start the THEPC API tunnel."
    echo "Log: $LOG_FILE"
    echo
    read -r "?Press Return to close."
    exit 1
  fi
fi

echo "Checking THEPC Polymarket runtime..."
if ! curl -fsS --max-time 4 "$API_URL/api/runtime/live?limit=1" >/dev/null 2>> "$LOG_FILE"; then
  echo
  echo "Could not reach THEPC runtime at $API_URL."
  echo "Check that THEPC is awake, Tailscale is connected, and the collector is running."
  echo "Log: $LOG_FILE"
  echo
  read -r "?Press Return to close."
  exit 1
fi

if [[ ! -x "$TUI_BIN" || "$last_built_head" != "$current_head" ]]; then
  echo "Building current Mac TUI..."
  {
    echo "building TUI"
    echo "cwd=$RUST_DIR"
  } >> "$LOG_FILE"
  (
    cd "$RUST_DIR" && cargo build --release -p polymarket-cockpit-tui
  ) 2>&1 | tee -a "$LOG_FILE"
  build_status=${pipestatus[1]}
  if [[ "$build_status" -ne 0 ]]; then
    echo
    echo "TUI build failed."
    echo "Log: $LOG_FILE"
    echo
    read -r "?Press Return to close."
    exit "$build_status"
  fi
  mkdir -p "$(dirname "$BUILD_MARKER")"
  printf '%s\n' "$current_head" > "$BUILD_MARKER"
fi

if [[ ! -x "$TUI_BIN" ]]; then
  echo
  echo "TUI binary is missing:"
  echo "$TUI_BIN"
  echo "Log: $LOG_FILE"
  echo
  read -r "?Press Return to close."
  exit 1
fi

if [[ "${POLYMARKET_TUI_TEST_LAUNCH:-0}" == "1" ]]; then
  echo "Mac TUI launcher ready."
  exit 0
fi

echo "Opening Polymarket TUI..."
"$TUI_BIN" --engine-api-url "$API_URL" --poll-interval-ms "$POLL_INTERVAL_MS"
status=$?

if [[ "$status" -ne 0 ]]; then
  echo
  echo "Polymarket TUI exited with status $status."
  echo "Log: $LOG_FILE"
  echo
  read -r "?Press Return to close."
fi

exit "$status"
