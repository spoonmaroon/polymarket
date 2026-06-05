# GPU Monte Carlo Setup

THEPC has an NVIDIA GeForce RTX 5060 Ti with compute capability 12.0 and
16 GB VRAM. WSL2 Ubuntu is the intended CUDA build and runtime path for this
repo's GPU Monte Carlo backend.

The Windows NVIDIA driver provides GPU access to WSL. Do not install a Linux
display driver inside WSL. The WSL environment only needs the CUDA toolkit,
Rust, Python headers, and normal build tools.

## Setup Flow

From the repo root inside THEPC WSL Ubuntu:

```bash
./scripts/thepc_gpu_preflight.sh
./scripts/install_thepc_wsl_gpu_toolchain.sh
source ~/.profile
./scripts/thepc_gpu_preflight.sh
```

The installer refuses to run outside WSL. It installs the NVIDIA WSL CUDA
keyring and `cuda-toolkit-13-2`; it does not install a Linux display driver.

## Success Signals

- `nvidia-smi` sees the RTX 5060 Ti.
- `nvcc --version` works.
- `nvcc --list-gpu-arch` includes `compute_120` or `sm_120`.
- `cargo --version` works.

## Backend Rule

The CPU backend remains the correctness reference and fallback. CUDA is an
acceleration backend only, intended for larger Monte Carlo visualization runs,
backtests, calibration sweeps, and generator ensembles after parity checks pass.
