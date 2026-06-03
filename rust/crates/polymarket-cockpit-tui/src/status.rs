use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeStatus {
    pub ok: bool,
    pub schema_kind: String,
    pub mode: String,
    pub age_ms: Option<u64>,
    pub counts: RuntimeCounts,
    pub latency_marks: Vec<RuntimeLatencyMark>,
    pub health_flags: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeCounts {
    pub prices: usize,
    pub orderbooks: usize,
    pub current: usize,
    pub next: usize,
    pub next_next: usize,
    pub websocket_status: usize,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeLatencyMark {
    pub name: String,
    pub elapsed_ms: Option<u64>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeGates {
    pub ok: bool,
    pub failures: Vec<String>,
}

impl RuntimeStatus {
    pub fn state_label(&self) -> &'static str {
        if self.ok && self.health_flags.is_empty() {
            "OK"
        } else {
            "BLOCKED"
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{RuntimeGates, RuntimeStatus};

    #[test]
    fn status_payload_parses_and_labels_ok() {
        let payload = r#"{
            "ok": true,
            "schema_kind": "rust-live-probe-state-manager-v1",
            "mode": "state-manager",
            "age_ms": 10,
            "counts": {"prices": 2, "orderbooks": 4, "current": 2, "next": 2, "next_next": 0, "websocket_status": 2},
            "latency_marks": [{"name": "current_orderbook_age_ms", "elapsed_ms": 3}],
            "health_flags": []
        }"#;

        let status: RuntimeStatus = serde_json::from_str(payload).unwrap();

        assert_eq!(status.state_label(), "OK");
        assert_eq!(status.counts.current, 2);
    }

    #[test]
    fn gate_payload_keeps_block_reasons() {
        let payload = r#"{"ok": false, "failures": ["status file stale"]}"#;

        let gates: RuntimeGates = serde_json::from_str(payload).unwrap();

        assert!(!gates.ok);
        assert_eq!(gates.failures, vec!["status file stale"]);
    }
}
