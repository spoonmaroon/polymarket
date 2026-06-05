use ratatui::{
    Frame,
    layout::{Constraint, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{state::AppState, status::RuntimeProbabilityRow};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbabilityDisplayRow {
    pub contract: String,
    pub p_finish: String,
    pub p_no_touch: String,
    pub edge_required: String,
    pub gate: String,
    pub diagnosis: String,
    pub weights: String,
    pub age_flags: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbabilityTableModel {
    pub headers: Vec<&'static str>,
    pub rows: Vec<Vec<String>>,
}

pub fn probability_header_labels() -> [&'static str; 8] {
    [
        "Contract",
        "p_finish",
        "p_no_touch",
        "edge/req",
        "gate",
        "diag",
        "weights",
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
                        row.edge_required,
                        row.gate,
                        row.diagnosis,
                        row.weights,
                        row.age_flags,
                    ]
                })
                .collect(),
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

fn probability_row(row: &RuntimeProbabilityRow) -> ProbabilityDisplayRow {
    ProbabilityDisplayRow {
        contract: row.contract.clone(),
        p_finish: format_probability(row.p_finish),
        p_no_touch: format_probability(row.p_no_touch),
        edge_required: edge_required(row),
        gate: gate_label(row),
        diagnosis: diagnosis(row),
        weights: weights(row),
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

fn edge_required(row: &RuntimeProbabilityRow) -> String {
    match (row.edge_after_costs, row.required_edge) {
        (Some(edge), Some(required)) => format!("{edge:.3}/{required:.3}"),
        _ => "-".to_string(),
    }
}

fn gate_label(row: &RuntimeProbabilityRow) -> String {
    match row.decision_hint.as_deref() {
        Some("TRADE_CANDIDATE") => "PAPER_CANDIDATE".to_string(),
        Some(value) => value.to_string(),
        None => "-".to_string(),
    }
}

fn diagnosis(row: &RuntimeProbabilityRow) -> String {
    if !row.path_diagnosis.is_empty() {
        return row.path_diagnosis.join(",");
    }
    format!("z {:.3}", row.z_path)
}

fn weights(row: &RuntimeProbabilityRow) -> String {
    if row.effective_weights.is_empty() {
        return "-".to_string();
    }
    row.effective_weights
        .iter()
        .map(|(name, weight)| format!("{} {:.0}%", weight_label(name), weight * 100.0))
        .collect::<Vec<_>>()
        .join(" ")
}

fn weight_label(name: &str) -> &str {
    match name {
        "empirical_conditional" => "emp",
        "lognormal_baseline" => "log",
        "stress_overlay" => "stress",
        value => value,
    }
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

fn probability_widths(_column_count: usize) -> Vec<Constraint> {
    vec![
        Constraint::Length(16),
        Constraint::Length(9),
        Constraint::Length(10),
        Constraint::Length(12),
        Constraint::Length(16),
        Constraint::Length(18),
        Constraint::Length(22),
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
                    mc_dispersion: Some(0.073),
                    uncertainty_buffer: Some(0.046),
                    path_diagnosis: vec!["FRAGILE".to_string(), "NEAR_THRESHOLD".to_string()],
                    effective_weights: [
                        ("lognormal_baseline".to_string(), 0.55),
                        ("empirical_conditional".to_string(), 0.30),
                        ("stress_overlay".to_string(), 0.15),
                    ]
                    .into(),
                    decision_hint: Some("WAIT".to_string()),
                    edge_after_costs: Some(0.019),
                    required_edge: Some(0.086),
                    gate_reasons: vec!["NEAR_THRESHOLD".to_string()],
                    generator_metadata: [(
                        "snapshot_id".to_string(),
                        serde_json::json!("weights-1"),
                    )]
                    .into(),
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
                "edge/req",
                "gate",
                "diag",
                "weights",
                "Age/Flags"
            ]
        );
        assert_eq!(rows[0].contract, "BTC 5m UP");
        assert_eq!(rows[0].p_finish, "0.575");
        assert_eq!(rows[0].p_no_touch, "0.315");
        assert_eq!(rows[0].edge_required, "0.019/0.086");
        assert_eq!(rows[0].gate, "WAIT");
        assert_eq!(rows[0].diagnosis, "FRAGILE,NEAR_THRESHOLD");
        assert_eq!(rows[0].weights, "emp 30% log 55% stress 15%");
        assert_eq!(rows[0].age_flags, "850ms OK");
    }

    #[test]
    fn probability_table_stays_pending_when_probabilities_are_empty_even_with_volatility() {
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

        assert_eq!(table.headers, probability_header_labels().to_vec());
        assert_eq!(
            table.rows[0],
            vec![
                "probability pending".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
            ]
        );
    }
}
