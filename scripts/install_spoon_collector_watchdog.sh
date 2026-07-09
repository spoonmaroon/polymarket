#!/usr/bin/env bash
set -euo pipefail

REPO="${POLYMARKET_REPO:-/home/spoon/polymarket}"
DATA_DIR="${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}"
BIN_DIR="${POLYMARKET_BIN_DIR:-/home/spoon/bin}"
SERVICE_DIR="${POLYMARKET_SYSTEMD_USER_DIR:-/home/spoon/.config/systemd/user}"
ENV_FILE="${POLYMARKET_ENV_FILE:-$REPO/deploy/collector/.env}"
COMPOSE_FILE="${POLYMARKET_COMPOSE_FILE:-$REPO/deploy/collector/docker-compose.yml}"
SPOON_OVERLAY="${POLYMARKET_SPOON_OVERLAY:-$REPO/deploy/collector/docker-compose.spoon-cpu-authority.yml}"
SERVICE_NAME="${POLYMARKET_COLLECTOR_SERVICE:-collector}"
CONTAINER_NAME="${POLYMARKET_COLLECTOR_CONTAINER:-polymarket-rust-collector-collector-1}"
CHECK_INTERVAL_SECONDS="${POLYMARKET_WATCHDOG_INTERVAL_SECONDS:-30}"
UNHEALTHY_GRACE_CYCLES="${POLYMARKET_WATCHDOG_UNHEALTHY_GRACE_CYCLES:-2}"
WATCHDOG_SCRIPT="$BIN_DIR/polymarket-spoon-collector-watchdog.sh"
SERVICE_PATH="$SERVICE_DIR/polymarket-spoon-collector-watchdog.service"

mkdir -p "$BIN_DIR" "$SERVICE_DIR" "$DATA_DIR/logs"

cat > "$WATCHDOG_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail

REPO="$REPO"
ENV_FILE="$ENV_FILE"
COMPOSE_FILE="$COMPOSE_FILE"
SPOON_OVERLAY="$SPOON_OVERLAY"
SERVICE_NAME="$SERVICE_NAME"
CONTAINER_NAME="$CONTAINER_NAME"
CHECK_INTERVAL_SECONDS="$CHECK_INTERVAL_SECONDS"
UNHEALTHY_GRACE_CYCLES="$UNHEALTHY_GRACE_CYCLES"

cd "\$REPO"

compose() {
  docker compose --env-file "\$ENV_FILE" \\
    -f "\$COMPOSE_FILE" \\
    -f "\$SPOON_OVERLAY" "\$@"
}

health_status() {
  docker inspect "\$CONTAINER_NAME" \\
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \\
    2>/dev/null || printf 'missing\\n'
}

unhealthy_count=0
while true; do
  status="\$(health_status)"
  case "\$status" in
    healthy)
      unhealthy_count=0
      ;;
    starting)
      ;;
    *)
      unhealthy_count=\$((unhealthy_count + 1))
      if [ "\$unhealthy_count" -ge "\$UNHEALTHY_GRACE_CYCLES" ]; then
        printf 'collector watchdog restarting %s after %s consecutive %s checks\\n' \\
          "\$SERVICE_NAME" "\$unhealthy_count" "\$status" >&2
        compose restart "\$SERVICE_NAME" || compose up -d "\$SERVICE_NAME"
        unhealthy_count=0
      fi
      ;;
  esac
  sleep "\$CHECK_INTERVAL_SECONDS"
done
EOF
chmod 755 "$WATCHDOG_SCRIPT"

cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Polymarket Spoon collector health watchdog
After=network-online.target docker.service

[Service]
Type=simple
ExecStart=$WATCHDOG_SCRIPT
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now polymarket-spoon-collector-watchdog.service

echo "Installed $WATCHDOG_SCRIPT"
echo "Installed $SERVICE_PATH"
