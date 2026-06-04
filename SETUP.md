# Setup

## GitHub

Private repo:

```text
git@github.com:AnimeWeeb9000/polymarket.git
```

`main` is the only long-lived GitHub branch. Use short-lived local branches for
work, merge them back to `main`, push `main`, then delete the remote feature
branch.

Clone and enter the repo:

```bash
git clone git@github.com:AnimeWeeb9000/polymarket.git
cd polymarket
git checkout main
```

## Requirements

- `uv`
- Rust toolchain with `cargo`
- Docker Desktop on the Mac for prebuilt THEPC deploys
- Tailscale/SSH access to THEPC as `ender@100.72.104.49`
- Optional: CMake for `probability_core`
- Optional: Node.js for the retired React scaffold

## Python

Create or refresh the local environment:

```bash
uv sync --dev
```

Run the local read-only API against local files:

```bash
uv run uvicorn polymarket_engine.app:app --host 127.0.0.1 --port 8000 --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Rust

Run Rust checks from the workspace:

```bash
cargo test --manifest-path rust/Cargo.toml
```

Run a finite local state-manager smoke:

```bash
cd rust
cargo run -p polymarket-live-probe -- \
  --mode state-manager \
  --assets BTC,ETH \
  --interval 5m \
  --prewarm-windows 2 \
  --run-for-seconds 30 \
  --out ../reports/live_probe/state_manager.json
```

## THEPC Runtime

THEPC is the active always-on read-only runtime. The deploy helper builds Linux
images on the Mac, sends a pinned git bundle and image tarballs into THEPC's
Ubuntu WSL environment, restarts `collector`, `normalizer`, and `api`, and
refreshes the Windows desktop TUI shortcut.

```bash
cd /Users/goon/polymarket
./scripts/deploy_pc.sh
```

Defaults:

- `PC_HOST=ender@100.72.104.49`
- `PC_REPO=/home/ender/polymarket`
- `PC_DATA_DIR=/home/ender/polymarket-data`
- `PC_NORMALIZER_INTERVAL_SECONDS=0.1`
- API URL: `http://100.72.104.49:8000`

Verify THEPC after deploy:

```bash
curl -fsS http://100.72.104.49:8000/health
curl -fsS 'http://100.72.104.49:8000/api/runtime/live?limit=8' | python3 -m json.tool | head -80
```

## Cockpit TUI

On THEPC, use the refreshed Windows desktop shortcut named `Polymarket TUI`.

On the Mac, run:

```bash
./scripts/open_tui_mac.sh
```

The TUI is read-only. It must not place orders, write collector state, restart
containers, or access auth secrets.

## Optional C++

Configure and build `probability_core`:

```bash
cmake -S . -B cmake-build-debug
cmake --build cmake-build-debug
```

## Optional UI Scaffold

Install UI dependencies:

```bash
cd ui
npm install
npm run dev
```

## Secrets

Copy `.env.example` to `.env` only for local experiments that need it. Do not
commit `.env`. Live runtime secrets and deploy env belong on the runtime host,
not in Git.
