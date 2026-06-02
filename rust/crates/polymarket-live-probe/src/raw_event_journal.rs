use anyhow::{Result, anyhow};
use chrono::{DateTime, Datelike, Timelike, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs::File;
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

    pub fn writer(&self) -> RawEventJournalWriter {
        RawEventJournalWriter {
            journal: self.clone(),
            open_path: None,
            file: None,
        }
    }

    #[allow(dead_code)]
    pub fn append(&self, event: &RawEventRecord) -> Result<PathBuf> {
        self.writer().append(event)
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

pub struct RawEventJournalWriter {
    journal: RawEventJournal,
    open_path: Option<PathBuf>,
    file: Option<File>,
}

impl RawEventJournalWriter {
    pub fn append(&mut self, event: &RawEventRecord) -> Result<PathBuf> {
        let path = self.journal.partition_path(event);
        if self.open_path.as_ref() != Some(&path) {
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            self.file = Some(
                std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&path)?,
            );
            self.open_path = Some(path.clone());
        }

        let file = self
            .file
            .as_mut()
            .expect("raw event journal writer opened file");
        serde_json::to_writer(&mut *file, event)?;
        file.write_all(b"\n")?;
        Ok(path)
    }
}

#[derive(Clone)]
pub struct RawEventSink {
    sender: mpsc::Sender<RawEventRecord>,
}

impl RawEventSink {
    pub fn start(root: PathBuf, buffer_size: usize) -> (Self, JoinHandle<Result<()>>) {
        let (sender, mut receiver) = mpsc::channel(buffer_size);
        let mut journal = RawEventJournal::new(root).writer();
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

    #[test]
    fn streaming_writer_reuses_current_partition_and_rolls_hours() {
        let root = temp_root("streaming-writer");
        let journal = RawEventJournal::new(root.clone());
        let first = RawEventRecord {
            source_key: "polymarket_clob_market_ws".to_owned(),
            stream_key: "best_bid_ask".to_owned(),
            symbol: "token-1".to_owned(),
            event_type: "best_bid_ask".to_owned(),
            event_ts: Utc.timestamp_opt(1_780_302_400, 0).unwrap(),
            observed_ts: Utc.timestamp_opt(1_780_302_401, 0).unwrap(),
            payload: json!({"seq": 1}),
        };
        let mut second = first.clone();
        second.observed_ts = Utc.timestamp_opt(1_780_302_402, 0).unwrap();
        second.payload = json!({"seq": 2});
        let mut next_hour = first.clone();
        next_hour.event_ts = Utc.timestamp_opt(1_780_306_000, 0).unwrap();
        next_hour.observed_ts = Utc.timestamp_opt(1_780_306_001, 0).unwrap();
        next_hour.payload = json!({"seq": 3});

        let mut writer = journal.writer();
        let first_path = writer.append(&first).unwrap();
        let second_path = writer.append(&second).unwrap();
        let next_hour_path = writer.append(&next_hour).unwrap();

        assert_eq!(first_path, second_path);
        assert_ne!(first_path, next_hour_path);
        let first_hour_lines = std::fs::read_to_string(first_path).unwrap();
        assert_eq!(first_hour_lines.lines().count(), 2);
        assert!(first_hour_lines.contains("\"seq\":1"));
        assert!(first_hour_lines.contains("\"seq\":2"));
        let next_hour_lines = std::fs::read_to_string(next_hour_path).unwrap();
        assert_eq!(next_hour_lines.lines().count(), 1);
        assert!(next_hour_lines.contains("\"seq\":3"));
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
