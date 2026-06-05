use std::env;
use std::time::Duration;
use std::time::Instant;

use chrono::{TimeZone, Utc};
use polymarket_probability_core::backend::SimulationBackend;
use polymarket_probability_core::cpu::CpuRayonBackend;
use polymarket_probability_core::schema::{
    Asset, ComparisonOperator, ProbabilityInput, Side, SimulationBackendKind, SimulationConfig,
};

fn main() -> anyhow::Result<()> {
    let args = BenchmarkArgs::parse()?;
    let backend = CpuRayonBackend;
    let row = run_case(&backend, &args, SimulationBackendKind::CpuRayon)?;
    println!("{row}");
    Ok(())
}

struct BenchmarkArgs {
    label: String,
    path_count: usize,
    steps: usize,
    iterations: usize,
}

impl BenchmarkArgs {
    fn parse() -> anyhow::Result<Self> {
        let mut args = env::args().skip(1);
        Ok(Self {
            label: args.next().unwrap_or_else(|| "default".to_string()),
            path_count: args
                .next()
                .unwrap_or_else(|| "100000".to_string())
                .parse()?,
            steps: args.next().unwrap_or_else(|| "300".to_string()).parse()?,
            iterations: args.next().unwrap_or_else(|| "3".to_string()).parse()?,
        })
    }
}

fn run_case(
    backend: &impl SimulationBackend,
    args: &BenchmarkArgs,
    backend_kind: SimulationBackendKind,
) -> anyhow::Result<String> {
    let mut durations = Vec::with_capacity(args.iterations);
    let mut last_p_finish = 0.0;
    let mut last_p_no_touch = 0.0;

    for iteration in 0..args.iterations {
        let config = SimulationConfig {
            path_count: args.path_count,
            steps: args.steps,
            seed: 20260605 + u64::try_from(iteration)?,
            backend: backend_kind,
            model_version: "benchmark-cpu-v1".to_string(),
            emit_artifacts: false,
            sample_path_limit: 0,
        };
        let input = benchmark_input();
        let started_at = Instant::now();
        let run = backend.run(&input, &config)?;
        durations.push(started_at.elapsed());
        last_p_finish = run.p_finish;
        last_p_no_touch = run.p_no_touch;
    }

    let stats = DurationStats::from_durations(&durations)?;
    Ok(format!(
        "| {} | cpu_rayon | {} | {} | {} | {:.3} | {:.3} | {:.3} | {:.3} | {:.6} | {:.6} |",
        args.label,
        args.path_count,
        args.steps,
        args.iterations,
        stats.average_ms,
        stats.min_ms,
        stats.median_ms,
        stats.max_ms,
        last_p_finish,
        last_p_no_touch
    ))
}

fn benchmark_input() -> ProbabilityInput {
    ProbabilityInput {
        state_id: "benchmark-btc-up".to_string(),
        asof_ts: Utc.with_ymd_and_hms(2026, 6, 5, 12, 0, 0).unwrap(),
        asset: Asset::BTC,
        side: Side::UP,
        comparison_operator: ComparisonOperator::GreaterThanOrEqual,
        seconds_left: 300.0,
        settlement_price: 100.0,
        threshold: 100.25,
        sigma_tau: 0.0025,
        executable_price: 0.52,
        source_age_ms: 25,
        book_age_ms: 35,
        z_path: 0.1,
    }
}

struct DurationStats {
    average_ms: f64,
    min_ms: f64,
    median_ms: f64,
    max_ms: f64,
}

impl DurationStats {
    fn from_durations(durations: &[Duration]) -> anyhow::Result<Self> {
        if durations.is_empty() {
            anyhow::bail!("iterations must be positive");
        }

        let mut values = durations
            .iter()
            .map(|duration| duration.as_secs_f64() * 1000.0)
            .collect::<Vec<_>>();
        values.sort_by(|left, right| left.total_cmp(right));
        let sum = values.iter().sum::<f64>();
        let midpoint = values.len() / 2;
        let median_ms = if values.len() % 2 == 0 {
            (values[midpoint - 1] + values[midpoint]) / 2.0
        } else {
            values[midpoint]
        };

        Ok(Self {
            average_ms: sum / values.len() as f64,
            min_ms: values[0],
            median_ms,
            max_ms: *values.last().unwrap(),
        })
    }
}
