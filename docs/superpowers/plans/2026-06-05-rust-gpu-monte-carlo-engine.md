# Rust GPU Monte Carlo Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a robust, future-proof Monte Carlo probability engine with a Rust CPU reference backend, an optional CUDA GPU backend for THEPC's RTX 5060 Ti, cached read-only probability outputs, TUI summaries, and browser research visualizations.

**Architecture:** Keep the live collector, normalizer, runtime API, and TUI hot path read-only and low-latency. Add a native Rust probability core under the existing Rust workspace, expose it to Python through an optional native bridge, and make GPU acceleration a selectable backend that produces the same versioned `SimulationRun` contract as the CPU backend. Store compact live outputs in DuckDB and store larger visualization artifacts separately so terminal repainting never triggers heavy simulation.

**Tech Stack:** Rust 2024 workspace, `rayon`, `rand`, `rand_chacha`, `rand_distr`, optional CUDA via `cudarc` plus CUDA kernels built on THEPC WSL, PyO3/maturin optional Python bridge, Python 3.11 runtime wrappers, DuckDB/Parquet, FastAPI, existing Ratatui TUI, existing Vite/React UI.

---

## Sources And Current State

- `docs/BINARY_CONTRACT_ENGINE_PLAN.md`
  - Monte Carlo is the primary estimator.
  - Closed-form formulas are debugging baselines.
  - Live decisions must use as-of state only.
  - Heavy Monte Carlo should use cached probability grids and conditional refresh, not every tick.
- `docs/superpowers/plans/2026-06-04-section-5-6-probability-engine.md`
  - Section 5/6 output shape centers on `p_finish`, `p_no_touch`, `z_path`, `sigma_tau`, generator uncertainty, and cache/status fields.
- `src/polymarket_engine/probability/schema.py`
  - Existing strict `ProbabilityInput` and `ProbabilityOutput` are the current Python contract.
- `src/polymarket_engine/probability/monte_carlo.py`
  - Existing NumPy baseline is deterministic and scales per-step sigma as `sigma_tau / sqrt(steps)`.
- `src/polymarket_engine/probability/runtime.py`
  - Runtime probabilities are disabled by default and cached no faster than about 1 second.
- `rust/Cargo.toml`
  - Existing Rust workspace uses edition 2024 and `rust-version = "1.91"`.
- `ui/src/App.tsx`
  - Browser UI is currently a scaffold and can become the research visualization surface.
- `cpp/probability_core/`
  - Existing C++ core is only a rough placeholder, not a Monte Carlo engine. Do not extend it as the main implementation.

## THEPC Verification Result

Verified over Tailscale SSH at `ender@100.72.104.49`:

- GPU: `NVIDIA GeForce RTX 5060 Ti`
- VRAM: `16311 MiB`
- Compute capability: `12.0`
- Windows driver: `595.79`
- Driver CUDA support: `13.2`
- WSL2 Ubuntu: running and can see the GPU through `/usr/lib/wsl/lib/nvidia-smi`
- Missing: CUDA toolkit / `nvcc`
- Missing: Rust toolchain / `cargo`
- Missing on Windows PATH: MSVC `cl.exe`
- Docker Desktop Linux engine: installed but stopped during the check

Implementation should use WSL Ubuntu for GPU development/runtime. Native Windows CUDA/MSVC is unnecessary for this plan.

## Non-Goals

- No real order placement.
- No wallet, signing, private keys, or authenticated trading path.
- No TUI-triggered full simulation.
- No GPU-only dependency. CPU stays the correctness reference and fallback.
- No migration from DuckDB/Parquet unless measured bottlenecks prove the need.
- No use of the placeholder C++ probability core as authority.

## File Structure

Create these Rust crates:

- `rust/crates/polymarket-probability-core/`
  - Pure Rust types, exact path scoring, CPU Monte Carlo backend, backend trait, deterministic output schema.
  - No CUDA, no PyO3, no DuckDB, no HTTP.
- `rust/crates/polymarket-probability-cuda/`
  - Optional THEPC-only CUDA backend using `cudarc`.
  - Depends on `polymarket-probability-core`.
  - Built only when GPU tests or THEPC deployment ask for it.
- `rust/crates/polymarket-probability-native/`
  - Optional PyO3 module exposing the Rust CPU backend first, then GPU backend when available.
  - Depends on `polymarket-probability-core`.
  - Does not force CUDA onto Mac builds.

Modify these Python/API files:

- `src/polymarket_engine/probability/native.py`
  - Import optional native module.
  - Fall back to current Python NumPy implementation.
- `src/polymarket_engine/probability/runtime.py`
  - Select backend by config/env.
  - Persist backend and timing diagnostics.
- `src/polymarket_engine/probability/schema.py`
  - Add versioned `SimulationRun`/diagnostic shapes if they are not introduced in Section 5/6 first.
- `src/polymarket_engine/storage/duckdb_store.py`
  - Store compact live probability output fields and larger visualization metadata.
- `src/polymarket_engine/runtime_api.py`
  - Expose cached MC summary/status and research visualization artifact endpoints.

Modify these UI files:

- `rust/crates/polymarket-cockpit-tui/src/client.rs`
- `rust/crates/polymarket-cockpit-tui/src/state.rs`
- `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
- `ui/src/App.tsx`
- `ui/src/styles.css`

Create these support scripts/docs:

- `scripts/thepc_gpu_preflight.sh`
- `scripts/install_thepc_wsl_gpu_toolchain.sh`
- `scripts/build_probability_native.sh`
- `docs/GPU_MONTE_CARLO.md`

## Subagent Deployment

- Agent A: THEPC WSL GPU toolchain and smoke tests.
- Agent B: Rust CPU probability core and parity tests.
- Agent C: Python bridge, runtime cache, and DuckDB storage.
- Agent D: CUDA backend and GPU parity benchmarks.
- Agent E: TUI/API/browser visualization surfaces.
- Agent F: final audit, docs, deploy plan, and regression verification.

Agents B and C can start before Agent A completes. Agent D waits for Agent A and B. Agent E waits for C, and only uses cached outputs.

## Task 1: Add THEPC WSL GPU Preflight Scripts

**Files:**

- Create: `scripts/thepc_gpu_preflight.sh`
- Create: `scripts/install_thepc_wsl_gpu_toolchain.sh`
- Create: `docs/GPU_MONTE_CARLO.md`

- [ ] **Step 1: Write the preflight script**

Create `scripts/thepc_gpu_preflight.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "== OS =="
cat /etc/os-release | sed -n '1,8p'
uname -a

echo "== GPU =="
command -v nvidia-smi
nvidia-smi --query-gpu=name,driver_version,compute_cap,memory.total --format=csv

echo "== CUDA Toolkit =="
if command -v nvcc >/dev/null 2>&1; then
  nvcc --version
  nvcc --list-gpu-arch | tail -n 30
  if nvcc --list-gpu-arch | grep -Eq 'compute_120|sm_120'; then
    echo "CUDA toolkit supports RTX 5060 Ti compute capability 12.0"
  else
    echo "WARNING: nvcc does not list compute_120/sm_120"
  fi
else
  echo "MISSING: nvcc"
fi

echo "== Rust =="
if command -v cargo >/dev/null 2>&1; then
  rustc --version
  cargo --version
else
  echo "MISSING: cargo"
fi

echo "== Python =="
python3 --version

echo "== Done =="
```

- [ ] **Step 2: Write the install script**

Create `scripts/install_thepc_wsl_gpu_toolchain.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

if ! grep -qi microsoft /proc/version; then
  echo "This script is intended for THEPC WSL2 Ubuntu." >&2
  exit 2
fi

sudo apt-get update
sudo apt-get install -y \
  build-essential \
  ca-certificates \
  curl \
  git \
  pkg-config \
  python3-dev \
  python3-pip \
  wget

if ! command -v rustup >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --default-toolchain stable
fi

source "${HOME}/.cargo/env"
rustup default stable

CUDA_KEYRING_DEB="/tmp/cuda-keyring_1.1-1_all.deb"
wget -O "${CUDA_KEYRING_DEB}" \
  https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i "${CUDA_KEYRING_DEB}"
sudo apt-get update
sudo apt-get install -y cuda-toolkit-13-2

if ! grep -q '/usr/local/cuda/bin' "${HOME}/.profile"; then
  {
    echo 'export PATH=/usr/local/cuda/bin:${PATH}'
    echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}'
  } >> "${HOME}/.profile"
fi

echo "Restart the shell or run:"
echo "source ~/.profile && ./scripts/thepc_gpu_preflight.sh"
```

- [ ] **Step 3: Make scripts executable**

Run:

```bash
chmod +x scripts/thepc_gpu_preflight.sh scripts/install_thepc_wsl_gpu_toolchain.sh
```

- [ ] **Step 4: Run preflight on THEPC before installation**

Run from Mac:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket && ./scripts/thepc_gpu_preflight.sh"'
```

Expected before install:

```text
MISSING: nvcc
MISSING: cargo
```

- [ ] **Step 5: Document THEPC GPU setup**

Create `docs/GPU_MONTE_CARLO.md`:

```markdown
# GPU Monte Carlo Setup

THEPC has an RTX 5060 Ti with compute capability 12.0 and 16 GB VRAM.

The engine uses WSL2 Ubuntu for CUDA builds and runtime checks. The Windows NVIDIA
driver provides GPU access to WSL. Do not install a Linux display driver inside
WSL.

Run:

```bash
./scripts/thepc_gpu_preflight.sh
./scripts/install_thepc_wsl_gpu_toolchain.sh
source ~/.profile
./scripts/thepc_gpu_preflight.sh
```

The required success signal is:

- `nvidia-smi` sees RTX 5060 Ti
- `nvcc --version` works
- `nvcc --list-gpu-arch` includes `compute_120` or `sm_120`
- `cargo --version` works

The CPU backend is always the correctness reference. CUDA is an acceleration
backend, not a required runtime dependency for the collector or TUI.
```
```

- [ ] **Step 6: Commit**

```bash
git add scripts/thepc_gpu_preflight.sh scripts/install_thepc_wsl_gpu_toolchain.sh docs/GPU_MONTE_CARLO.md
git commit -m "Add THEPC GPU Monte Carlo preflight"
```

## Task 2: Create The Rust Probability Core Contract

**Files:**

- Modify: `rust/Cargo.toml`
- Create: `rust/crates/polymarket-probability-core/Cargo.toml`
- Create: `rust/crates/polymarket-probability-core/src/lib.rs`
- Create: `rust/crates/polymarket-probability-core/src/schema.rs`
- Create: `rust/crates/polymarket-probability-core/src/backend.rs`
- Create: `rust/crates/polymarket-probability-core/src/scoring.rs`

- [ ] **Step 1: Add workspace member and dependencies**

Modify `rust/Cargo.toml`:

```toml
[workspace]
resolver = "2"
members = [
    "crates/polymarket-runtime-types",
    "crates/polymarket-live-probe",
    "crates/polymarket-cockpit-tui",
    "crates/polymarket-probability-core",
]

[workspace.dependencies]
anyhow = "1.0"
chrono = { version = "0.4", features = ["serde"] }
clap = { version = "4.5", features = ["derive"] }
crossterm = "0.29"
futures = "0.3"
rand = "0.9"
rand_chacha = "0.9"
rand_distr = "0.5"
ratatui = "0.30"
rayon = "1.10"
reqwest = { version = "0.12", features = ["json", "rustls-tls"], default-features = false }
rustls = { version = "0.23", default-features = false, features = ["aws_lc_rs"] }
rust_decimal = { version = "1.36", features = ["serde"] }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
tokio = { version = "1.40", features = ["rt-multi-thread", "macros", "sync", "time"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
unicode-width = "0.2"
polymarket_client_sdk_v2 = { git = "https://github.com/Polymarket/rs-clob-client-v2", features = ["clob", "gamma", "rtds", "ws", "tracing"] }
```

- [ ] **Step 2: Add crate manifest**

Create `rust/crates/polymarket-probability-core/Cargo.toml`:

```toml
[package]
name = "polymarket-probability-core"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true

[dependencies]
anyhow.workspace = true
chrono.workspace = true
rand.workspace = true
rand_chacha.workspace = true
rand_distr.workspace = true
rayon.workspace = true
serde.workspace = true
serde_json.workspace = true
```

- [ ] **Step 3: Define schema types**

Create `rust/crates/polymarket-probability-core/src/schema.rs`:

```rust
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct ProbabilityInput {
    pub state_id: String,
    pub asof_ts: DateTime<Utc>,
    pub asset: Asset,
    pub side: Side,
    pub comparison_operator: ComparisonOperator,
    pub seconds_left: f64,
    pub settlement_price: f64,
    pub threshold: f64,
    pub sigma_tau: f64,
    pub executable_price: f64,
    pub source_age_ms: u64,
    pub book_age_ms: u64,
    pub z_path: f64,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "UPPERCASE")]
pub enum Asset {
    BTC,
    ETH,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "UPPERCASE")]
pub enum Side {
    UP,
    DOWN,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub enum ComparisonOperator {
    #[serde(rename = ">")]
    Greater,
    #[serde(rename = ">=")]
    GreaterEqual,
    #[serde(rename = "<")]
    Less,
    #[serde(rename = "<=")]
    LessEqual,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct SimulationConfig {
    pub path_count: usize,
    pub steps: usize,
    pub seed: u64,
    pub backend: SimulationBackendKind,
    pub model_version: String,
    pub emit_artifacts: bool,
    pub sample_path_limit: usize,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SimulationBackendKind {
    CpuRayon,
    Cuda,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct SimulationRun {
    pub state_id: String,
    pub asof_ts: DateTime<Utc>,
    pub p_finish: f64,
    pub p_no_touch: f64,
    pub z_path: f64,
    pub model_version: String,
    pub seed: u64,
    pub backend: SimulationBackendKind,
    pub diagnostics: Value,
    pub artifacts: SimulationArtifacts,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
pub struct SimulationArtifacts {
    pub percentile_paths: Vec<PercentilePoint>,
    pub sample_paths: Vec<Vec<f64>>,
    pub terminal_histogram: Vec<HistogramBin>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct PercentilePoint {
    pub step: usize,
    pub p05: f64,
    pub p25: f64,
    pub p50: f64,
    pub p75: f64,
    pub p95: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct HistogramBin {
    pub min_price: f64,
    pub max_price: f64,
    pub count: usize,
}
```

- [ ] **Step 4: Define backend trait**

Create `rust/crates/polymarket-probability-core/src/backend.rs`:

```rust
use anyhow::Result;

use crate::schema::{ProbabilityInput, SimulationConfig, SimulationRun};

pub trait SimulationBackend {
    fn run(&self, input: &ProbabilityInput, config: &SimulationConfig) -> Result<SimulationRun>;
}
```

- [ ] **Step 5: Add exact scoring helpers**

Create `rust/crates/polymarket-probability-core/src/scoring.rs`:

```rust
use crate::schema::{ComparisonOperator, ProbabilityInput};

pub fn price_satisfies_contract(input: &ProbabilityInput, price: f64) -> bool {
    match input.comparison_operator {
        ComparisonOperator::Greater => price > input.threshold,
        ComparisonOperator::GreaterEqual => price >= input.threshold,
        ComparisonOperator::Less => price < input.threshold,
        ComparisonOperator::LessEqual => price <= input.threshold,
    }
}

pub fn score_path(input: &ProbabilityInput, path: &[f64]) -> (bool, bool) {
    let final_price = path[path.len() - 1];
    let terminal_win = price_satisfies_contract(input, final_price);
    let no_touch_win = path.iter().all(|price| price_satisfies_contract(input, *price));
    (terminal_win, no_touch_win)
}
```

- [ ] **Step 6: Export modules**

Create `rust/crates/polymarket-probability-core/src/lib.rs`:

```rust
pub mod backend;
pub mod schema;
pub mod scoring;
```

- [ ] **Step 7: Run tests**

Run:

```bash
cd rust
cargo test -p polymarket-probability-core
```

Expected:

```text
test result: ok
```

- [ ] **Step 8: Commit**

```bash
git add rust/Cargo.toml rust/crates/polymarket-probability-core
git commit -m "Add Rust probability core schema"
```

## Task 3: Implement CPU Reference Backend

**Files:**

- Create: `rust/crates/polymarket-probability-core/src/cpu.rs`
- Modify: `rust/crates/polymarket-probability-core/src/lib.rs`
- Test: `rust/crates/polymarket-probability-core/src/cpu.rs`
- Test: `rust/crates/polymarket-probability-core/src/scoring.rs`

- [ ] **Step 1: Add exact scoring tests**

Append to `rust/crates/polymarket-probability-core/src/scoring.rs`:

```rust
#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};

    use super::*;
    use crate::schema::{Asset, Side};

    fn input(operator: ComparisonOperator) -> ProbabilityInput {
        ProbabilityInput {
            state_id: "state".to_string(),
            asof_ts: Utc.with_ymd_and_hms(2026, 6, 5, 12, 0, 0).unwrap(),
            asset: Asset::BTC,
            side: Side::UP,
            comparison_operator: operator,
            seconds_left: 60.0,
            settlement_price: 100.0,
            threshold: 100.0,
            sigma_tau: 0.01,
            executable_price: 0.5,
            source_age_ms: 10,
            book_age_ms: 10,
            z_path: 0.0,
        }
    }

    #[test]
    fn strict_greater_rejects_equal_threshold() {
        assert!(!price_satisfies_contract(&input(ComparisonOperator::Greater), 100.0));
        assert!(price_satisfies_contract(&input(ComparisonOperator::Greater), 100.01));
    }

    #[test]
    fn greater_equal_accepts_equal_threshold() {
        assert!(price_satisfies_contract(&input(ComparisonOperator::GreaterEqual), 100.0));
    }

    #[test]
    fn strict_less_rejects_equal_threshold() {
        assert!(!price_satisfies_contract(&input(ComparisonOperator::Less), 100.0));
        assert!(price_satisfies_contract(&input(ComparisonOperator::Less), 99.99));
    }

    #[test]
    fn less_equal_accepts_equal_threshold() {
        assert!(price_satisfies_contract(&input(ComparisonOperator::LessEqual), 100.0));
    }
}
```

- [ ] **Step 2: Run exact tests and verify they pass**

Run:

```bash
cd rust
cargo test -p polymarket-probability-core scoring::tests
```

Expected:

```text
4 passed
```

- [ ] **Step 3: Add CPU backend**

Create `rust/crates/polymarket-probability-core/src/cpu.rs`:

```rust
use std::time::Instant;

use anyhow::{bail, Result};
use rand::SeedableRng;
use rand_chacha::ChaCha20Rng;
use rand_distr::{Distribution, Normal};
use rayon::prelude::*;
use serde_json::json;

use crate::backend::SimulationBackend;
use crate::schema::{
    ProbabilityInput, SimulationArtifacts, SimulationBackendKind, SimulationConfig, SimulationRun,
};
use crate::scoring::score_path;

#[derive(Default)]
pub struct CpuRayonBackend;

impl SimulationBackend for CpuRayonBackend {
    fn run(&self, input: &ProbabilityInput, config: &SimulationConfig) -> Result<SimulationRun> {
        validate_config(config)?;
        validate_input(input)?;
        let started = Instant::now();
        let per_step_sigma = input.sigma_tau / (config.steps as f64).sqrt();

        let counts = (0..config.path_count)
            .into_par_iter()
            .map(|path_index| {
                let mut rng = ChaCha20Rng::seed_from_u64(config.seed ^ path_index as u64);
                let normal = Normal::new(0.0, per_step_sigma).expect("positive sigma");
                let mut path = Vec::with_capacity(config.steps + 1);
                path.push(input.settlement_price);
                let mut cumulative_log_return = 0.0;
                for _ in 0..config.steps {
                    cumulative_log_return += normal.sample(&mut rng);
                    path.push(input.settlement_price * cumulative_log_return.exp());
                }
                let (terminal_win, no_touch_win) = score_path(input, &path);
                (usize::from(terminal_win), usize::from(no_touch_win))
            })
            .reduce(|| (0usize, 0usize), |a, b| (a.0 + b.0, a.1 + b.1));

        Ok(SimulationRun {
            state_id: input.state_id.clone(),
            asof_ts: input.asof_ts,
            p_finish: counts.0 as f64 / config.path_count as f64,
            p_no_touch: counts.1 as f64 / config.path_count as f64,
            z_path: input.z_path,
            model_version: config.model_version.clone(),
            seed: config.seed,
            backend: SimulationBackendKind::CpuRayon,
            diagnostics: json!({
                "path_count": config.path_count,
                "steps": config.steps,
                "elapsed_ms": started.elapsed().as_secs_f64() * 1000.0,
                "per_step_sigma": per_step_sigma,
            }),
            artifacts: SimulationArtifacts::default(),
        })
    }
}

fn validate_config(config: &SimulationConfig) -> Result<()> {
    if config.path_count == 0 {
        bail!("path_count must be positive");
    }
    if config.steps == 0 {
        bail!("steps must be positive");
    }
    if config.model_version.is_empty() {
        bail!("model_version must be non-empty");
    }
    Ok(())
}

fn validate_input(input: &ProbabilityInput) -> Result<()> {
    if input.settlement_price <= 0.0 || !input.settlement_price.is_finite() {
        bail!("settlement_price must be positive and finite");
    }
    if input.threshold <= 0.0 || !input.threshold.is_finite() {
        bail!("threshold must be positive and finite");
    }
    if input.sigma_tau <= 0.0 || !input.sigma_tau.is_finite() {
        bail!("sigma_tau must be positive and finite");
    }
    Ok(())
}
```

- [ ] **Step 4: Export CPU backend**

Modify `rust/crates/polymarket-probability-core/src/lib.rs`:

```rust
pub mod backend;
pub mod cpu;
pub mod schema;
pub mod scoring;
```

- [ ] **Step 5: Add CPU backend tests**

Append to `rust/crates/polymarket-probability-core/src/cpu.rs`:

```rust
#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};

    use super::*;
    use crate::schema::{Asset, ComparisonOperator, Side, SimulationBackendKind};

    fn input() -> ProbabilityInput {
        ProbabilityInput {
            state_id: "state".to_string(),
            asof_ts: Utc.with_ymd_and_hms(2026, 6, 5, 12, 0, 0).unwrap(),
            asset: Asset::BTC,
            side: Side::UP,
            comparison_operator: ComparisonOperator::Greater,
            seconds_left: 60.0,
            settlement_price: 100.0,
            threshold: 100.0,
            sigma_tau: 0.01,
            executable_price: 0.5,
            source_age_ms: 10,
            book_age_ms: 10,
            z_path: 0.0,
        }
    }

    #[test]
    fn cpu_backend_is_seed_deterministic() {
        let backend = CpuRayonBackend;
        let config = SimulationConfig {
            path_count: 4096,
            steps: 16,
            seed: 123,
            backend: SimulationBackendKind::CpuRayon,
            model_version: "rust-cpu-test-v1".to_string(),
            emit_artifacts: false,
            sample_path_limit: 0,
        };
        let first = backend.run(&input(), &config).unwrap();
        let second = backend.run(&input(), &config).unwrap();
        assert_eq!(first.p_finish, second.p_finish);
        assert_eq!(first.p_no_touch, second.p_no_touch);
        assert_eq!(first.seed, 123);
    }

    #[test]
    fn cpu_backend_outputs_probabilities() {
        let backend = CpuRayonBackend;
        let config = SimulationConfig {
            path_count: 2048,
            steps: 8,
            seed: 456,
            backend: SimulationBackendKind::CpuRayon,
            model_version: "rust-cpu-test-v1".to_string(),
            emit_artifacts: false,
            sample_path_limit: 0,
        };
        let run = backend.run(&input(), &config).unwrap();
        assert!((0.0..=1.0).contains(&run.p_finish));
        assert!((0.0..=1.0).contains(&run.p_no_touch));
        assert_eq!(run.backend, SimulationBackendKind::CpuRayon);
    }
}
```

- [ ] **Step 6: Run Rust core tests**

Run:

```bash
cd rust
cargo test -p polymarket-probability-core
cargo fmt --check
cargo clippy -p polymarket-probability-core -- -D warnings
```

Expected:

```text
test result: ok
```

- [ ] **Step 7: Commit**

```bash
git add rust/crates/polymarket-probability-core
git commit -m "Add Rust CPU Monte Carlo backend"
```

## Task 4: Add Python Native Bridge With Safe Fallback

**Files:**

- Create: `rust/crates/polymarket-probability-native/Cargo.toml`
- Create: `rust/crates/polymarket-probability-native/src/lib.rs`
- Modify: `rust/Cargo.toml`
- Create: `src/polymarket_engine/probability/native.py`
- Modify: `src/polymarket_engine/probability/runtime.py`
- Create: `tests/probability/test_native_probability.py`
- Create: `scripts/build_probability_native.sh`

- [ ] **Step 1: Add native crate to workspace**

Modify `rust/Cargo.toml`:

```toml
members = [
    "crates/polymarket-runtime-types",
    "crates/polymarket-live-probe",
    "crates/polymarket-cockpit-tui",
    "crates/polymarket-probability-core",
    "crates/polymarket-probability-native",
]

[workspace.dependencies]
pyo3 = { version = "0.23", features = ["extension-module"] }
```

- [ ] **Step 2: Add PyO3 crate manifest**

Create `rust/crates/polymarket-probability-native/Cargo.toml`:

```toml
[package]
name = "polymarket-probability-native"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true

[lib]
name = "polymarket_probability_native"
crate-type = ["cdylib"]

[dependencies]
anyhow.workspace = true
polymarket-probability-core = { path = "../polymarket-probability-core" }
pyo3.workspace = true
serde_json.workspace = true
```

- [ ] **Step 3: Expose a JSON bridge**

Create `rust/crates/polymarket-probability-native/src/lib.rs`:

```rust
use polymarket_probability_core::backend::SimulationBackend;
use polymarket_probability_core::cpu::CpuRayonBackend;
use polymarket_probability_core::schema::{ProbabilityInput, SimulationConfig};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
fn run_cpu_json(input_json: &str, config_json: &str) -> PyResult<String> {
    let input: ProbabilityInput =
        serde_json::from_str(input_json).map_err(|exc| PyValueError::new_err(exc.to_string()))?;
    let config: SimulationConfig =
        serde_json::from_str(config_json).map_err(|exc| PyValueError::new_err(exc.to_string()))?;
    let run = CpuRayonBackend
        .run(&input, &config)
        .map_err(|exc| PyValueError::new_err(exc.to_string()))?;
    serde_json::to_string(&run).map_err(|exc| PyValueError::new_err(exc.to_string()))
}

#[pymodule]
fn polymarket_probability_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(run_cpu_json, module)?)?;
    Ok(())
}
```

- [ ] **Step 4: Add build helper**

Create `scripts/build_probability_native.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../rust/crates/polymarket-probability-native"
uv tool run maturin develop --release
```

- [ ] **Step 5: Add Python wrapper**

Create `src/polymarket_engine/probability/native.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from polymarket_engine.probability.monte_carlo import run_seeded_monte_carlo
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


def run_native_or_python(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
    backend: str = "cpu_rayon",
) -> ProbabilityOutput:
    if backend == "python_numpy":
        return run_seeded_monte_carlo(
            probability_input,
            path_count=path_count,
            steps=steps,
            seed=seed,
        )
    try:
        import polymarket_probability_native
    except ImportError:
        return run_seeded_monte_carlo(
            probability_input,
            path_count=path_count,
            steps=steps,
            seed=seed,
        )

    config = {
        "path_count": path_count,
        "steps": steps,
        "seed": seed,
        "backend": "cpu_rayon",
        "model_version": "rust-cpu-rayon-v1",
        "emit_artifacts": False,
        "sample_path_limit": 0,
    }
    raw = polymarket_probability_native.run_cpu_json(
        json.dumps(probability_input.to_json_dict(), allow_nan=False, sort_keys=True),
        json.dumps(config, allow_nan=False, sort_keys=True),
    )
    run: dict[str, Any] = json.loads(raw)
    return ProbabilityOutput(
        state_id=str(run["state_id"]),
        asof_ts=probability_input.asof_ts,
        p_finish=float(run["p_finish"]),
        p_no_touch=float(run["p_no_touch"]),
        z_path=float(run["z_path"]),
        model_version=str(run["model_version"]),
        seed=int(run["seed"]),
        diagnostics={
            **dict(run["diagnostics"]),
            "backend": run["backend"],
            "native_available": True,
        },
    )
```

- [ ] **Step 6: Add fallback tests**

Create `tests/probability/test_native_probability.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from polymarket_engine.probability.native import run_native_or_python
from polymarket_engine.probability.schema import ProbabilityInput


def _input() -> ProbabilityInput:
    return ProbabilityInput(
        state_id="state",
        asof_ts=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        asset="BTC",
        side="UP",
        comparison_operator=">",
        seconds_left=60.0,
        settlement_price=100.0,
        threshold=100.0,
        sigma_tau=0.01,
        executable_price=0.5,
        source_age_ms=10,
        book_age_ms=10,
        z_path=0.0,
    )


def test_native_wrapper_returns_probability_output() -> None:
    output = run_native_or_python(_input(), path_count=512, steps=8, seed=123)

    assert 0.0 <= output.p_finish <= 1.0
    assert 0.0 <= output.p_no_touch <= 1.0
    assert output.seed == 123
```

- [ ] **Step 7: Run Python tests**

Run:

```bash
chmod +x scripts/build_probability_native.sh
uv run pytest tests/probability/test_native_probability.py -q
uv run ruff check src/polymarket_engine/probability/native.py tests/probability/test_native_probability.py
```

Expected:

```text
1 passed
```

- [ ] **Step 8: Commit**

```bash
git add rust/Cargo.toml rust/crates/polymarket-probability-native src/polymarket_engine/probability/native.py tests/probability/test_native_probability.py scripts/build_probability_native.sh
git commit -m "Add optional native probability bridge"
```

## Task 5: Wire Runtime Probability Selection And Status

**Files:**

- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `src/polymarket_engine/cli.py`
- Modify: `tests/test_runtime_api.py`

- [ ] **Step 1: Add backend selection test**

Append to `tests/test_runtime_api.py`:

```python
def test_runtime_probabilities_marks_backend_in_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    monkeypatch.setenv("POLYMARKET_PROBABILITY_BACKEND", "python_numpy")
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["diagnostics"]["backend"] == "python_numpy"
```

- [ ] **Step 2: Use native wrapper in runtime**

Modify `src/polymarket_engine/probability/runtime.py` imports:

```python
import os
from polymarket_engine.probability.native import run_native_or_python
```

Modify `_compute_and_persist_rows(...)` to call:

```python
backend = os.environ.get("POLYMARKET_PROBABILITY_BACKEND", "cpu_rayon")
output = run_native_or_python(
    runtime_input.probability_input,
    path_count=DEFAULT_PROBABILITY_PATH_COUNT,
    steps=_steps_for_input(runtime_input.probability_input),
    seed=_seed_for_input(runtime_input.probability_input),
    backend=backend,
)
output = ProbabilityOutput(
    state_id=output.state_id,
    asof_ts=output.asof_ts,
    p_finish=output.p_finish,
    p_no_touch=output.p_no_touch,
    z_path=output.z_path,
    model_version=output.model_version,
    seed=output.seed,
    diagnostics={**output.diagnostics, "backend": backend},
)
```

- [ ] **Step 3: Run runtime tests**

Run:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_probabilities_marks_backend_in_diagnostics -q
```

If the exact test selector is unavailable because the surrounding test file was reorganized, run:

```bash
uv run pytest tests/test_runtime_api.py -k "runtime_probabilities and backend" -q
```

Expected:

```text
1 passed
```

- [ ] **Step 4: Commit**

```bash
git add src/polymarket_engine/probability/runtime.py src/polymarket_engine/cli.py tests/test_runtime_api.py
git commit -m "Select probability backend at runtime"
```

## Task 6: Add CUDA Backend Crate And Smoke Kernel

**Files:**

- Modify: `rust/Cargo.toml`
- Create: `rust/crates/polymarket-probability-cuda/Cargo.toml`
- Create: `rust/crates/polymarket-probability-cuda/src/lib.rs`
- Create: `rust/crates/polymarket-probability-cuda/kernels/smoke.cu`
- Create: `rust/crates/polymarket-probability-cuda/tests/smoke.rs`

- [ ] **Step 1: Add CUDA crate only to workspace**

Modify `rust/Cargo.toml`:

```toml
members = [
    "crates/polymarket-runtime-types",
    "crates/polymarket-live-probe",
    "crates/polymarket-cockpit-tui",
    "crates/polymarket-probability-core",
    "crates/polymarket-probability-native",
    "crates/polymarket-probability-cuda",
]

[workspace.dependencies]
cudarc = { version = "0.13", default-features = false, features = ["std", "driver", "nvrtc", "cuda-version-from-build-system", "dynamic-loading"] }
```

- [ ] **Step 2: Add CUDA crate manifest**

Create `rust/crates/polymarket-probability-cuda/Cargo.toml`:

```toml
[package]
name = "polymarket-probability-cuda"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true

[dependencies]
anyhow.workspace = true
cudarc.workspace = true
polymarket-probability-core = { path = "../polymarket-probability-core" }
serde_json.workspace = true
```

- [ ] **Step 3: Add smoke kernel**

Create `rust/crates/polymarket-probability-cuda/kernels/smoke.cu`:

```cuda
extern "C" __global__ void add_one(double *out, const double *inp, unsigned long long n) {
    unsigned long long i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) {
        out[i] = inp[i] + 1.0;
    }
}
```

- [ ] **Step 4: Add smoke wrapper**

Create `rust/crates/polymarket-probability-cuda/src/lib.rs`:

```rust
use anyhow::Result;
use cudarc::driver::{CudaContext, LaunchConfig, PushKernelArg};
use cudarc::nvrtc::compile_ptx;

pub fn cuda_smoke_add_one(values: &[f64]) -> Result<Vec<f64>> {
    let ctx = CudaContext::new(0)?;
    let stream = ctx.default_stream();
    let ptx = compile_ptx(include_str!("../kernels/smoke.cu"))?;
    let module = ctx.load_module(ptx)?;
    let function = module.load_function("add_one")?;
    let inp = stream.clone_htod(values)?;
    let mut out = stream.alloc_zeros::<f64>(values.len())?;
    let mut builder = stream.launch_builder(&function);
    builder.arg(&mut out);
    builder.arg(&inp);
    builder.arg(&(values.len() as u64));
    unsafe {
        builder.launch(LaunchConfig::for_num_elems(values.len() as u32))?;
    }
    Ok(stream.clone_dtoh(&out)?)
}
```

- [ ] **Step 5: Add ignored GPU smoke test**

Create `rust/crates/polymarket-probability-cuda/tests/smoke.rs`:

```rust
#[test]
#[ignore = "requires THEPC CUDA runtime"]
fn cuda_smoke_adds_one() {
    let out = polymarket_probability_cuda::cuda_smoke_add_one(&[1.0, 2.0, 3.0]).unwrap();
    assert_eq!(out, vec![2.0, 3.0, 4.0]);
}
```

- [ ] **Step 6: Run non-GPU checks on Mac**

Run:

```bash
cd rust
cargo test -p polymarket-probability-core
```

Expected:

```text
test result: ok
```

- [ ] **Step 7: Run GPU smoke on THEPC WSL**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket/rust && cargo test -p polymarket-probability-cuda --test smoke -- --ignored --nocapture"'
```

Expected:

```text
cuda_smoke_adds_one ... ok
```

- [ ] **Step 8: Commit**

```bash
git add rust/Cargo.toml rust/crates/polymarket-probability-cuda
git commit -m "Add CUDA probability smoke backend"
```

## Task 7: Implement CUDA Monte Carlo Backend

**Files:**

- Create: `rust/crates/polymarket-probability-cuda/kernels/monte_carlo.cu`
- Modify: `rust/crates/polymarket-probability-cuda/src/lib.rs`
- Create: `rust/crates/polymarket-probability-cuda/tests/monte_carlo.rs`

- [ ] **Step 1: Add CUDA Monte Carlo kernel**

Create `rust/crates/polymarket-probability-cuda/kernels/monte_carlo.cu`:

```cuda
#include <curand_kernel.h>

extern "C" __global__ void score_lognormal_paths(
    unsigned long long seed,
    unsigned long long path_count,
    unsigned int steps,
    double settlement_price,
    double threshold,
    double per_step_sigma,
    int comparison_operator,
    unsigned long long *terminal_wins,
    unsigned long long *no_touch_wins
) {
    unsigned long long path_id = blockIdx.x * blockDim.x + threadIdx.x;
    if (path_id >= path_count) {
        return;
    }

    curandStatePhilox4_32_10_t rng;
    curand_init(seed, path_id, 0, &rng);

    double cumulative_log_return = 0.0;
    double price = settlement_price;
    bool no_touch = true;

    for (unsigned int step = 0; step < steps; ++step) {
        double shock = curand_normal_double(&rng) * per_step_sigma;
        cumulative_log_return += shock;
        price = settlement_price * exp(cumulative_log_return);

        bool satisfies = false;
        if (comparison_operator == 0) {
            satisfies = price > threshold;
        } else if (comparison_operator == 1) {
            satisfies = price >= threshold;
        } else if (comparison_operator == 2) {
            satisfies = price < threshold;
        } else {
            satisfies = price <= threshold;
        }
        no_touch = no_touch && satisfies;
    }

    bool terminal = false;
    if (comparison_operator == 0) {
        terminal = price > threshold;
    } else if (comparison_operator == 1) {
        terminal = price >= threshold;
    } else if (comparison_operator == 2) {
        terminal = price < threshold;
    } else {
        terminal = price <= threshold;
    }

    if (terminal) {
        atomicAdd(terminal_wins, 1ULL);
    }
    if (no_touch) {
        atomicAdd(no_touch_wins, 1ULL);
    }
}
```

- [ ] **Step 2: Add CUDA backend wrapper**

Modify `rust/crates/polymarket-probability-cuda/src/lib.rs` to add:

```rust
use std::time::Instant;

use cudarc::driver::{CudaContext, LaunchConfig, PushKernelArg};
use polymarket_probability_core::backend::SimulationBackend;
use polymarket_probability_core::schema::{
    ComparisonOperator, ProbabilityInput, SimulationArtifacts, SimulationBackendKind,
    SimulationConfig, SimulationRun,
};
use serde_json::json;

pub struct CudaBackend;

impl SimulationBackend for CudaBackend {
    fn run(&self, input: &ProbabilityInput, config: &SimulationConfig) -> Result<SimulationRun> {
        let started = Instant::now();
        let ctx = CudaContext::new(0)?;
        let stream = ctx.default_stream();
        let ptx = compile_ptx(include_str!("../kernels/monte_carlo.cu"))?;
        let module = ctx.load_module(ptx)?;
        let function = module.load_function("score_lognormal_paths")?;
        let mut terminal_wins = stream.alloc_zeros::<u64>(1)?;
        let mut no_touch_wins = stream.alloc_zeros::<u64>(1)?;
        let per_step_sigma = input.sigma_tau / (config.steps as f64).sqrt();
        let operator = comparison_operator_id(input.comparison_operator);

        let mut builder = stream.launch_builder(&function);
        builder.arg(&config.seed);
        builder.arg(&(config.path_count as u64));
        builder.arg(&(config.steps as u32));
        builder.arg(&input.settlement_price);
        builder.arg(&input.threshold);
        builder.arg(&per_step_sigma);
        builder.arg(&operator);
        builder.arg(&mut terminal_wins);
        builder.arg(&mut no_touch_wins);
        unsafe {
            builder.launch(LaunchConfig::for_num_elems(config.path_count as u32))?;
        }

        let terminal: Vec<u64> = stream.clone_dtoh(&terminal_wins)?;
        let no_touch: Vec<u64> = stream.clone_dtoh(&no_touch_wins)?;

        Ok(SimulationRun {
            state_id: input.state_id.clone(),
            asof_ts: input.asof_ts,
            p_finish: terminal[0] as f64 / config.path_count as f64,
            p_no_touch: no_touch[0] as f64 / config.path_count as f64,
            z_path: input.z_path,
            model_version: config.model_version.clone(),
            seed: config.seed,
            backend: SimulationBackendKind::Cuda,
            diagnostics: json!({
                "path_count": config.path_count,
                "steps": config.steps,
                "elapsed_ms": started.elapsed().as_secs_f64() * 1000.0,
                "per_step_sigma": per_step_sigma,
                "gpu": "cuda",
            }),
            artifacts: SimulationArtifacts::default(),
        })
    }
}

fn comparison_operator_id(operator: ComparisonOperator) -> i32 {
    match operator {
        ComparisonOperator::Greater => 0,
        ComparisonOperator::GreaterEqual => 1,
        ComparisonOperator::Less => 2,
        ComparisonOperator::LessEqual => 3,
    }
}
```

- [ ] **Step 3: Add GPU determinism and range tests**

Create `rust/crates/polymarket-probability-cuda/tests/monte_carlo.rs`:

```rust
use chrono::{TimeZone, Utc};
use polymarket_probability_core::backend::SimulationBackend;
use polymarket_probability_core::schema::{
    Asset, ComparisonOperator, ProbabilityInput, Side, SimulationBackendKind, SimulationConfig,
};
use polymarket_probability_cuda::CudaBackend;

fn input() -> ProbabilityInput {
    ProbabilityInput {
        state_id: "state".to_string(),
        asof_ts: Utc.with_ymd_and_hms(2026, 6, 5, 12, 0, 0).unwrap(),
        asset: Asset::BTC,
        side: Side::UP,
        comparison_operator: ComparisonOperator::Greater,
        seconds_left: 60.0,
        settlement_price: 100.0,
        threshold: 100.0,
        sigma_tau: 0.01,
        executable_price: 0.5,
        source_age_ms: 10,
        book_age_ms: 10,
        z_path: 0.0,
    }
}

fn config(seed: u64) -> SimulationConfig {
    SimulationConfig {
        path_count: 65_536,
        steps: 32,
        seed,
        backend: SimulationBackendKind::Cuda,
        model_version: "cuda-test-v1".to_string(),
        emit_artifacts: false,
        sample_path_limit: 0,
    }
}

#[test]
#[ignore = "requires THEPC CUDA runtime"]
fn cuda_backend_is_seed_deterministic() {
    let backend = CudaBackend;
    let first = backend.run(&input(), &config(123)).unwrap();
    let second = backend.run(&input(), &config(123)).unwrap();
    assert_eq!(first.p_finish, second.p_finish);
    assert_eq!(first.p_no_touch, second.p_no_touch);
}

#[test]
#[ignore = "requires THEPC CUDA runtime"]
fn cuda_backend_outputs_probabilities() {
    let backend = CudaBackend;
    let run = backend.run(&input(), &config(456)).unwrap();
    assert!((0.0..=1.0).contains(&run.p_finish));
    assert!((0.0..=1.0).contains(&run.p_no_touch));
    assert_eq!(run.backend, SimulationBackendKind::Cuda);
}
```

- [ ] **Step 4: Run CUDA tests on THEPC**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket/rust && cargo test -p polymarket-probability-cuda -- --ignored --nocapture"'
```

Expected:

```text
cuda_backend_is_seed_deterministic ... ok
cuda_backend_outputs_probabilities ... ok
```

- [ ] **Step 5: Commit**

```bash
git add rust/crates/polymarket-probability-cuda
git commit -m "Add CUDA Monte Carlo backend"
```

## Task 8: Add Simulation Artifacts For Visualization

**Files:**

- Modify: `rust/crates/polymarket-probability-core/src/cpu.rs`
- Create: `rust/crates/polymarket-probability-core/src/artifacts.rs`
- Modify: `rust/crates/polymarket-probability-core/src/lib.rs`
- Test: `rust/crates/polymarket-probability-core/src/artifacts.rs`

- [ ] **Step 1: Add artifact helpers**

Create `rust/crates/polymarket-probability-core/src/artifacts.rs`:

```rust
use crate::schema::{HistogramBin, PercentilePoint};

pub fn terminal_histogram(terminals: &[f64], bins: usize) -> Vec<HistogramBin> {
    if terminals.is_empty() || bins == 0 {
        return Vec::new();
    }
    let min = terminals.iter().copied().fold(f64::INFINITY, f64::min);
    let max = terminals.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if min == max {
        return vec![HistogramBin {
            min_price: min,
            max_price: max,
            count: terminals.len(),
        }];
    }
    let width = (max - min) / bins as f64;
    let mut counts = vec![0usize; bins];
    for value in terminals {
        let mut index = ((*value - min) / width).floor() as usize;
        if index >= bins {
            index = bins - 1;
        }
        counts[index] += 1;
    }
    counts
        .into_iter()
        .enumerate()
        .map(|(index, count)| HistogramBin {
            min_price: min + width * index as f64,
            max_price: min + width * (index + 1) as f64,
            count,
        })
        .collect()
}

pub fn percentile_points(paths: &[Vec<f64>]) -> Vec<PercentilePoint> {
    if paths.is_empty() {
        return Vec::new();
    }
    let steps = paths[0].len();
    (0..steps)
        .map(|step| {
            let mut values: Vec<f64> = paths.iter().map(|path| path[step]).collect();
            values.sort_by(|a, b| a.total_cmp(b));
            PercentilePoint {
                step,
                p05: percentile(&values, 0.05),
                p25: percentile(&values, 0.25),
                p50: percentile(&values, 0.50),
                p75: percentile(&values, 0.75),
                p95: percentile(&values, 0.95),
            }
        })
        .collect()
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    let index = ((sorted.len() - 1) as f64 * p).round() as usize;
    sorted[index]
}
```

- [ ] **Step 2: Export artifact helpers**

Modify `rust/crates/polymarket-probability-core/src/lib.rs`:

```rust
pub mod artifacts;
pub mod backend;
pub mod cpu;
pub mod schema;
pub mod scoring;
```

- [ ] **Step 3: Add artifact tests**

Append to `rust/crates/polymarket-probability-core/src/artifacts.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn histogram_counts_all_terminals() {
        let bins = terminal_histogram(&[1.0, 2.0, 3.0, 4.0], 2);
        assert_eq!(bins.iter().map(|bin| bin.count).sum::<usize>(), 4);
    }

    #[test]
    fn percentile_points_emit_one_row_per_step() {
        let paths = vec![vec![100.0, 101.0], vec![100.0, 99.0], vec![100.0, 102.0]];
        let points = percentile_points(&paths);
        assert_eq!(points.len(), 2);
        assert_eq!(points[0].p50, 100.0);
    }
}
```

- [ ] **Step 4: Wire artifacts to CPU backend when requested**

Modify `rust/crates/polymarket-probability-core/src/cpu.rs` so `emit_artifacts = true` stores:

```rust
SimulationArtifacts {
    percentile_paths: percentile_points(&sample_paths),
    sample_paths,
    terminal_histogram: terminal_histogram(&terminal_prices, 40),
}
```

Keep sample paths bounded by `sample_path_limit`.

- [ ] **Step 5: Run tests**

Run:

```bash
cd rust
cargo test -p polymarket-probability-core
```

Expected:

```text
test result: ok
```

- [ ] **Step 6: Commit**

```bash
git add rust/crates/polymarket-probability-core
git commit -m "Add Monte Carlo visualization artifacts"
```

## Task 9: Persist Compact Outputs And Research Artifacts

**Files:**

- Modify: `src/polymarket_engine/storage/schema.sql`
- Modify: `src/polymarket_engine/storage/duckdb_store.py`
- Modify: `tests/storage/test_normalized_writes.py`

- [ ] **Step 1: Add storage tests**

Add to `tests/storage/test_normalized_writes.py`:

```python
def test_insert_probability_output_persists_simulation_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    probability_input = ProbabilityInput.from_decision_state(state)
    output = ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=0.55,
        p_no_touch=0.44,
        z_path=probability_input.z_path,
        model_version="rust-cpu-rayon-v1",
        seed=123,
        diagnostics={
            "backend": "cpu_rayon",
            "path_count": 1024,
            "artifact_id": "artifact-1",
        },
    )

    store.insert_probability_output(
        output_id="prob-1",
        probability_input=probability_input,
        output=output,
    )

    with duckdb.connect(str(db_path)) as conn:
        row = conn.execute(
            "select diagnostics_json from features.probability_outputs where output_id = ?",
            ["prob-1"],
        ).fetchone()

    assert '"backend":"cpu_rayon"' in row[0]
```

- [ ] **Step 2: Add optional artifact table**

Modify `src/polymarket_engine/storage/schema.sql`:

```sql
create table if not exists features.simulation_artifacts (
    artifact_id varchar primary key,
    output_id varchar not null,
    state_id varchar not null,
    asof_ts timestamp not null,
    model_version varchar not null,
    backend varchar not null,
    artifact_json varchar not null,
    created_at timestamp not null default current_timestamp
);
```

- [ ] **Step 3: Add store method**

Modify `src/polymarket_engine/storage/duckdb_store.py`:

```python
def insert_simulation_artifact(
    self,
    *,
    artifact_id: str,
    output_id: str,
    state_id: str,
    asof_ts: datetime,
    model_version: str,
    backend: str,
    artifact: Mapping[str, Any],
) -> None:
    artifact_json = json.dumps(dict(artifact), sort_keys=True, separators=(",", ":"), allow_nan=False)
    with self.connect() as conn:
        conn.execute(
            """
            insert or replace into features.simulation_artifacts (
                artifact_id, output_id, state_id, asof_ts, model_version, backend, artifact_json
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [artifact_id, output_id, state_id, asof_ts, model_version, backend, artifact_json],
        )
```

- [ ] **Step 4: Run storage tests**

Run:

```bash
uv run pytest tests/storage/test_normalized_writes.py -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/storage/schema.sql src/polymarket_engine/storage/duckdb_store.py tests/storage/test_normalized_writes.py
git commit -m "Persist Monte Carlo simulation artifacts"
```

## Task 10: Add Runtime API For Cached MC Summary And Visual Artifacts

**Files:**

- Modify: `src/polymarket_engine/runtime_api.py`
- Modify: `tests/test_runtime_api.py`

- [ ] **Step 1: Add API tests**

Add to `tests/test_runtime_api.py`:

```python
def test_runtime_monte_carlo_status_reads_cached_probability_outputs(tmp_path: Path) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    probability_input = ProbabilityInput.from_decision_state(state)
    output = ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=0.61,
        p_no_touch=0.52,
        z_path=probability_input.z_path,
        model_version="rust-cpu-rayon-v1",
        seed=123,
        diagnostics={"backend": "cpu_rayon", "path_count": 1024},
    )
    store.insert_probability_output(
        output_id="prob-1",
        probability_input=probability_input,
        output=output,
    )
    app = create_app(status_path=tmp_path / "missing-status.json", duckdb_path=db_path)

    response = TestClient(app).get("/api/runtime/monte-carlo/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["rows"][0]["p_finish"] == pytest.approx(0.61)
    assert payload["rows"][0]["backend"] == "cpu_rayon"
```

- [ ] **Step 2: Add status route**

Modify `src/polymarket_engine/runtime_api.py`:

```python
@app.get("/api/runtime/monte-carlo/status")
def monte_carlo_status(limit: int = 8) -> dict[str, Any]:
    rows = probability_runtime.latest_probability_output_rows(
        duckdb_path=settings.duckdb_path,
        limit=limit,
        active_only=True,
    )
    return {
        "ok": True,
        "state": "OK",
        "rows": [
            {
                "contract": row["contract"],
                "p_finish": row["p_finish"],
                "p_no_touch": row["p_no_touch"],
                "z_path": row["z_path"],
                "sigma_tau": row["sigma_tau"],
                "backend": row.get("diagnostics", {}).get("backend"),
                "path_count": row.get("diagnostics", {}).get("path_count"),
                "model_version": row["model_version"],
                "flags": row["flags"],
            }
            for row in rows
        ],
    }
```

- [ ] **Step 3: Add artifact route**

Add:

```python
@app.get("/api/research/simulation-artifacts/{artifact_id}")
def simulation_artifact(artifact_id: str) -> dict[str, Any]:
    artifact = read_simulation_artifact(settings.duckdb_path, artifact_id=artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="simulation artifact missing")
    return artifact
```

Implement `read_simulation_artifact(...)` next to other runtime API DuckDB helpers. It should open DuckDB read-only with retry, fetch one row from `features.simulation_artifacts`, parse `artifact_json`, and return `{"ok": True, ...}`.

- [ ] **Step 4: Run API tests**

Run:

```bash
uv run pytest tests/test_runtime_api.py -k "monte_carlo or simulation_artifact" -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/runtime_api.py tests/test_runtime_api.py
git commit -m "Expose cached Monte Carlo runtime status"
```

## Task 11: Add TUI Monte Carlo Summary

**Files:**

- Modify: `rust/crates/polymarket-cockpit-tui/src/client.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
- Test: existing Rust TUI tests or new module-local tests if present.

- [ ] **Step 1: Add client/state types**

Add these structs to the TUI state/client model:

```rust
#[derive(Clone, Debug, Deserialize, Default)]
pub struct MonteCarloStatus {
    pub ok: bool,
    pub state: String,
    pub rows: Vec<MonteCarloRow>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct MonteCarloRow {
    pub contract: String,
    pub p_finish: Option<f64>,
    pub p_no_touch: Option<f64>,
    pub z_path: Option<f64>,
    pub sigma_tau: Option<f64>,
    pub backend: Option<String>,
    pub path_count: Option<u64>,
    pub model_version: Option<String>,
    pub flags: Vec<String>,
}
```

- [ ] **Step 2: Poll status slower than market stream**

Update the TUI event loop so `/api/runtime/monte-carlo/status` is fetched with the auxiliary polling cadence, not every market repaint.

- [ ] **Step 3: Render compact rows only**

Render columns:

```text
Contract | p_finish | p_no_touch | z_path | sigma_tau | backend | paths | age/flags
```

Do not render path clouds or histograms in terminal.

- [ ] **Step 4: Verify TUI build**

Run:

```bash
cd rust
cargo test -p polymarket-cockpit-tui
cargo build -p polymarket-cockpit-tui --release
```

Expected:

```text
test result: ok
Finished release
```

- [ ] **Step 5: Commit**

```bash
git add rust/crates/polymarket-cockpit-tui
git commit -m "Show Monte Carlo summary in TUI"
```

## Task 12: Add Browser Research Visualization

**Files:**

- Modify: `ui/package.json`
- Modify: `ui/src/App.tsx`
- Modify: `ui/src/styles.css`
- Create: `ui/src/api.ts`
- Create: `ui/src/MonteCarloView.tsx`

- [ ] **Step 1: Add chart dependency**

Run:

```bash
cd ui
npm install recharts
```

- [ ] **Step 2: Add API client**

Create `ui/src/api.ts`:

```typescript
export type MonteCarloRow = {
  contract: string;
  p_finish: number | null;
  p_no_touch: number | null;
  z_path: number | null;
  sigma_tau: number | null;
  backend: string | null;
  path_count: number | null;
  model_version: string | null;
  flags: string[];
};

export type MonteCarloStatus = {
  ok: boolean;
  state: string;
  rows: MonteCarloRow[];
};

export async function fetchMonteCarloStatus(): Promise<MonteCarloStatus> {
  const response = await fetch("/api/runtime/monte-carlo/status");
  if (!response.ok) {
    throw new Error(`Monte Carlo status failed: ${response.status}`);
  }
  return response.json();
}
```

- [ ] **Step 3: Add view component**

Create `ui/src/MonteCarloView.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fetchMonteCarloStatus, type MonteCarloRow } from "./api";

export function MonteCarloView() {
  const [rows, setRows] = useState<MonteCarloRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const payload = await fetchMonteCarloStatus();
        if (alive) {
          setRows(payload.rows);
          setError(null);
        }
      } catch (exc) {
        if (alive) {
          setError(exc instanceof Error ? exc.message : String(exc));
        }
      }
    };
    load();
    const id = window.setInterval(load, 3000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  return (
    <section className="mc-panel">
      <header>
        <p>Monte Carlo</p>
        <h1>Probability Research</h1>
      </header>
      {error ? <div className="error">{error}</div> : null}
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="contract" />
          <YAxis domain={[0, 1]} />
          <Tooltip />
          <Bar dataKey="p_finish" fill="#42a5f5" />
          <Bar dataKey="p_no_touch" fill="#26a69a" />
        </BarChart>
      </ResponsiveContainer>
      <div className="mc-grid">
        {rows.map((row) => (
          <article key={row.contract}>
            <strong>{row.contract}</strong>
            <span>backend {row.backend ?? "-"}</span>
            <span>paths {row.path_count ?? "-"}</span>
            <span>flags {row.flags.join(", ")}</span>
          </article>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Render the view**

Modify `ui/src/App.tsx`:

```tsx
import { MonteCarloView } from "./MonteCarloView";

export function App() {
  return (
    <main>
      <MonteCarloView />
    </main>
  );
}
```

- [ ] **Step 5: Add basic styling**

Modify `ui/src/styles.css`:

```css
body {
  margin: 0;
  background: #0d1117;
  color: #e6edf3;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

main {
  max-width: 1180px;
  margin: 0 auto;
  padding: 24px;
}

.mc-panel {
  display: grid;
  gap: 16px;
}

.mc-panel header p {
  color: #7dd3fc;
  margin: 0;
  text-transform: uppercase;
  font-size: 12px;
  letter-spacing: 0;
}

.mc-panel h1 {
  margin: 4px 0 0;
  font-size: 28px;
  letter-spacing: 0;
}

.mc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.mc-grid article {
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 12px;
  display: grid;
  gap: 6px;
  background: #161b22;
}

.error {
  color: #ffb4a8;
}
```

- [ ] **Step 6: Build UI**

Run:

```bash
cd ui
npm run build
```

Expected:

```text
built
```

- [ ] **Step 7: Commit**

```bash
git add ui/package.json ui/package-lock.json ui/src
git commit -m "Add Monte Carlo research dashboard"
```

## Task 13: Benchmark CPU Versus GPU On THEPC

**Files:**

- Create: `scripts/benchmark_monte_carlo_backends.sh`
- Create at runtime: `docs/reports/monte-carlo-backend-benchmark-2026-06-05.md`

- [ ] **Step 1: Add benchmark script**

Create `scripts/benchmark_monte_carlo_backends.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT="${REPO_ROOT}/docs/reports/monte-carlo-backend-benchmark-2026-06-05.md"
mkdir -p "${REPO_ROOT}/docs/reports"

{
  echo "# Monte Carlo Backend Benchmark - 2026-06-05"
  echo
  echo "Host: THEPC WSL2 Ubuntu"
  echo
  echo "## GPU"
  echo
  nvidia-smi --query-gpu=name,driver_version,compute_cap,memory.total --format=csv
  echo
  echo "## Rust"
  echo
  rustc --version
  cargo --version
  echo
  echo "## CPU Rayon"
  echo
  (cd "${REPO_ROOT}/rust" && cargo test -p polymarket-probability-core cpu_backend_outputs_probabilities --release -- --nocapture)
  echo
  echo "## CUDA"
  echo
  (cd "${REPO_ROOT}/rust" && cargo test -p polymarket-probability-cuda cuda_backend_outputs_probabilities --release -- --ignored --nocapture)
  echo
  echo "## Decision Rules"
  echo
  echo "- Use CPU for small live runs if CUDA launch overhead dominates."
  echo "- Use CUDA for large visualization runs, backtests, calibration sweeps, and generator ensembles."
  echo "- Keep TUI/API reading cached outputs only."
} | tee "${REPORT}"

echo "Wrote ${REPORT}"
```

- [ ] **Step 2: Run benchmark on THEPC**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket && chmod +x scripts/benchmark_monte_carlo_backends.sh && ./scripts/benchmark_monte_carlo_backends.sh"'
```

- [ ] **Step 3: Write benchmark report**

The benchmark script writes `docs/reports/monte-carlo-backend-benchmark-2026-06-05.md` automatically. Inspect it:

```bash
sed -n '1,220p' docs/reports/monte-carlo-backend-benchmark-2026-06-05.md
```

Expected: the report contains GPU identity, Rust versions, CPU backend output, CUDA backend output, and decision rules.

- [ ] **Step 4: Commit**

```bash
git add scripts/benchmark_monte_carlo_backends.sh docs/reports/monte-carlo-backend-benchmark-2026-06-05.md
git commit -m "Benchmark Monte Carlo backends on THEPC"
```

## Risk Register

- CUDA toolkit on WSL Ubuntu 26.04 may need the NVIDIA WSL repository rather than the generic Ubuntu repository. The install script uses the WSL-specific keyring.
- `cudarc` feature names can drift. Pin the exact working version after the smoke test passes.
- CPU and GPU RNG streams will not be path-identical. Require deterministic repeated runs per backend and statistical parity, not exact path equality.
- GPU launch overhead may be slower than CPU for small live workloads. The backend selector must use CPU for small live runs if benchmarks prove that.
- Browser visual artifacts can grow fast. Store percentiles, histograms, and bounded sample paths, not every path.
- Existing `cpp/probability_core` is a placeholder. Do not extend it unless the team explicitly reverses the Rust decision.

## Execution Order

1. Task 1: THEPC WSL GPU preflight.
2. Task 2: Rust core schema.
3. Task 3: CPU backend.
4. Task 4: Python bridge.
5. Task 5: Runtime backend selection.
6. Task 6: CUDA smoke backend.
7. Task 7: CUDA Monte Carlo backend.
8. Task 8: visualization artifacts.
9. Task 9: storage.
10. Task 10: API.
11. Task 11: TUI summary.
12. Task 12: browser research visualization.
13. Task 13: benchmark.

## Self-Review

- Spec coverage: The plan covers THEPC setup, native Rust CPU core, optional GPU backend, Python bridge, runtime cache, storage, TUI, browser UI, and benchmarking.
- Placeholder scan: No `TBD` or `TODO` markers remain. Benchmark evidence is generated by `scripts/benchmark_monte_carlo_backends.sh` instead of requiring manual timing placeholders.
- Boundary check: No task adds trading, signing, wallet access, or TUI-triggered heavy simulation.
- Type consistency: `ProbabilityInput`, `SimulationConfig`, and `SimulationRun` names match across core, native bridge, runtime, and tests.
- Scope check: This is large but still one coherent subsystem: Monte Carlo probability engine plus cached visualization surfaces. Execution should use subagents.
