# Monte Carlo Backend Benchmark - 2026-06-05

Host: THEPC WSL2 Ubuntu

## GPU
name, driver_version, compute_cap, memory.total [MiB]
NVIDIA GeForce RTX 5060 Ti, 595.79, 12.0, 16311 MiB

## CUDA Toolkit
nvcc: NVIDIA (R) Cuda compiler driver
Copyright (c) 2005-2026 NVIDIA Corporation
Built on Thu_Mar_19_11:12:51_PM_PDT_2026
Cuda compilation tools, release 13.2, V13.2.78
Build cuda_13.2.r13.2/compiler.37668154_0

## Rust
rustc 1.96.0 (ac68faa20 2026-05-25)
cargo 1.96.0 (30a34c682 2026-05-25)

## Build

## Timed Runs

| case | backend | paths | steps | iterations | avg_ms | min_ms | median_ms | max_ms | p_finish | p_no_touch |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| live-small | cpu_rayon | 8192 | 64 | 5 | 2.488 | 1.050 | 1.521 | 5.513 | 0.154907 | 0.000000 |
| live-small | cuda | 8192 | 64 | 5 | 61.179 | 1.226 | 1.234 | 300.961 | 0.163940 | 0.000000 |
| visual-large | cpu_rayon | 100000 | 300 | 3 | 51.802 | 48.967 | 51.083 | 55.358 | 0.161590 | 0.000000 |
| visual-large | cuda | 100000 | 300 | 3 | 120.694 | 16.417 | 17.075 | 328.589 | 0.157970 | 0.000000 |
| sweep-large | cpu_rayon | 1000000 | 300 | 1 | 552.190 | 552.190 | 552.190 | 552.190 | 0.158641 | 0.000000 |
| sweep-large | cuda | 1000000 | 300 | 1 | 499.706 | 499.706 | 499.706 | 499.706 | 0.158436 | 0.000000 |

## Decision Rules
- Use CPU for live cached probability runs when measured latency is lower at the target path count.
- Use CUDA only for workloads where the warm-cache benchmark beats CPU after context/module reuse.
- Re-benchmark CUDA again before large batched generator sweeps, especially if path counts or artifact emission change.
- Keep TUI/API reading cached outputs only.
