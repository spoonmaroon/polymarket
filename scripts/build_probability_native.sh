#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../rust/crates/polymarket-probability-native"
uv tool run maturin develop --release
