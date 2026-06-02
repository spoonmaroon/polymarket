use anyhow::{Result, anyhow};
use chrono::{DateTime, Datelike, Timelike, Utc};
use polymarket_runtime_types::{
    ContractSide, HotDecisionQualityFlag, HotDecisionState, HotDecisionTriggerKind,
};
use rust_decimal::Decimal;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::PathBuf;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

const HOT_DECISION_SINK_BATCH_LIMIT: usize = 1024;

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
    file: Option<BufWriter<File>>,
}

#[derive(Clone, PartialEq)]
struct HotDecisionPersistSignature {
    partition_year: i32,
    partition_month: u32,
    partition_day: u32,
    partition_hour: u32,
    trigger_kind: HotDecisionTriggerKind,
    trigger_symbol: Option<String>,
    trigger_token_id: Option<String>,
    contract_asset: String,
    contract_interval: String,
    contract_start_ts: DateTime<Utc>,
    contract_end_ts: DateTime<Utc>,
    up_token_id: String,
    down_token_id: String,
    side: ContractSide,
    token_id: String,
    threshold_price: Option<Decimal>,
    threshold_event_ts: Option<DateTime<Utc>>,
    settlement_price: Option<Decimal>,
    settlement_event_ts: Option<DateTime<Utc>>,
    best_bid: Option<Decimal>,
    best_ask: Option<Decimal>,
    executable_price: Option<Decimal>,
    spread: Option<Decimal>,
    data_quality_flags: Vec<HotDecisionQualityFlag>,
}

impl HotDecisionJournalWriter {
    pub fn append(&mut self, state: &HotDecisionState) -> Result<PathBuf> {
        let path = self.append_buffered(state)?;
        self.flush()?;
        Ok(path)
    }

    fn append_buffered(&mut self, state: &HotDecisionState) -> Result<PathBuf> {
        let path = self.journal.partition_path(state);
        if self.open_path.as_ref() != Some(&path) {
            self.flush()?;
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            let file = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&path)?;
            self.file = Some(BufWriter::new(file));
            self.open_path = Some(path.clone());
        }

        let file = self
            .file
            .as_mut()
            .expect("hot decision journal writer opened file");
        write_hot_decision_jsonl_line(file, state)?;
        Ok(path)
    }

    fn flush(&mut self) -> Result<()> {
        if let Some(file) = &mut self.file {
            file.flush()?;
        }
        Ok(())
    }
}

fn write_hot_decision_jsonl_line<W: Write>(writer: &mut W, state: &HotDecisionState) -> Result<()> {
    serde_json::to_writer(&mut *writer, state)?;
    writer.write_all(b"\n")?;
    Ok(())
}

#[cfg(test)]
fn write_hot_decision_jsonl_batch<W: Write>(
    writer: &mut W,
    states: &[HotDecisionState],
) -> Result<()> {
    for state in states {
        write_hot_decision_jsonl_line(writer, state)?;
    }
    writer.flush()?;
    Ok(())
}

fn hot_decision_persist_signature(state: &HotDecisionState) -> HotDecisionPersistSignature {
    HotDecisionPersistSignature {
        partition_year: state.asof_ts.year(),
        partition_month: state.asof_ts.month(),
        partition_day: state.asof_ts.day(),
        partition_hour: state.asof_ts.hour(),
        trigger_kind: state.trigger_kind.clone(),
        trigger_symbol: state.trigger_symbol.clone(),
        trigger_token_id: state.trigger_token_id.clone(),
        contract_asset: state.contract.window.asset.clone(),
        contract_interval: state.contract.window.interval.clone(),
        contract_start_ts: state.contract.window.start_ts,
        contract_end_ts: state.contract.window.end_ts,
        up_token_id: state.contract.up.token_id.clone(),
        down_token_id: state.contract.down.token_id.clone(),
        side: state.side.clone(),
        token_id: state.token_id.clone(),
        threshold_price: state.threshold_price,
        threshold_event_ts: state.threshold_event_ts,
        settlement_price: state.settlement_price,
        settlement_event_ts: state.settlement_event_ts,
        best_bid: state.best_bid,
        best_ask: state.best_ask,
        executable_price: state.executable_price,
        spread: state.spread,
        data_quality_flags: state.data_quality_flags.clone(),
    }
}

fn append_if_changed(
    journal: &mut HotDecisionJournalWriter,
    state: &HotDecisionState,
    last_signature: &mut Option<HotDecisionPersistSignature>,
) -> Result<()> {
    let signature = hot_decision_persist_signature(state);
    if last_signature.as_ref() == Some(&signature) {
        return Ok(());
    }
    *last_signature = Some(signature);
    journal.append_buffered(state)?;
    Ok(())
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
            let mut last_signature: Option<HotDecisionPersistSignature> = None;
            while let Some(state) = receiver.recv().await {
                append_if_changed(&mut journal, &state, &mut last_signature)?;
                let mut drained = 1;
                while drained < HOT_DECISION_SINK_BATCH_LIMIT {
                    let Ok(state) = receiver.try_recv() else {
                        break;
                    };
                    append_if_changed(&mut journal, &state, &mut last_signature)?;
                    drained += 1;
                }
                journal.flush()?;
            }
            journal.flush()?;
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
    use std::io::{BufWriter, Write};

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

    #[tokio::test]
    async fn sink_skips_consecutive_duplicate_hot_decision_market_state() {
        let root = std::env::temp_dir().join(format!(
            "polymarket-hot-decision-dedup-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let (sink, handle) = HotDecisionSink::start(root.clone(), 16);
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let first = sample_state(start);
        let mut duplicate = first.clone();
        duplicate.state_id = "state-2".to_owned();
        duplicate.asof_ts = start + Duration::seconds(1);
        duplicate.source_age_ms = Some(501);
        duplicate.book_age_ms = Some(61);
        duplicate.latency.observed_to_state_us = 900;

        sink.try_record(first).unwrap();
        sink.try_record(duplicate).unwrap();
        drop(sink);
        handle.await.unwrap().unwrap();

        let path = root
            .join("polymarket_decision_state")
            .join("hot_state")
            .join("date=2026-06-01")
            .join("hour=08")
            .join("decision-state.jsonl");
        let rows = std::fs::read_to_string(path)
            .unwrap()
            .lines()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        assert_eq!(rows.len(), 1);
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&rows[0]).unwrap()["state_id"],
            "state-1"
        );
    }

    #[tokio::test]
    async fn sink_persists_hot_decision_when_market_state_changes_after_duplicate() {
        let root = std::env::temp_dir().join(format!(
            "polymarket-hot-decision-dedup-change-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let (sink, handle) = HotDecisionSink::start(root.clone(), 16);
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let first = sample_state(start);
        let mut duplicate = first.clone();
        duplicate.state_id = "state-2".to_owned();
        duplicate.asof_ts = start + Duration::seconds(1);
        let mut changed = first.clone();
        changed.state_id = "state-3".to_owned();
        changed.best_bid = Some(rust_decimal::Decimal::new(62, 2));

        sink.try_record(first).unwrap();
        sink.try_record(duplicate).unwrap();
        sink.try_record(changed).unwrap();
        drop(sink);
        handle.await.unwrap().unwrap();

        let path = root
            .join("polymarket_decision_state")
            .join("hot_state")
            .join("date=2026-06-01")
            .join("hour=08")
            .join("decision-state.jsonl");
        let rows = std::fs::read_to_string(path)
            .unwrap()
            .lines()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        assert_eq!(rows.len(), 2);
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&rows[0]).unwrap()["state_id"],
            "state-1"
        );
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&rows[1]).unwrap()["state_id"],
            "state-3"
        );
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

    #[test]
    fn hot_decision_jsonl_batch_flushes_multiple_rows_once() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let first = sample_state(start);
        let mut second = sample_state(start + Duration::seconds(1));
        second.state_id = "state-2".to_owned();
        let mut inner = CountingWriter::default();
        {
            let mut writer = BufWriter::new(&mut inner);
            write_hot_decision_jsonl_batch(&mut writer, &[first, second]).unwrap();
        }

        assert_eq!(inner.write_calls, 1);
        let raw = String::from_utf8(inner.bytes).unwrap();
        let rows = raw.lines().collect::<Vec<_>>();
        assert_eq!(rows.len(), 2);
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(rows[0]).unwrap()["state_id"],
            "state-1"
        );
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(rows[1]).unwrap()["state_id"],
            "state-2"
        );
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

    #[derive(Default)]
    struct CountingWriter {
        bytes: Vec<u8>,
        write_calls: usize,
    }

    impl Write for CountingWriter {
        fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
            self.write_calls += 1;
            self.bytes.extend_from_slice(buf);
            Ok(buf.len())
        }

        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
}
