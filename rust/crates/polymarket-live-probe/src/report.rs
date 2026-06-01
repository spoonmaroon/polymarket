use anyhow::Result;
use chrono::Utc;
use polymarket_runtime_types::{
    LatencyMark, NormalizedOrderBook, NormalizedPriceTick, PriceDisagreement, ProbeReport,
};
use std::path::Path;
use std::time::Instant;

pub const REPORT_SCHEMA_VERSION: &str = "rust-live-probe-v1";

pub struct ProbeTimer {
    started: Instant,
    marks: Vec<LatencyMark>,
}

pub struct ReportInput {
    pub assets: Vec<String>,
    pub interval: String,
    pub windows: u8,
    pub elapsed_ms: u128,
    pub latency_marks: Vec<LatencyMark>,
    pub orderbooks: Vec<NormalizedOrderBook>,
    pub prices: Vec<NormalizedPriceTick>,
    pub source_disagreements: Vec<PriceDisagreement>,
}

impl ProbeTimer {
    pub fn start() -> Self {
        Self {
            started: Instant::now(),
            marks: Vec::new(),
        }
    }

    pub fn mark(&mut self, name: &str) {
        self.marks.push(LatencyMark {
            name: name.to_owned(),
            elapsed_ms: self.started.elapsed().as_millis(),
        });
    }

    pub fn elapsed_ms(&self) -> u128 {
        self.started.elapsed().as_millis()
    }

    pub fn marks_snapshot(&self) -> Vec<LatencyMark> {
        self.marks.clone()
    }
}

pub fn build_report(input: ReportInput) -> ProbeReport {
    ProbeReport {
        schema_version: REPORT_SCHEMA_VERSION.to_owned(),
        generated_at: Utc::now(),
        elapsed_ms: input.elapsed_ms,
        assets: input.assets,
        interval: input.interval,
        windows: input.windows,
        orderbooks: input.orderbooks,
        prices: input.prices,
        source_disagreements: input.source_disagreements,
        latency_marks: input.latency_marks,
    }
}

pub fn write_report(path: &Path, report: &ProbeReport) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp_path = path.with_extension("json.tmp");
    std::fs::write(&tmp_path, serde_json::to_vec_pretty(report)?)?;
    std::fs::rename(tmp_path, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn report_has_schema_version() {
        let report = build_report(ReportInput {
            assets: vec!["BTC".to_owned()],
            interval: "5m".to_owned(),
            windows: 1,
            elapsed_ms: 10,
            latency_marks: vec![LatencyMark {
                name: "start".to_owned(),
                elapsed_ms: 0,
            }],
            orderbooks: vec![],
            prices: vec![],
            source_disagreements: vec![],
        });

        assert_eq!(report.schema_version, "rust-live-probe-v1");
        assert_eq!(report.elapsed_ms, 10);
    }
}
