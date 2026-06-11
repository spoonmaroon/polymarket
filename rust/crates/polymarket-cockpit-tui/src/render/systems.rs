use crate::state::AppState;
use ratatui::{
    Frame,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, List, ListItem},
};

pub fn systems_summary_lines(app: &AppState) -> Vec<String> {
    let mut lines = if let Some(status) = &app.runtime_status {
        vec![
            format!("Engine API {}", status.state_label()),
            format!(
                "status_age_ms={}",
                status
                    .age_ms
                    .map_or("-".to_string(), |value| value.to_string())
            ),
            format!("prices={}", status.counts.prices),
            format!("orderbooks={}", status.counts.orderbooks),
        ]
    } else {
        vec!["Engine API UNKNOWN".to_string()]
    };

    if let Some(gates) = &app.runtime_gates {
        for failure in &gates.failures {
            lines.push(format!("block={failure}"));
        }
    }

    if let Some(recovery) = app.runtime_recovery.as_ref() {
        let phase = value_or_dash(&recovery.runtime_phase);
        let boot = recovery.boot_id.as_deref().unwrap_or("-");

        lines.push(format!("phase={phase}"));
        lines.push(format_offload(app.runtime_offload.as_ref()));
        lines.push(format!(
            "worker={}",
            format_worker(app.runtime_offload.as_ref())
        ));
        lines.push(format!("boot={boot}"));

        if !recovery.reasons.is_empty() {
            lines.push(format!("recovery_reasons={}", first_two(&recovery.reasons)));
        }
    } else if app
        .runtime_offload
        .as_ref()
        .is_some_and(has_meaningful_offload)
    {
        lines.push(format_offload(app.runtime_offload.as_ref()));
        lines.push(format!(
            "worker={}",
            format_worker(app.runtime_offload.as_ref())
        ));
    }

    if let Some(lag) = app.runtime_display_lag.as_ref() {
        if let Some(value) = lag.status_age_ms {
            lines.push(format!("status_age_ms={value}"));
        }
        if let Some(value) = lag.observed_to_state_us {
            lines.push(format!("state_us={value}"));
        }
        if let Some(value) = lag.api_build_ms {
            lines.push(format!("api_build_ms={value}"));
        }
        if let Some(value) = lag.tui_receive_lag_ms {
            lines.push(format!("tui_rx_ms={value}"));
        }
    }

    if let Some(volatility) = app.runtime_volatility.as_ref() {
        for row in &volatility.rows {
            lines.push(format!(
                "{} sigma={} short={} med={} long={} regime={}",
                row.asset,
                format_optional_vol(row.sigma_tau),
                format_optional_vol(row.short_realized_vol),
                format_optional_vol(row.medium_realized_vol),
                format_optional_vol(row.long_realized_vol),
                row.volatility_regime.as_deref().unwrap_or("-")
            ));
        }
        for error in &volatility.errors {
            lines.push(format!("volatility_error={error}"));
        }
    }

    if let Some(error) = &app.runtime_error {
        lines.push(format!("runtime_error={error}"));
    }

    lines
}

fn format_offload(offload: Option<&crate::status::RuntimeOffloadSummary>) -> String {
    let Some(offload) = offload.filter(|offload| has_meaningful_offload(offload)) else {
        return "offload=UNKNOWN".to_string();
    };
    let state = if offload.offload_allowed {
        "ALLOWED"
    } else {
        "BLOCKED"
    };
    format!(
        "offload={state} reasons={}",
        first_two_or_dash(&offload.reason_codes)
    )
}

fn format_worker(offload: Option<&crate::status::RuntimeOffloadSummary>) -> &str {
    offload
        .filter(|offload| has_meaningful_offload(offload))
        .map_or("-", |offload| {
            value_or_dash(&offload.recommended_worker_mode)
        })
}

fn has_meaningful_offload(offload: &crate::status::RuntimeOffloadSummary) -> bool {
    offload.offload_allowed
        || !offload.reason_codes.is_empty()
        || !offload.recommended_worker_mode.is_empty()
}

fn first_two(values: &[String]) -> String {
    values.iter().take(2).cloned().collect::<Vec<_>>().join(",")
}

fn first_two_or_dash(values: &[String]) -> String {
    if values.is_empty() {
        "-".to_string()
    } else {
        first_two(values)
    }
}

fn value_or_dash(value: &str) -> &str {
    if value.is_empty() { "-" } else { value }
}

fn format_optional_vol(value: Option<f64>) -> String {
    value.map_or_else(|| "-".to_string(), |value| format!("{value:.5}"))
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let items = systems_summary_lines(app)
        .into_iter()
        .map(ListItem::new)
        .collect::<Vec<_>>();
    let list = List::new(items)
        .block(Block::bordered().title("Systems"))
        .style(Style::default().fg(Color::Gray));

    frame.render_widget(list, area);
}

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{
            RuntimeCounts, RuntimeDisplayLag, RuntimeGates, RuntimeOffloadSummary,
            RuntimeRecoverySummary, RuntimeStatus, RuntimeVolatility, RuntimeVolatilityRow,
        },
    };

    use super::systems_summary_lines;

    #[test]
    fn systems_summary_shows_counts_and_gate_failures() {
        let app = AppState {
            runtime_status: Some(runtime_status(false, vec!["source stale".to_string()])),
            runtime_gates: Some(RuntimeGates {
                ok: false,
                failures: vec!["status file stale".to_string()],
            }),
            ..Default::default()
        };

        let text = systems_summary_lines(&app).join("\n");

        assert!(text.contains("Engine API BLOCKED"));
        assert!(text.contains("status_age_ms=42"));
        assert!(text.contains("prices=2"));
        assert!(text.contains("block=status file stale"));
    }

    #[test]
    fn systems_summary_keeps_counts_without_gates() {
        let app = AppState {
            runtime_status: Some(runtime_status(true, vec![])),
            ..Default::default()
        };

        let text = systems_summary_lines(&app).join("\n");

        assert!(text.contains("Engine API OK"));
        assert!(text.contains("status_age_ms=42"));
        assert!(text.contains("prices=2"));
        assert!(text.contains("orderbooks=4"));
        assert!(!text.contains("block="));
    }

    #[test]
    fn systems_summary_shows_runtime_lag_metrics() {
        let app = AppState {
            runtime_display_lag: Some(RuntimeDisplayLag {
                status_age_ms: Some(57),
                api_build_ms: Some(1),
                observed_to_state_us: Some(220),
                tui_receive_lag_ms: Some(8),
                ..RuntimeDisplayLag::default()
            }),
            ..Default::default()
        };

        let text = systems_summary_lines(&app).join("\n");

        assert!(text.contains("status_age_ms=57"));
        assert!(text.contains("state_us=220"));
        assert!(text.contains("tui_rx_ms=8"));
    }

    #[test]
    fn systems_summary_shows_recovery_and_offload_state() {
        let app = AppState {
            runtime_recovery: Some(RuntimeRecoverySummary {
                runtime_phase: "WARMING".to_string(),
                ready: false,
                reasons: vec!["warmup_active".to_string()],
                boot_id: Some("boot-1".to_string()),
            }),
            runtime_offload: Some(RuntimeOffloadSummary {
                offload_allowed: false,
                reason_codes: vec![
                    "runtime_not_ready".to_string(),
                    "probability_stale".to_string(),
                    "ignored_third".to_string(),
                ],
                recommended_worker_mode: "nowcast_only".to_string(),
            }),
            ..Default::default()
        };

        let text = systems_summary_lines(&app).join("\n");

        assert!(text.contains("phase=WARMING"));
        assert!(text.contains("offload=BLOCKED reasons=runtime_not_ready,probability_stale"));
        assert!(text.contains("worker=nowcast_only"));
        assert!(text.contains("boot=boot-1"));
        assert!(text.contains("recovery_reasons=warmup_active"));
    }

    #[test]
    fn systems_summary_does_not_block_when_recovery_present_and_offload_absent() {
        let app = AppState {
            runtime_recovery: Some(RuntimeRecoverySummary {
                runtime_phase: "WARMING".to_string(),
                ready: false,
                reasons: vec!["warmup_active".to_string()],
                boot_id: Some("boot-1".to_string()),
            }),
            ..Default::default()
        };

        let text = systems_summary_lines(&app).join("\n");

        assert!(text.contains("phase=WARMING"));
        assert!(text.contains("boot=boot-1"));
        assert!(text.contains("recovery_reasons=warmup_active"));
        assert!(text.contains("offload=UNKNOWN"));
        assert!(!text.contains("offload=BLOCKED"));
    }

    #[test]
    fn systems_summary_shows_live_volatility_diagnostics() {
        let app = AppState {
            runtime_volatility: Some(RuntimeVolatility {
                state: "OK".to_string(),
                rows: vec![RuntimeVolatilityRow {
                    asset: "BTC".to_string(),
                    asof_ts: Some("2026-06-03T21:00:00+00:00".to_string()),
                    sigma_tau: Some(0.0012),
                    short_realized_vol: Some(0.0001),
                    medium_realized_vol: Some(0.0002),
                    long_realized_vol: Some(0.0003),
                    volatility_regime: Some("normal".to_string()),
                    age_ms: Some(120),
                    flags: vec!["OK".to_string()],
                }],
                errors: vec![],
                ..RuntimeVolatility::default()
            }),
            ..Default::default()
        };

        let text = systems_summary_lines(&app).join("\n");

        assert!(text.contains("BTC sigma=0.00120"));
        assert!(text.contains("short=0.00010"));
        assert!(text.contains("med=0.00020"));
        assert!(text.contains("long=0.00030"));
        assert!(text.contains("regime=normal"));
    }

    fn runtime_status(ok: bool, health_flags: Vec<String>) -> RuntimeStatus {
        RuntimeStatus {
            ok,
            schema_kind: "rust-live-probe-state-manager-v1".to_string(),
            mode: "state-manager".to_string(),
            age_ms: Some(42),
            counts: RuntimeCounts {
                prices: 2,
                orderbooks: 4,
                current: 2,
                next: 2,
                next_next: 0,
                websocket_status: 2,
            },
            latency_marks: vec![],
            health_flags,
        }
    }
}
