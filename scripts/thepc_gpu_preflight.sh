#!/usr/bin/env bash
set -euo pipefail

echo "== OS =="
if [[ -r /etc/os-release ]]; then
  sed -n '1,8p' /etc/os-release
elif command -v sw_vers >/dev/null 2>&1; then
  sw_vers
else
  echo "MISSING: /etc/os-release"
fi
uname -a

echo
echo "== GPU =="
nvidia_smi=""
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia_smi="$(command -v nvidia-smi)"
elif [[ -x /usr/lib/wsl/lib/nvidia-smi ]]; then
  nvidia_smi="/usr/lib/wsl/lib/nvidia-smi"
fi

if [[ -n "${nvidia_smi}" ]]; then
  echo "${nvidia_smi}"
  if ! "${nvidia_smi}" --query-gpu=name,driver_version,compute_cap,memory.total --format=csv; then
    echo "WARNING: nvidia-smi query failed"
  fi
else
  echo "MISSING: nvidia-smi"
fi

echo
echo "== CUDA Toolkit =="
if command -v nvcc >/dev/null 2>&1; then
  nvcc --version
  arch_list="$(nvcc --list-gpu-arch 2>&1 || true)"
  printf '%s\n' "${arch_list}" | tail -n 30
  if printf '%s\n' "${arch_list}" | grep -Eq 'compute_120|sm_120'; then
    echo "CUDA toolkit supports RTX 5060 Ti compute capability 12.0"
  else
    echo "WARNING: nvcc does not list compute_120/sm_120"
  fi
else
  echo "MISSING: nvcc"
fi

echo
echo "== Rust =="
if command -v cargo >/dev/null 2>&1; then
  rustc --version
  cargo --version
else
  echo "MISSING: cargo"
fi

echo
echo "== Python =="
if command -v python3 >/dev/null 2>&1; then
  python3 --version
else
  echo "MISSING: python3"
fi

echo
echo "== Done =="
