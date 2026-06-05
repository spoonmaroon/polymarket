use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;

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
    pub diagnostics: Value,
    pub artifacts: SimulationArtifacts,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct SimulationArtifacts {
    pub percentile_paths: Vec<PercentilePoint>,
    pub sample_paths: Vec<Vec<f64>>,
    pub terminal_histogram: Vec<HistogramBin>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PercentilePoint {
    pub step: usize,
    pub p05: f64,
    pub p25: f64,
    pub p50: f64,
    pub p75: f64,
    pub p95: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HistogramBin {
    pub min_price: f64,
    pub max_price: f64,
    pub count: usize,
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};

    use super::{
        Asset, ComparisonOperator, HistogramBin, PercentilePoint, ProbabilityInput, Side,
        SimulationArtifacts, SimulationBackendKind, SimulationConfig, SimulationRun,
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

    #[test]
    fn simulation_run_uses_json_diagnostics_object() {
        let run = SimulationRun {
            state_id: "state-1".to_string(),
            asof_ts: Utc.with_ymd_and_hms(2026, 6, 5, 12, 0, 0).unwrap(),
            p_finish: 0.42,
            p_no_touch: 0.58,
            z_path: 0.5,
            model_version: "cpu-ref-v1".to_string(),
            seed: 7,
            backend: SimulationBackendKind::CpuRayon,
            diagnostics: serde_json::json!({
                "path_count": 1024,
                "steps": 16,
                "elapsed_ms": 3,
                "per_step_sigma": 0.01
            }),
            artifacts: SimulationArtifacts::default(),
        };

        let value = serde_json::to_value(&run).unwrap();

        assert!(value["diagnostics"].is_object());
        assert_eq!(value["diagnostics"]["path_count"], 1024);
        assert_eq!(value["diagnostics"]["steps"], 16);
        assert_eq!(value["diagnostics"]["elapsed_ms"], 3);
        assert_eq!(value["diagnostics"]["per_step_sigma"], 0.01);
    }

    #[test]
    fn simulation_artifacts_keep_planned_json_field_names() {
        let artifacts = SimulationArtifacts {
            percentile_paths: vec![PercentilePoint {
                step: 1,
                p05: 95.0,
                p25: 97.0,
                p50: 100.0,
                p75: 103.0,
                p95: 105.0,
            }],
            sample_paths: vec![vec![100.0, 101.0]],
            terminal_histogram: vec![HistogramBin {
                min_price: 99.0,
                max_price: 101.0,
                count: 7,
            }],
        };

        let value = serde_json::to_value(&artifacts).unwrap();

        assert!(value.get("percentile_paths").is_some());
        assert!(value.get("sample_paths").is_some());
        assert!(value.get("terminal_histogram").is_some());
        assert_eq!(value["percentile_paths"][0]["step"], 1);
        assert_eq!(value["percentile_paths"][0]["p05"], 95.0);
        assert_eq!(value["percentile_paths"][0]["p25"], 97.0);
        assert_eq!(value["percentile_paths"][0]["p50"], 100.0);
        assert_eq!(value["percentile_paths"][0]["p75"], 103.0);
        assert_eq!(value["percentile_paths"][0]["p95"], 105.0);
        assert_eq!(value["sample_paths"][0][0], 100.0);
        assert_eq!(value["terminal_histogram"][0]["min_price"], 99.0);
        assert_eq!(value["terminal_histogram"][0]["max_price"], 101.0);
        assert_eq!(value["terminal_histogram"][0]["count"], 7);
    }
}
