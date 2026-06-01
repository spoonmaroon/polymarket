#!/usr/bin/env sh
set -eu

cat >&2 <<'EOF'
The Python Polymarket live collector is retired and cannot be started from this image.
Use the Rust runtime instead:

  cd rust
  cargo run -p polymarket-live-probe -- --assets BTC,ETH --interval 5m --windows 1

EOF
exit 64
