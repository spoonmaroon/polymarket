#!/usr/bin/env bash
set -euo pipefail

SPOON_HOSTNAME="${SPOON_HOSTNAME:-100.126.126.1}"
SPOON_USER="${SPOON_USER:-spoon}"
SPOON_ALIAS="${POLYMARKET_SPOON_SSH_ALIAS:-spoon}"
DATA_DIR="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}"
BIN_DIR="${POLYMARKET_BIN_DIR:-$HOME/bin}"
LIVE_DIR="$DATA_DIR/live"
LOG_DIR="$DATA_DIR/logs"
SYNC_SCRIPT="$BIN_DIR/polymarket-sync-spoon-artifacts.sh"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_PATH="$SERVICE_DIR/polymarket-spoon-artifact-sync.service"

mkdir -p "$BIN_DIR" "$LIVE_DIR" "$LOG_DIR" "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/config"
chmod 600 "$HOME/.ssh/config"

python3 - "$HOME/.ssh/config" "$SPOON_HOSTNAME" "$SPOON_USER" "$SPOON_ALIAS" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
hostname = sys.argv[2]
user = sys.argv[3]
alias = sys.argv[4]
text = path.read_text(encoding="utf-8") if path.exists() else ""
block = f"""
Host {alias}
  HostName {hostname}
  User {user}
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
"""
lines = text.splitlines()
out = []
skip = False
for line in lines:
    if line.strip().lower() == f"host {alias}".lower():
        skip = True
        continue
    if skip and (line.startswith("Host ") or line.startswith("Match ")):
        skip = False
    if not skip:
        out.append(line)
path.write_text("\n".join(out).rstrip() + block + "\n", encoding="utf-8")
PY

cat > "$SYNC_SCRIPT" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

SPOON_ALIAS="${POLYMARKET_SPOON_SSH_ALIAS:-spoon}"
src="$SPOON_ALIAS:/home/spoon/polymarket-data/live"
dst="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}/live"
mkdir -p "$dst"
for file in status.json normalized_health.json probability_inputs.json probability_fragments.json outcomes.json volatility.json; do
  if ! rsync -az --delay-updates --partial --timeout=5 "$src/$file" "$dst/$file"; then
    printf 'artifact sync skipped %s\n' "$file" >&2
  fi
done
SH
chmod 755 "$SYNC_SCRIPT"

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE_PATH" <<UNIT
[Unit]
Description=Polymarket Spoon artifact sync loop
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/bash -lc 'while true; do $SYNC_SCRIPT; sleep 1; done'
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now polymarket-spoon-artifact-sync.service
else
  if [ -f "$LIVE_DIR/artifact-sync.pid" ]; then
    old_pid="$(cat "$LIVE_DIR/artifact-sync.pid" || true)"
    if [ -n "$old_pid" ]; then
      kill "$old_pid" >/dev/null 2>&1 || true
    fi
  fi
  nohup bash -lc "while true; do $SYNC_SCRIPT; sleep 1; done" > "$LOG_DIR/artifact-sync.log" 2>&1 &
  echo "$!" > "$LIVE_DIR/artifact-sync.pid"
fi

"$SYNC_SCRIPT"
