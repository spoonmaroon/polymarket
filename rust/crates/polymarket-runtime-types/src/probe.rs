use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::{NormalizedOrderBook, NormalizedPriceTick, PriceDisagreement};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LatencyMark {
    pub name: String,
    pub elapsed_ms: u128,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProbeReport {
    pub schema_version: String,
    pub generated_at: DateTime<Utc>,
    pub elapsed_ms: u128,
    pub assets: Vec<String>,
    pub interval: String,
    pub windows: u8,
    pub orderbooks: Vec<NormalizedOrderBook>,
    pub prices: Vec<NormalizedPriceTick>,
    pub source_disagreements: Vec<PriceDisagreement>,
    pub latency_marks: Vec<LatencyMark>,
}
