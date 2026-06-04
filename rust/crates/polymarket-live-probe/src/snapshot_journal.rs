use crate::report::StateManagerReport;
use anyhow::Result;
use chrono::{Datelike, Timelike};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::PathBuf;

#[derive(Clone)]
pub struct StateSnapshotJournal {
    root: PathBuf,
}

impl StateSnapshotJournal {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn writer(&self) -> StateSnapshotJournalWriter {
        StateSnapshotJournalWriter {
            journal: self.clone(),
            open_path: None,
            file: None,
        }
    }

    #[cfg(test)]
    pub fn append(&self, report: &StateManagerReport) -> Result<PathBuf> {
        self.writer().append(report)
    }

    fn partition_path(&self, report: &StateManagerReport) -> PathBuf {
        let generated_at = report.generated_at;
        self.root
            .join(format!(
                "date={:04}-{:02}-{:02}",
                generated_at.year(),
                generated_at.month(),
                generated_at.day()
            ))
            .join(format!("hour={:02}", generated_at.hour()))
            .join("state-manager.jsonl")
    }
}

pub struct StateSnapshotJournalWriter {
    journal: StateSnapshotJournal,
    open_path: Option<PathBuf>,
    file: Option<BufWriter<File>>,
}

impl StateSnapshotJournalWriter {
    pub fn append(&mut self, report: &StateManagerReport) -> Result<PathBuf> {
        let path = self.partition_path(report);
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
            .expect("state snapshot journal writer opened file");
        write_state_snapshot_jsonl_line(file, report)?;
        self.flush()?;
        Ok(path)
    }

    fn partition_path(&self, report: &StateManagerReport) -> PathBuf {
        self.journal.partition_path(report)
    }

    fn flush(&mut self) -> Result<()> {
        if let Some(file) = &mut self.file {
            file.flush()?;
        }
        Ok(())
    }
}

fn write_state_snapshot_jsonl_line<W: Write>(
    writer: &mut W,
    report: &StateManagerReport,
) -> Result<()> {
    serde_json::to_writer(&mut *writer, report)?;
    writer.write_all(b"\n")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use crate::report::{
        StateManagerReportInput, StateManagerSubscription, WebSocketStatus,
        build_state_manager_report,
    };
    use crate::snapshot_journal::{StateSnapshotJournal, write_state_snapshot_jsonl_line};
    use chrono::{TimeZone, Utc};
    use polymarket_runtime_types::{
        ContractSide, ContractToken, ContractWindow, WarmStateSnapshot, WarmedContract,
    };
    use std::io::{BufWriter, Write};

    #[test]
    fn appends_state_reports_to_utc_hour_partition() {
        let root = temp_root("hour-partition");
        let journal = StateSnapshotJournal::new(root.clone());
        let mut report = sample_report(1_780_302_400);

        let path = journal.append(&report).unwrap();
        report.elapsed_ms = 2;
        journal.append(&report).unwrap();

        assert_eq!(
            path,
            root.join("date=2026-06-01")
                .join("hour=08")
                .join("state-manager.jsonl")
        );
        let lines = std::fs::read_to_string(path)
            .unwrap()
            .lines()
            .map(str::to_owned)
            .collect::<Vec<_>>();
        assert_eq!(lines.len(), 2);
        assert!(lines[0].contains("\"schema_version\":\"rust-live-probe-state-manager-v1\""));
        assert!(lines[0].contains("\"mode\":\"state-manager\""));
        assert!(lines[1].contains("\"elapsed_ms\":2"));
    }

    #[test]
    fn partitions_state_reports_by_generated_hour() {
        let root = temp_root("cross-hour");
        let journal = StateSnapshotJournal::new(root.clone());

        let first = journal.append(&sample_report(1_780_302_400)).unwrap();
        let second = journal.append(&sample_report(1_780_306_000)).unwrap();

        assert_ne!(first, second);
        assert!(first.ends_with("date=2026-06-01/hour=08/state-manager.jsonl"));
        assert!(second.ends_with("date=2026-06-01/hour=09/state-manager.jsonl"));
    }

    #[test]
    fn streaming_writer_reuses_current_partition_and_rolls_hours() {
        let root = temp_root("streaming-writer");
        let journal = StateSnapshotJournal::new(root.clone());
        let mut writer = journal.writer();

        let first = writer.append(&sample_report(1_780_302_400)).unwrap();
        let second = writer.append(&sample_report(1_780_302_401)).unwrap();
        let next_hour = writer.append(&sample_report(1_780_306_000)).unwrap();

        assert_eq!(first, second);
        assert_ne!(first, next_hour);
        let first_hour_lines = std::fs::read_to_string(first).unwrap();
        assert_eq!(first_hour_lines.lines().count(), 2);
        let next_hour_lines = std::fs::read_to_string(next_hour).unwrap();
        assert_eq!(next_hour_lines.lines().count(), 1);
    }

    #[test]
    fn state_snapshot_jsonl_line_is_buffered_until_flush() {
        let mut inner = CountingWriter::default();
        {
            let mut writer = BufWriter::new(&mut inner);
            write_state_snapshot_jsonl_line(&mut writer, &sample_report(1_780_302_400)).unwrap();
            writer.flush().unwrap();
        }

        assert_eq!(inner.write_calls, 1);
        let raw = String::from_utf8(inner.bytes).unwrap();
        assert!(raw.ends_with('\n'));
        let row: serde_json::Value = serde_json::from_str(raw.trim_end()).unwrap();
        assert_eq!(row["schema_version"], "rust-live-probe-state-manager-v1");
        assert_eq!(row["mode"], "state-manager");
    }

    fn sample_report(epoch: i64) -> crate::report::StateManagerReport {
        let ts = Utc.timestamp_opt(epoch, 0).unwrap();
        let window =
            ContractWindow::new("BTC", "5m", ts, ts + chrono::Duration::minutes(5)).unwrap();
        let contract = WarmedContract::new(
            window,
            ContractToken::new("BTC", ContractSide::Up, "btc-up"),
            ContractToken::new("BTC", ContractSide::Down, "btc-down"),
        )
        .unwrap();
        let snapshot = WarmStateSnapshot {
            observed_ts: ts,
            current: vec![contract],
            next: vec![],
            next_next: vec![],
            chainlink_prices: vec![],
            proxy_prices: vec![],
            orderbooks: vec![],
            targets: vec![],
            freshness: vec![],
            health_flags: vec![],
        };
        let mut report = build_state_manager_report(StateManagerReportInput {
            elapsed_ms: 1,
            snapshot,
            subscriptions: vec![StateManagerSubscription {
                source_key: "polymarket_clob_market_ws".to_owned(),
                channel: "best_bid_ask".to_owned(),
                asset: "BTC".to_owned(),
                token_id: "btc-up".to_owned(),
            }],
            websocket_status: vec![WebSocketStatus {
                source_key: "polymarket_rtds_chainlink".to_owned(),
                channel: "crypto_prices_chainlink".to_owned(),
                connection_state: "Connected".to_owned(),
                reconnect_count: 0,
                subscription_count: 1,
                active_token_count: 2,
                ended_stream_count: 0,
                stream_error_count: 0,
                last_event_age_ms: Some(10),
            }],
            hot_decision_telemetry: None,
        });
        report.generated_at = ts;
        report
    }

    fn temp_root(label: &str) -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!(
            "polymarket-snapshot-journal-{label}-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ));
        if root.exists() {
            std::fs::remove_dir_all(&root).unwrap();
        }
        root
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
