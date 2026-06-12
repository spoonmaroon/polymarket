#!/usr/bin/env bash
set -euo pipefail

LABEL="com.goon.polymarket-thepc-api-tunnel"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
HEALTH_URL="${POLYMARKET_TUNNEL_HEALTH_URL:-http://127.0.0.1:8000/health}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$SCRIPT_DIR/run_mac_polymarket_tunnel.sh"

if [ ! -x "$RUNNER" ]; then
  echo "missing tunnel runner: $RUNNER" >&2
  exit 1
fi

write_plist() {
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$RUNNER</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/polymarket-thepc-api-tunnel.out.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/polymarket-thepc-api-tunnel.err.log</string>
</dict>
</plist>
EOF
}

if [ ! -f "$PLIST" ] || ! grep -Fq "run_mac_polymarket_tunnel.sh" "$PLIST"; then
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  fi
  write_plist
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
