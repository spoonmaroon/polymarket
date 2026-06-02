use anyhow::{Result, anyhow};
use chrono::{DateTime, Datelike, Timelike, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::io::Write;
use std::path::PathBuf;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RawEventRecord {
    pub source_key: String,
    pub stream_key: String,
    pub symbol: String,
    pub event_type: String,
    pub event_ts: DateTime<Utc>,
    pub observed_ts: DateTime<Utc>,
    pub payload: Value,
}

#[derive(Clone)]
pub struct RawEventJournal {
    root: PathBuf,
}

impl RawEventJournal {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn append(&self, event: &RawEventRecord) -> Result<PathBuf> {
        let path = self.partition_path(event);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)?;
        serde_json::to_writer(&mut file, event)?;
        file.write_all(b"\n")?;
        Ok(path)
    }

    fn partition_path(&self, event: &RawEventRecord) -> PathBuf {
        let event_ts = event.event_ts;
        self.root
            .join(&event.source_key)
            .join(&event.stream_key)
            .join(format!(
                "date={:04}-{:02}-{:02}",
                event_ts.year(),
                event_ts.month(),
                event_ts.day()
            ))
            .join(format!("hour={:02}", event_ts.hour()))
            .join("events.jsonl")
    }
}

#[derive(Clone)]
pub struct RawEventSink {
    sender: mpsc::Sender<RawEventRecord>,
}

impl RawEventSink {
    pub fn start(root: PathBuf, buffer_size: usize) -> (Self, JoinHandle<Result<()>>) {
        let (sender, mut receiver) = mpsc::channel(buffer_size);
        let journal = RawEventJournal::new(root);
        let handle = tokio::spawn(async move {
            while let Some(event) = receiver.recv().await {
                journal.append(&event)?;
            }
            Ok(())
        });
        (Self { sender }, handle)
    }

    pub fn try_record(&self, event: RawEventRecord) -> Result<()> {
        self.sender
            .try_send(event)
            .map_err(|error| anyhow!("raw event journal queue unavailable: {error}"))
    }
}

#[cfg(test)]
mod tests {
    use crate::raw_event_journal::{RawEventJournal, RawEventRecord, RawEventSink};
    use chrono::{TimeZone, Utc};
    use serde_json::json;

    #[test]
    fn appends_raw_events_to_source_stream_hour_partition() {
        let root = temp_root("append");
        let journal = RawEventJournal::new(root.clone());
        let event = RawEventRecord {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            stream_key: "price_update".to_owned(),
            symbol: "BTC/USD".to_owned(),
            event_type: "chainlink_price".to_owned(),
            event_ts: Utc.timestamp_opt(1_780_302_400, 0).unwrap(),
            observed_ts: Utc.timestamp_opt(1_780_302_401, 0).unwrap(),
            payload: json!({"value": "70000.1"}),
        };

        let path = journal.append(&event).unwrap();

        assert_eq!(
            path,
            root.join("polymarket_rtds_chainlink")
                .join("price_update")
                .join("date=2026-06-01")
                .join("hour=08")
                .join("events.jsonl")
        );
        let row: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        assert_eq!(row["source_key"], "polymarket_rtds_chainlink");
        assert_eq!(row["stream_key"], "price_update");
        assert_eq!(row["symbol"], "BTC/USD");
        assert_eq!(row["event_type"], "chainlink_price");
        assert_eq!(row["event_ts"], "2026-06-01T08:26:40Z");
        assert_eq!(row["observed_ts"], "2026-06-01T08:26:41Z");
        assert_eq!(row["payload"]["value"], "70000.1");
    }

    #[tokio::test]
    async fn sink_records_events_without_calling_writer_on_hot_path() {
        let root = temp_root("sink");
        let (sink, handle) = RawEventSink::start(root.clone(), 16);
        let event = RawEventRecord {
            source_key: "polymarket_clob_market_ws".to_owned(),
            stream_key: "best_bid_ask".to_owned(),
            symbol: "token-1".to_owned(),
            event_type: "best_bid_ask".to_owned(),
            event_ts: Utc.timestamp_opt(1_780_302_400, 0).unwrap(),
            observed_ts: Utc.timestamp_opt(1_780_302_401, 0).unwrap(),
            payload: json!({"best_bid": "0.49", "best_ask": "0.51"}),
        };

        sink.try_record(event).unwrap();
        drop(sink);
        handle.await.unwrap().unwrap();

        let path = root
            .join("polymarket_clob_market_ws")
            .join("best_bid_ask")
            .join("date=2026-06-01")
            .join("hour=08")
            .join("events.jsonl");
        let row: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        assert_eq!(row["source_key"], "polymarket_clob_market_ws");
        assert_eq!(row["stream_key"], "best_bid_ask");
        assert_eq!(row["payload"]["best_bid"], "0.49");
    }

    fn temp_root(label: &str) -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!(
            "polymarket-raw-event-journal-{label}-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ));
        if root.exists() {
            std::fs::remove_dir_all(&root).unwrap();
        }
        root
    }
}
