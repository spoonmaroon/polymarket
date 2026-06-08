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
    pub edge: String,
    pub required_edge: String,
    pub hint_reasons: String,
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
        "Edge",
        "Req",
        "Hint/Reasons",
    ]
}

pub fn probability_table(app: &AppState) -> ProbabilityTableModel {
    let probability_rows = probability_rows(app);
    if !probability_rows.is_empty() {
        let mut rows = probability_rows
            .into_iter()
            .map(|row| {
                vec![
                    row.contract,
                    row.p_finish,
                    row.p_no_touch,
                    row.edge,
                    row.required_edge,
                    row.hint_reasons,
                ]
            })
            .collect::<Vec<_>>();
        if let Some(probabilities) = app
            .runtime_probabilities
            .as_ref()
            .filter(|probabilities| has_probability_status_problem(probabilities))
        {
            rows.insert(0, probability_status_row(probabilities));
        }
        return ProbabilityTableModel {
            headers: probability_header_labels().to_vec(),
            rows,
        };
    }

    if let Some(probabilities) = app
        .runtime_probabilities
        .as_ref()
        .filter(|probabilities| has_probability_status_problem(probabilities))
    {
        return ProbabilityTableModel {
            headers: probability_header_labels().to_vec(),
            rows: vec![probability_status_row(probabilities)],
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

pub fn compact_probability_table(app: &AppState) -> ProbabilityTableModel {
    let rows = app
        .runtime_probabilities
        .as_ref()
        .map(|probabilities| {
            probabilities
                .rows
                .iter()
                .map(|row| {
                    vec![
                        row.contract.clone(),
                        format_probability(row.p_finish),
                        format_probability(row.p_no_touch),
                        row.path_count
                            .map(|value| value.to_string())
                            .unwrap_or_else(|| "-".to_string()),
                        row.model_version.clone().unwrap_or_else(|| "-".to_string()),
                    ]
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    ProbabilityTableModel {
        headers: vec!["Contract", "p", "NoTouch", "Paths", "Model"],
        rows: if rows.is_empty() {
            vec![vec![
                "probability pending".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
            ]]
        } else {
            rows
        },
    }
}

fn has_probability_status_problem(probabilities: &crate::status::RuntimeProbabilities) -> bool {
    let state = probabilities.state.trim();
    !probabilities.ok
        || (!state.is_empty() && state != "OK")
        || probabilities
            .error
            .as_ref()
            .is_some_and(|error| !error.is_empty())
        || !probabilities.errors.is_empty()
}

fn probability_status_row(probabilities: &crate::status::RuntimeProbabilities) -> Vec<String> {
    vec![
        format!("probability {}", probability_status_label(probabilities)),
        "-".to_string(),
        "-".to_string(),
        "-".to_string(),
        "-".to_string(),
        probability_status_detail(probabilities),
    ]
}

fn probability_status_label(probabilities: &crate::status::RuntimeProbabilities) -> String {
    let state = probabilities.state.trim();
    if !state.is_empty() && state != "OK" {
        return state.to_string();
    }
    if !probabilities.ok {
        return "ERROR".to_string();
    }
    "WARNING".to_string()
}

fn probability_status_detail(probabilities: &crate::status::RuntimeProbabilities) -> String {
    if let Some(error) = probabilities
        .error
        .as_ref()
        .filter(|error| !error.is_empty())
    {
        return error.clone();
    }
    if !probabilities.errors.is_empty() {
        return probabilities.errors.join(",");
    }
    "-".to_string()
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
        edge: format_optional_probability(row.edge_after_costs),
        required_edge: format_optional_probability(row.required_edge),
        hint_reasons: hint_reasons(row),
    }
}

fn format_probability(value: f64) -> String {
    format!("{:.3}", value)
}

fn format_optional_probability(value: Option<f64>) -> String {
    value
        .map(format_probability)
        .unwrap_or_else(|| "-".to_string())
}

fn hint_reasons(row: &RuntimeProbabilityRow) -> String {
    let hint = row
        .decision_hint
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "READ_ONLY".to_string());
    if row.skip_reasons.is_empty() {
        return hint;
    }
    format!("{hint} {}", row.skip_reasons.join(","))
}

#[allow(dead_code)]
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

pub fn render_compact(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let model = compact_probability_table(app);
    let rows = model
        .rows
        .into_iter()
        .map(|row| Row::new(row.into_iter().map(Cell::from).collect::<Vec<_>>()))
        .collect::<Vec<_>>();
    let table = Table::new(
        rows,
        vec![
            Constraint::Length(18),
            Constraint::Length(8),
            Constraint::Length(10),
            Constraint::Length(9),
            Constraint::Min(12),
        ],
    )
    .header(Row::new(model.headers).style(Style::default().fg(Color::Cyan)))
    .block(Block::bordered().title("Contract Probabilities"));

    frame.render_widget(table, area);
}

#[allow(dead_code)]
fn probability_widths(_column_count: usize) -> Vec<Constraint> {
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

    use super::{
        compact_probability_table, probability_header_labels, probability_rows, probability_table,
    };

    #[test]
    fn probability_rows_render_read_only_probability_outputs() {
        let app = AppState {
            runtime_probabilities: Some(RuntimeProbabilities {
                ok: true,
                state: "OK".to_string(),
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
                    decision_hint: Some("PAPER_TRADE".to_string()),
                    edge_after_costs: Some(0.10),
                    required_edge: Some(0.06),
                    skip_reasons: vec![],
                    model_version: None,
                    generator_version: None,
                    path_count: None,
                    generator_count: None,
                }],
                error: None,
                errors: Vec::new(),
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
                "Edge",
                "Req",
                "Hint/Reasons"
            ]
        );
        assert_eq!(rows[0].contract, "BTC 5m UP");
        assert_eq!(rows[0].p_finish, "0.575");
        assert_eq!(rows[0].p_no_touch, "0.315");
        assert_eq!(rows[0].edge, "0.100");
        assert_eq!(rows[0].required_edge, "0.060");
        assert_eq!(rows[0].hint_reasons, "PAPER_TRADE");
    }

    #[test]
    fn probability_rows_default_missing_decision_fields_to_read_only() {
        let probabilities: RuntimeProbabilities = serde_json::from_str(
            r#"{
                "ok": true,
                "state": "OK",
                "generated_at": "2026-06-03T21:06:00Z",
                "cached": true,
                "rows": [{
                    "contract": "BTC 5m DOWN",
                    "p_finish": 0.4251,
                    "p_no_touch": 0.2149,
                    "z_path": 0.2219,
                    "sigma_tau": 0.01234,
                    "age_ms": 750,
                    "flags": ["OK"]
                }],
                "error": null,
                "errors": []
            }"#,
        )
        .unwrap();
        let app = AppState {
            runtime_probabilities: Some(probabilities),
            ..Default::default()
        };

        let rows = probability_rows(&app);

        assert_eq!(rows[0].contract, "BTC 5m DOWN");
        assert_eq!(rows[0].edge, "-");
        assert_eq!(rows[0].required_edge, "-");
        assert_eq!(rows[0].hint_reasons, "READ_ONLY");
    }

    #[test]
    fn probability_table_shows_status_problem_before_stale_rows() {
        let app = AppState {
            runtime_probabilities: Some(RuntimeProbabilities {
                ok: false,
                state: "COMPUTE_DISABLED".to_string(),
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                cached: true,
                rows: vec![RuntimeProbabilityRow {
                    contract: "BTC 5m UP".to_string(),
                    p_finish: 0.5749,
                    p_no_touch: 0.3149,
                    z_path: 0.4219,
                    sigma_tau: 0.01234,
                    age_ms: 850,
                    flags: vec!["STALE".to_string()],
                    decision_hint: Some("WAIT".to_string()),
                    edge_after_costs: Some(0.10),
                    required_edge: Some(0.06),
                    skip_reasons: vec!["stale_probability_status".to_string()],
                    model_version: None,
                    generator_version: None,
                    path_count: None,
                    generator_count: None,
                }],
                error: Some("runtime probability compute fallback disabled".to_string()),
                errors: Vec::new(),
            }),
            ..Default::default()
        };

        let table = probability_table(&app);

        assert_eq!(
            table.rows[0],
            vec![
                "probability COMPUTE_DISABLED".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "runtime probability compute fallback disabled".to_string(),
            ]
        );
        assert_eq!(table.rows[1][0], "BTC 5m UP");
        assert_eq!(table.rows[1][5], "WAIT stale_probability_status");
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
            ]
        );
    }

    #[test]
    fn probability_table_shows_empty_runtime_state() {
        let app = AppState {
            runtime_probabilities: Some(RuntimeProbabilities {
                ok: false,
                state: "COMPUTE_DISABLED".to_string(),
                generated_at: "2026-06-04T20:00:00Z".to_string(),
                cached: false,
                rows: Vec::new(),
                error: Some(
                    "probability status missing and runtime probability compute fallback disabled"
                        .to_string(),
                ),
                errors: Vec::new(),
            }),
            ..Default::default()
        };

        let table = probability_table(&app);

        assert_eq!(
            table.rows[0],
            vec![
                "probability COMPUTE_DISABLED".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "probability status missing and runtime probability compute fallback disabled"
                    .to_string(),
            ]
        );
    }

    #[test]
    fn probability_table_shows_disabled_without_ok_detail() {
        let app = AppState {
            runtime_probabilities: Some(RuntimeProbabilities {
                ok: true,
                state: "DISABLED".to_string(),
                generated_at: "2026-06-04T20:00:00Z".to_string(),
                cached: false,
                rows: Vec::new(),
                error: None,
                errors: Vec::new(),
            }),
            ..Default::default()
        };

        let table = probability_table(&app);

        assert_eq!(
            table.rows[0],
            vec![
                "probability DISABLED".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
            ]
        );
    }

    #[test]
    fn compact_probability_table_shows_contract_rows_without_status_error_row() {
        let app = AppState {
            runtime_probabilities: Some(RuntimeProbabilities {
                ok: true,
                state: "NOWCAST".to_string(),
                generated_at: "2026-06-07T21:15:27Z".to_string(),
                cached: false,
                rows: vec![
                    probability_row("BTC 5m UP", 0.4729, 80_000),
                    probability_row("BTC 5m DOWN", 0.4271, 80_000),
                    probability_row("ETH 5m UP", 0.3700, 80_000),
                    probability_row("ETH 5m DOWN", 0.5300, 80_000),
                ],
                error: Some("transient nowcast".to_string()),
                errors: vec![],
            }),
            ..Default::default()
        };

        let table = compact_probability_table(&app);

        assert_eq!(
            table.headers,
            vec!["Contract", "p", "NoTouch", "Paths", "Model"]
        );
        assert_eq!(table.rows.len(), 4);
        assert_eq!(table.rows[0][0], "BTC 5m UP");
        assert_eq!(table.rows[0][1], "0.473");
        assert_eq!(table.rows[0][3], "80000");
        assert!(
            table
                .rows
                .iter()
                .all(|row| !row[0].starts_with("probability "))
        );
    }

    fn probability_row(
        contract: &str,
        p_finish: f64,
        effective_path_count: u64,
    ) -> RuntimeProbabilityRow {
        RuntimeProbabilityRow {
            contract: contract.to_string(),
            p_finish,
            p_no_touch: 0.25,
            z_path: 0.42,
            sigma_tau: 0.01234,
            age_ms: 850,
            flags: vec!["OK".to_string()],
            decision_hint: Some("READ_ONLY".to_string()),
            edge_after_costs: None,
            required_edge: None,
            skip_reasons: vec![],
            model_version: Some("ensemble-v1".to_string()),
            generator_version: Some("four-generator-ensemble-v1".to_string()),
            path_count: Some(effective_path_count),
            generator_count: Some(4),
        }
    }
}
