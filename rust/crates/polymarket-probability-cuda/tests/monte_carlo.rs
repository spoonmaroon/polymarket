use anyhow::Result;
use chrono::{TimeZone, Utc};
use polymarket_probability_core::backend::SimulationBackend;
use polymarket_probability_core::schema::{
    Asset, ComparisonOperator, ProbabilityInput, Side, SimulationBackendKind, SimulationConfig,
};
use polymarket_probability_cuda::CudaBackend;

fn input() -> ProbabilityInput {
    ProbabilityInput {
        state_id: "state-1".to_string(),
        asof_ts: Utc.with_ymd_and_hms(2026, 6, 5, 12, 0, 0).unwrap(),
        asset: Asset::BTC,
        side: Side::UP,
        comparison_operator: ComparisonOperator::GreaterThanOrEqual,
        seconds_left: 60.0,
        settlement_price: 100.0,
        threshold: 101.0,
        sigma_tau: 0.2,
        executable_price: 0.5,
        source_age_ms: 10,
        book_age_ms: 20,
        z_path: 0.0,
    }
}

fn config() -> SimulationConfig {
    SimulationConfig {
        path_count: 512,
        steps: 8,
        seed: 123,
        backend: SimulationBackendKind::Cuda,
        model_version: "cuda-v1".to_string(),
        emit_artifacts: false,
        sample_path_limit: 0,
    }
}

#[test]
#[ignore = "requires THEPC CUDA driver/runtime and NVRTC"]
fn cuda_backend_is_deterministic_for_same_seed_and_config() -> Result<()> {
    let backend = CudaBackend;
    let first = backend.run(&input(), &config())?;
    let second = backend.run(&input(), &config())?;

    assert_eq!(first.state_id, second.state_id);
    assert_eq!(first.asof_ts, second.asof_ts);
    assert_eq!(first.p_finish, second.p_finish);
    assert_eq!(first.p_no_touch, second.p_no_touch);
    assert_eq!(first.z_path, second.z_path);
    assert_eq!(first.model_version, second.model_version);
    assert_eq!(first.seed, second.seed);
    assert_eq!(first.backend, second.backend);
    assert_eq!(
        first.diagnostics["per_step_sigma"],
        second.diagnostics["per_step_sigma"]
    );
    Ok(())
}

#[test]
#[ignore = "requires THEPC CUDA driver/runtime and NVRTC"]
fn cuda_backend_outputs_probability_range_backend_and_diagnostics() -> Result<()> {
    let backend = CudaBackend;
    let run = backend.run(&input(), &config())?;

    assert!((0.0..=1.0).contains(&run.p_finish));
    assert!((0.0..=1.0).contains(&run.p_no_touch));
    assert_eq!(run.backend, SimulationBackendKind::Cuda);
    assert_eq!(run.diagnostics["path_count"], 512);
    assert_eq!(run.diagnostics["steps"], 8);
    assert!(run.diagnostics["elapsed_ms"].as_u64().is_some());
    assert_eq!(run.diagnostics["per_step_sigma"], 0.2 / (8.0f64).sqrt());
    assert!(run.diagnostics["gpu"].is_object());
    Ok(())
}

#[test]
fn cuda_backend_rejects_mismatched_backend_config_without_cuda_runtime() {
    let backend = CudaBackend;
    let mut bad_config = config();
    bad_config.backend = SimulationBackendKind::CpuRayon;

    assert!(backend.run(&input(), &bad_config).is_err());
}
