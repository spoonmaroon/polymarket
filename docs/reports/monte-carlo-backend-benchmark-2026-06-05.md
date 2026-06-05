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
| live-small | cpu_rayon | 8192 | 64 | 5 | 1.473 | 0.912 | 1.580 | 2.025 | 0.154907 | 0.000000 |
| live-small | cuda | 8192 | 64 | 5 | 219.126 | 174.968 | 182.822 | 371.406 | 0.163940 | 0.000000 |
| visual-large | cpu_rayon | 100000 | 300 | 3 | 41.425 | 39.342 | 39.810 | 45.123 | 0.161590 | 0.000000 |
| visual-large | cuda | 100000 | 300 | 3 | 220.927 | 176.471 | 181.786 | 304.524 | 0.157970 | 0.000000 |
| sweep-large | cpu_rayon | 1000000 | 300 | 1 | 397.410 | 397.410 | 397.410 | 397.410 | 0.158641 | 0.000000 |
| sweep-large | cuda | 1000000 | 300 | 1 | 476.844 | 476.844 | 476.844 | 476.844 | 0.158436 | 0.000000 |

## Decision Rules
- Use CPU for live cached probability runs and the tested visualization sizes until CUDA context/module reuse is implemented.
- Re-benchmark CUDA after persistent context/module caching or much larger batched generator sweeps.
- Keep TUI/API reading cached outputs only.
