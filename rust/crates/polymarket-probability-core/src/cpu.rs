use anyhow::{Result, bail};
use rand::SeedableRng;
use rand_chacha::ChaCha20Rng;
use rand_distr::{Distribution, Normal};
use rayon::prelude::*;
use serde_json::json;
use std::time::Instant;

use crate::backend::SimulationBackend;
use crate::schema::{
    ComparisonOperator, HistogramBin, PercentilePoint, ProbabilityInput, Side, SimulationArtifacts,
    SimulationBackendKind, SimulationConfig, SimulationRun,
};
use crate::scoring::price_satisfies_contract;

#[derive(Clone, Copy, Debug, Default)]
pub struct CpuRayonBackend;

#[derive(Clone, Debug)]
struct PathResult {
    prices: Vec<f64>,
    terminal_price: f64,
    terminal: bool,
    no_touch: bool,
}

#[derive(Clone, Copy, Debug, Default)]
struct SimulationCounts {
    terminal_count: usize,
    no_touch_count: usize,
}

impl SimulationCounts {
    fn add_path(&mut self, path: &PathResult) {
        self.terminal_count += usize::from(path.terminal);
        self.no_touch_count += usize::from(path.no_touch);
    }

    fn merge(&mut self, other: Self) {
        self.terminal_count += other.terminal_count;
        self.no_touch_count += other.no_touch_count;
    }
}

impl SimulationBackend for CpuRayonBackend {
    fn run(&self, input: &ProbabilityInput, config: &SimulationConfig) -> Result<SimulationRun> {
        validate_input(input)?;
        validate_config(config)?;

        let started_at = Instant::now();
        let per_step_sigma = input.sigma_tau / (config.steps as f64).sqrt();
        let normal = Normal::new(0.0, per_step_sigma)
            .map_err(|error| anyhow::anyhow!("invalid normal distribution: {error}"))?;

        let (counts, artifacts) = if config.emit_artifacts {
            let path_results = (0..config.path_count)
                .into_par_iter()
                .map(|path_index| simulate_path(path_index, input, config, &normal, true))
                .collect::<Result<Vec<_>, _>>()?;
            let mut counts = SimulationCounts::default();
            for path in &path_results {
                counts.add_path(path);
            }
            build_artifacts(&path_results, config.sample_path_limit)
                .map(|artifacts| (counts, artifacts))?
        } else {
            let counts = (0..config.path_count)
                .into_par_iter()
                .map(|path_index| {
                    simulate_path(path_index, input, config, &normal, false).map(|path| {
                        let mut counts = SimulationCounts::default();
                        counts.add_path(&path);
                        counts
                    })
                })
                .try_reduce(SimulationCounts::default, |mut left, right| {
                    left.merge(right);
                    Ok(left)
                })?;
            (counts, SimulationArtifacts::default())
        };

        Ok(SimulationRun {
            state_id: input.state_id.clone(),
            asof_ts: input.asof_ts,
            p_finish: counts.terminal_count as f64 / config.path_count as f64,
            p_no_touch: counts.no_touch_count as f64 / config.path_count as f64,
            z_path: input.z_path,
            model_version: config.model_version.clone(),
            seed: config.seed,
            backend: SimulationBackendKind::CpuRayon,
            diagnostics: json!({
                "path_count": config.path_count,
                "steps": config.steps,
                "elapsed_ms": started_at.elapsed().as_millis(),
                "per_step_sigma": per_step_sigma,
            }),
            artifacts,
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
    if config.path_count == 0 {
        bail!("path_count must be positive");
    }
    if config.steps == 0 {
        bail!("steps must be positive");
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

fn simulate_path(
    path_index: usize,
    input: &ProbabilityInput,
    config: &SimulationConfig,
    normal: &Normal<f64>,
    retain_prices: bool,
) -> Result<PathResult> {
    let mut rng = ChaCha20Rng::seed_from_u64(path_seed(config.seed, path_index));
    let mut prices = if retain_prices {
        let mut prices = Vec::with_capacity(config.steps + 1);
        prices.push(input.settlement_price);
        prices
    } else {
        Vec::new()
    };
    let mut log_price = input.settlement_price.ln();
    let mut terminal_price = input.settlement_price;
    let mut no_touch = price_satisfies_contract(input, input.settlement_price);

    for _ in 0..config.steps {
        log_price += normal.sample(&mut rng);
        terminal_price = log_price.exp();
        if !terminal_price.is_finite() || terminal_price <= 0.0 {
            bail!("generated path price must be positive and finite");
        }
        no_touch &= price_satisfies_contract(input, terminal_price);
        if retain_prices {
            prices.push(terminal_price);
        }
    }

    Ok(PathResult {
        prices,
        terminal_price,
        terminal: price_satisfies_contract(input, terminal_price),
        no_touch,
    })
}

fn path_seed(seed: u64, path_index: usize) -> u64 {
    let index = u64::try_from(path_index).unwrap_or(u64::MAX);
    seed ^ index.wrapping_mul(0x9E37_79B9_7F4A_7C15)
}

fn build_artifacts(
    path_results: &[PathResult],
    sample_path_limit: usize,
) -> Result<SimulationArtifacts> {
    let sample_paths = path_results
        .iter()
        .take(sample_path_limit)
        .map(|result| result.prices.clone())
        .collect::<Vec<_>>();

    Ok(SimulationArtifacts {
        percentile_paths: percentile_points(path_results)?,
        sample_paths,
        terminal_histogram: terminal_histogram(path_results, 10),
    })
}

fn percentile_points(path_results: &[PathResult]) -> Result<Vec<PercentilePoint>> {
    let Some(first_path) = path_results.first() else {
        return Ok(Vec::new());
    };

    (0..first_path.prices.len())
        .map(|step| {
            let mut prices = path_results
                .iter()
                .map(|path| {
                    path.prices
                        .get(step)
                        .copied()
                        .ok_or_else(|| anyhow::anyhow!("generated paths must have equal length"))
                })
                .collect::<Result<Vec<_>>>()?;
            prices.sort_by(|left, right| {
                left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal)
            });
            Ok(PercentilePoint {
                step,
                p05: percentile_value(&prices, 0.05),
                p25: percentile_value(&prices, 0.25),
                p50: percentile_value(&prices, 0.50),
                p75: percentile_value(&prices, 0.75),
                p95: percentile_value(&prices, 0.95),
            })
        })
        .collect()
}

fn terminal_histogram(path_results: &[PathResult], bin_count: usize) -> Vec<HistogramBin> {
    let terminals = path_results
        .iter()
        .map(|path| path.terminal_price)
        .collect::<Vec<_>>();
    let min = terminals.iter().copied().fold(f64::INFINITY, f64::min);
    let max = terminals.iter().copied().fold(f64::NEG_INFINITY, f64::max);

    if (max - min).abs() < f64::EPSILON {
        return vec![HistogramBin {
            min_price: min,
            max_price: max,
            count: terminals.len(),
        }];
    }

    let width = (max - min) / bin_count as f64;
    let mut counts = vec![0usize; bin_count];
    for terminal in terminals {
        let raw_index = ((terminal - min) / width).floor() as usize;
        let index = raw_index.min(bin_count - 1);
        counts[index] += 1;
    }

    counts
        .into_iter()
        .enumerate()
        .map(|(index, count)| {
            let min_price = min + width * index as f64;
            HistogramBin {
                min_price,
                max_price: min_price + width,
                count,
            }
        })
        .collect()
}

fn percentile_value(sorted_values: &[f64], percentile: f64) -> f64 {
    if sorted_values.is_empty() {
        return 0.0;
    }
    sorted_values[percentile_index(sorted_values.len(), percentile)]
}

fn percentile_index(len: usize, percentile: f64) -> usize {
    let last_index = len.saturating_sub(1);
    ((last_index as f64) * percentile).round() as usize
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};

    use super::CpuRayonBackend;
    use crate::backend::SimulationBackend;
    use crate::schema::{
        Asset, ComparisonOperator, ProbabilityInput, Side, SimulationBackendKind, SimulationConfig,
    };

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
            backend: SimulationBackendKind::CpuRayon,
            model_version: "cpu-ref-v1".to_string(),
            emit_artifacts: true,
            sample_path_limit: 3,
        }
    }

    #[test]
    fn cpu_backend_is_deterministic_for_same_seed_and_config() {
        let backend = CpuRayonBackend;
        let first = backend.run(&input(), &config()).unwrap();
        let second = backend.run(&input(), &config()).unwrap();

        assert_eq!(first.state_id, second.state_id);
        assert_eq!(first.asof_ts, second.asof_ts);
        assert_eq!(first.p_finish, second.p_finish);
        assert_eq!(first.p_no_touch, second.p_no_touch);
        assert_eq!(first.z_path, second.z_path);
        assert_eq!(first.model_version, second.model_version);
        assert_eq!(first.seed, second.seed);
        assert_eq!(first.backend, second.backend);
        assert_eq!(first.artifacts, second.artifacts);
        assert_eq!(
            first.diagnostics["per_step_sigma"],
            second.diagnostics["per_step_sigma"]
        );
    }

    #[test]
    fn cpu_backend_outputs_probability_range_and_diagnostics() {
        let backend = CpuRayonBackend;
        let run = backend.run(&input(), &config()).unwrap();

        assert!((0.0..=1.0).contains(&run.p_finish));
        assert!((0.0..=1.0).contains(&run.p_no_touch));
        assert_eq!(run.backend, SimulationBackendKind::CpuRayon);
        assert!(run.diagnostics.is_object());
        assert_eq!(run.diagnostics["path_count"], 512);
        assert_eq!(run.diagnostics["steps"], 8);
        assert!(run.diagnostics["per_step_sigma"].as_f64().unwrap() > 0.0);
        assert!(run.diagnostics["elapsed_ms"].as_u64().is_some());
    }

    #[test]
    fn cpu_backend_artifacts_match_planned_schema_shape() {
        let backend = CpuRayonBackend;
        let run = backend.run(&input(), &config()).unwrap();

        assert_eq!(run.artifacts.sample_paths.len(), 3);
        assert!(
            run.artifacts
                .sample_paths
                .iter()
                .all(|path| path.len() == config().steps + 1)
        );
        assert_eq!(run.artifacts.percentile_paths.len(), config().steps + 1);
        assert_eq!(run.artifacts.percentile_paths[0].step, 0);
        assert!(run.artifacts.percentile_paths[0].p05 > 0.0);
        assert_eq!(
            run.artifacts
                .terminal_histogram
                .iter()
                .map(|bin| bin.count)
                .sum::<usize>(),
            config().path_count
        );
        assert!(
            run.artifacts
                .terminal_histogram
                .iter()
                .all(|bin| bin.min_price <= bin.max_price)
        );
    }

    #[test]
    fn cpu_backend_omits_artifacts_when_disabled() {
        let backend = CpuRayonBackend;
        let mut config = config();
        config.emit_artifacts = false;

        let run = backend.run(&input(), &config).unwrap();

        assert!((0.0..=1.0).contains(&run.p_finish));
        assert!((0.0..=1.0).contains(&run.p_no_touch));
        assert!(run.artifacts.percentile_paths.is_empty());
        assert!(run.artifacts.sample_paths.is_empty());
        assert!(run.artifacts.terminal_histogram.is_empty());
    }

    #[test]
    fn cpu_backend_builds_full_population_artifacts_when_sample_limit_is_zero() {
        let backend = CpuRayonBackend;
        let mut config = config();
        config.sample_path_limit = 0;

        let run = backend.run(&input(), &config).unwrap();

        assert!(run.artifacts.sample_paths.is_empty());
        assert_eq!(run.artifacts.percentile_paths.len(), config.steps + 1);
        assert_eq!(
            run.artifacts
                .terminal_histogram
                .iter()
                .map(|bin| bin.count)
                .sum::<usize>(),
            config.path_count
        );
    }

    #[test]
    fn cpu_backend_bounds_sample_paths_to_sample_path_limit() {
        let backend = CpuRayonBackend;
        let mut config = config();
        config.path_count = 32;
        config.sample_path_limit = 5;

        let run = backend.run(&input(), &config).unwrap();

        assert_eq!(run.artifacts.sample_paths.len(), config.sample_path_limit);
    }

    #[test]
    fn cpu_backend_rejects_invalid_inputs_and_config() {
        let backend = CpuRayonBackend;
        let mut bad_input = input();
        bad_input.sigma_tau = f64::NAN;
        assert!(backend.run(&bad_input, &config()).is_err());

        let mut bad_config = config();
        bad_config.path_count = 0;
        assert!(backend.run(&input(), &bad_config).is_err());

        let mut bad_version = config();
        bad_version.model_version = " ".to_string();
        assert!(backend.run(&input(), &bad_version).is_err());
    }

    #[test]
    fn cpu_backend_rejects_invalid_z_path() {
        let backend = CpuRayonBackend;
        let mut bad_input = input();
        bad_input.z_path = f64::INFINITY;

        assert!(backend.run(&bad_input, &config()).is_err());
    }

    #[test]
    fn cpu_backend_rejects_invalid_executable_price() {
        let backend = CpuRayonBackend;
        for executable_price in [f64::NAN, -0.01, 1.01] {
            let mut bad_input = input();
            bad_input.executable_price = executable_price;

            assert!(backend.run(&bad_input, &config()).is_err());
        }
    }

    #[test]
    fn cpu_backend_rejects_negative_seconds_left() {
        let backend = CpuRayonBackend;
        let mut bad_input = input();
        bad_input.seconds_left = -1.0;

        assert!(backend.run(&bad_input, &config()).is_err());
    }

    #[test]
    fn cpu_backend_rejects_side_operator_mismatch() {
        let backend = CpuRayonBackend;

        let down_with_greater = ProbabilityInput {
            side: Side::DOWN,
            comparison_operator: ComparisonOperator::GreaterThan,
            ..input()
        };
        assert!(backend.run(&down_with_greater, &config()).is_err());

        let up_with_less = ProbabilityInput {
            side: Side::UP,
            comparison_operator: ComparisonOperator::LessThan,
            ..input()
        };
        assert!(backend.run(&up_with_less, &config()).is_err());
    }
}
