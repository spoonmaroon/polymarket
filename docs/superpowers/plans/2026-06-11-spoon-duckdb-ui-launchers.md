# Spoon DuckDB UI And Launcher Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DuckDB UI show fresh spoon-owned live data, and refresh the THEPC and Mac launchers for DuckDB UI and TUI.

**Architecture:** Spoon is the live collector/normalizer authority, so DuckDB UI must snapshot from `/home/spoon/polymarket-data/db/polymarket.duckdb`, not THEPC's stale local database. A spoon helper creates a fresh read-only snapshot on launch and serves the existing local Python table browser on `127.0.0.1:4213`; THEPC and Mac launchers tunnel/open that spoon-hosted UI. The TUI launchers continue to use THEPC's API, but the Windows and Mac shortcuts are regenerated from canonical scripts.

**Tech Stack:** Bash/zsh, Windows `.cmd`/PowerShell shortcuts, SSH/Tailscale, DuckDB CLI, Python `ThreadingHTTPServer`, pytest script-content tests.

---

## File Structure

- Create `scripts/install_spoon_duckdb_ui.sh`
  - Installs `/home/spoon/bin/open-polymarket-duckdb-ui.sh`.
  - Installs `/home/spoon/polymarket-data/duckdb-ui/polymarket_duckdb_viewer.py`.
  - Installs/uses DuckDB CLI on spoon.
  - Creates a fresh snapshot from spoon's live DuckDB on each launch.
  - Starts the viewer on spoon loopback port `4213`.
- Modify `scripts/open_duckdb_ui_mac.sh`
  - Starts the spoon helper over SSH.
  - Opens a Mac tunnel to spoon `127.0.0.1:4213`.
  - Stops referring to THEPC for DuckDB UI data.
- Modify `scripts/deploy_pc.sh`
  - Stops installing a THEPC-local DuckDB snapshot viewer.
  - Installs THEPC launchers that open/tunnel to spoon-hosted DuckDB UI.
  - Regenerates `Polymarket TUI.lnk` and `Polymarket DuckDB UI.lnk`.
- Modify `tests/scripts/test_duckdb_ui_launcher.py`
  - Locks the Mac launcher to spoon-hosted DuckDB UI.
- Modify `tests/scripts/test_deploy_script.py`
  - Locks THEPC shortcut behavior and prevents reintroducing THEPC-local DuckDB snapshots for the default `thepc-gpu-api` role.
- Modify `tests/scripts/test_mac_tui_launcher.py`
  - Locks the canonical Mac desktop launcher content if needed.
- Modify `docs/SPOON_DEPLOYMENT.md`
  - Documents spoon-owned DuckDB UI and launcher refresh commands.

## Task 1: Add Failing Launcher Contract Tests

**Files:**
- Modify: `tests/scripts/test_duckdb_ui_launcher.py`
- Modify: `tests/scripts/test_deploy_script.py`
- Modify: `tests/scripts/test_mac_tui_launcher.py`

- [ ] **Step 1: Update the Mac DuckDB UI launcher test**

Replace `test_mac_duckdb_ui_launcher_tunnels_thepc_duckdb_ui` in `tests/scripts/test_duckdb_ui_launcher.py` with:

```python
def test_mac_duckdb_ui_launcher_tunnels_spoon_duckdb_ui() -> None:
    script = (ROOT / "scripts" / "open_duckdb_ui_mac.sh").read_text(
        encoding="utf-8"
    )

    assert 'SPOON_HOST="${SPOON_HOST:-spoon@100.126.126.1}"' in script
    assert "POLYMARKET_DUCKDB_UI_PORT:-4213" in script
    assert "/home/spoon/bin/open-polymarket-duckdb-ui.sh" in script
    assert "ssh -f -N -L" in script
    assert "http://127.0.0.1:${LOCAL_PORT}" in script
    assert "THEPC" not in script
    assert "POLYMARKET_DUCKDB_UI_TEST_LAUNCH" in script
```

- [ ] **Step 2: Add a spoon helper installer test**

Append this test to `tests/scripts/test_duckdb_ui_launcher.py`:

```python
def test_spoon_duckdb_ui_installer_snapshots_spoon_live_db() -> None:
    script = (ROOT / "scripts" / "install_spoon_duckdb_ui.sh").read_text(
        encoding="utf-8"
    )

    assert 'SPOON_HOST="${SPOON_HOST:-spoon@100.126.126.1}"' in script
    assert "/home/spoon/polymarket-data/db/polymarket.duckdb" in script
    assert "/home/spoon/polymarket-data/duckdb-ui/current-polymarket.duckdb" in script
    assert "polymarket_duckdb_viewer.py" in script
    assert "ATTACH $(quote_sql_string \"$SOURCE_DB\") AS source_db (READ_ONLY)" in script
    assert "COPY FROM DATABASE source_db TO snapshot" in script
    assert "ThreadingHTTPServer" in script
    assert "Snapshot generated" in script
```

- [ ] **Step 3: Replace the THEPC DuckDB UI deploy test**

In `tests/scripts/test_deploy_script.py`, replace `test_pc_deploy_installs_duckdb_ui_snapshot_launcher` with:

```python
def test_pc_deploy_installs_spoon_duckdb_ui_launcher() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert "open-polymarket-duckdb-ui.cmd" in script
    assert "Polymarket DuckDB UI.lnk" in script
    assert "ssh -f -N -L 4213:127.0.0.1:4213 spoon" in script
    assert "/home/spoon/bin/open-polymarket-duckdb-ui.sh --port 4213" in script
    assert "start \"\" \"http://127.0.0.1:4213\"" in script
    assert 'IconLocation = "C:\\\\WINDOWS\\\\System32\\\\shell32.dll,220"' in script
```

- [ ] **Step 4: Add a regression test against THEPC-local DuckDB snapshots**

Append this test to `tests/scripts/test_deploy_script.py`:

```python
def test_pc_default_duckdb_ui_no_longer_snapshots_thepc_local_db() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    duckdb_block_start = script.index('cat > "\\$PC_BIN_DIR/open-polymarket-duckdb-ui.sh"')
    duckdb_block_end = script.index('cat > "\\$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh"')
    duckdb_block = script[duckdb_block_start:duckdb_block_end]

    assert 'SOURCE_DB="\\${POLYMARKET_DUCKDB_SOURCE_DB:-\\$DATA_DIR/db/polymarket.duckdb}"' not in duckdb_block
    assert "COPY FROM DATABASE source_db TO snapshot" not in duckdb_block
    assert "polymarket_duckdb_viewer.py" not in duckdb_block
```

- [ ] **Step 5: Run tests and verify RED**

Run:

```bash
uv run pytest -q \
  tests/scripts/test_duckdb_ui_launcher.py \
  tests/scripts/test_deploy_script.py::test_pc_deploy_installs_spoon_duckdb_ui_launcher \
  tests/scripts/test_deploy_script.py::test_pc_default_duckdb_ui_no_longer_snapshots_thepc_local_db
```

Expected: failures because `scripts/install_spoon_duckdb_ui.sh` does not exist and launchers still point at THEPC-local DuckDB.

## Task 2: Implement Spoon-Hosted DuckDB UI And Launchers

**Files:**
- Create: `scripts/install_spoon_duckdb_ui.sh`
- Modify: `scripts/open_duckdb_ui_mac.sh`
- Modify: `scripts/deploy_pc.sh`

- [ ] **Step 1: Create `scripts/install_spoon_duckdb_ui.sh`**

Create an executable zsh script with this behavior:

```zsh
#!/bin/zsh
emulate -L zsh
set -euo pipefail

SPOON_HOST="${SPOON_HOST:-spoon@100.126.126.1}"
REMOTE_SCRIPT="${POLYMARKET_DUCKDB_UI_REMOTE_SCRIPT:-/home/spoon/bin/open-polymarket-duckdb-ui.sh}"
REMOTE_PORT="${POLYMARKET_DUCKDB_UI_PORT:-4213}"

ssh "$SPOON_HOST" "bash -s" <<'REMOTE'
set -euo pipefail

BIN_DIR="${HOME}/bin"
DATA_DIR="${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}"
SOURCE_DB="${POLYMARKET_DUCKDB_SOURCE_DB:-$DATA_DIR/db/polymarket.duckdb}"
SNAPSHOT_DIR="${POLYMARKET_DUCKDB_UI_SNAPSHOT_DIR:-$DATA_DIR/duckdb-ui}"
SNAPSHOT_DB="$SNAPSHOT_DIR/current-polymarket.duckdb"
SNAPSHOT_TMP="$SNAPSHOT_DIR/snapshot.duckdb"
META_PATH="$SNAPSHOT_DIR/current-polymarket-meta.json"
LOG_DIR="$DATA_DIR/logs"
LOG_FILE="$LOG_DIR/duckdb-ui.log"
VIEWER_SCRIPT="$SNAPSHOT_DIR/polymarket_duckdb_viewer.py"
DUCKDB_BIN="${DUCKDB_BIN:-$HOME/.duckdb/cli/latest/duckdb}"

mkdir -p "$BIN_DIR" "$SNAPSHOT_DIR" "$LOG_DIR"

cat > "$BIN_DIR/open-polymarket-duckdb-ui.sh" <<'LAUNCHER'
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

DATA_DIR="${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}"
SOURCE_DB="${POLYMARKET_DUCKDB_SOURCE_DB:-$DATA_DIR/db/polymarket.duckdb}"
SNAPSHOT_DIR="${POLYMARKET_DUCKDB_UI_SNAPSHOT_DIR:-$DATA_DIR/duckdb-ui}"
SNAPSHOT_DB="$SNAPSHOT_DIR/current-polymarket.duckdb"
SNAPSHOT_TMP="$SNAPSHOT_DIR/snapshot.duckdb"
META_PATH="$SNAPSHOT_DIR/current-polymarket-meta.json"
LOG_DIR="$DATA_DIR/logs"
LOG_FILE="$LOG_DIR/duckdb-ui.log"
VIEWER_SCRIPT="$SNAPSHOT_DIR/polymarket_duckdb_viewer.py"
DUCKDB_BIN="${DUCKDB_BIN:-$HOME/.duckdb/cli/latest/duckdb}"

mkdir -p "$SNAPSHOT_DIR" "$LOG_DIR"

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

rm -f "$SNAPSHOT_TMP" "$SNAPSHOT_TMP.wal"
"$DUCKDB_BIN" "$SNAPSHOT_TMP" -batch -c "ATTACH $(quote_sql_string "$SOURCE_DB") AS source_db (READ_ONLY); COPY FROM DATABASE source_db TO snapshot;"
mv "$SNAPSHOT_TMP" "$SNAPSHOT_DB"
python3 - "$SOURCE_DB" "$SNAPSHOT_DB" "$META_PATH" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

source, snapshot, meta = sys.argv[1:]
payload = {
    "source_host": "spoon",
    "source_db": source,
    "snapshot_db": snapshot,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "source_mtime": datetime.fromtimestamp(os.stat(source).st_mtime, timezone.utc).isoformat(),
    "snapshot_mtime": datetime.fromtimestamp(os.stat(snapshot).st_mtime, timezone.utc).isoformat(),
}
with open(meta, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, sort_keys=True)
    fh.write("\n")
print("Snapshot generated", payload["generated_at"])
PY

pkill -f "polymarket_duckdb_viewer.py.*--port $PORT" >/dev/null 2>&1 || true
nohup python3 "$VIEWER_SCRIPT" --db "$SNAPSHOT_DB" --meta "$META_PATH" --duckdb-bin "$DUCKDB_BIN" --port "$PORT" >/dev/null 2>> "$LOG_FILE" &

for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/api/tables" >/dev/null 2>> "$LOG_FILE"; then
    echo "Polymarket DuckDB viewer ready at http://127.0.0.1:${PORT}"
    echo "Source: $SOURCE_DB"
    echo "Snapshot: $SNAPSHOT_DB"
    exit 0
  fi
  sleep 0.5
done

echo "Polymarket DuckDB viewer did not answer on http://127.0.0.1:${PORT}" >&2
exit 1
LAUNCHER
chmod 755 "$BIN_DIR/open-polymarket-duckdb-ui.sh"
REMOTE

ssh "$SPOON_HOST" "$REMOTE_SCRIPT --port $REMOTE_PORT"
```

The worker should insert the existing `polymarket_duckdb_viewer.py` implementation from `scripts/deploy_pc.sh`, but add:

- `--meta` argparse argument.
- `/api/meta` endpoint that returns the JSON meta file.
- Header text showing `Source: spoon`, `Snapshot generated: <generated_at>`, and the snapshot DB basename.
- A `Refresh snapshot` button can be omitted in this first pass; launch-time refresh is required.

- [ ] **Step 2: Update `scripts/open_duckdb_ui_mac.sh`**

Change it to:

- Default `SPOON_HOST=spoon@100.126.126.1`.
- Default remote script `/home/spoon/bin/open-polymarket-duckdb-ui.sh`.
- Call `./scripts/install_spoon_duckdb_ui.sh` when the remote helper is missing or when `POLYMARKET_DUCKDB_UI_INSTALL=1`.
- Start SSH tunnel to spoon, not THEPC.
- Preserve `POLYMARKET_DUCKDB_UI_TEST_LAUNCH=1`.

- [ ] **Step 3: Replace THEPC DuckDB helper block in `scripts/deploy_pc.sh`**

The installed `/home/ender/bin/open-polymarket-duckdb-ui.sh` should:

```bash
#!/usr/bin/env bash
set -euo pipefail

PORT="${POLYMARKET_DUCKDB_UI_PORT:-4213}"
SPOON_ALIAS="${POLYMARKET_SPOON_SSH_ALIAS:-spoon}"
REMOTE_SCRIPT="${POLYMARKET_SPOON_DUCKDB_UI_SCRIPT:-/home/spoon/bin/open-polymarket-duckdb-ui.sh}"

ssh "$SPOON_ALIAS" "$REMOTE_SCRIPT --port $PORT"
if ! curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/api/tables" >/dev/null 2>&1; then
  pkill -f "ssh -f -N -L ${PORT}:127.0.0.1:${PORT} ${SPOON_ALIAS}" >/dev/null 2>&1 || true
  ssh -f -N -L "${PORT}:127.0.0.1:${PORT}" "$SPOON_ALIAS"
fi
echo "Polymarket DuckDB UI ready at http://127.0.0.1:${PORT}"
```

Keep `/home/ender/bin/open-polymarket-duckdb-ui-window.sh`, but have it call this helper and keep the terminal open on failure.

- [ ] **Step 4: Refresh THEPC Windows launchers**

In `scripts/deploy_pc.sh`, keep writing:

- `C:\Users\ender\open-polymarket-tui.cmd`
- `C:\Users\ender\open-polymarket-duckdb-ui.cmd`
- Desktop `Polymarket TUI.lnk`
- Desktop `Polymarket DuckDB UI.lnk`

Use stable icon locations:

```powershell
$shortcut.IconLocation = "C:\WINDOWS\System32\shell32.dll,13"
```

for TUI and:

```powershell
$shortcut.IconLocation = "C:\WINDOWS\System32\shell32.dll,220"
```

for DuckDB UI.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest -q \
  tests/scripts/test_duckdb_ui_launcher.py \
  tests/scripts/test_deploy_script.py::test_pc_deploy_installs_spoon_duckdb_ui_launcher \
  tests/scripts/test_deploy_script.py::test_pc_default_duckdb_ui_no_longer_snapshots_thepc_local_db \
  tests/scripts/test_deploy_script.py::test_pc_deploy_script_refreshes_tui_desktop_launcher \
  tests/scripts/test_deploy_script.py::test_pc_tui_desktop_launcher_logs_failures_and_forces_new_terminal_window
```

Expected: all selected tests pass.

## Task 3: Docs, Runtime Install, And Verification

**Files:**
- Modify: `docs/SPOON_DEPLOYMENT.md`
- Runtime: spoon `/home/spoon/bin/open-polymarket-duckdb-ui.sh`
- Runtime: THEPC `C:\Users\ender\open-polymarket-*.cmd` and Desktop `.lnk`
- Runtime: Mac desktop `.command` launchers if present

- [ ] **Step 1: Update `docs/SPOON_DEPLOYMENT.md`**

Replace `### THEPC DuckDB Viewer` with `### Spoon DuckDB Viewer` and document:

- Spoon owns live DuckDB now.
- THEPC launchers tunnel to spoon.
- Mac launcher tunnels to spoon.
- Snapshot is fresh on launch and displays metadata.
- The DB is not opened directly by browsers.

- [ ] **Step 2: Run preflight before installing DuckDB CLI on spoon**

Run locally:

```bash
env | grep -E "^(ANTHROPIC_|OLLAMA_|OPENAI_)" | sort
grep -nE "^(export|alias|source)" ~/.zshrc | grep -iE "(anthropic|ollama|codex|openai)" || true
command -v node && node --version
command -v python3 && python3 --version
command -v codex && codex --version 2>/dev/null || true
```

Proceed if no stale agent/API config is implicated in this local shell.

- [ ] **Step 3: Install and verify spoon DuckDB UI helper**

Run:

```bash
./scripts/install_spoon_duckdb_ui.sh
```

Expected output includes:

```text
Snapshot generated
Polymarket DuckDB viewer ready at http://127.0.0.1:4213
Source: /home/spoon/polymarket-data/db/polymarket.duckdb
Snapshot: /home/spoon/polymarket-data/duckdb-ui/current-polymarket.duckdb
```

- [ ] **Step 4: Refresh THEPC launchers without a main deploy**

Until this branch is merged to `main`, install the launcher refresh directly from the working copy:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -s" < scripts/thepc_refresh_launchers.sh
```

If the worker chooses not to create `scripts/thepc_refresh_launchers.sh`, run the equivalent launcher block from `scripts/deploy_pc.sh` through SSH.

- [ ] **Step 5: Refresh Mac desktop command launchers**

If either desktop path exists:

```bash
printf '#!/bin/zsh\nexec /Users/goon/polymarket/scripts/open_tui_mac.sh\n' > "/Users/goon/Desktop/Polymarket TUI.command"
chmod 755 "/Users/goon/Desktop/Polymarket TUI.command"
printf '#!/bin/zsh\nexec /Users/goon/polymarket/scripts/open_duckdb_ui_mac.sh\n' > "/Users/goon/Desktop/Polymarket DuckDB UI.command"
chmod 755 "/Users/goon/Desktop/Polymarket DuckDB UI.command"
```

Use `apply_patch` for repo files; direct `printf` is acceptable for desktop launcher files outside the repository.

- [ ] **Step 6: Verify endpoints**

Run:

```bash
ssh spoon@100.126.126.1 "bash -lc 'curl -fsS http://127.0.0.1:4213/api/meta && curl -fsS http://127.0.0.1:4213/api/tables | head -c 200'"
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'curl -fsS http://127.0.0.1:4213/api/meta && curl -fsS http://127.0.0.1:4213/api/tables | head -c 200'"
POLYMARKET_DUCKDB_UI_TEST_LAUNCH=1 ./scripts/open_duckdb_ui_mac.sh
```

Expected:

- Spoon returns meta with `"source_host": "spoon"`.
- THEPC local port `4213` returns the same meta after the tunnel.
- Mac launcher test prints `Mac DuckDB UI launcher ready.`

## Self-Review

- Spec coverage: The plan moves DuckDB UI data authority to spoon, refreshes THEPC and Mac launchers, keeps TUI launchers, and avoids THEPC-local stale DB snapshots.
- Placeholder scan: No TBD/TODO placeholders remain.
- Type/name consistency: Script names are consistent across tests, docs, and launchers.
- Risks: Snapshotting a 5GB+ DuckDB file can cost CPU and I/O. The design refreshes on launch instead of polling continuously, so the user gets accurate current data without constant DB copy pressure.
