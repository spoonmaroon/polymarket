use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{
    state::AppState,
    status::{MonteCarloRow, RuntimeMonteCarloStatus, RuntimeProbabilityRow},
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MonteCarloDisplayRow {
    pub contract: String,
    pub p_finish: String,
    pub p_no_touch: String,
    pub z_path: String,
    pub sigma_tau: String,
    pub backend: String,
    pub paths: String,
    pub age_flags: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MonteCarloTableModel {
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

pub fn monte_carlo_header_labels() -> [&'static str; 8] {
    [
        "Contract",
        "p_finish",
        "p_no_touch",
        "z_path",
        "sigma_tau",
        "backend",
        "paths",
        "age/flags",
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

pub fn monte_carlo_table(app: &AppState) -> MonteCarloTableModel {
    let Some(status) = app.runtime_monte_carlo.as_ref() else {
        return MonteCarloTableModel {
            headers: monte_carlo_header_labels().to_vec(),
            rows: vec![monte_carlo_pending_row("monte carlo pending", "-")],
        };
    };

    let monte_carlo_rows = monte_carlo_rows(status);
    if !monte_carlo_rows.is_empty() {
        return MonteCarloTableModel {
            headers: monte_carlo_header_labels().to_vec(),
            rows: monte_carlo_rows
                .into_iter()
                .map(|row| {
                    vec![
                        row.contract,
                        row.p_finish,
                        row.p_no_touch,
                        row.z_path,
                        row.sigma_tau,
                        row.backend,
                        row.paths,
                        row.age_flags,
                    ]
                })
                .collect(),
        };
    }

    let detail = if status.errors.is_empty() {
        status.state.clone()
    } else {
        status.errors.join(",")
    };
    MonteCarloTableModel {
        headers: monte_carlo_header_labels().to_vec(),
        rows: vec![monte_carlo_pending_row("monte carlo unavailable", &detail)],
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

pub fn monte_carlo_rows(status: &RuntimeMonteCarloStatus) -> Vec<MonteCarloDisplayRow> {
    status.rows.iter().map(monte_carlo_row).collect()
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

fn monte_carlo_row(row: &MonteCarloRow) -> MonteCarloDisplayRow {
    MonteCarloDisplayRow {
        contract: row.contract.clone(),
        p_finish: format_optional_probability(row.p_finish),
        p_no_touch: format_optional_probability(row.p_no_touch),
        z_path: format_optional_decimal(row.z_path, 3),
        sigma_tau: format_optional_decimal(row.sigma_tau, 5),
        backend: row.backend.clone().unwrap_or_else(|| "-".to_string()),
        paths: row
            .path_count
            .map(format_path_count)
            .unwrap_or_else(|| "-".to_string()),
        age_flags: monte_carlo_age_flags(row),
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

fn format_optional_decimal(value: Option<f64>, precision: usize) -> String {
    value
        .map(|value| format!("{value:.precision$}"))
        .unwrap_or_else(|| "-".to_string())
}

fn format_path_count(value: u64) -> String {
    if value >= 1_000_000 {
        format!("{:.1}M", value as f64 / 1_000_000.0)
    } else if value >= 1_000 {
        format!("{:.1}k", value as f64 / 1_000.0)
    } else {
        value.to_string()
    }
}

fn age_flags(row: &RuntimeProbabilityRow) -> String {
    let flags = if row.flags.is_empty() {
        "OK".to_string()
    } else {
        row.flags.join(",")
    };
    format!("{}ms {flags}", row.age_ms)
}

fn monte_carlo_age_flags(row: &MonteCarloRow) -> String {
    let age = row
        .age_ms
        .map(|age_ms| format!("{age_ms}ms"))
        .unwrap_or_else(|| "-".to_string());
    let flags = if row.flags.is_empty() {
        "OK".to_string()
    } else {
        row.flags.join(",")
    };
    format!("{age} {flags}")
}

fn monte_carlo_pending_row(label: &str, detail: &str) -> Vec<String> {
    vec![
        label.to_string(),
        "-".to_string(),
        "-".to_string(),
        "-".to_string(),
        "-".to_string(),
        "-".to_string(),
        "-".to_string(),
        detail.to_string(),
    ]
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
        .split(area);

    render_probability_table(frame, chunks[0], app);
    render_monte_carlo_table(frame, chunks[1], app);
}

fn render_probability_table(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let model = probability_table(app);
    let widths = probability_widths();
    let rows = model
        .rows
        .into_iter()
        .map(|row| Row::new(row.into_iter().map(Cell::from).collect::<Vec<_>>()))
        .collect::<Vec<_>>();
    let table = Table::new(rows, widths)
        .header(Row::new(model.headers).style(Style::default().fg(Color::Cyan)))
        .block(Block::bordered().title("Cached Probability"));

    frame.render_widget(table, area);
}

fn render_monte_carlo_table(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let model = monte_carlo_table(app);
    let rows = model
        .rows
        .into_iter()
        .map(|row| Row::new(row.into_iter().map(Cell::from).collect::<Vec<_>>()))
        .collect::<Vec<_>>();
    let table = Table::new(rows, monte_carlo_widths())
        .header(Row::new(model.headers).style(Style::default().fg(Color::Green)))
        .block(Block::bordered().title("Monte Carlo"));

    frame.render_widget(table, area);
}

fn probability_widths() -> Vec<Constraint> {
    vec![
        Constraint::Length(18),
        Constraint::Length(10),
        Constraint::Length(12),
        Constraint::Length(9),
        Constraint::Length(11),
        Constraint::Min(12),
    ]
}

fn monte_carlo_widths() -> Vec<Constraint> {
    vec![
        Constraint::Length(18),
        Constraint::Length(9),
        Constraint::Length(11),
        Constraint::Length(8),
        Constraint::Length(10),
        Constraint::Length(11),
        Constraint::Length(8),
        Constraint::Min(10),
    ]
}

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{
            MonteCarloRow, RuntimeMonteCarloStatus, RuntimeProbabilities, RuntimeProbabilityRow,
            RuntimeVolatility, RuntimeVolatilityRow,
        },
    };

    use super::{
        monte_carlo_header_labels, monte_carlo_table, probability_header_labels, probability_rows,
        probability_table,
    };

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
    fn monte_carlo_table_renders_compact_cached_status_rows() {
        let app = AppState {
            runtime_monte_carlo: Some(RuntimeMonteCarloStatus {
                ok: true,
                state: "OK".to_string(),
                generated_at: Some("2026-06-05T12:00:00Z".to_string()),
                rows: vec![MonteCarloRow {
                    contract: "BTC 5m UP".to_string(),
                    p_finish: Some(0.5749),
                    p_no_touch: Some(0.3149),
                    z_path: Some(0.4219),
                    sigma_tau: Some(0.01234),
                    backend: Some("cpu-rayon".to_string()),
                    path_count: Some(65_536),
                    model_version: Some("rust-mc-v1".to_string()),
                    age_ms: Some(850),
                    flags: vec!["cached".to_string()],
                    artifact_id: Some("artifact-1".to_string()),
                }],
                errors: Vec::new(),
            }),
            ..Default::default()
        };

        let table = monte_carlo_table(&app);

        assert_eq!(table.headers, monte_carlo_header_labels().to_vec());
        assert_eq!(
            table.rows[0],
            vec![
                "BTC 5m UP".to_string(),
                "0.575".to_string(),
                "0.315".to_string(),
                "0.422".to_string(),
                "0.01234".to_string(),
                "cpu-rayon".to_string(),
                "65.5k".to_string(),
                "850ms cached".to_string(),
            ]
        );
    }

    #[test]
    fn monte_carlo_table_shows_unavailable_state_and_errors_without_rows() {
        let app = AppState {
            runtime_monte_carlo: Some(RuntimeMonteCarloStatus {
                ok: false,
                state: "MISSING".to_string(),
                generated_at: Some("2026-06-05T12:00:00Z".to_string()),
                rows: Vec::new(),
                errors: vec!["duckdb missing".to_string()],
            }),
            ..Default::default()
        };

        let table = monte_carlo_table(&app);

        assert_eq!(table.headers, monte_carlo_header_labels().to_vec());
        assert_eq!(table.rows[0][0], "monte carlo unavailable");
        assert_eq!(table.rows[0][7], "duckdb missing");
    }
}
