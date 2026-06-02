use anyhow::{Result, anyhow};
use chrono::{Datelike, Timelike};
use polymarket_runtime_types::HotDecisionState;
use std::fs::File;
use std::io::Write;
use std::path::PathBuf;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

#[derive(Clone)]
pub struct HotDecisionJournal {
    root: PathBuf,
}

impl HotDecisionJournal {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn writer(&self) -> HotDecisionJournalWriter {
        HotDecisionJournalWriter {
            journal: self.clone(),
            open_path: None,
            file: None,
        }
    }

    #[allow(dead_code)]
    pub fn append(&self, state: &HotDecisionState) -> Result<PathBuf> {
        self.writer().append(state)
    }

    fn partition_path(&self, state: &HotDecisionState) -> PathBuf {
        let ts = state.asof_ts;
        self.root
            .join("polymarket_decision_state")
            .join("hot_state")
            .join(format!(
                "date={:04}-{:02}-{:02}",
                ts.year(),
                ts.month(),
                ts.day()
            ))
            .join(format!("hour={:02}", ts.hour()))
            .join("decision-state.jsonl")
    }
}

pub struct HotDecisionJournalWriter {
    journal: HotDecisionJournal,
    open_path: Option<PathBuf>,
    file: Option<File>,
}

impl HotDecisionJournalWriter {
    pub fn append(&mut self, state: &HotDecisionState) -> Result<PathBuf> {
        let path = self.journal.partition_path(state);
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
            .expect("hot decision journal writer opened file");
        serde_json::to_writer(&mut *file, state)?;
        file.write_all(b"\n")?;
        Ok(path)
    }
}

#[derive(Clone)]
pub struct HotDecisionSink {
    sender: mpsc::Sender<HotDecisionState>,
}

impl HotDecisionSink {
    pub fn start(root: PathBuf, buffer_size: usize) -> (Self, JoinHandle<Result<()>>) {
        let (sender, mut receiver) = mpsc::channel(buffer_size.max(1));
        let mut journal = HotDecisionJournal::new(root).writer();
        let handle = tokio::spawn(async move {
            while let Some(state) = receiver.recv().await {
                journal.append(&state)?;
            }
            Ok(())
        });
        (Self { sender }, handle)
    }

    pub fn try_record(&self, state: HotDecisionState) -> Result<()> {
        self.sender
            .try_send(state)
            .map_err(|error| anyhow!("hot decision journal queue unavailable: {error}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Duration, TimeZone, Utc};
    use polymarket_runtime_types::{
        ContractSide, ContractToken, ContractWindow, HOT_DECISION_STATE_SCHEMA_VERSION,
        HotDecisionLatency, HotDecisionQualityFlag, HotDecisionTriggerKind, WarmedContract,
    };

    #[tokio::test]
    async fn sink_records_hot_decision_without_writing_on_hot_path() {
        let root = std::env::temp_dir().join(format!(
            "polymarket-hot-decision-journal-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let (sink, handle) = HotDecisionSink::start(root.clone(), 16);
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        sink.try_record(sample_state(start)).unwrap();
        drop(sink);
        handle.await.unwrap().unwrap();

        let path = root
            .join("polymarket_decision_state")
            .join("hot_state")
            .join("date=2026-06-01")
            .join("hour=08")
            .join("decision-state.jsonl");
        let row: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        assert_eq!(row["schema_version"], HOT_DECISION_STATE_SCHEMA_VERSION);
        assert_eq!(row["data_quality_flags"], serde_json::json!([]));
    }

    #[test]
    fn streaming_writer_reuses_current_partition_and_rolls_hours() {
        let root = std::env::temp_dir().join(format!(
            "polymarket-hot-decision-streaming-writer-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let journal = HotDecisionJournal::new(root);
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();

        let mut writer = journal.writer();
        let first_path = writer.append(&sample_state(start)).unwrap();
        let second_path = writer
            .append(&sample_state(start + Duration::seconds(1)))
            .unwrap();
        let next_hour_path = writer
            .append(&sample_state(start + Duration::seconds(3_600)))
            .unwrap();

        assert_eq!(first_path, second_path);
        assert_ne!(first_path, next_hour_path);
        let first_hour_lines = std::fs::read_to_string(first_path).unwrap();
        assert_eq!(first_hour_lines.lines().count(), 2);
        let next_hour_lines = std::fs::read_to_string(next_hour_path).unwrap();
        assert_eq!(next_hour_lines.lines().count(), 1);
    }

    fn sample_state(start: chrono::DateTime<Utc>) -> polymarket_runtime_types::HotDecisionState {
        let contract = WarmedContract::new(
            ContractWindow::new("BTC", "5m", start, start + Duration::seconds(300)).unwrap(),
            ContractToken::new("BTC", ContractSide::Up, "up-token"),
            ContractToken::new("BTC", ContractSide::Down, "down-token"),
        )
        .unwrap();
        polymarket_runtime_types::HotDecisionState {
            schema_version: HOT_DECISION_STATE_SCHEMA_VERSION.to_owned(),
            state_id: "state-1".to_owned(),
            trigger_kind: HotDecisionTriggerKind::OrderBookTopOfBook,
            trigger_symbol: None,
            trigger_token_id: Some("up-token".to_owned()),
            asof_ts: start,
            contract,
            side: ContractSide::Up,
            token_id: "up-token".to_owned(),
            threshold_price: None,
            threshold_event_ts: None,
            settlement_price: None,
            settlement_event_ts: None,
            best_bid: None,
            best_ask: None,
            executable_price: None,
            spread: None,
            source_age_ms: None,
            book_age_ms: None,
            data_quality_flags: Vec::<HotDecisionQualityFlag>::new(),
            latency: HotDecisionLatency {
                trigger_event_to_observed_ms: 10,
                observed_to_state_us: 20,
                state_to_persist_us: None,
                total_event_to_persist_ms: None,
            },
        }
    }
}
