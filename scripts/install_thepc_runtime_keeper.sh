#!/usr/bin/env bash
set -euo pipefail

REPO="${POLYMARKET_REPO:-/home/ender/polymarket}"
DATA_DIR="${POLYMARKET_DATA_DIR:-/home/ender/polymarket-data}"
BIN_DIR="${POLYMARKET_BIN_DIR:-/home/ender/bin}"
WSL_DISTRO="${POLYMARKET_WSL_DISTRO:-Ubuntu}"
WINDOWS_USER_DIR="${POLYMARKET_WINDOWS_USER_DIR:-/mnt/c/Users/ender}"
LOOP_SCRIPT="$BIN_DIR/polymarket-runtime-keeper-loop.sh"
POWERSHELL_SCRIPT="$WINDOWS_USER_DIR/polymarket-runtime-keeper.ps1"
TASK_NAME="Polymarket Runtime Keeper"

mkdir -p "$BIN_DIR" "$DATA_DIR/live"

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
  --loop \\
  --loop-interval-seconds 30
EOF
chmod 755 "$LOOP_SCRIPT"

if [ ! -d "$WINDOWS_USER_DIR" ]; then
  echo "Windows user directory missing: $WINDOWS_USER_DIR" >&2
  exit 1
fi

cat > "$POWERSHELL_SCRIPT" <<EOF
\$ErrorActionPreference = 'Stop'
Start-Sleep -Seconds 20
& wsl.exe -d $WSL_DISTRO -- "$LOOP_SCRIPT"
EOF

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "\
\$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File \"\$env:USERPROFILE\\polymarket-runtime-keeper.ps1\"'; \
\$trigger = New-ScheduledTaskTrigger -AtLogOn; \
\$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); \
Register-ScheduledTask -TaskName '$TASK_NAME' -Action \$action -Trigger \$trigger -Settings \$settings -Force | Out-Null"

echo "Installed $LOOP_SCRIPT"
echo "Installed $POWERSHELL_SCRIPT"
echo "Registered scheduled task: $TASK_NAME"
