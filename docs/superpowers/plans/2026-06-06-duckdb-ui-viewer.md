# DuckDB UI Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open Polymarket's THEPC DuckDB data through DuckDB's official browser UI and provide a Mac launcher that tunnels to it.

**Architecture:** Use the official DuckDB UI server on port `4213`, backed by a writable UI catalog database that attaches a disposable read-only snapshot of the live Polymarket DuckDB. Snapshot creation briefly stops only `normalizer` and `outcome-refresh`, copies the database using DuckDB `ATTACH ... (READ_ONLY)` plus `COPY FROM DATABASE`, then restarts those services so the live writer lock is not exposed to the UI.

**Tech Stack:** Bash/zsh, THEPC Windows SSH, WSL Ubuntu, DuckDB CLI/UI extension, Docker Compose, pytest script-content tests.

---

## File Structure

- Create `scripts/open_duckdb_ui_mac.sh`
  - Mac entrypoint. Ensures the THEPC DuckDB UI helper is running, starts an SSH tunnel from Mac `localhost:4213` to THEPC `localhost:4213`, then opens the local browser.
- Modify `scripts/deploy_pc.sh`
  - Installs THEPC WSL helper scripts:
    - `/home/ender/bin/open-polymarket-duckdb-ui.sh`
    - `/home/ender/bin/open-polymarket-duckdb-ui-window.sh`
  - Installs Windows launcher/shortcut:
    - `C:\Users\ender\open-polymarket-duckdb-ui.cmd`
    - Desktop `Polymarket DuckDB UI.lnk`
- Create `tests/scripts/test_duckdb_ui_launcher.py`
  - Locks the launcher behavior so it stays snapshot-backed, uses the official DuckDB UI, and opens through the Mac tunnel.
- Modify `tests/scripts/test_deploy_script.py`
  - Adds deploy-script text checks for the THEPC helper and Windows shortcut.
- Modify `docs/SPOON_DEPLOYMENT.md`
  - Adds a short runbook for THEPC local DuckDB UI and Mac tunneling.

## Task 1: Add Tests For The DuckDB UI Launchers

**Files:**
- Create: `tests/scripts/test_duckdb_ui_launcher.py`
- Modify: `tests/scripts/test_deploy_script.py`

- [ ] **Step 1: Write the failing Mac launcher test**

Create `tests/scripts/test_duckdb_ui_launcher.py`:

```python
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_mac_duckdb_ui_launcher_tunnels_thepc_duckdb_ui() -> None:
    script = (ROOT / "scripts" / "open_duckdb_ui_mac.sh").read_text(
        encoding="utf-8"
    )

    assert "PC_HOST=\"${PC_HOST:-ender@100.72.104.49}\"" in script
    assert "PC_WSL_DISTRO=\"${PC_WSL_DISTRO:-Ubuntu}\"" in script
    assert "POLYMARKET_DUCKDB_UI_PORT:-4213" in script
    assert "/home/ender/bin/open-polymarket-duckdb-ui.sh" in script
    assert "ssh -f -N -L" in script
    assert "http://127.0.0.1:${LOCAL_PORT}" in script
    assert "POLYMARKET_DUCKDB_UI_TEST_LAUNCH" in script
```

- [ ] **Step 2: Write the failing THEPC deploy helper test**

Append this test to `tests/scripts/test_deploy_script.py`:

```python
def test_pc_deploy_installs_duckdb_ui_snapshot_launcher() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert "open-polymarket-duckdb-ui.sh" in script
    assert "open-polymarket-duckdb-ui-window.sh" in script
    assert "open-polymarket-duckdb-ui.cmd" in script
    assert "Polymarket DuckDB UI.lnk" in script
    assert "https://install.duckdb.org" in script
    assert "CALL start_ui_server()" in script
    assert "SET ui_local_port" in script
    assert "ATTACH '\\$SOURCE_DB' AS source_db (READ_ONLY)" in script
    assert "COPY FROM DATABASE source_db TO snapshot" in script
    assert "ATTACH '\\$SNAPSHOT_DB' AS polymarket (READ_ONLY)" in script
    assert "stop normalizer outcome-refresh" in script
    assert "up -d --no-deps normalizer outcome-refresh" in script
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
uv run pytest -q \
  tests/scripts/test_duckdb_ui_launcher.py \
  tests/scripts/test_deploy_script.py::test_pc_deploy_installs_duckdb_ui_snapshot_launcher
```

Expected:

```text
FAILED tests/scripts/test_duckdb_ui_launcher.py::test_mac_duckdb_ui_launcher_tunnels_thepc_duckdb_ui
FAILED tests/scripts/test_deploy_script.py::test_pc_deploy_installs_duckdb_ui_snapshot_launcher
```

The first failure should be because `scripts/open_duckdb_ui_mac.sh` does not exist. The second should be because `deploy_pc.sh` does not yet install a DuckDB UI helper.

## Task 2: Add The Mac DuckDB UI Opener

**Files:**
- Create: `scripts/open_duckdb_ui_mac.sh`

- [ ] **Step 1: Add the Mac launcher**

Create `scripts/open_duckdb_ui_mac.sh`:

```zsh
#!/bin/zsh
emulate -L zsh
set -uo pipefail

PC_HOST="${PC_HOST:-ender@100.72.104.49}"
PC_WSL_DISTRO="${PC_WSL_DISTRO:-Ubuntu}"
REMOTE_SCRIPT="${POLYMARKET_DUCKDB_UI_REMOTE_SCRIPT:-/home/ender/bin/open-polymarket-duckdb-ui.sh}"
REMOTE_PORT="${POLYMARKET_DUCKDB_UI_PORT:-4213}"
LOCAL_PORT="${POLYMARKET_DUCKDB_UI_LOCAL_PORT:-$REMOTE_PORT}"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/polymarket-duckdb-ui-mac-launch.log"
URL="http://127.0.0.1:${LOCAL_PORT}"

mkdir -p "$LOG_DIR"
{
  echo "launch $(date -Iseconds)"
  echo "pc_host=$PC_HOST"
  echo "remote_port=$REMOTE_PORT"
  echo "local_port=$LOCAL_PORT"
} >> "$LOG_FILE"

echo "Starting DuckDB UI on THEPC..."
if ! ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- $REMOTE_SCRIPT --port $REMOTE_PORT" >> "$LOG_FILE" 2>&1; then
  echo
  echo "Could not start DuckDB UI on THEPC."
  echo "Run ./scripts/deploy_pc.sh once so the THEPC DuckDB UI helper is installed."
  echo "Log: $LOG_FILE"
  echo
  read -r "?Press Return to close."
  exit 1
fi

if [[ "${POLYMARKET_DUCKDB_UI_TEST_LAUNCH:-0}" == "1" ]]; then
  echo "Mac DuckDB UI launcher ready."
  exit 0
fi

if ! curl -fsS --max-time 2 "$URL" >/dev/null 2>> "$LOG_FILE"; then
  echo "Opening SSH tunnel to THEPC DuckDB UI..."
  ssh -f -N -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" "$PC_HOST" >> "$LOG_FILE" 2>&1 || {
    echo
    echo "Could not open SSH tunnel to THEPC DuckDB UI."
    echo "Log: $LOG_FILE"
    echo
    read -r "?Press Return to close."
    exit 1
  }
fi

for _ in {1..20}; do
  if curl -fsS --max-time 2 "$URL" >/dev/null 2>> "$LOG_FILE"; then
    echo "Opening $URL"
    open "$URL"
    exit 0
  fi
  sleep 0.5
done

echo
echo "DuckDB UI tunnel opened, but the UI did not answer at $URL."
echo "Log: $LOG_FILE"
echo
read -r "?Press Return to close."
exit 1
```

- [ ] **Step 2: Make it executable**

Run:

```bash
chmod 755 scripts/open_duckdb_ui_mac.sh
```

- [ ] **Step 3: Run focused test**

Run:

```bash
POLYMARKET_DUCKDB_UI_TEST_LAUNCH=1 ./scripts/open_duckdb_ui_mac.sh
```

Expected after deploy helper exists:

```text
Starting DuckDB UI on THEPC...
Mac DuckDB UI launcher ready.
```

Before the deploy helper exists, this can fail with the expected message that `./scripts/deploy_pc.sh` needs to run once.

## Task 3: Install The Official DuckDB UI Helper On THEPC

**Files:**
- Modify: `scripts/deploy_pc.sh`

- [ ] **Step 1: Add helper installation after the TUI launcher block**

Insert this block in `scripts/deploy_pc.sh` inside the remote WSL heredoc, after the existing `open-polymarket-tui-window.sh` setup and before the Windows shortcut block:

```bash
cat > "$PC_BIN_DIR/open-polymarket-duckdb-ui.sh" <<'DUCKDB_UI_LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

PORT="${POLYMARKET_DUCKDB_UI_PORT:-4213}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

PC_REPO="${PC_REPO:-/home/ender/polymarket}"
DATA_DIR="${POLYMARKET_DATA_DIR:-/home/ender/polymarket-data}"
SOURCE_DB="${POLYMARKET_DUCKDB_SOURCE_DB:-$DATA_DIR/db/polymarket.duckdb}"
SNAPSHOT_DIR="${POLYMARKET_DUCKDB_UI_SNAPSHOT_DIR:-$DATA_DIR/duckdb-ui}"
SNAPSHOT_DB="$SNAPSHOT_DIR/current-polymarket.duckdb"
SNAPSHOT_TMP="$SNAPSHOT_DIR/current-polymarket.tmp.duckdb"
UI_CATALOG="$SNAPSHOT_DIR/ui-catalog.duckdb"
LOG_DIR="$DATA_DIR/logs"
LOG_FILE="$LOG_DIR/duckdb-ui.log"
DUCKDB_BIN="${DUCKDB_BIN:-$HOME/.duckdb/cli/latest/duckdb}"

mkdir -p "$SNAPSHOT_DIR" "$LOG_DIR" "$HOME/bin"

if ! command -v duckdb >/dev/null 2>&1 && [ ! -x "$DUCKDB_BIN" ]; then
  curl -fsSL https://install.duckdb.org | sh >> "$LOG_FILE" 2>&1
fi

if command -v duckdb >/dev/null 2>&1; then
  DUCKDB_BIN="$(command -v duckdb)"
elif [ -x "$DUCKDB_BIN" ]; then
  DUCKDB_BIN="$DUCKDB_BIN"
else
  echo "DuckDB CLI is not installed and could not be found" >&2
  exit 1
fi

if [ ! -f "$SOURCE_DB" ]; then
  echo "source DuckDB missing: $SOURCE_DB" >&2
  exit 1
fi

quote_sql_string() {
  printf "%s" "$1" | sed "s/'/''/g; s/^/'/; s/$/'/"
}

cd "$PC_REPO"
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml stop normalizer outcome-refresh >/dev/null
rm -f "$SNAPSHOT_TMP" "$SNAPSHOT_TMP.wal"
"$DUCKDB_BIN" "$SNAPSHOT_TMP" -batch -c "ATTACH $(quote_sql_string "$SOURCE_DB") AS source_db (READ_ONLY); COPY FROM DATABASE source_db TO snapshot;"
mv "$SNAPSHOT_TMP" "$SNAPSHOT_DB"
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d --no-deps normalizer outcome-refresh >/dev/null

pkill -f "duckdb.*$UI_CATALOG" >/dev/null 2>&1 || true
nohup "$DUCKDB_BIN" "$UI_CATALOG" \
  -cmd "SET ui_local_port=${PORT}; ATTACH $(quote_sql_string "$SNAPSHOT_DB") AS polymarket (READ_ONLY); USE polymarket; CALL start_ui_server();" \
  >/dev/null 2>> "$LOG_FILE" &

for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}" >/dev/null 2>> "$LOG_FILE"; then
    echo "DuckDB UI ready at http://127.0.0.1:${PORT}"
    echo "Snapshot: $SNAPSHOT_DB"
    exit 0
  fi
  sleep 0.5
done

echo "DuckDB UI did not answer on http://127.0.0.1:${PORT}" >&2
exit 1
DUCKDB_UI_LAUNCHER
chmod 755 "$PC_BIN_DIR/open-polymarket-duckdb-ui.sh"

cat > "$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh" <<'DUCKDB_UI_WINDOW_LAUNCHER'
#!/usr/bin/env bash
set +e
__PC_BIN_DIR__/open-polymarket-duckdb-ui.sh
status=$?
if [ "$status" -ne 0 ]; then
  echo
  echo "Polymarket DuckDB UI exited with status $status"
  read -r -p "Press Enter to close"
  exit "$status"
fi
echo
echo "Open http://127.0.0.1:4213 in the Windows browser."
read -r -p "Press Enter to close"
DUCKDB_UI_WINDOW_LAUNCHER
sed -i "s|__PC_BIN_DIR__|$PC_BIN_DIR|g" "$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh"
chmod 755 "$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh"
```

- [ ] **Step 2: Restart services if snapshot creation fails**

Wrap the snapshot section in `open-polymarket-duckdb-ui.sh` with a trap so `normalizer` and `outcome-refresh` restart even if DuckDB copy fails:

```bash
restart_refresh_services() {
  (
    cd "$PC_REPO" &&
      docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d --no-deps normalizer outcome-refresh >/dev/null
  ) || true
}

cd "$PC_REPO"
trap restart_refresh_services EXIT
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml stop normalizer outcome-refresh >/dev/null
rm -f "$SNAPSHOT_TMP" "$SNAPSHOT_TMP.wal"
"$DUCKDB_BIN" "$SNAPSHOT_TMP" -batch -c "ATTACH $(quote_sql_string "$SOURCE_DB") AS source_db (READ_ONLY); COPY FROM DATABASE source_db TO snapshot;"
mv "$SNAPSHOT_TMP" "$SNAPSHOT_DB"
restart_refresh_services
trap - EXIT
```

- [ ] **Step 3: Add Windows CMD launcher and shortcut**

Inside the existing `if [ -d "$WINDOWS_USER_DIR" ]; then` block in `scripts/deploy_pc.sh`, after the TUI `.cmd` launcher creation, add:

```bash
  cat > "$WINDOWS_USER_DIR/open-polymarket-duckdb-ui.cmd" <<CMD_DUCKDB_UI_LAUNCHER
@echo off
start "Polymarket DuckDB UI" wsl.exe -d $PC_WSL_DISTRO -- $PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh
timeout /t 3 >nul
start "" "http://127.0.0.1:4213"
CMD_DUCKDB_UI_LAUNCHER
```

Then add this PowerShell shortcut block after the existing TUI shortcut script:

```powershell
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Polymarket DuckDB UI.lnk'
$launcherPath = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'open-polymarket-duckdb-ui.cmd'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcherPath
$shortcut.Arguments = ''
$shortcut.WorkingDirectory = [Environment]::GetFolderPath('UserProfile')
$shortcut.IconLocation = 'C:\WINDOWS\System32\cmd.exe,0'
$shortcut.WindowStyle = 1
$shortcut.Save()
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest -q \
  tests/scripts/test_duckdb_ui_launcher.py \
  tests/scripts/test_deploy_script.py::test_pc_deploy_installs_duckdb_ui_snapshot_launcher
```

Expected:

```text
2 passed
```

## Task 4: Add Runbook Notes

**Files:**
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Add the runbook section**

Add this section near the THEPC deployment notes:

```markdown
## THEPC DuckDB UI

THEPC can expose the live Polymarket DuckDB through DuckDB's official browser UI at `http://127.0.0.1:4213`.

The launcher does not open the live DuckDB file directly. It briefly pauses `normalizer` and `outcome-refresh`, creates `/home/ender/polymarket-data/duckdb-ui/current-polymarket.duckdb` by attaching the source database read-only and running `COPY FROM DATABASE`, restarts the paused services, then starts the UI with a writable catalog that attaches the snapshot as read-only.

On THEPC, open the desktop shortcut:

```text
Polymarket DuckDB UI
```

On the Mac, run:

```bash
./scripts/open_duckdb_ui_mac.sh
```

The Mac script starts the THEPC helper, opens an SSH tunnel from Mac `localhost:4213` to THEPC `localhost:4213`, and opens `http://127.0.0.1:4213`.
```

- [ ] **Step 2: Run docs/script syntax checks**

Run:

```bash
bash -n scripts/deploy_pc.sh
zsh -n scripts/open_duckdb_ui_mac.sh
uv run pytest -q tests/docs/test_active_runtime_docs.py
```

Expected:

```text
no shell syntax errors
tests/docs/test_active_runtime_docs.py passes
```

## Task 5: Deploy And Verify On THEPC

**Files:**
- Runtime-only deployment on THEPC

- [ ] **Step 1: Run preflight before installing DuckDB CLI on THEPC**

Run the local preflight checklist, then continue only if there are no broken API/agent env vars affecting this shell:

```bash
env | grep -E "^(ANTHROPIC_|OLLAMA_|OPENAI_)" | sort
grep -nE "^(export|alias|source)" ~/.zshrc | grep -iE "(anthropic|ollama|codex|openai)" || true
command -v node && node --version
command -v python3 && python3 --version
command -v codex && codex --version 2>/dev/null || true
```

Expected:

```text
Env vars and shell rc have no stale values that would interfere with this install.
Node/Python/Codex versions print or Codex is absent without blocking this local-only DuckDB install.
```

- [ ] **Step 2: Commit and deploy**

Run:

```bash
git add \
  scripts/open_duckdb_ui_mac.sh \
  scripts/deploy_pc.sh \
  tests/scripts/test_duckdb_ui_launcher.py \
  tests/scripts/test_deploy_script.py \
  docs/SPOON_DEPLOYMENT.md \
  docs/superpowers/plans/2026-06-06-duckdb-ui-viewer.md
git commit -m "Add DuckDB UI viewer launchers"
./scripts/deploy_pc.sh
```

Expected:

```text
deploy OK <commit-sha>
THEPC TUI installed /home/ender/bin/polymarket-cockpit-tui
THEPC TUI launcher installed /home/ender/bin/open-polymarket-tui.sh
THEPC deployed <commit-sha>
```

- [ ] **Step 3: Start THEPC DuckDB UI helper**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- /home/ender/bin/open-polymarket-duckdb-ui.sh --port 4213"
```

Expected:

```text
DuckDB UI ready at http://127.0.0.1:4213
Snapshot: /home/ender/polymarket-data/duckdb-ui/current-polymarket.duckdb
```

- [ ] **Step 4: Verify runtime health survived the snapshot**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'cd /home/ender/polymarket && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml ps normalizer outcome-refresh api gpu-probability-worker'"
curl -fsS http://100.72.104.49:8000/health
```

Expected:

```text
normalizer, outcome-refresh, api, and gpu-probability-worker are running/healthy
{"status":"ok"}
```

- [ ] **Step 5: Verify Mac opener**

Run:

```bash
./scripts/open_duckdb_ui_mac.sh
```

Expected:

```text
Starting DuckDB UI on THEPC...
Opening http://127.0.0.1:4213
```

The Mac browser opens DuckDB UI. In the UI, the attached database should be named `polymarket`.

## Self-Review

- Spec coverage: The plan uses DuckDB's official UI, exposes THEPC localhost, provides a Mac opener, and protects the live writer by serving a snapshot instead of the locked live DuckDB file.
- Placeholder scan: No `TBD`, `TODO`, or "implement later" placeholders remain.
- Type/name consistency: Script names are consistent: `open_duckdb_ui_mac.sh`, `open-polymarket-duckdb-ui.sh`, `open-polymarket-duckdb-ui-window.sh`, and `open-polymarket-duckdb-ui.cmd`.
- Risk: Installing DuckDB's CLI can download the UI extension and the UI assets from DuckDB/MotherDuck infrastructure. This is the intended official DuckDB UI path; the live Polymarket data remains local unless MotherDuck sign-in is explicitly used in the UI.
