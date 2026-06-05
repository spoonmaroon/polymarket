use anyhow::Result;
use polymarket_probability_cuda::cuda_smoke_add_one;

#[test]
fn smoke_add_one_returns_result_without_panicking_when_cuda_is_unavailable() {
    let result = cuda_smoke_add_one(&[1.0, 2.5, -3.0]);

    if let Ok(output) = result {
        assert_eq!(output, vec![2.0, 3.5, -2.0]);
    }
}

#[test]
#[ignore = "requires THEPC CUDA driver/runtime and NVRTC"]
fn smoke_add_one_runs_on_cuda_runtime() -> Result<()> {
    let output = cuda_smoke_add_one(&[1.0, 2.5, -3.0])?;

    assert_eq!(output, vec![2.0, 3.5, -2.0]);
    Ok(())
}
