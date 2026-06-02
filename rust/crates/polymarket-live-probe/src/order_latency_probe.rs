use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::time::Instant;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OrderLatencyProbeResult {
    pub schema_version: String,
    pub url: String,
    pub iterations: usize,
    pub payload_build_us: Vec<u128>,
    pub synthetic_sign_us: Vec<u128>,
    pub http_round_trip_ms: Vec<u128>,
}

impl OrderLatencyProbeResult {
    pub fn p50_http_round_trip_ms(&self) -> Option<u128> {
        percentile(&self.http_round_trip_ms, 50)
    }

    pub fn p95_http_round_trip_ms(&self) -> Option<u128> {
        percentile(&self.http_round_trip_ms, 95)
    }
}

pub async fn run_order_latency_probe(
    url: &str,
    iterations: usize,
) -> Result<OrderLatencyProbeResult> {
    let client = reqwest::Client::builder().build()?;
    let mut payload_build_us = Vec::with_capacity(iterations);
    let mut synthetic_sign_us = Vec::with_capacity(iterations);
    let mut http_round_trip_ms = Vec::with_capacity(iterations);

    for index in 0..iterations {
        let payload_started = Instant::now();
        let payload = serde_json::json!({
            "probe": "no-auth-order-latency",
            "iteration": index,
            "side": "BUY",
            "price": "0.50",
            "size": "1",
        });
        let payload_bytes = serde_json::to_vec(&payload)?;
        payload_build_us.push(payload_started.elapsed().as_micros());

        let sign_started = Instant::now();
        let mut hasher = DefaultHasher::new();
        payload_bytes.hash(&mut hasher);
        let _synthetic_signature = hasher.finish();
        synthetic_sign_us.push(sign_started.elapsed().as_micros());

        let http_started = Instant::now();
        let response = client.get(url).send().await?;
        let _status = response.status();
        let _body = response.bytes().await?;
        http_round_trip_ms.push(http_started.elapsed().as_millis());
    }

    Ok(OrderLatencyProbeResult {
        schema_version: "rust-order-latency-probe-v1".to_owned(),
        url: url.to_owned(),
        iterations,
        payload_build_us,
        synthetic_sign_us,
        http_round_trip_ms,
    })
}

fn percentile(values: &[u128], pct: u32) -> Option<u128> {
    if values.is_empty() {
        return None;
    }
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    let index = ((sorted.len() - 1) as u32 * pct / 100) as usize;
    sorted.get(index).copied()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentile_reports_p50_and_p95_from_sorted_copy() {
        let result = OrderLatencyProbeResult {
            schema_version: "rust-order-latency-probe-v1".to_owned(),
            url: "http://127.0.0.1:1".to_owned(),
            iterations: 5,
            payload_build_us: vec![1, 2, 3, 4, 5],
            synthetic_sign_us: vec![1, 2, 3, 4, 5],
            http_round_trip_ms: vec![100, 10, 50, 20, 30],
        };

        assert_eq!(result.p50_http_round_trip_ms(), Some(30));
        assert_eq!(result.p95_http_round_trip_ms(), Some(50));
    }
}
