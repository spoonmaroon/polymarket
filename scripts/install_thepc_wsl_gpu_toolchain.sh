#!/usr/bin/env bash
set -euo pipefail

if [[ ! -r /proc/version ]] || ! grep -qi microsoft /proc/version; then
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

if [[ -r "${HOME}/.cargo/env" ]]; then
  # shellcheck source=/dev/null
  source "${HOME}/.cargo/env"
fi
rustup default stable

CUDA_KEYRING_DEB="/tmp/cuda-keyring_1.1-1_all.deb"
wget -O "${CUDA_KEYRING_DEB}" \
  https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i "${CUDA_KEYRING_DEB}"
sudo apt-get update
sudo apt-get install -y cuda-toolkit-13-2

touch "${HOME}/.profile"
if ! grep -q '/usr/local/cuda/bin' "${HOME}/.profile"; then
  {
    echo 'export PATH=/usr/local/cuda/bin:${PATH}'
    echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}'
  } >> "${HOME}/.profile"
fi

echo "Restart the shell or run:"
echo "source ~/.profile && ./scripts/thepc_gpu_preflight.sh"
