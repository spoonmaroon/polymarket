#!/usr/bin/env bash
set -euo pipefail

LABEL="com.goon.polymarket-thepc-api-tunnel"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
HEALTH_URL="${POLYMARKET_TUNNEL_HEALTH_URL:-http://127.0.0.1:8000/health}"

if [ ! -f "$PLIST" ]; then
  echo "missing LaunchAgent plist: $PLIST" >&2
  exit 1
fi

if ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl bootstrap "$DOMAIN" "$PLIST"
fi

launchctl kickstart -k "$DOMAIN/$LABEL"

for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null; then
    echo "Mac tunnel OK: $HEALTH_URL"
    exit 0
  fi
  sleep 1
done

echo "Mac tunnel did not become healthy: $HEALTH_URL" >&2
exit 1
