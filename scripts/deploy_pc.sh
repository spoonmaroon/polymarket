#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist/docker}"
DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-HEAD}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"

PC_HOST="${PC_HOST:-ender@100.72.104.49}"
PC_WSL_DISTRO="${PC_WSL_DISTRO:-Ubuntu}"
PC_REPO="${PC_REPO:-/home/ender/polymarket}"
PC_BUNDLE="${PC_BUNDLE:-/home/ender/polymarket.bundle}"
PC_DATA_DIR="${PC_DATA_DIR:-/home/ender/polymarket-data}"
PC_DIST_DIR="${PC_DIST_DIR:-/home/ender/polymarket-image-artifacts}"
PC_BIN_DIR="${PC_BIN_DIR:-/home/ender/bin}"
PC_NORMALIZER_INTERVAL_SECONDS="${PC_NORMALIZER_INTERVAL_SECONDS:-0.1}"
PC_REST_BACKUP_INTERVAL_MS="${PC_REST_BACKUP_INTERVAL_MS:-1000}"
PC_API_PORT="${PC_API_PORT:-8000}"
PC_DEPLOY_BUILD_IMAGES="${PC_DEPLOY_BUILD_IMAGES:-1}"
PC_BRANCH="${PC_BRANCH:-$(git -C "$ROOT" branch --show-current)}"

if [ -z "$PC_BRANCH" ]; then
  echo "could not infer current git branch; set PC_BRANCH explicitly" >&2
  exit 1
fi

if ! git -C "$ROOT" diff --quiet; then
  echo "working tree has unstaged changes; commit or stash before deploying to THEPC" >&2
  exit 1
fi

if ! git -C "$ROOT" diff --cached --quiet; then
  echo "working tree has staged changes; commit or unstage before deploying to THEPC" >&2
  exit 1
fi

if [ -n "$(git -C "$ROOT" ls-files --others --exclude-standard)" ]; then
  echo "working tree has untracked files; commit, remove, or ignore them before deploying to THEPC" >&2
  exit 1
fi

FULL_SHA="$(git -C "$ROOT" rev-parse "$DEPLOY_REF^{commit}")"
HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"
if [ "$HEAD_SHA" != "$FULL_SHA" ]; then
  echo "deploy ref $DEPLOY_REF resolves to $FULL_SHA but HEAD is $HEAD_SHA; checkout the deploy ref first" >&2
  exit 1
fi
SHORT_SHA="${FULL_SHA:0:12}"

COLLECTOR_IMAGE="polymarket-rust-collector:${SHORT_SHA}"
NORMALIZER_IMAGE="polymarket-normalizer:${SHORT_SHA}"
COLLECTOR_TAR="$DIST_DIR/polymarket-rust-collector-${SHORT_SHA}.tar"
NORMALIZER_TAR="$DIST_DIR/polymarket-normalizer-${SHORT_SHA}.tar"
TUI_BIN="$DIST_DIR/polymarket-cockpit-tui-${SHORT_SHA}"
LOCAL_BUNDLE="$DIST_DIR/polymarket-${SHORT_SHA}.bundle"

if [ "$PC_DEPLOY_BUILD_IMAGES" = "1" ]; then
  TARGET_PLATFORM="$TARGET_PLATFORM" POLYMARKET_DEPLOY_REF="$DEPLOY_REF" "$ROOT/scripts/build_images_pc.sh"
fi

if [ ! -f "$COLLECTOR_TAR" ]; then
  echo "missing collector image tarball: $COLLECTOR_TAR" >&2
  exit 1
fi

if [ ! -f "$NORMALIZER_TAR" ]; then
  echo "missing normalizer image tarball: $NORMALIZER_TAR" >&2
  exit 1
fi

if [ ! -f "$TUI_BIN" ]; then
  echo "missing TUI binary: $TUI_BIN" >&2
  exit 1
fi

mkdir -p "$DIST_DIR"
git -C "$ROOT" bundle create "$LOCAL_BUNDLE.tmp" --branches --tags
mv "$LOCAL_BUNDLE.tmp" "$LOCAL_BUNDLE"

shell_quote() {
  printf "%q" "$1"
}

wsl_put_file() {
  local src="$1"
  local dest="$2"
  local dest_dir
  local dest_dir_q
  local dest_q

  dest_dir="$(dirname "$dest")"
  dest_dir_q="$(shell_quote "$dest_dir")"
  dest_q="$(shell_quote "$dest")"

  ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- bash -lc \"mkdir -p $dest_dir_q && cat > $dest_q\"" < "$src"
}

echo "copying git bundle and image tarballs to THEPC WSL"
wsl_put_file "$LOCAL_BUNDLE" "$PC_BUNDLE"
wsl_put_file "$COLLECTOR_TAR" "$PC_DIST_DIR/$(basename "$COLLECTOR_TAR")"
wsl_put_file "$NORMALIZER_TAR" "$PC_DIST_DIR/$(basename "$NORMALIZER_TAR")"
wsl_put_file "$TUI_BIN" "$PC_DIST_DIR/$(basename "$TUI_BIN")"

ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- bash -s" <<EOF
set -euo pipefail

FULL_SHA=$(shell_quote "$FULL_SHA")
SHORT_SHA=$(shell_quote "$SHORT_SHA")
PC_BRANCH=$(shell_quote "$PC_BRANCH")
PC_REPO=$(shell_quote "$PC_REPO")
PC_BUNDLE=$(shell_quote "$PC_BUNDLE")
PC_DATA_DIR=$(shell_quote "$PC_DATA_DIR")
PC_DIST_DIR=$(shell_quote "$PC_DIST_DIR")
PC_BIN_DIR=$(shell_quote "$PC_BIN_DIR")
PC_WSL_DISTRO=$(shell_quote "$PC_WSL_DISTRO")
PC_NORMALIZER_INTERVAL_SECONDS=$(shell_quote "$PC_NORMALIZER_INTERVAL_SECONDS")
PC_REST_BACKUP_INTERVAL_MS=$(shell_quote "$PC_REST_BACKUP_INTERVAL_MS")
PC_API_PORT=$(shell_quote "$PC_API_PORT")
COLLECTOR_IMAGE=$(shell_quote "$COLLECTOR_IMAGE")
NORMALIZER_IMAGE=$(shell_quote "$NORMALIZER_IMAGE")
COLLECTOR_TAR=$(shell_quote "$PC_DIST_DIR/$(basename "$COLLECTOR_TAR")")
NORMALIZER_TAR=$(shell_quote "$PC_DIST_DIR/$(basename "$NORMALIZER_TAR")")
TUI_BIN=$(shell_quote "$PC_DIST_DIR/$(basename "$TUI_BIN")")

set_env() {
  key="\$1"
  value="\$2"
  file="\$3"
  tmp="\$(mktemp)"
  touch "\$file"
  awk -v key="\$key" -v value="\$value" '
    BEGIN { found = 0 }
    \$0 ~ "^" key "=" {
      print key "=" value
      found = 1
      next
    }
    { print }
    END {
      if (!found) {
        print key "=" value
      }
    }
  ' "\$file" > "\$tmp"
  mv "\$tmp" "\$file"
}

mkdir -p "\$PC_DATA_DIR/raw" "\$PC_DATA_DIR/db" "\$PC_DATA_DIR/live" "\$PC_DATA_DIR/logs" "\$PC_DIST_DIR" "\$PC_BIN_DIR"
touch "\$PC_DATA_DIR/raw/.polymarket_archive_root"

if [ ! -d "\$PC_REPO/.git" ]; then
  git clone "\$PC_BUNDLE" "\$PC_REPO"
fi

cd "\$PC_REPO"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "THEPC repo is dirty; refusing deploy" >&2
  git status --porcelain >&2
  exit 1
fi

git remote set-url origin "\$PC_BUNDLE" 2>/dev/null || git remote add origin "\$PC_BUNDLE"
git fetch --quiet origin
git checkout -B "\$PC_BRANCH" "\$FULL_SHA"

if [ ! -f deploy/collector/.env ]; then
  cp deploy/collector/.env.example deploy/collector/.env
fi

set_env POLYMARKET_UID "\$(id -u)" deploy/collector/.env
set_env POLYMARKET_GID "\$(id -g)" deploy/collector/.env
set_env POLYMARKET_DATA_DIR "\$PC_DATA_DIR" deploy/collector/.env
set_env POLYMARKET_NORMALIZER_INTERVAL_SECONDS "\$PC_NORMALIZER_INTERVAL_SECONDS" deploy/collector/.env
set_env POLYMARKET_REST_BACKUP_INTERVAL_MS "\$PC_REST_BACKUP_INTERVAL_MS" deploy/collector/.env
set_env POLYMARKET_API_PORT "\$PC_API_PORT" deploy/collector/.env
set_env POLYMARKET_COLLECTOR_IMAGE "\$COLLECTOR_IMAGE" deploy/collector/.env
set_env POLYMARKET_NORMALIZER_IMAGE "\$NORMALIZER_IMAGE" deploy/collector/.env

docker load -i "\$COLLECTOR_TAR"
docker load -i "\$NORMALIZER_TAR"
install -m 755 "\$TUI_BIN" "\$PC_BIN_DIR/polymarket-cockpit-tui"

{
  printf '%s\n' '#!/usr/bin/env bash'
  printf '%s\n' 'set -euo pipefail'
  printf 'cd %q\n' "\$PC_REPO"
  printf '%s\n' "echo 'Checking Polymarket runtime...'"
  printf 'if curl -fsS --max-time 2 http://127.0.0.1:%s/api/runtime/live?limit=8 2>/dev/null | python3 -c %q >/dev/null 2>&1; then\n' "\$PC_API_PORT" 'import json,sys; p=json.load(sys.stdin); m=p.get("monitor") or {}; sys.exit(0 if p.get("ok") and len(m.get("orderbooks") or []) > 0 else 1)'
  printf '%s\n' "  echo 'Runtime already live.'"
  printf '%s\n' 'else'
  printf '%s\n' "  echo 'Runtime not live; starting containers...'"
  printf '%s\n' '  docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d --no-recreate collector normalizer outcome-refresh api >/dev/null 2>&1 || true'
  printf '%s\n' 'fi'
  printf '%s\n' "echo 'Waiting for runtime API and live market rows...'"
  printf '%s\n' 'for _ in \$(seq 1 45); do'
  printf '  if curl -fsS --max-time 2 http://127.0.0.1:%s/api/runtime/live?limit=8 2>/dev/null | python3 -c %q >/dev/null 2>&1; then\n' "\$PC_API_PORT" 'import json,sys; p=json.load(sys.stdin); m=p.get("monitor") or {}; sys.exit(0 if p.get("ok") and len(m.get("orderbooks") or []) > 0 else 1)'
  printf '%s\n' '    break'
  printf '%s\n' '  fi'
  printf '%s\n' '  sleep 1'
  printf '%s\n' 'done'
  printf 'exec %q --engine-api-url http://127.0.0.1:%s --poll-interval-ms 250\n' "\$PC_BIN_DIR/polymarket-cockpit-tui" "\$PC_API_PORT"
} > "\$PC_BIN_DIR/open-polymarket-tui.sh"
chmod 755 "\$PC_BIN_DIR/open-polymarket-tui.sh"

cat > "\$PC_BIN_DIR/open-polymarket-tui-window.sh" <<'TUI_WINDOW_LAUNCHER'
#!/usr/bin/env bash
set +e
__PC_BIN_DIR__/open-polymarket-tui.sh
status=\$?
if [ "\$status" -ne 0 ]; then
  echo
  echo "Polymarket TUI exited with status \$status"
  read -r -p "Press Enter to close"
fi
exit "\$status"
TUI_WINDOW_LAUNCHER
sed -i "s|__PC_BIN_DIR__|\$PC_BIN_DIR|g" "\$PC_BIN_DIR/open-polymarket-tui-window.sh"
chmod 755 "\$PC_BIN_DIR/open-polymarket-tui-window.sh"

WINDOWS_USER_DIR="/mnt/c/Users/ender"
if [ -d "\$WINDOWS_USER_DIR" ]; then
  cat > "\$WINDOWS_USER_DIR/open-polymarket-tui.ps1" <<'PS_LAUNCHER'
\$ErrorActionPreference = 'Stop'
\$logPath = Join-Path \$env:USERPROFILE 'polymarket-tui-launch.log'
\$arguments = @('-w', 'new', 'new-tab', '--title', 'Polymarket TUI', 'wsl.exe', '-d', '__PC_WSL_DISTRO__', '--', '__PC_BIN_DIR__/open-polymarket-tui-window.sh')

try {
  Start-Transcript -Path \$logPath -Append | Out-Null
} catch {
}

try {
  Add-Content -Path \$logPath -Value ("launch " + (Get-Date).ToString("o"))
  Start-Process -FilePath 'wt.exe' -ArgumentList \$arguments -WindowStyle Normal
} catch {
  Write-Host 'Failed to launch Windows Terminal for Polymarket TUI.'
  Write-Host \$_
  Write-Host 'Falling back to WSL in this PowerShell window.'
  & wsl.exe -d __PC_WSL_DISTRO__ -- __PC_BIN_DIR__/open-polymarket-tui-window.sh
  \$exitCode = \$LASTEXITCODE
  if (\$exitCode -ne 0) {
    Read-Host 'Press Enter to close'
  }
  exit \$exitCode
} finally {
  try {
    Stop-Transcript | Out-Null
  } catch {
  }
}
PS_LAUNCHER
  sed -i "s|__PC_BIN_DIR__|\$PC_BIN_DIR|g; s|__PC_WSL_DISTRO__|\$PC_WSL_DISTRO|g" "\$WINDOWS_USER_DIR/open-polymarket-tui.ps1"
  cat > "\$WINDOWS_USER_DIR/open-polymarket-tui.cmd" <<CMD_LAUNCHER
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\\open-polymarket-tui.ps1"
CMD_LAUNCHER
  POWERSHELL_SCRIPT="\$WINDOWS_USER_DIR/AppData/Local/Temp/polymarket-tui-shortcut.ps1"
  cat > "\$POWERSHELL_SCRIPT" <<'PS1'
\$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Polymarket TUI.lnk'
\$launcherPath = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'open-polymarket-tui.ps1'
\$shell = New-Object -ComObject WScript.Shell
\$shortcut = \$shell.CreateShortcut(\$shortcutPath)
\$shortcut.TargetPath = 'powershell.exe'
\$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + \$launcherPath + '"'
\$shortcut.WorkingDirectory = [Environment]::GetFolderPath('UserProfile')
\$shortcut.IconLocation = 'C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe,0'
\$shortcut.WindowStyle = 1
\$shortcut.Save()
PS1
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\$(wslpath -w "\$POWERSHELL_SCRIPT")" >/dev/null < /dev/null
  rm -f "\$POWERSHELL_SCRIPT"
fi

export POLYMARKET_DEPLOY_USE_PREBUILT=1
export POLYMARKET_DEPLOY_REF="\$FULL_SHA"
export POLYMARKET_EXPECTED_DEPLOY_SHA="\$FULL_SHA"
export POLYMARKET_COLLECTOR_IMAGE="\$COLLECTOR_IMAGE"
export POLYMARKET_NORMALIZER_IMAGE="\$NORMALIZER_IMAGE"
export POLYMARKET_DATA_DIR="\$PC_DATA_DIR"
export POLYMARKET_NORMALIZER_INTERVAL_SECONDS="\$PC_NORMALIZER_INTERVAL_SECONDS"
export POLYMARKET_REST_BACKUP_INTERVAL_MS="\$PC_REST_BACKUP_INTERVAL_MS"
export DEPLOY_FORCE=1
./scripts/deploy.sh

python3 scripts/check_collector_status.py \\
  --status-path "\$PC_DATA_DIR/live/status.json" \\
  --max-status-age-seconds 30 \\
  --max-price-age-ms 30000 \\
  --max-orderbook-age-ms 30000 \\
  --max-websocket-event-age-ms 30000 \\
  --raw-root "\$PC_DATA_DIR/raw" \\
  --max-raw-event-age-ms 30000 \\
  --normalized-health-path "\$PC_DATA_DIR/live/normalized_health.json" \\
  --max-normalized-health-age-ms 30000 \\
  --expected-prewarm-windows 2

POLYMARKET_API_PORT="\$PC_API_PORT" python3 - <<'PY'
import json
import os
import urllib.request

base = f"http://127.0.0.1:{os.environ['POLYMARKET_API_PORT']}"


def get_json(path: str) -> dict[str, object]:
    with urllib.request.urlopen(base + path, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


health = get_json("/health")
if health.get("status") != "ok":
    raise SystemExit(f"health smoke failed: {health}")

live = get_json("/api/runtime/live?limit=8")
if live.get("ok") is not True or not live.get("monitor", {}).get("orderbooks"):
    raise SystemExit(f"runtime live smoke failed: {live}")

outcomes = get_json("/api/runtime/outcomes?limit=8")
if outcomes.get("ok") is not True or not isinstance(outcomes.get("rows"), list):
    raise SystemExit(f"runtime outcomes smoke failed: {outcomes}")

with urllib.request.urlopen(
    base + "/api/runtime/live/stream?limit=8&interval_ms=250&max_events=1",
    timeout=15,
) as response:
    body = response.read().decode("utf-8")
if "event: live" not in body or "data: " not in body:
    raise SystemExit("runtime SSE smoke failed")
PY

docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml ps
printf 'THEPC TUI installed %s\\n' "\$PC_BIN_DIR/polymarket-cockpit-tui"
printf 'THEPC TUI launcher installed %s\\n' "\$PC_BIN_DIR/open-polymarket-tui.sh"
printf 'THEPC deployed %s\\n' "\$FULL_SHA"
EOF
