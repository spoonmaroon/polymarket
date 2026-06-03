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
    pub z_path: String,
    pub sigma_tau: String,
    pub age_flags: String,
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
    let rows = probability_rows(app)
        .into_iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(row.contract),
                Cell::from(row.p_finish),
                Cell::from(row.p_no_touch),
                Cell::from(row.z_path),
                Cell::from(row.sigma_tau),
                Cell::from(row.age_flags),
            ])
        })
        .collect::<Vec<_>>();
    let rows = if rows.is_empty() {
        vec![Row::new(vec![
            Cell::from("probability pending"),
            Cell::from("-"),
            Cell::from("-"),
            Cell::from("-"),
            Cell::from("-"),
            Cell::from("-"),
        ])]
    } else {
        rows
    };
    let table = Table::new(
        rows,
        [
            Constraint::Length(18),
            Constraint::Length(10),
            Constraint::Length(12),
            Constraint::Length(9),
            Constraint::Length(11),
            Constraint::Min(12),
        ],
    )
    .header(Row::new(probability_header_labels().to_vec()).style(Style::default().fg(Color::Cyan)))
    .block(Block::bordered().title("Probability"));

    frame.render_widget(table, area);
}

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{RuntimeProbabilities, RuntimeProbabilityRow},
    };

    use super::{probability_header_labels, probability_rows};

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
}
