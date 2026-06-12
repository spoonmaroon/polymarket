#!/usr/bin/env bash
set -euo pipefail

THEPC_SSH="${POLYMARKET_THEPC_SSH:-ender@100.72.104.49}"
WSL_DISTRO="${POLYMARKET_THEPC_WSL_DISTRO:-Ubuntu}"
LOCAL_BIND="${POLYMARKET_TUNNEL_LOCAL_BIND:-127.0.0.1}"
LOCAL_PORT="${POLYMARKET_TUNNEL_LOCAL_PORT:-8000}"
REMOTE_PORT="${POLYMARKET_TUNNEL_REMOTE_PORT:-8000}"

resolve_wsl_ip() {
  ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    "$THEPC_SSH" \
    "wsl.exe -d $WSL_DISTRO -- bash -lc \"ip -4 -o addr show eth0 | awk '{split(\\\$4,a,\\\"/\\\"); print a[1]; exit}' || hostname -I | awk '{print \\\$1}'\"" \
    | tr -d '\r' \
    | awk 'NF {print; exit}'
}

wsl_ip="$(resolve_wsl_ip)"
if [[ -z "$wsl_ip" ]]; then
  echo "Could not resolve THEPC WSL IP through $THEPC_SSH" >&2
  exit 1
fi

if [[ "$LOCAL_BIND" == "127.0.0.1" && "$LOCAL_PORT" == "8000" && "$REMOTE_PORT" == "8000" ]]; then
  forward_spec="127.0.0.1:8000:${wsl_ip}:8000"
else
  forward_spec="${LOCAL_BIND}:${LOCAL_PORT}:${wsl_ip}:${REMOTE_PORT}"
fi

exec ssh \
  -N \
  -L "$forward_spec" \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o BatchMode=yes \
  "$THEPC_SSH"
