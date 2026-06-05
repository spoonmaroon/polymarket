use anyhow::{Result, bail};
use cudarc::driver::{CudaContext, LaunchConfig, PushKernelArg};
use cudarc::nvrtc::compile_ptx;
use polymarket_probability_core::backend::SimulationBackend;
use polymarket_probability_core::schema::{
    ComparisonOperator, ProbabilityInput, Side, SimulationArtifacts, SimulationBackendKind,
    SimulationConfig, SimulationRun,
};
use serde_json::json;
use std::time::Instant;

const SMOKE_KERNEL: &str = include_str!("../kernels/smoke.cu");
const MONTE_CARLO_KERNEL: &str = include_str!("../kernels/monte_carlo.cu");

#[derive(Clone, Copy, Debug, Default)]
pub struct CudaBackend;

impl SimulationBackend for CudaBackend {
    fn run(&self, input: &ProbabilityInput, config: &SimulationConfig) -> Result<SimulationRun> {
        validate_input(input)?;
        validate_config(config)?;

        let started_at = Instant::now();
        let per_step_sigma = input.sigma_tau / (config.steps as f64).sqrt();
        let counts = run_cuda_monte_carlo(input, config, per_step_sigma)?;

        Ok(SimulationRun {
            state_id: input.state_id.clone(),
            asof_ts: input.asof_ts,
            p_finish: counts.terminal_count as f64 / config.path_count as f64,
            p_no_touch: counts.no_touch_count as f64 / config.path_count as f64,
            z_path: input.z_path,
            model_version: config.model_version.clone(),
            seed: config.seed,
            backend: SimulationBackendKind::Cuda,
            diagnostics: json!({
                "path_count": config.path_count,
                "steps": config.steps,
                "elapsed_ms": started_at.elapsed().as_millis(),
                "per_step_sigma": per_step_sigma,
                "gpu": counts.gpu,
            }),
            artifacts: SimulationArtifacts::default(),
        })
    }
}

#[derive(Clone, Debug)]
struct CudaCounts {
    terminal_count: u64,
    no_touch_count: u64,
    gpu: serde_json::Value,
}

#[repr(C)]
#[derive(Clone, Copy, Debug)]
struct CudaSimulationInput {
    settlement_price: f64,
    threshold: f64,
    per_step_sigma: f64,
    seed: u64,
    path_count: u64,
    steps: u32,
    operator: u32,
}

unsafe impl cudarc::driver::DeviceRepr for CudaSimulationInput {}

pub fn cuda_smoke_add_one(input: &[f64]) -> Result<Vec<f64>> {
    if input.is_empty() {
        return Ok(Vec::new());
    }

    let ptx = compile_ptx(SMOKE_KERNEL)?;
    let ctx = CudaContext::new(0)?;
    let stream = ctx.default_stream();
    let module = ctx.load_module(ptx)?;
    let function = module.load_function("add_one")?;

    let input_dev = stream.clone_htod(input)?;
    let mut output_dev = stream.alloc_zeros::<f64>(input.len())?;
    let len = u64::try_from(input.len())?;

    let mut builder = stream.launch_builder(&function);
    builder.arg(&input_dev);
    builder.arg(&mut output_dev);
    builder.arg(&len);
    unsafe { builder.launch(LaunchConfig::for_num_elems(u32::try_from(input.len())?)) }?;

    Ok(stream.clone_dtoh(&output_dev)?)
}

fn run_cuda_monte_carlo(
    input: &ProbabilityInput,
    config: &SimulationConfig,
    per_step_sigma: f64,
) -> Result<CudaCounts> {
    let ptx = compile_ptx(MONTE_CARLO_KERNEL)?;
    let ctx = CudaContext::new(0)?;
    let gpu_name = ctx.name().unwrap_or_else(|_| "unknown".to_string());
    let compute_capability = ctx.compute_capability().ok();
    let stream = ctx.default_stream();
    let module = ctx.load_module(ptx)?;
    let function = module.load_function("simulate_monte_carlo")?;

    let cuda_input = CudaSimulationInput {
        settlement_price: input.settlement_price,
        threshold: input.threshold,
        per_step_sigma,
        seed: config.seed,
        path_count: u64::try_from(config.path_count)?,
        steps: u32::try_from(config.steps)?,
        operator: operator_code(input.comparison_operator),
    };
    let mut counts_dev = stream.alloc_zeros::<u64>(2)?;

    let mut builder = stream.launch_builder(&function);
    builder.arg(&cuda_input);
    builder.arg(&mut counts_dev);
    unsafe {
        builder.launch(LaunchConfig::for_num_elems(u32::try_from(
            config.path_count,
        )?))?;
    }

    let counts = stream.clone_dtoh(&counts_dev)?;
    let terminal_count = *counts
        .first()
        .ok_or_else(|| anyhow::anyhow!("CUDA kernel did not return terminal count"))?;
    let no_touch_count = *counts
        .get(1)
        .ok_or_else(|| anyhow::anyhow!("CUDA kernel did not return no-touch count"))?;

    Ok(CudaCounts {
        terminal_count,
        no_touch_count,
        gpu: json!({
            "device_ordinal": ctx.ordinal(),
            "name": gpu_name,
            "compute_capability": compute_capability
                .map(|(major, minor)| format!("{major}.{minor}"))
                .unwrap_or_else(|| "unknown".to_string()),
        }),
    })
}

fn validate_input(input: &ProbabilityInput) -> Result<()> {
    validate_non_negative_finite("seconds_left", input.seconds_left)?;
    validate_positive_finite("settlement_price", input.settlement_price)?;
    validate_positive_finite("threshold", input.threshold)?;
    validate_positive_finite("sigma_tau", input.sigma_tau)?;
    validate_probability("executable_price", input.executable_price)?;
    validate_finite("z_path", input.z_path)?;
    validate_side_operator(input)
}

fn validate_config(config: &SimulationConfig) -> Result<()> {
    if config.backend != SimulationBackendKind::Cuda {
        bail!("CudaBackend requires Cuda simulation backend config");
    }
    if config.path_count == 0 {
        bail!("path_count must be positive");
    }
    if config.steps == 0 {
        bail!("steps must be positive");
    }
    if config.path_count > u32::MAX as usize {
        bail!("path_count must fit in u32 for CUDA launch");
    }
    if config.steps > u32::MAX as usize {
        bail!("steps must fit in u32 for CUDA kernel input");
    }
    if config.model_version.trim().is_empty() {
        bail!("model_version must be non-empty");
    }
    Ok(())
}

fn validate_positive_finite(name: &str, value: f64) -> Result<()> {
    if value.is_finite() && value > 0.0 {
        Ok(())
    } else {
        bail!("{name} must be positive and finite")
    }
}

fn validate_non_negative_finite(name: &str, value: f64) -> Result<()> {
    if value.is_finite() && value >= 0.0 {
        Ok(())
    } else {
        bail!("{name} must be non-negative and finite")
    }
}

fn validate_probability(name: &str, value: f64) -> Result<()> {
    if value.is_finite() && (0.0..=1.0).contains(&value) {
        Ok(())
    } else {
        bail!("{name} must be finite and in [0, 1]")
    }
}

fn validate_finite(name: &str, value: f64) -> Result<()> {
    if value.is_finite() {
        Ok(())
    } else {
        bail!("{name} must be finite")
    }
}

fn validate_side_operator(input: &ProbabilityInput) -> Result<()> {
    match (input.side, input.comparison_operator) {
        (Side::UP, ComparisonOperator::GreaterThan | ComparisonOperator::GreaterThanOrEqual)
        | (Side::DOWN, ComparisonOperator::LessThan | ComparisonOperator::LessThanOrEqual) => {
            Ok(())
        }
        (Side::UP, _) => bail!("UP side requires greater-than comparison operator"),
        (Side::DOWN, _) => bail!("DOWN side requires less-than comparison operator"),
    }
}

fn operator_code(operator: ComparisonOperator) -> u32 {
    match operator {
        ComparisonOperator::GreaterThan => 0,
        ComparisonOperator::GreaterThanOrEqual => 1,
        ComparisonOperator::LessThan => 2,
        ComparisonOperator::LessThanOrEqual => 3,
    }
}
