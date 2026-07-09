#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export POLYMARKET_DATA_DIR="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}"
export POLYMARKET_BIN_DIR="${POLYMARKET_BIN_DIR:-$HOME/bin}"
exec "$SCRIPT_DIR/install_gpu_node_spoon_artifact_sync.sh"
