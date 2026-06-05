use anyhow::{Result, bail};
use cudarc::driver::{CudaContext, CudaFunction, CudaStream, LaunchConfig, PushKernelArg};
use cudarc::nvrtc::compile_ptx;
use polymarket_probability_core::backend::SimulationBackend;
use polymarket_probability_core::schema::{
    ComparisonOperator, ProbabilityInput, Side, SimulationArtifacts, SimulationBackendKind,
    SimulationConfig, SimulationRun,
};
use serde_json::json;
use std::any::Any;
use std::panic::{self, AssertUnwindSafe};
use std::sync::Arc;
use std::sync::Mutex;
use std::time::Instant;

const SMOKE_KERNEL: &str = include_str!("../kernels/smoke.cu");
const MONTE_CARLO_KERNEL: &str = include_str!("../kernels/monte_carlo.cu");
static CUDA_PANIC_HOOK_LOCK: Mutex<()> = Mutex::new(());

#[derive(Debug, Default)]
pub struct CudaBackend {
    runtime: Mutex<Option<CudaRuntime>>,
}

impl CudaBackend {
    pub fn new() -> Self {
        Self::default()
    }

    fn run_cached_monte_carlo(
        &self,
        input: &ProbabilityInput,
        config: &SimulationConfig,
        per_step_sigma: f64,
    ) -> Result<CudaCounts> {
        let mut runtime_guard = self
            .runtime
            .lock()
            .map_err(|_| anyhow::anyhow!("CUDA runtime cache mutex poisoned"))?;
        let cache_hit = runtime_guard.is_some();
        if runtime_guard.is_none() {
            *runtime_guard = Some(CudaRuntime::new()?);
        }
        let runtime = runtime_guard
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("CUDA runtime cache failed to initialize"))?;
        runtime.run_monte_carlo(input, config, per_step_sigma, cache_hit)
    }
}

impl SimulationBackend for CudaBackend {
    fn run(&self, input: &ProbabilityInput, config: &SimulationConfig) -> Result<SimulationRun> {
        validate_input(input)?;
        validate_config(config)?;

        let started_at = Instant::now();
        let per_step_sigma = input.sigma_tau / (config.steps as f64).sqrt();
        let counts =
            catch_cuda_panic(|| self.run_cached_monte_carlo(input, config, per_step_sigma))?;

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
                "cuda_cache_hit": counts.cache_hit,
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
    cache_hit: bool,
    gpu: serde_json::Value,
}

#[derive(Debug)]
struct CudaRuntime {
    stream: Arc<CudaStream>,
    function: CudaFunction,
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
    catch_cuda_panic(|| cuda_smoke_add_one_inner(input))
}

fn cuda_smoke_add_one_inner(input: &[f64]) -> Result<Vec<f64>> {
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

    stream.synchronize()?;

    Ok(stream.clone_dtoh(&output_dev)?)
}

impl CudaRuntime {
    fn new() -> Result<Self> {
        let ptx = compile_ptx(MONTE_CARLO_KERNEL)?;
        let ctx = CudaContext::new(0)?;
        let gpu_name = ctx.name().unwrap_or_else(|_| "unknown".to_string());
        let compute_capability = ctx.compute_capability().ok();
        let stream = ctx.default_stream();
        let module = ctx.load_module(ptx)?;
        let function = module.load_function("simulate_monte_carlo")?;

        Ok(Self {
            stream,
            function,
            gpu: json!({
                "device_ordinal": ctx.ordinal(),
                "name": gpu_name,
                "compute_capability": compute_capability
                    .map(|(major, minor)| format!("{major}.{minor}"))
                    .unwrap_or_else(|| "unknown".to_string()),
            }),
        })
    }

    fn run_monte_carlo(
        &self,
        input: &ProbabilityInput,
        config: &SimulationConfig,
        per_step_sigma: f64,
        cache_hit: bool,
    ) -> Result<CudaCounts> {
        let cuda_input = CudaSimulationInput {
            settlement_price: input.settlement_price,
            threshold: input.threshold,
            per_step_sigma,
            seed: config.seed,
            path_count: u64::try_from(config.path_count)?,
            steps: u32::try_from(config.steps)?,
            operator: operator_code(input.comparison_operator),
        };
        let mut counts_dev = self.stream.alloc_zeros::<u64>(3)?;

        let mut builder = self.stream.launch_builder(&self.function);
        builder.arg(&cuda_input);
        builder.arg(&mut counts_dev);
        unsafe {
            builder.launch(LaunchConfig::for_num_elems(u32::try_from(
                config.path_count,
            )?))?;
        }

        self.stream.synchronize()?;

        let counts = self.stream.clone_dtoh(&counts_dev)?;
        let terminal_count = *counts
            .first()
            .ok_or_else(|| anyhow::anyhow!("CUDA kernel did not return terminal count"))?;
        let no_touch_count = *counts
            .get(1)
            .ok_or_else(|| anyhow::anyhow!("CUDA kernel did not return no-touch count"))?;
        let invalid_price_count = *counts
            .get(2)
            .ok_or_else(|| anyhow::anyhow!("CUDA kernel did not return invalid price count"))?;
        if invalid_price_count > 0 {
            bail!("CUDA generated invalid path prices on {invalid_price_count} paths");
        }

        Ok(CudaCounts {
            terminal_count,
            no_touch_count,
            cache_hit,
            gpu: self.gpu.clone(),
        })
    }
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

fn catch_cuda_panic<T>(operation: impl FnOnce() -> Result<T>) -> Result<T> {
    let _hook_guard = CUDA_PANIC_HOOK_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let previous_hook = panic::take_hook();
    panic::set_hook(Box::new(|_| {}));
    let result = panic::catch_unwind(AssertUnwindSafe(operation));
    panic::set_hook(previous_hook);

    match result {
        Ok(result) => result,
        Err(payload) => bail!(
            "CUDA/NVRTC entrypoint panicked: {}",
            panic_payload_message(&payload)
        ),
    }
}

fn panic_payload_message(payload: &Box<dyn Any + Send>) -> String {
    if let Some(message) = payload.downcast_ref::<&str>() {
        (*message).to_string()
    } else if let Some(message) = payload.downcast_ref::<String>() {
        message.clone()
    } else {
        "unknown panic payload".to_string()
    }
}
