#!/usr/bin/env bash
set -euo pipefail

REPO="${POLYMARKET_REPO:-/home/ender/polymarket}"
DATA_DIR="${POLYMARKET_DATA_DIR:-/home/ender/polymarket-data}"
BIN_DIR="${POLYMARKET_BIN_DIR:-/home/ender/bin}"
WSL_DISTRO="${POLYMARKET_WSL_DISTRO:-Ubuntu}"
WINDOWS_USER_DIR="${POLYMARKET_WINDOWS_USER_DIR:-/mnt/c/Users/ender}"
LOOP_SCRIPT="$BIN_DIR/polymarket-runtime-keeper-loop.sh"
POWERSHELL_SCRIPT="$WINDOWS_USER_DIR/polymarket-runtime-keeper.ps1"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_PATH="$SERVICE_DIR/polymarket-runtime-keeper.service"
TASK_NAME="Polymarket Runtime Keeper"

mkdir -p "$BIN_DIR" "$DATA_DIR/live" "$SERVICE_DIR"

cd "$REPO"
python3 -m pip install --user --break-system-packages -e "$REPO"

cat > "$LOOP_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PATH="\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:\$PATH"
cd "$REPO"
ENGINE_BIN="\${POLYMARKET_ENGINE_BIN:-\$HOME/.local/bin/polymarket-engine}"
if [ ! -x "\$ENGINE_BIN" ]; then
  ENGINE_BIN="polymarket-engine"
fi
exec "\$ENGINE_BIN" runtime-keeper \\
  --repo "$REPO" \\
  --data-dir "$DATA_DIR" \\
  --api-base-url "http://127.0.0.1:8000" \\
  --compose-file "$REPO/deploy/collector/docker-compose.yml" \\
  --compose-file "$REPO/deploy/collector/docker-compose.thepc-gpu-api.yml" \\
  --required-service "api" \\
  --required-service "gpu-probability-worker" \\
  --recovery-warmup-min-seconds 15 \\
  --recovery-required-healthy-cycles 1 \\
  --loop \\
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
  echo "systemd user service unavailable; Windows scheduled task will remain the fallback" >&2
fi

if [ ! -d "$WINDOWS_USER_DIR" ]; then
  echo "Windows user directory missing: $WINDOWS_USER_DIR" >&2
  exit 1
fi
POWERSHELL_SCRIPT_WINDOWS="$(wslpath -w "$POWERSHELL_SCRIPT")"

cat > "$POWERSHELL_SCRIPT" <<EOF
\$ErrorActionPreference = 'Continue'
while (\$true) {
  Start-Sleep -Seconds 20
  & wsl.exe -d $WSL_DISTRO -- bash -lc 'systemctl --user start polymarket-runtime-keeper.service polymarket-spoon-artifact-sync.service >/dev/null 2>&1 || true; exec sleep 3600'
  Start-Sleep -Seconds 15
}
EOF

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "\
\$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File \"$POWERSHELL_SCRIPT_WINDOWS\"'; \
\$trigger = New-ScheduledTaskTrigger -AtLogOn; \
\$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0); \
Register-ScheduledTask -TaskName '$TASK_NAME' -Action \$action -Trigger \$trigger -Settings \$settings -Force | Out-Null"

echo "Installed $LOOP_SCRIPT"
echo "Installed $SERVICE_PATH"
echo "Installed $POWERSHELL_SCRIPT"
echo "Registered scheduled task: $TASK_NAME"
