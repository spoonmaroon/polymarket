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
fn cuda_backend_reports_missing_cuda_or_runs_without_panicking() {
    let backend = CudaBackend::default();
    let result = backend.run(&input(), &config());

    if let Ok(run) = result {
        assert_eq!(run.backend, SimulationBackendKind::Cuda);
        assert!((0.0..=1.0).contains(&run.p_finish));
        assert!((0.0..=1.0).contains(&run.p_no_touch));
    }
}

#[test]
#[ignore = "requires THEPC CUDA driver/runtime and NVRTC"]
fn cuda_backend_is_deterministic_for_same_seed_and_config() -> Result<()> {
    let backend = CudaBackend::default();
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
    let backend = CudaBackend::default();
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
#[ignore = "requires THEPC CUDA driver/runtime and NVRTC"]
fn cuda_backend_reports_cache_miss_then_hit_when_reused() -> Result<()> {
    let backend = CudaBackend::default();
    let first = backend.run(&input(), &config())?;
    let second = backend.run(&input(), &config())?;

    assert_eq!(first.diagnostics["cuda_cache_hit"], false);
    assert_eq!(second.diagnostics["cuda_cache_hit"], true);
    assert_eq!(first.diagnostics["gpu"], second.diagnostics["gpu"]);
    Ok(())
}

#[test]
#[ignore = "requires THEPC CUDA driver/runtime and NVRTC"]
fn cuda_backend_distinguishes_strict_and_inclusive_threshold_comparisons() -> Result<()> {
    let backend = CudaBackend::default();
    let mut equal_input = input();
    equal_input.threshold = equal_input.settlement_price;
    equal_input.sigma_tau = f64::MIN_POSITIVE;

    let mut tiny_config = config();
    tiny_config.path_count = 16;
    tiny_config.steps = 1;

    equal_input.comparison_operator = ComparisonOperator::GreaterThan;
    let strict = backend.run(&equal_input, &tiny_config)?;

    equal_input.comparison_operator = ComparisonOperator::GreaterThanOrEqual;
    let inclusive = backend.run(&equal_input, &tiny_config)?;

    assert_eq!(strict.p_finish, 0.0);
    assert_eq!(inclusive.p_finish, 1.0);
    Ok(())
}

#[test]
#[ignore = "requires THEPC CUDA driver/runtime and NVRTC"]
fn cuda_backend_supports_down_contracts() -> Result<()> {
    let backend = CudaBackend::default();
    let mut down_input = input();
    down_input.side = Side::DOWN;
    down_input.comparison_operator = ComparisonOperator::LessThanOrEqual;
    down_input.threshold = down_input.settlement_price;
    down_input.sigma_tau = f64::MIN_POSITIVE;

    let mut tiny_config = config();
    tiny_config.path_count = 16;
    tiny_config.steps = 1;

    let run = backend.run(&down_input, &tiny_config)?;

    assert_eq!(run.backend, SimulationBackendKind::Cuda);
    assert_eq!(run.p_finish, 1.0);
    assert_eq!(run.p_no_touch, 1.0);
    Ok(())
}

#[test]
#[ignore = "requires THEPC CUDA driver/runtime and NVRTC"]
fn cuda_backend_runs_different_seed_configs_in_probability_range() -> Result<()> {
    let backend = CudaBackend::default();
    let mut other_config = config();
    other_config.seed += 1;

    let first = backend.run(&input(), &config())?;
    let second = backend.run(&input(), &other_config)?;

    assert_ne!(first.seed, second.seed);
    assert!((0.0..=1.0).contains(&first.p_finish));
    assert!((0.0..=1.0).contains(&first.p_no_touch));
    assert!((0.0..=1.0).contains(&second.p_finish));
    assert!((0.0..=1.0).contains(&second.p_no_touch));
    Ok(())
}

#[test]
#[ignore = "requires THEPC CUDA driver/runtime and NVRTC"]
fn cuda_backend_rejects_invalid_generated_prices() {
    let backend = CudaBackend::default();
    let mut explosive_input = input();
    explosive_input.sigma_tau = f64::MAX;

    let mut tiny_config = config();
    tiny_config.path_count = 512;
    tiny_config.steps = 1;

    let error = backend
        .run(&explosive_input, &tiny_config)
        .expect_err("huge sigma_tau should generate invalid CUDA path prices");

    assert!(
        error
            .to_string()
            .contains("CUDA generated invalid path prices"),
        "{error:#}"
    );
}

#[test]
fn cuda_backend_rejects_mismatched_backend_config_without_cuda_runtime() {
    let backend = CudaBackend::default();
    let mut bad_config = config();
    bad_config.backend = SimulationBackendKind::CpuRayon;

    assert!(backend.run(&input(), &bad_config).is_err());
}

#[test]
fn cuda_backend_rejects_invalid_input_without_cuda_runtime() {
    let backend = CudaBackend::default();
    let mut bad_input = input();
    bad_input.sigma_tau = f64::INFINITY;

    let error = backend
        .run(&bad_input, &config())
        .expect_err("invalid sigma_tau should fail before CUDA setup");

    assert!(error.to_string().contains("sigma_tau"));
}
