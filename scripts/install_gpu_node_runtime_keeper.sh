#!/usr/bin/env bash
set -euo pipefail

REPO="${POLYMARKET_REPO:-/home/enoch/polymarket}"
DATA_DIR="${POLYMARKET_DATA_DIR:-/home/enoch/polymarket-data}"
BIN_DIR="${POLYMARKET_BIN_DIR:-/home/enoch/bin}"
LOOP_SCRIPT="$BIN_DIR/polymarket-runtime-keeper-loop.sh"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_PATH="$SERVICE_DIR/polymarket-runtime-keeper.service"

mkdir -p "$BIN_DIR" "$DATA_DIR/live" "$SERVICE_DIR"

cd "$REPO"
python3 -m pip install --user --break-system-packages -e "$REPO"

cat > "$LOOP_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
cd "$REPO"
ENGINE_BIN="${POLYMARKET_ENGINE_BIN:-$HOME/.local/bin/polymarket-engine}"
if [ ! -x "$ENGINE_BIN" ]; then
  ENGINE_BIN="polymarket-engine"
fi
exec "$ENGINE_BIN" runtime-keeper \
  --repo "$REPO" \
  --data-dir "$DATA_DIR" \
  --api-base-url "http://127.0.0.1:8000" \
  --compose-file "$REPO/deploy/collector/docker-compose.yml" \
  --compose-file "$REPO/deploy/collector/docker-compose.thepc-gpu-api.yml" \
  --required-service "api" \
  --required-service "gpu-probability-worker" \
  --recovery-warmup-min-seconds 15 \
  --recovery-required-healthy-cycles 1 \
  --loop \
  --loop-interval-seconds 30
EOF
chmod 755 "$LOOP_SCRIPT"

cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Polymarket runtime keeper loop
After=default.target

[Service]
Type=simple
ExecStart=$LOOP_SCRIPT
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
EOF

if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable --now polymarket-runtime-keeper.service
else
  echo "systemd user service unavailable; start $LOOP_SCRIPT manually or enable linger for this user" >&2
  exit 1
fi

echo "Installed $LOOP_SCRIPT"
echo "Installed $SERVICE_PATH"
