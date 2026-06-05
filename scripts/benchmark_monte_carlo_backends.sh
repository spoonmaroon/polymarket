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
  echo "## CPU Rayon"
  (
    cd "${REPO_ROOT}/rust"
    cargo test -p polymarket-probability-core \
      cpu_backend_outputs_probability_range_and_diagnostics \
      --release -- --nocapture
  )
  echo
  echo "## CUDA"
  (
    cd "${REPO_ROOT}/rust"
    cargo test -p polymarket-probability-cuda \
      cuda_backend_outputs_probability_range_backend_and_diagnostics \
      --release -- --ignored --nocapture
  )
  echo
  echo "## Decision Rules"
  echo "- Use CPU for small live runs if CUDA launch overhead dominates."
  echo "- Use CUDA for large visualization runs, backtests, calibration sweeps, and generator ensembles."
  echo "- Keep TUI/API reading cached outputs only."
} | tee "${REPORT}"

echo "Wrote ${REPORT}"
