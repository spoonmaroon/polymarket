use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum Asset {
    BTC,
    ETH,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum Side {
    UP,
    DOWN,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ComparisonOperator {
    #[serde(rename = ">")]
    GreaterThan,
    #[serde(rename = ">=")]
    GreaterThanOrEqual,
    #[serde(rename = "<")]
    LessThan,
    #[serde(rename = "<=")]
    LessThanOrEqual,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ProbabilityInput {
    pub state_id: String,
    pub asof_ts: DateTime<Utc>,
    pub asset: Asset,
    pub side: Side,
    pub comparison_operator: ComparisonOperator,
    pub seconds_left: f64,
    pub settlement_price: f64,
    pub threshold: f64,
    pub sigma_tau: f64,
    pub executable_price: f64,
    pub source_age_ms: u64,
    pub book_age_ms: u64,
    pub z_path: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SimulationBackendKind {
    CpuRayon,
    Cuda,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SimulationConfig {
    pub path_count: usize,
    pub steps: usize,
    pub seed: u64,
    pub backend: SimulationBackendKind,
    pub model_version: String,
    pub emit_artifacts: bool,
    pub sample_path_limit: usize,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SimulationRun {
    pub state_id: String,
    pub asof_ts: DateTime<Utc>,
    pub p_finish: f64,
    pub p_no_touch: f64,
    pub z_path: f64,
    pub model_version: String,
    pub seed: u64,
    pub backend: SimulationBackendKind,
    pub diagnostics: SimulationDiagnostics,
    pub artifacts: SimulationArtifacts,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SimulationDiagnostics {
    pub path_count: usize,
    pub steps: usize,
    pub elapsed_ms: u128,
    pub per_step_sigma: f64,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct SimulationArtifacts {
    pub percentile_paths: Vec<PercentilePath>,
    pub sample_paths: Vec<SamplePath>,
    pub terminal_histogram: TerminalHistogram,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PercentilePath {
    pub percentile: f64,
    pub path: Vec<f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SamplePath {
    pub path_index: usize,
    pub prices: Vec<f64>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct TerminalHistogram {
    pub min: f64,
    pub max: f64,
    pub bins: Vec<TerminalHistogramBin>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TerminalHistogramBin {
    pub lower: f64,
    pub upper: f64,
    pub count: usize,
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};

    use super::{
        Asset, ComparisonOperator, ProbabilityInput, Side, SimulationBackendKind, SimulationConfig,
    };

    #[test]
    fn enums_serialize_to_required_wire_values() {
        assert_eq!(serde_json::to_string(&Asset::BTC).unwrap(), "\"BTC\"");
        assert_eq!(serde_json::to_string(&Asset::ETH).unwrap(), "\"ETH\"");
        assert_eq!(serde_json::to_string(&Side::UP).unwrap(), "\"UP\"");
        assert_eq!(serde_json::to_string(&Side::DOWN).unwrap(), "\"DOWN\"");
        assert_eq!(
            serde_json::to_string(&SimulationBackendKind::CpuRayon).unwrap(),
            "\"cpu_rayon\""
        );
        assert_eq!(
            serde_json::to_string(&SimulationBackendKind::Cuda).unwrap(),
            "\"cuda\""
        );
    }

    #[test]
    fn comparison_operator_round_trips_symbol_values() {
        for (wire, operator) in [
            ("\">\"", ComparisonOperator::GreaterThan),
            ("\">=\"", ComparisonOperator::GreaterThanOrEqual),
            ("\"<\"", ComparisonOperator::LessThan),
            ("\"<=\"", ComparisonOperator::LessThanOrEqual),
        ] {
            assert_eq!(serde_json::to_string(&operator).unwrap(), wire);
            assert_eq!(
                serde_json::from_str::<ComparisonOperator>(wire).unwrap(),
                operator
            );
        }
    }

    #[test]
    fn probability_input_keeps_strict_contract_fields_serializable() {
        let input = ProbabilityInput {
            state_id: "state-1".to_string(),
            asof_ts: Utc.with_ymd_and_hms(2026, 6, 5, 12, 0, 0).unwrap(),
            asset: Asset::BTC,
            side: Side::UP,
            comparison_operator: ComparisonOperator::GreaterThanOrEqual,
            seconds_left: 90.0,
            settlement_price: 100.0,
            threshold: 101.0,
            sigma_tau: 0.2,
            executable_price: 0.55,
            source_age_ms: 42,
            book_age_ms: 84,
            z_path: 0.5,
        };

        let value = serde_json::to_value(&input).unwrap();

        assert_eq!(value["state_id"], "state-1");
        assert_eq!(value["asset"], "BTC");
        assert_eq!(value["side"], "UP");
        assert_eq!(value["comparison_operator"], ">=");
        assert_eq!(value["z_path"], 0.5);
    }

    #[test]
    fn simulation_config_keeps_backend_and_model_version_serializable() {
        let config = SimulationConfig {
            path_count: 1024,
            steps: 16,
            seed: 7,
            backend: SimulationBackendKind::CpuRayon,
            model_version: "cpu-ref-v1".to_string(),
            emit_artifacts: true,
            sample_path_limit: 4,
        };

        let value = serde_json::to_value(&config).unwrap();

        assert_eq!(value["backend"], "cpu_rayon");
        assert_eq!(value["model_version"], "cpu-ref-v1");
        assert_eq!(value["sample_path_limit"], 4);
    }
}
