use anyhow::{Result, bail};
use rand::SeedableRng;
use rand_chacha::ChaCha20Rng;
use rand_distr::{Distribution, Normal};
use rayon::prelude::*;

use crate::backend::SimulationBackend;
use crate::schema::{
    PercentilePath, ProbabilityInput, SamplePath, SimulationArtifacts, SimulationBackendKind,
    SimulationConfig, SimulationDiagnostics, SimulationRun, TerminalHistogram,
    TerminalHistogramBin,
};
use crate::scoring::score_path;

#[derive(Clone, Copy, Debug, Default)]
pub struct CpuRayonBackend;

#[derive(Clone, Debug)]
struct PathResult {
    path_index: usize,
    prices: Vec<f64>,
    terminal: bool,
    no_touch: bool,
}

impl SimulationBackend for CpuRayonBackend {
    fn run(&self, input: &ProbabilityInput, config: &SimulationConfig) -> Result<SimulationRun> {
        validate_input(input)?;
        validate_config(config)?;

        let per_step_sigma = input.sigma_tau / (config.steps as f64).sqrt();
        let normal = Normal::new(0.0, per_step_sigma)
            .map_err(|error| anyhow::anyhow!("invalid normal distribution: {error}"))?;

        let path_results = (0..config.path_count)
            .into_par_iter()
            .map(|path_index| simulate_path(path_index, input, config, &normal))
            .collect::<Result<Vec<_>, _>>()?;

        let terminal_count = path_results.iter().filter(|result| result.terminal).count();
        let no_touch_count = path_results.iter().filter(|result| result.no_touch).count();

        let artifacts = if config.emit_artifacts {
            build_artifacts(&path_results, config.sample_path_limit)
        } else {
            SimulationArtifacts::default()
        };

        Ok(SimulationRun {
            state_id: input.state_id.clone(),
            asof_ts: input.asof_ts,
            p_finish: terminal_count as f64 / config.path_count as f64,
            p_no_touch: no_touch_count as f64 / config.path_count as f64,
            z_path: input.z_path,
            model_version: config.model_version.clone(),
            seed: config.seed,
            backend: SimulationBackendKind::CpuRayon,
            diagnostics: SimulationDiagnostics {
                path_count: config.path_count,
                steps: config.steps,
                elapsed_ms: 0,
                per_step_sigma,
            },
            artifacts,
        })
    }
}

fn validate_input(input: &ProbabilityInput) -> Result<()> {
    validate_positive_finite("settlement_price", input.settlement_price)?;
    validate_positive_finite("threshold", input.threshold)?;
    validate_positive_finite("sigma_tau", input.sigma_tau)
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

fn simulate_path(
    path_index: usize,
    input: &ProbabilityInput,
    config: &SimulationConfig,
    normal: &Normal<f64>,
) -> Result<PathResult> {
    let mut rng = ChaCha20Rng::seed_from_u64(path_seed(config.seed, path_index));
    let mut prices = Vec::with_capacity(config.steps + 1);
    let mut log_price = input.settlement_price.ln();
    prices.push(input.settlement_price);

    for _ in 0..config.steps {
        log_price += normal.sample(&mut rng);
        let price = log_price.exp();
        if !price.is_finite() || price <= 0.0 {
            bail!("generated path price must be positive and finite");
        }
        prices.push(price);
    }

    let score = score_path(input, &prices);
    Ok(PathResult {
        path_index,
        prices,
        terminal: score.terminal,
        no_touch: score.no_touch,
    })
}

fn path_seed(seed: u64, path_index: usize) -> u64 {
    let index = u64::try_from(path_index).unwrap_or(u64::MAX);
    seed ^ index.wrapping_mul(0x9E37_79B9_7F4A_7C15)
}

fn build_artifacts(path_results: &[PathResult], sample_path_limit: usize) -> SimulationArtifacts {
    let mut sorted_by_terminal = path_results.iter().collect::<Vec<_>>();
    sorted_by_terminal.sort_by(|left, right| {
        terminal_price(left)
            .partial_cmp(&terminal_price(right))
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    SimulationArtifacts {
        percentile_paths: percentile_paths(&sorted_by_terminal),
        sample_paths: path_results
            .iter()
            .take(sample_path_limit)
            .map(|result| SamplePath {
                path_index: result.path_index,
                prices: result.prices.clone(),
            })
            .collect(),
        terminal_histogram: terminal_histogram(path_results, 10),
    }
}

fn percentile_paths(sorted_by_terminal: &[&PathResult]) -> Vec<PercentilePath> {
    if sorted_by_terminal.is_empty() {
        return Vec::new();
    }

    [0.1, 0.5, 0.9]
        .into_iter()
        .map(|percentile| {
            let index = percentile_index(sorted_by_terminal.len(), percentile);
            PercentilePath {
                percentile,
                path: sorted_by_terminal[index].prices.clone(),
            }
        })
        .collect()
}

fn percentile_index(len: usize, percentile: f64) -> usize {
    let last_index = len.saturating_sub(1);
    ((last_index as f64) * percentile).round() as usize
}

fn terminal_histogram(path_results: &[PathResult], bin_count: usize) -> TerminalHistogram {
    let terminals = path_results.iter().map(terminal_price).collect::<Vec<_>>();
    let min = terminals.iter().copied().fold(f64::INFINITY, f64::min);
    let max = terminals.iter().copied().fold(f64::NEG_INFINITY, f64::max);

    if (max - min).abs() < f64::EPSILON {
        return TerminalHistogram {
            min,
            max,
            bins: vec![TerminalHistogramBin {
                lower: min,
                upper: max,
                count: terminals.len(),
            }],
        };
    }

    let width = (max - min) / bin_count as f64;
    let mut counts = vec![0usize; bin_count];
    for terminal in terminals {
        let raw_index = ((terminal - min) / width).floor() as usize;
        let index = raw_index.min(bin_count - 1);
        counts[index] += 1;
    }

    let bins = counts
        .into_iter()
        .enumerate()
        .map(|(index, count)| {
            let lower = min + width * index as f64;
            TerminalHistogramBin {
                lower,
                upper: lower + width,
                count,
            }
        })
        .collect();

    TerminalHistogram { min, max, bins }
}

fn terminal_price(path_result: &PathResult) -> f64 {
    path_result.prices.last().copied().unwrap_or_default()
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

        assert_eq!(first, second);
    }

    #[test]
    fn cpu_backend_outputs_probability_range_and_diagnostics() {
        let backend = CpuRayonBackend;
        let run = backend.run(&input(), &config()).unwrap();

        assert!((0.0..=1.0).contains(&run.p_finish));
        assert!((0.0..=1.0).contains(&run.p_no_touch));
        assert_eq!(run.backend, SimulationBackendKind::CpuRayon);
        assert_eq!(run.diagnostics.path_count, 512);
        assert_eq!(run.diagnostics.steps, 8);
        assert!(run.diagnostics.per_step_sigma > 0.0);
        assert_eq!(run.diagnostics.elapsed_ms, 0);
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
}
