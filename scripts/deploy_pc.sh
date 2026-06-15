#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist/docker}"
DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-HEAD}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"

PC_HOST="${PC_HOST:-ender@100.72.104.49}"
PC_WSL_DISTRO="${PC_WSL_DISTRO:-Ubuntu}"
PC_REPO="${PC_REPO:-/home/ender/polymarket}"
PC_GIT_REMOTE="${PC_GIT_REMOTE:-git@github.com:AnimeWeeb9000/polymarket.git}"
PC_DATA_DIR="${PC_DATA_DIR:-/home/ender/polymarket-data}"
PC_DIST_DIR="${PC_DIST_DIR:-/home/ender/polymarket-image-artifacts}"
PC_BIN_DIR="${PC_BIN_DIR:-/home/ender/bin}"
PC_NORMALIZER_INTERVAL_SECONDS="${PC_NORMALIZER_INTERVAL_SECONDS:-0.1}"
PC_REST_BACKUP_INTERVAL_MS="${PC_REST_BACKUP_INTERVAL_MS:-1000}"
PC_DEPLOY_ROLE="${PC_DEPLOY_ROLE:-thepc-gpu-api}"
PC_PROBABILITY_CPU_TARGET_PERCENT="${PC_PROBABILITY_CPU_TARGET_PERCENT:-15.0}"
PC_PROBABILITY_CPU_SOFT_MAX_PERCENT="${PC_PROBABILITY_CPU_SOFT_MAX_PERCENT:-20.0}"
PC_PROBABILITY_MAX_CYCLE_RUNTIME_MS="${PC_PROBABILITY_MAX_CYCLE_RUNTIME_MS:-10000}"
PC_PROBABILITY_MAX_TOTAL_PATHS="${PC_PROBABILITY_MAX_TOTAL_PATHS:-10000}"
PC_PROBABILITY_MIN_TOTAL_PATHS="${PC_PROBABILITY_MIN_TOTAL_PATHS:-2000}"
PC_ENABLE_LIVE_PRIOR_FRAGMENTS="${PC_ENABLE_LIVE_PRIOR_FRAGMENTS:-0}"
PC_GPU_WORKER_MEM_LIMIT="${PC_GPU_WORKER_MEM_LIMIT:-1536m}"
PC_API_PORT="${PC_API_PORT:-8000}"
PC_DEPLOY_MODE="${PC_DEPLOY_MODE:-remote-build}"
PC_DEPLOY_BUILD_IMAGES="${PC_DEPLOY_BUILD_IMAGES:-1}"
PC_REMOTE_BUILD_SAVE_TARS="${PC_REMOTE_BUILD_SAVE_TARS:-0}"
PC_BRANCH="${PC_BRANCH:-main}"

if [ -z "$PC_BRANCH" ]; then
  echo "could not infer current git branch; set PC_BRANCH explicitly" >&2
  exit 1
fi

case "$PC_DEPLOY_MODE" in
  remote-build | image-tar)
    ;;
  *)
    echo "unsupported PC_DEPLOY_MODE=$PC_DEPLOY_MODE; expected remote-build or image-tar" >&2
    exit 2
    ;;
esac

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

if [ "$PC_BRANCH" != "main" ]; then
  echo "THEPC deploy is main-only; set PC_BRANCH=main" >&2
  exit 1
fi

git -C "$ROOT" fetch --quiet origin main
LOCAL_MAIN_SHA="$(git -C "$ROOT" rev-parse origin/main^{commit})"
if [ "$LOCAL_MAIN_SHA" != "$FULL_SHA" ]; then
  echo "origin/main is $LOCAL_MAIN_SHA but deploy ref is $FULL_SHA; push main before deploying" >&2
  exit 1
fi
SHORT_SHA="${FULL_SHA:0:12}"

COLLECTOR_IMAGE="polymarket-rust-collector:${SHORT_SHA}"
NORMALIZER_IMAGE="polymarket-normalizer:${SHORT_SHA}"
CUDA_PROBABILITY_IMAGE="polymarket-cuda-probability:${SHORT_SHA}"
COLLECTOR_TAR="$DIST_DIR/polymarket-rust-collector-${SHORT_SHA}.tar"
NORMALIZER_TAR="$DIST_DIR/polymarket-normalizer-${SHORT_SHA}.tar"
CUDA_PROBABILITY_TAR="$DIST_DIR/polymarket-cuda-probability-${SHORT_SHA}.tar"
TUI_BIN="$DIST_DIR/polymarket-cockpit-tui-${SHORT_SHA}"

if [ "$PC_DEPLOY_MODE" = "image-tar" ] && [ "$PC_DEPLOY_BUILD_IMAGES" = "1" ]; then
  TARGET_PLATFORM="$TARGET_PLATFORM" POLYMARKET_DEPLOY_REF="$DEPLOY_REF" "$ROOT/scripts/build_images_pc.sh"
fi

if [ "$PC_DEPLOY_MODE" = "image-tar" ]; then
  if [ ! -f "$COLLECTOR_TAR" ]; then
    echo "missing collector image tarball: $COLLECTOR_TAR" >&2
    exit 1
  fi

  if [ ! -f "$NORMALIZER_TAR" ]; then
    echo "missing normalizer image tarball: $NORMALIZER_TAR" >&2
    exit 1
  fi

  if [ ! -f "$CUDA_PROBABILITY_TAR" ]; then
    echo "missing CUDA probability image tarball: $CUDA_PROBABILITY_TAR" >&2
    exit 1
  fi

  if [ ! -f "$TUI_BIN" ]; then
    echo "missing TUI binary: $TUI_BIN" >&2
    exit 1
  fi
fi

mkdir -p "$DIST_DIR"

shell_quote() {
  printf "%q" "$1"
}

wsl_put_artifact_file() {
  local src="$1"
  local dest="$2"
  local dest_dir
  local dest_dir_q
  local dest_q
  local dest_tmp
  local dest_tmp_q

  dest_dir="$(dirname "$dest")"
  dest_tmp="$dest.tmp.$$"
  dest_dir_q="$(shell_quote "$dest_dir")"
  dest_q="$(shell_quote "$dest")"
  dest_tmp_q="$(shell_quote "$dest_tmp")"

  ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- bash -lc \"mkdir -p $dest_dir_q && cat > $dest_tmp_q && mv -f $dest_tmp_q $dest_q\"" < "$src"
}

if [ "$PC_DEPLOY_MODE" = "remote-build" ]; then
  echo "THEPC WSL will fetch GitHub main and build images locally"
else
  echo "THEPC WSL will fetch GitHub main; copying image tarballs"
fi
if [ "$PC_DEPLOY_MODE" = "image-tar" ]; then
  wsl_put_artifact_file "$COLLECTOR_TAR" "$PC_DIST_DIR/$(basename "$COLLECTOR_TAR")"
  wsl_put_artifact_file "$NORMALIZER_TAR" "$PC_DIST_DIR/$(basename "$NORMALIZER_TAR")"
  wsl_put_artifact_file "$CUDA_PROBABILITY_TAR" "$PC_DIST_DIR/$(basename "$CUDA_PROBABILITY_TAR")"
  wsl_put_artifact_file "$TUI_BIN" "$PC_DIST_DIR/$(basename "$TUI_BIN")"
fi

ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- bash -s" <<EOF
set -euo pipefail

FULL_SHA=$(shell_quote "$FULL_SHA")
SHORT_SHA=$(shell_quote "$SHORT_SHA")
PC_BRANCH=$(shell_quote "$PC_BRANCH")
PC_REPO=$(shell_quote "$PC_REPO")
PC_GIT_REMOTE=$(shell_quote "$PC_GIT_REMOTE")
PC_DATA_DIR=$(shell_quote "$PC_DATA_DIR")
PC_DIST_DIR=$(shell_quote "$PC_DIST_DIR")
PC_BIN_DIR=$(shell_quote "$PC_BIN_DIR")
PC_WSL_DISTRO=$(shell_quote "$PC_WSL_DISTRO")
PC_NORMALIZER_INTERVAL_SECONDS=$(shell_quote "$PC_NORMALIZER_INTERVAL_SECONDS")
PC_REST_BACKUP_INTERVAL_MS=$(shell_quote "$PC_REST_BACKUP_INTERVAL_MS")
PC_DEPLOY_ROLE=$(shell_quote "$PC_DEPLOY_ROLE")
PC_PROBABILITY_CPU_TARGET_PERCENT=$(shell_quote "$PC_PROBABILITY_CPU_TARGET_PERCENT")
PC_PROBABILITY_CPU_SOFT_MAX_PERCENT=$(shell_quote "$PC_PROBABILITY_CPU_SOFT_MAX_PERCENT")
PC_PROBABILITY_MAX_CYCLE_RUNTIME_MS=$(shell_quote "$PC_PROBABILITY_MAX_CYCLE_RUNTIME_MS")
PC_PROBABILITY_MAX_TOTAL_PATHS=$(shell_quote "$PC_PROBABILITY_MAX_TOTAL_PATHS")
PC_PROBABILITY_MIN_TOTAL_PATHS=$(shell_quote "$PC_PROBABILITY_MIN_TOTAL_PATHS")
PC_ENABLE_LIVE_PRIOR_FRAGMENTS=$(shell_quote "$PC_ENABLE_LIVE_PRIOR_FRAGMENTS")
PC_GPU_WORKER_MEM_LIMIT=$(shell_quote "$PC_GPU_WORKER_MEM_LIMIT")
PC_API_PORT=$(shell_quote "$PC_API_PORT")
PC_DEPLOY_MODE=$(shell_quote "$PC_DEPLOY_MODE")
PC_REMOTE_BUILD_SAVE_TARS=$(shell_quote "$PC_REMOTE_BUILD_SAVE_TARS")
TARGET_PLATFORM=$(shell_quote "$TARGET_PLATFORM")
COLLECTOR_IMAGE=$(shell_quote "$COLLECTOR_IMAGE")
NORMALIZER_IMAGE=$(shell_quote "$NORMALIZER_IMAGE")
CUDA_PROBABILITY_IMAGE=$(shell_quote "$CUDA_PROBABILITY_IMAGE")
COLLECTOR_TAR=$(shell_quote "$PC_DIST_DIR/$(basename "$COLLECTOR_TAR")")
NORMALIZER_TAR=$(shell_quote "$PC_DIST_DIR/$(basename "$NORMALIZER_TAR")")
CUDA_PROBABILITY_TAR=$(shell_quote "$PC_DIST_DIR/$(basename "$CUDA_PROBABILITY_TAR")")
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

mkdir -p "\$PC_DATA_DIR/raw" "\$PC_DATA_DIR/db" "\$PC_DATA_DIR/live" "\$PC_DATA_DIR/live/bug-reports" "\$PC_DATA_DIR/logs" "\$PC_DIST_DIR" "\$PC_BIN_DIR"
touch "\$PC_DATA_DIR/raw/.polymarket_archive_root"

if ! git ls-remote "\$PC_GIT_REMOTE" HEAD >/dev/null 2>&1; then
  echo "THEPC WSL cannot read \$PC_GIT_REMOTE over SSH." >&2
  mkdir -p /home/ender/.ssh
  chmod 700 /home/ender/.ssh
  if [ ! -f /home/ender/.ssh/id_ed25519.pub ]; then
    ssh-keygen -t ed25519 -N "" -C "thepc-polymarket@github" -f /home/ender/.ssh/id_ed25519
  fi
  echo "Add this key to GitHub, then rerun deploy:" >&2
  cat /home/ender/.ssh/id_ed25519.pub >&2
  exit 1
fi

if [ ! -d "\$PC_REPO/.git" ]; then
  git clone "\$PC_GIT_REMOTE" "\$PC_REPO"
fi

cd "\$PC_REPO"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "THEPC repo is dirty; refusing deploy" >&2
  git status --porcelain >&2
  exit 1
fi

git remote set-url origin "\$PC_GIT_REMOTE" 2>/dev/null || git remote add origin "\$PC_GIT_REMOTE"
git fetch --quiet --prune origin "\$PC_BRANCH"
git checkout -B "\$PC_BRANCH" "\$FULL_SHA"

if [ ! -f deploy/collector/.env ]; then
  cp deploy/collector/.env.example deploy/collector/.env
fi

set_env POLYMARKET_UID "\$(id -u)" deploy/collector/.env
set_env POLYMARKET_GID "\$(id -g)" deploy/collector/.env
set_env POLYMARKET_DATA_DIR "\$PC_DATA_DIR" deploy/collector/.env
set_env POLYMARKET_NORMALIZER_INTERVAL_SECONDS "\$PC_NORMALIZER_INTERVAL_SECONDS" deploy/collector/.env
set_env POLYMARKET_REST_BACKUP_INTERVAL_MS "\$PC_REST_BACKUP_INTERVAL_MS" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT "\$PC_PROBABILITY_CPU_TARGET_PERCENT" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT "\$PC_PROBABILITY_CPU_SOFT_MAX_PERCENT" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MAX_CYCLE_RUNTIME_MS "\$PC_PROBABILITY_MAX_CYCLE_RUNTIME_MS" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS "\$PC_PROBABILITY_MAX_TOTAL_PATHS" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS "\$PC_PROBABILITY_MIN_TOTAL_PATHS" deploy/collector/.env
set_env POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS "\$PC_ENABLE_LIVE_PRIOR_FRAGMENTS" deploy/collector/.env
set_env POLYMARKET_GPU_WORKER_MEM_LIMIT "\$PC_GPU_WORKER_MEM_LIMIT" deploy/collector/.env
set_env POLYMARKET_API_PORT "\$PC_API_PORT" deploy/collector/.env
set_env POLYMARKET_ENABLE_RUNTIME_PROBABILITIES 1 deploy/collector/.env
set_env POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE 0 deploy/collector/.env
set_env POLYMARKET_COLLECTOR_IMAGE "\$COLLECTOR_IMAGE" deploy/collector/.env
set_env POLYMARKET_NORMALIZER_IMAGE "\$NORMALIZER_IMAGE" deploy/collector/.env
set_env POLYMARKET_CUDA_PROBABILITY_IMAGE "\$CUDA_PROBABILITY_IMAGE" deploy/collector/.env

if [ "\$PC_DEPLOY_MODE" = "remote-build" ]; then
  DOCKER_CONFIG="\$PC_DATA_DIR/docker-config"
  mkdir -p "\$DOCKER_CONFIG"
  printf '%s\n' '{"auths":{}}' > "\$DOCKER_CONFIG/config.json"
  export DOCKER_CONFIG
  POLYMARKET_BUILD_SAVE_TARS="\$PC_REMOTE_BUILD_SAVE_TARS" \\
    TARGET_PLATFORM="\$TARGET_PLATFORM" \\
    POLYMARKET_DEPLOY_REF="\$FULL_SHA" \\
    ./scripts/build_images_pc.sh
  TUI_BIN="\$PC_REPO/dist/docker/polymarket-cockpit-tui-\$SHORT_SHA"
else
  docker load -i "\$COLLECTOR_TAR"
  docker load -i "\$NORMALIZER_TAR"
  docker load -i "\$CUDA_PROBABILITY_TAR"
fi
if [ ! -f "\$TUI_BIN" ]; then
  echo "missing TUI binary after deploy image prep: \$TUI_BIN" >&2
  exit 1
fi
install -m 755 "\$TUI_BIN" "\$PC_BIN_DIR/polymarket-cockpit-tui"
live_ready_check='import json,sys; p=json.load(sys.stdin); m=p.get("monitor") or {}; gates=p.get("gates") or {}; status=gates.get("status") or p.get("status") or {}; counts=status.get("counts") or {}; orderbooks=m.get("orderbooks") or []; sys.exit(0 if gates.get("ok") is True and (len(orderbooks) > 0 or int(counts.get("orderbooks") or 0) > 0) else 1)'

{
  printf '%s\n' '#!/usr/bin/env bash'
  printf '%s\n' 'set -euo pipefail'
  printf 'cd %q\n' "\$PC_REPO"
  printf '%s\n' "echo 'Checking Polymarket runtime...'"
  printf 'if curl -fsS --max-time 2 http://127.0.0.1:%s/api/runtime/live?limit=8 2>/dev/null | python3 -c %q >/dev/null 2>&1; then\n' "\$PC_API_PORT" "\$live_ready_check"
  printf '%s\n' "  echo 'Runtime already live.'"
  printf '%s\n' 'else'
  printf '%s\n' "  echo 'Runtime not live; starting containers...'"
  printf '%s\n' '  docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml -f deploy/collector/docker-compose.thepc-gpu-api.yml stop collector normalizer outcome-refresh >/dev/null 2>&1 || true'
  printf '%s\n' '  docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml -f deploy/collector/docker-compose.thepc-gpu-api.yml up -d --no-recreate api gpu-probability-worker >/dev/null 2>&1 || true'
  printf '%s\n' 'fi'
  printf '%s\n' "echo 'Waiting for runtime API and live market rows...'"
  printf '%s\n' 'for _ in \$(seq 1 45); do'
  printf '  if curl -fsS --max-time 2 http://127.0.0.1:%s/api/runtime/live?limit=8 2>/dev/null | python3 -c %q >/dev/null 2>&1; then\n' "\$PC_API_PORT" "\$live_ready_check"
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

cat > "\$PC_BIN_DIR/open-polymarket-duckdb-ui.sh" <<'DUCKDB_UI_LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

PORT="\${POLYMARKET_DUCKDB_UI_PORT:-4213}"
while [ "\$#" -gt 0 ]; do
  case "\$1" in
    --port)
      PORT="\$2"
      shift 2
      ;;
    *)
      echo "unknown argument: \$1" >&2
      exit 2
      ;;
  esac
done

SPOON_ALIAS="\${POLYMARKET_SPOON_SSH_ALIAS:-spoon}"
REMOTE_SCRIPT="\${POLYMARKET_SPOON_DUCKDB_UI_SCRIPT:-/home/spoon/bin/open-polymarket-duckdb-ui.sh}"
# Default remote start: /home/spoon/bin/open-polymarket-duckdb-ui.sh --port 4213
# Default local tunnel: 4213:127.0.0.1:4213
META_URL="http://127.0.0.1:\${PORT}/api/meta"

is_spoon_viewer() {
  curl -fsS --max-time 2 "\$META_URL" 2>/dev/null \\
    | python3 -c 'import json,sys; meta=json.load(sys.stdin); sys.exit(0 if meta.get("source_host") == "spoon" else 1)' \\
      >/dev/null 2>&1
}

clear_stale_tunnel() {
  pkill -f "polymarket_duckdb_viewer.py.*--port \${PORT}" >/dev/null 2>&1 || true
  pkill -f "ssh .* -L \${PORT}:127.0.0.1:\${PORT} .*\${SPOON_ALIAS}" >/dev/null 2>&1 || true
  pkill -f "ssh .* -L \${PORT}:localhost:\${PORT} .*\${SPOON_ALIAS}" >/dev/null 2>&1 || true
  sleep 0.5
}

if ! ssh -n "\$SPOON_ALIAS" "test -x \$REMOTE_SCRIPT" >/dev/null 2>&1; then
  echo "Spoon DuckDB UI helper is missing or not executable: \$REMOTE_SCRIPT" >&2
  echo "From the Mac, run: ./scripts/install_spoon_duckdb_ui.sh" >&2
  exit 1
fi

ssh -n "\$SPOON_ALIAS" "\$REMOTE_SCRIPT --port \$PORT"
if ! is_spoon_viewer; then
  clear_stale_tunnel
  ssh -o ExitOnForwardFailure=yes -f -N -L "\${PORT}:127.0.0.1:\${PORT}" "\$SPOON_ALIAS"
fi
if ! is_spoon_viewer; then
  echo "Polymarket DuckDB UI tunnel did not verify through \$META_URL" >&2
  exit 1
fi
echo "Polymarket DuckDB UI ready at http://127.0.0.1:\${PORT}"
DUCKDB_UI_LAUNCHER
chmod 755 "\$PC_BIN_DIR/open-polymarket-duckdb-ui.sh"

cat > "\$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh" <<'DUCKDB_UI_WINDOW_LAUNCHER'
#!/usr/bin/env bash
set +e
__PC_BIN_DIR__/open-polymarket-duckdb-ui.sh
status=\$?
if [ "\$status" -ne 0 ]; then
  echo
  echo "Polymarket DuckDB UI exited with status \$status"
  read -r -p "Press Enter to close"
  exit "\$status"
fi
echo
echo "Open http://127.0.0.1:4213 in the Windows browser."
read -r -p "Press Enter to close"
DUCKDB_UI_WINDOW_LAUNCHER
sed -i "s|__PC_BIN_DIR__|\$PC_BIN_DIR|g" "\$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh"
chmod 755 "\$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh"

WINDOWS_USER_DIR="/mnt/c/Users/ender"
if [ -d "\$WINDOWS_USER_DIR" ]; then
  cat > "\$WINDOWS_USER_DIR/open-polymarket-tui.ps1" <<'PS_LAUNCHER'
\$ErrorActionPreference = 'Stop'
\$logPath = Join-Path \$env:USERPROFILE ("polymarket-tui-launch-{0}.log" -f \$PID)
\$fallbackLogPath = Join-Path \$env:USERPROFILE 'polymarket-tui-launch-error.log'
\$arguments = @('-w', 'new', 'new-tab', '--title', 'Polymarket TUI', 'wsl.exe', '-d', '__PC_WSL_DISTRO__', '--', '__PC_BIN_DIR__/open-polymarket-tui-window.sh')

try {
  Start-Transcript -Path \$logPath -Append -ErrorAction SilentlyContinue | Out-Null
} catch {
}

try {
  Start-Process -FilePath 'wt.exe' -ArgumentList \$arguments -WindowStyle Normal
} catch {
  Add-Content -Path \$fallbackLogPath -Value ("{0} Failed to launch Windows Terminal for Polymarket TUI: {1}" -f (Get-Date -Format o), \$_)
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
start "Polymarket TUI" wsl.exe -d \$PC_WSL_DISTRO -- \$PC_BIN_DIR/open-polymarket-tui-window.sh
CMD_LAUNCHER
  cat > "\$WINDOWS_USER_DIR/open-polymarket-duckdb-ui.cmd" <<CMD_DUCKDB_UI_LAUNCHER
@echo off
start "Polymarket DuckDB UI" wsl.exe -d \$PC_WSL_DISTRO -- \$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh
timeout /t 3 >nul
start "" "http://127.0.0.1:4213"
CMD_DUCKDB_UI_LAUNCHER
  POWERSHELL_SCRIPT="\$WINDOWS_USER_DIR/AppData/Local/Temp/polymarket-tui-shortcut.ps1"
  cat > "\$POWERSHELL_SCRIPT" <<'PS1'
\$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Polymarket TUI.lnk'
\$launcherPath = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'open-polymarket-tui.cmd'
\$shell = New-Object -ComObject WScript.Shell
\$shortcut = \$shell.CreateShortcut(\$shortcutPath)
\$shortcut.TargetPath = \$launcherPath
\$shortcut.Arguments = ''
\$shortcut.WorkingDirectory = [Environment]::GetFolderPath('UserProfile')
\$shortcut.IconLocation = 'C:\WINDOWS\System32\shell32.dll,13'
\$shortcut.WindowStyle = 1
\$shortcut.Save()

\$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Polymarket DuckDB UI.lnk'
\$launcherPath = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'open-polymarket-duckdb-ui.cmd'
\$shortcut = \$shell.CreateShortcut(\$shortcutPath)
\$shortcut.TargetPath = \$launcherPath
\$shortcut.Arguments = ''
\$shortcut.WorkingDirectory = [Environment]::GetFolderPath('UserProfile')
\$shortcut.IconLocation = 'C:\WINDOWS\System32\shell32.dll,220'
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
export POLYMARKET_CUDA_PROBABILITY_IMAGE="\$CUDA_PROBABILITY_IMAGE"
export POLYMARKET_DATA_DIR="\$PC_DATA_DIR"
export POLYMARKET_NORMALIZER_INTERVAL_SECONDS="\$PC_NORMALIZER_INTERVAL_SECONDS"
export POLYMARKET_REST_BACKUP_INTERVAL_MS="\$PC_REST_BACKUP_INTERVAL_MS"
export DEPLOY_FORCE=1
export POLYMARKET_DEPLOY_STARTED_EPOCH="\$(date +%s)"

case "\$PC_DEPLOY_ROLE" in
  thepc-gpu-api)
    ./scripts/install_thepc_spoon_artifact_sync.sh
    docker compose --env-file deploy/collector/.env \\
      -f deploy/collector/docker-compose.yml \\
      -f deploy/collector/docker-compose.thepc-gpu-api.yml \\
      stop collector normalizer outcome-refresh >/dev/null 2>&1 || true
    docker compose --env-file deploy/collector/.env \\
      -f deploy/collector/docker-compose.yml \\
      -f deploy/collector/docker-compose.thepc-gpu-api.yml \\
      up -d --no-build api gpu-probability-worker
    ;;
  full)
    export POLYMARKET_DEPLOY_ROLE=full
    ./scripts/deploy.sh
    collector_status_ok=0
    for attempt in \$(seq 1 45); do
      if python3 scripts/check_collector_status.py \\
        --status-path "\$PC_DATA_DIR/live/status.json" \\
        --max-status-age-seconds 30 \\
        --max-price-age-ms 30000 \\
        --max-orderbook-age-ms 30000 \\
        --max-websocket-event-age-ms 30000 \\
        --raw-root "\$PC_DATA_DIR/raw" \\
        --max-raw-event-age-ms 30000 \\
        --normalized-health-path "\$PC_DATA_DIR/live/normalized_health.json" \\
        --max-normalized-health-age-ms 30000 \\
        --expected-prewarm-windows 2; then
        collector_status_ok=1
        break
      fi
      sleep 1
    done
    if [ "\$collector_status_ok" -ne 1 ]; then
      echo "collector status did not become ready after deploy" >&2
      exit 1
    fi
    ;;
  *)
    echo "unsupported PC_DEPLOY_ROLE=\$PC_DEPLOY_ROLE" >&2
    exit 2
    ;;
esac

POLYMARKET_API_PORT="\$PC_API_PORT" python3 - <<'PY'
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

base = f"http://127.0.0.1:{os.environ['POLYMARKET_API_PORT']}"
try:
    deploy_started_at = float(os.environ.get("POLYMARKET_DEPLOY_STARTED_EPOCH") or time.time())
except ValueError:
    deploy_started_at = time.time()
try:
    probability_smoke_attempts = int(
        os.environ.get("POLYMARKET_PROBABILITY_SMOKE_ATTEMPTS") or "90"
    )
except ValueError:
    probability_smoke_attempts = 90
required_generators = {
    "empirical_conditional",
    "block_bootstrap",
    "filtered_historical",
    "stress_overlay",
}
required_contracts = {
    ("BTC", "UP"),
    ("BTC", "DOWN"),
    ("ETH", "UP"),
    ("ETH", "DOWN"),
}


def get_json(path: str) -> dict[str, object]:
    with urllib.request.urlopen(base + path, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_json(path: str, attempts: int = 30) -> dict[str, object]:
    last: dict[str, object] = {}
    for _ in range(attempts):
        try:
            return get_json(path)
        except Exception as exc:
            last = {"error": repr(exc)}
            time.sleep(1)
    raise SystemExit(f"{path} smoke unavailable: {last}")


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def probability_candidate_rows(payload: dict[str, object]) -> list[object]:
    candidates: list[object] = []
    for key in ("rows", "last_good_rows"):
        rows = payload.get(key)
        if isinstance(rows, list):
            candidates.extend(rows)
    return candidates


def row_is_recent(row: dict[str, object], now: datetime) -> bool:
    generated_at = parse_ts(row.get("generated_at"))
    if generated_at is None or generated_at.timestamp() < deploy_started_at:
        return False
    valid_until = parse_ts(row.get("valid_until"))
    if valid_until is not None and valid_until > now:
        return True
    return (now - generated_at).total_seconds() <= 90


def row_contract(row: dict[str, object]) -> tuple[str, str] | None:
    asset = row.get("asset")
    side = row.get("side")
    if not isinstance(asset, str) or not isinstance(side, str):
        return None
    return (asset.upper(), side.upper())


def contract_pairs(rows: object) -> set[tuple[str, str]]:
    if not isinstance(rows, list):
        return set()
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        pair = row_contract(row)
        if pair is not None:
            pairs.add(pair)
    return pairs


def row_has_required_generators(row: dict[str, object]) -> bool:
    if row.get("prior_fragment_enabled") is False:
        generator_count = row.get("generator_count")
        if isinstance(generator_count, int) and generator_count >= len(required_generators):
            return True
        effective_weights = row.get("effective_weights")
        if isinstance(effective_weights, dict) and required_generators.issubset(
            {str(generator) for generator in effective_weights}
        ):
            return True
        generator_runs = row.get("generator_runs")
        if isinstance(generator_runs, list):
            run_generators = {
                str(run.get("generator_id"))
                for run in generator_runs
                if isinstance(run, dict) and run.get("generator_id") is not None
            }
            return required_generators.issubset(run_generators)
        return False
    generators = row.get("prior_fragment_generators")
    if not isinstance(generators, list):
        return False
    return required_generators.issubset({str(generator) for generator in generators})


def row_has_simulation_preview(row: dict[str, object]) -> bool:
    preview = row.get("simulation_preview")
    if not isinstance(preview, dict):
        return False
    sampled_paths = preview.get("sampled_paths")
    return isinstance(sampled_paths, list) and bool(sampled_paths)


def row_is_recent_mc(row: dict[str, object], now: datetime) -> bool:
    return (
        row.get("model_version") == "ensemble-v1"
        and str(row.get("probability_kind") or "MC").upper() == "MC"
        and row_is_recent(row, now)
        and row_has_required_generators(row)
        and row_has_simulation_preview(row)
    )


def offload_has_block_reasons(offload: dict[str, object]) -> bool:
    reason_codes = offload.get("reason_codes")
    if isinstance(reason_codes, list) and any(str(reason) for reason in reason_codes):
        return True
    for key in ("blocked_inputs",):
        blocked_inputs = offload.get(key)
        if isinstance(blocked_inputs, list) and any(
            isinstance(item, dict) and item.get("reason_codes")
            for item in blocked_inputs
        ):
            return True
    diagnostics = offload.get("input_diagnostics")
    if isinstance(diagnostics, dict):
        blocked_inputs = diagnostics.get("blocked_inputs")
        if isinstance(blocked_inputs, list) and any(
            isinstance(item, dict) and item.get("reason_codes")
            for item in blocked_inputs
        ):
            return True
    return False


def live_smoke_passed(live: dict[str, object]) -> bool:
    raw_monitor = live.get("monitor")
    monitor = raw_monitor if isinstance(raw_monitor, dict) else {}
    raw_gates = live.get("gates")
    gates = raw_gates if isinstance(raw_gates, dict) else {}
    raw_status = gates.get("status")
    status = raw_status if isinstance(raw_status, dict) else {}
    raw_counts = status.get("counts")
    counts = raw_counts if isinstance(raw_counts, dict) else {}
    orderbooks = monitor.get("orderbooks")
    has_orderbooks = isinstance(orderbooks, list) and bool(orderbooks)
    try:
        orderbook_count = int(counts.get("orderbooks") or 0)
    except (TypeError, ValueError):
        orderbook_count = 0
    return gates.get("ok") is True and (has_orderbooks or orderbook_count > 0)


def probability_smoke_passed(probabilities: dict[str, object], now: datetime) -> bool:
    if probabilities.get("ok") is not True:
        return False
    state = probabilities.get("state")
    if state not in {"OK", "NOWCAST", "OFFLOAD_BLOCKED"}:
        return False
    probability_rows = probability_candidate_rows(probabilities)
    recent_rows = [
        row for row in probability_rows if isinstance(row, dict) and row_is_recent(row, now)
    ]
    if not recent_rows:
        return False
    recent_mc_rows = [
        row for row in recent_rows if isinstance(row, dict) and row_is_recent_mc(row, now)
    ]
    raw_offload = probabilities.get("offload")
    if not isinstance(raw_offload, dict):
        return state == "OK" and bool(recent_mc_rows)
    offload = raw_offload
    offload_allowed = bool(offload.get("offload_allowed"))
    if (
        state in {"NOWCAST", "OFFLOAD_BLOCKED"}
        and not offload_has_block_reasons(offload)
        and not (
            state == "NOWCAST"
            and offload_allowed
            and required_contracts.issubset(contract_pairs(recent_mc_rows))
        )
    ):
        return False
    mc_eligible_input_count = int(offload.get("mc_eligible_input_count") or 0)
    if mc_eligible_input_count > 0 and not offload_allowed:
        return (
            probabilities.get("state") in {"NOWCAST", "OFFLOAD_BLOCKED"}
            and offload_has_block_reasons(offload)
        )
    if mc_eligible_input_count > 0 and offload_allowed:
        if not recent_mc_rows:
            return False
        blocked_required_contracts = contract_pairs(offload.get("blocked_inputs")) & required_contracts
        eligible_required_contracts = required_contracts - blocked_required_contracts
        recent_contracts = contract_pairs(recent_rows)
        recent_mc_contracts = contract_pairs(recent_mc_rows)
        if (
            mc_eligible_input_count >= len(required_contracts)
            and eligible_required_contracts == required_contracts
            and required_contracts.issubset(recent_contracts)
        ):
            required_recent_mc_contracts = required_contracts & recent_mc_contracts
            return required_contracts.issubset(required_recent_mc_contracts)
        return bool(recent_mc_rows)
    return (
        state in {"NOWCAST", "OFFLOAD_BLOCKED"}
        and offload_has_block_reasons(offload)
    )


health = wait_json("/health")
if health.get("status") != "ok":
    raise SystemExit(f"health smoke failed: {health}")

live = {}
for _ in range(30):
    try:
        live = get_json("/api/runtime/live?limit=8")
    except Exception as exc:
        live = {"error": repr(exc)}
        time.sleep(1)
        continue
    if live_smoke_passed(live):
        break
    time.sleep(1)
else:
    raise SystemExit(f"runtime live smoke failed: {live}")

probabilities = {}
for _ in range(probability_smoke_attempts):
    try:
        probabilities = get_json("/api/runtime/probabilities?limit=8")
    except Exception as exc:
        probabilities = {"error": repr(exc)}
        time.sleep(1)
        continue
    now = datetime.now(timezone.utc)
    if probability_smoke_passed(probabilities, now):
        break
    time.sleep(1)
else:
    raise SystemExit(f"runtime probabilities smoke failed: {probabilities}")

outcomes = wait_json("/api/runtime/outcomes?limit=8")
if (
    not isinstance(outcomes.get("rows"), list)
    or outcomes.get("ok") is not True
    or outcomes.get("state") == "LOCKED"
):
    raise SystemExit(f"runtime outcomes smoke failed: {outcomes}")

with urllib.request.urlopen(
    base + "/api/runtime/live/stream?limit=8&interval_ms=250&max_events=1",
    timeout=15,
) as response:
    body = response.read().decode("utf-8")
if "event: live" not in body or "data: " not in body:
    raise SystemExit("runtime SSE smoke failed")
PY

if [ "\$PC_DEPLOY_ROLE" = "full" ]; then
  docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml ps
else
  docker compose --env-file deploy/collector/.env \\
    -f deploy/collector/docker-compose.yml \\
    -f deploy/collector/docker-compose.thepc-gpu-api.yml ps
fi
printf 'THEPC TUI installed %s\\n' "\$PC_BIN_DIR/polymarket-cockpit-tui"
printf 'THEPC TUI launcher installed %s\\n' "\$PC_BIN_DIR/open-polymarket-tui.sh"
printf 'THEPC deployed %s\\n' "\$FULL_SHA"
EOF
