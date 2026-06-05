#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT="${REPO_ROOT}/docs/reports/monte-carlo-backend-benchmark-2026-06-05.md"
NVIDIA_SMI="${NVIDIA_SMI:-}"

if [[ -z "${NVIDIA_SMI}" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    NVIDIA_SMI="$(command -v nvidia-smi)"
  elif [[ -x /usr/lib/wsl/lib/nvidia-smi ]]; then
    NVIDIA_SMI="/usr/lib/wsl/lib/nvidia-smi"
  fi
fi

mkdir -p "${REPO_ROOT}/docs/reports"

{
  echo "# Monte Carlo Backend Benchmark - 2026-06-05"
  echo
  echo "Host: THEPC WSL2 Ubuntu"
  echo
  echo "## GPU"
  if [[ -n "${NVIDIA_SMI}" ]]; then
    "${NVIDIA_SMI}" --query-gpu=name,driver_version,compute_cap,memory.total --format=csv
  else
    echo "MISSING: nvidia-smi"
  fi
  echo
  echo "## CUDA Toolkit"
  if command -v nvcc >/dev/null 2>&1; then
    nvcc --version
  else
    echo "MISSING: nvcc"
  fi
  echo
  echo "## Rust"
  rustc --version
  cargo --version
  echo
  echo "## Build"
  (
    cd "${REPO_ROOT}/rust"
    cargo build --release -p polymarket-probability-core --example benchmark_cpu
    cargo build --release -p polymarket-probability-cuda --example benchmark_cuda
  )
  echo
  echo "## Timed Runs"
  echo
  echo "| case | backend | paths | steps | iterations | avg_ms | min_ms | median_ms | max_ms | p_finish | p_no_touch |"
  echo "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
  (
    cd "${REPO_ROOT}/rust"
    target/release/examples/benchmark_cpu live-small 8192 64 5
    target/release/examples/benchmark_cuda live-small 8192 64 5
    target/release/examples/benchmark_cpu visual-large 100000 300 3
    target/release/examples/benchmark_cuda visual-large 100000 300 3
  )
  echo
  echo "## Decision Rules"
  echo "- Use CPU for small live runs if CUDA launch overhead dominates."
  echo "- Use CUDA for large visualization runs, backtests, calibration sweeps, and generator ensembles."
  echo "- Keep TUI/API reading cached outputs only."
} | tee "${REPORT}"

echo "Wrote ${REPORT}"
