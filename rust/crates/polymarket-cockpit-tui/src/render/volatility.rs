use ratatui::{
    Frame,
    layout::{Constraint, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{
    state::AppState,
    status::{RuntimeVolatility, RuntimeVolatilityRow},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VolatilityTableModel {
    pub headers: Vec<&'static str>,
    pub rows: Vec<Vec<String>>,
}

pub fn volatility_header_labels() -> [&'static str; 9] {
    [
        "Asset",
        "sigma_tau",
        "short",
        "medium",
        "long",
        "regime",
        "Source",
        "Lookback",
        "Age/Flags",
    ]
}

pub fn volatility_table(app: &AppState) -> VolatilityTableModel {
    let Some(volatility) = app.runtime_volatility.as_ref() else {
        return VolatilityTableModel {
            headers: volatility_header_labels().to_vec(),
            rows: vec![vec![
                "volatility pending".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
            ]],
        };
    };

    if volatility.rows.is_empty() {
        let status = if volatility.errors.is_empty() {
            volatility.state.clone()
        } else {
            volatility.errors.join(",")
        };
        return VolatilityTableModel {
            headers: volatility_header_labels().to_vec(),
            rows: vec![vec![
                "volatility pending".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                status,
                format_source(&volatility.source_key),
                format_lookback(volatility.lookback_limit),
                "-".to_string(),
            ]],
        };
    }

    VolatilityTableModel {
        headers: volatility_header_labels().to_vec(),
        rows: volatility
            .rows
            .iter()
            .map(|row| volatility_row(volatility, row))
            .collect(),
    }
}

fn volatility_row(volatility: &RuntimeVolatility, row: &RuntimeVolatilityRow) -> Vec<String> {
    vec![
        row.asset.clone(),
        format_optional_vol(row.sigma_tau),
        format_optional_vol(row.short_realized_vol),
        format_optional_vol(row.medium_realized_vol),
        format_optional_vol(row.long_realized_vol),
        row.volatility_regime
            .clone()
            .unwrap_or_else(|| "-".to_string()),
        format_source(&volatility.source_key),
        format_lookback(volatility.lookback_limit),
        volatility_age_flags(row),
    ]
}

fn format_optional_vol(value: Option<f64>) -> String {
    value.map_or_else(|| "-".to_string(), |value| format!("{value:.5}"))
}

fn format_source(value: &Option<String>) -> String {
    let source = value
        .as_deref()
        .filter(|value| !value.is_empty())
        .unwrap_or("-");
    source
        .strip_prefix("polymarket_")
        .unwrap_or(source)
        .to_string()
}

fn format_lookback(value: Option<u64>) -> String {
    value.map_or_else(|| "-".to_string(), |value| value.to_string())
}

fn volatility_age_flags(row: &RuntimeVolatilityRow) -> String {
    let flags = if row.flags.is_empty() {
        "OK".to_string()
    } else {
        row.flags.join(",")
    };
    match row.age_ms {
        Some(age_ms) => format!("{age_ms}ms {flags}"),
        None => format!("- {flags}"),
    }
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let model = volatility_table(app);
    let rows = model
        .rows
        .into_iter()
        .map(|row| Row::new(row.into_iter().map(Cell::from).collect::<Vec<_>>()))
        .collect::<Vec<_>>();
    let table = Table::new(rows, volatility_widths(area.width))
        .header(Row::new(model.headers).style(Style::default().fg(Color::Cyan)))
        .block(Block::bordered().title("Volatility"));

    frame.render_widget(table, area);
}

fn volatility_widths(area_width: u16) -> Vec<Constraint> {
    if area_width < 104 {
        vec![
            Constraint::Length(4),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Length(6),
            Constraint::Length(14),
            Constraint::Length(3),
            Constraint::Min(4),
        ]
    } else {
        vec![
            Constraint::Length(6),
            Constraint::Length(10),
            Constraint::Length(9),
            Constraint::Length(9),
            Constraint::Length(9),
            Constraint::Length(12),
            Constraint::Length(26),
            Constraint::Length(9),
            Constraint::Min(12),
        ]
    }
}

#[cfg(test)]
mod tests {
    use ratatui::{Terminal, backend::TestBackend, layout::Rect};

    use crate::{
        state::AppState,
        status::{RuntimeVolatility, RuntimeVolatilityRow},
    };

    use super::{volatility_header_labels, volatility_table};

    #[test]
    fn volatility_table_renders_live_source_lookback_and_rows() {
        let app = AppState {
            runtime_volatility: Some(RuntimeVolatility {
                state: "OK".to_string(),
                generated_at: Some("2026-06-04T01:00:00+00:00".to_string()),
                source_key: Some("polymarket_rtds_chainlink".to_string()),
                lookback_limit: Some(180),
                rows: vec![
                    RuntimeVolatilityRow {
                        asset: "BTC".to_string(),
                        asof_ts: Some("2026-06-04T01:00:00+00:00".to_string()),
                        sigma_tau: Some(0.001234),
                        short_realized_vol: Some(0.000101),
                        medium_realized_vol: Some(0.000202),
                        long_realized_vol: Some(0.000303),
                        volatility_regime: Some("normal".to_string()),
                        age_ms: Some(120),
                        flags: vec!["OK".to_string()],
                    },
                    RuntimeVolatilityRow {
                        asset: "ETH".to_string(),
                        asof_ts: Some("2026-06-04T01:00:00+00:00".to_string()),
                        sigma_tau: None,
                        short_realized_vol: None,
                        medium_realized_vol: Some(0.000404),
                        long_realized_vol: Some(0.000505),
                        volatility_regime: Some("stale_reference_source".to_string()),
                        age_ms: Some(2200),
                        flags: vec!["missing_volatility".to_string()],
                    },
                ],
                errors: vec![],
            }),
            ..Default::default()
        };

        let table = volatility_table(&app);

        assert_eq!(table.headers, volatility_header_labels().to_vec());
        assert_eq!(
            table.rows[0],
            vec![
                "BTC".to_string(),
                "0.00123".to_string(),
                "0.00010".to_string(),
                "0.00020".to_string(),
                "0.00030".to_string(),
                "normal".to_string(),
                "rtds_chainlink".to_string(),
                "180".to_string(),
                "120ms OK".to_string(),
            ]
        );
        assert_eq!(
            table.rows[1],
            vec![
                "ETH".to_string(),
                "-".to_string(),
                "-".to_string(),
                "0.00040".to_string(),
                "0.00051".to_string(),
                "stale_reference_source".to_string(),
                "rtds_chainlink".to_string(),
                "180".to_string(),
                "2200ms missing_volatility".to_string(),
            ]
        );
    }

    #[test]
    fn volatility_render_keeps_key_fields_visible_in_primary_pane_width() {
        let app = AppState {
            runtime_volatility: Some(RuntimeVolatility {
                state: "OK".to_string(),
                generated_at: Some("2026-06-04T01:00:00+00:00".to_string()),
                source_key: Some("polymarket_rtds_chainlink".to_string()),
                lookback_limit: Some(180),
                rows: vec![RuntimeVolatilityRow {
                    asset: "BTC".to_string(),
                    asof_ts: Some("2026-06-04T01:00:00+00:00".to_string()),
                    sigma_tau: Some(0.001234),
                    short_realized_vol: Some(0.000101),
                    medium_realized_vol: Some(0.000202),
                    long_realized_vol: Some(0.000303),
                    volatility_regime: Some("normal".to_string()),
                    age_ms: Some(120),
                    flags: vec!["OK".to_string()],
                }],
                errors: vec![],
            }),
            ..Default::default()
        };

        for width in [69, 92] {
            let backend = TestBackend::new(width, 12);
            let mut terminal = Terminal::new(backend).expect("terminal should initialize");
            terminal
                .draw(|frame| super::render(frame, Rect::new(0, 0, width, 12), &app))
                .expect("render should complete");
            let rendered = terminal
                .backend()
                .buffer()
                .content
                .iter()
                .map(|cell| cell.symbol())
                .collect::<String>();

            assert!(rendered.contains("Volatility"), "{rendered}");
            assert!(rendered.contains("BTC"), "{rendered}");
            assert!(rendered.contains("0.00123"), "{rendered}");
            assert!(rendered.contains("rtds_chainlink"), "{rendered}");
            assert!(rendered.contains("180"), "{rendered}");
        }
    }
}
