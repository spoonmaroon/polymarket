use ratatui::{
    Frame,
    layout::{Constraint, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{
    state::AppState,
    status::{RuntimeProbabilityRow, RuntimeVolatilityRow},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbabilityDisplayRow {
    pub contract: String,
    pub p_finish: String,
    pub p_no_touch: String,
    pub z_path: String,
    pub sigma_tau: String,
    pub age_flags: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbabilityTableModel {
    pub headers: Vec<&'static str>,
    pub rows: Vec<Vec<String>>,
}

pub fn probability_header_labels() -> [&'static str; 6] {
    [
        "Contract",
        "p_finish",
        "p_no_touch",
        "z_path",
        "sigma_tau",
        "Age/Flags",
    ]
}

pub fn probability_table(app: &AppState) -> ProbabilityTableModel {
    let probability_rows = probability_rows(app);
    if !probability_rows.is_empty() {
        return ProbabilityTableModel {
            headers: probability_header_labels().to_vec(),
            rows: probability_rows
                .into_iter()
                .map(|row| {
                    vec![
                        row.contract,
                        row.p_finish,
                        row.p_no_touch,
                        row.z_path,
                        row.sigma_tau,
                        row.age_flags,
                    ]
                })
                .collect(),
        };
    }

    let volatility_rows = app
        .runtime_volatility
        .as_ref()
        .map(|volatility| {
            volatility
                .rows
                .iter()
                .map(volatility_probability_row)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if !volatility_rows.is_empty() {
        return ProbabilityTableModel {
            headers: vec![
                "Asset",
                "sigma_tau",
                "short",
                "medium",
                "long",
                "regime",
                "Age/Flags",
            ],
            rows: volatility_rows,
        };
    }

    ProbabilityTableModel {
        headers: probability_header_labels().to_vec(),
        rows: vec![vec![
            "probability pending".to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
        ]],
    }
}

pub fn probability_rows(app: &AppState) -> Vec<ProbabilityDisplayRow> {
    app.runtime_probabilities
        .as_ref()
        .map(|probabilities| {
            probabilities
                .rows
                .iter()
                .map(probability_row)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn volatility_probability_row(row: &RuntimeVolatilityRow) -> Vec<String> {
    vec![
        row.asset.clone(),
        format_optional_vol(row.sigma_tau),
        format_optional_vol(row.short_realized_vol),
        format_optional_vol(row.medium_realized_vol),
        format_optional_vol(row.long_realized_vol),
        row.volatility_regime
            .clone()
            .unwrap_or_else(|| "-".to_string()),
        volatility_age_flags(row),
    ]
}

fn format_optional_vol(value: Option<f64>) -> String {
    value.map_or_else(|| "-".to_string(), |value| format!("{value:.5}"))
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

fn probability_row(row: &RuntimeProbabilityRow) -> ProbabilityDisplayRow {
    ProbabilityDisplayRow {
        contract: row.contract.clone(),
        p_finish: format_probability(row.p_finish),
        p_no_touch: format_probability(row.p_no_touch),
        z_path: format!("{:.3}", row.z_path),
        sigma_tau: format!("{:.5}", row.sigma_tau),
        age_flags: age_flags(row),
    }
}

fn format_probability(value: f64) -> String {
    format!("{:.3}", value)
}

fn age_flags(row: &RuntimeProbabilityRow) -> String {
    let flags = if row.flags.is_empty() {
        "OK".to_string()
    } else {
        row.flags.join(",")
    };
    format!("{}ms {flags}", row.age_ms)
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let model = probability_table(app);
    let widths = probability_widths(model.headers.len());
    let rows = model
        .rows
        .into_iter()
        .map(|row| Row::new(row.into_iter().map(Cell::from).collect::<Vec<_>>()))
        .collect::<Vec<_>>();
    let table = Table::new(rows, widths)
        .header(Row::new(model.headers).style(Style::default().fg(Color::Cyan)))
        .block(Block::bordered().title("Probability"));

    frame.render_widget(table, area);
}

fn probability_widths(column_count: usize) -> Vec<Constraint> {
    if column_count == 7 {
        return vec![
            Constraint::Length(8),
            Constraint::Length(11),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(12),
            Constraint::Min(12),
        ];
    }
    vec![
        Constraint::Length(18),
        Constraint::Length(10),
        Constraint::Length(12),
        Constraint::Length(9),
        Constraint::Length(11),
        Constraint::Min(12),
    ]
}

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{
            RuntimeProbabilities, RuntimeProbabilityRow, RuntimeVolatility, RuntimeVolatilityRow,
        },
    };

    use super::{probability_header_labels, probability_rows, probability_table};

    #[test]
    fn probability_rows_render_read_only_probability_outputs() {
        let app = AppState {
            runtime_probabilities: Some(RuntimeProbabilities {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                cached: true,
                rows: vec![RuntimeProbabilityRow {
                    contract: "BTC 5m UP".to_string(),
                    p_finish: 0.5749,
                    p_no_touch: 0.3149,
                    z_path: 0.4219,
                    sigma_tau: 0.01234,
                    age_ms: 850,
                    flags: vec!["OK".to_string()],
                }],
            }),
            ..Default::default()
        };

        let rows = probability_rows(&app);

        assert_eq!(
            probability_header_labels(),
            [
                "Contract",
                "p_finish",
                "p_no_touch",
                "z_path",
                "sigma_tau",
                "Age/Flags"
            ]
        );
        assert_eq!(rows[0].contract, "BTC 5m UP");
        assert_eq!(rows[0].p_finish, "0.575");
        assert_eq!(rows[0].p_no_touch, "0.315");
        assert_eq!(rows[0].z_path, "0.422");
        assert_eq!(rows[0].sigma_tau, "0.01234");
        assert_eq!(rows[0].age_flags, "850ms OK");
    }

    #[test]
    fn probability_table_renders_volatility_when_probabilities_are_empty() {
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

        let table = probability_table(&app);

        assert_eq!(
            table.headers,
            vec![
                "Asset",
                "sigma_tau",
                "short",
                "medium",
                "long",
                "regime",
                "Age/Flags"
            ]
        );
        assert_eq!(
            table.rows[0],
            vec![
                "BTC".to_string(),
                "0.00120".to_string(),
                "0.00010".to_string(),
                "0.00020".to_string(),
                "0.00030".to_string(),
                "normal".to_string(),
                "120ms OK".to_string(),
            ]
        );
    }
}
