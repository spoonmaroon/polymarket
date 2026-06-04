use chrono::{DateTime, Local};
use ratatui::{
    Frame,
    layout::{Constraint, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::state::AppState;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutcomeDisplayRow {
    pub market: String,
    pub expiry: String,
    pub winner: String,
    pub token: String,
    pub status: String,
}

pub fn outcome_rows(app: &AppState) -> Vec<OutcomeDisplayRow> {
    app.runtime_outcomes
        .as_ref()
        .map(|outcomes| {
            outcomes
                .rows
                .iter()
                .map(|row| OutcomeDisplayRow {
                    market: row.market.clone(),
                    expiry: compact_timestamp(row.expiry_ts.as_deref()),
                    winner: optional_as_dash(row.official_winner.as_deref()),
                    token: optional_as_dash(row.winning_token_id.as_deref()),
                    status: row.official_resolution_status.clone(),
                })
                .collect()
        })
        .unwrap_or_default()
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let rows = outcome_rows(app)
        .into_iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(row.market),
                Cell::from(row.expiry),
                Cell::from(row.winner),
                Cell::from(row.token),
                Cell::from(row.status),
            ])
        })
        .collect::<Vec<_>>();
    let rows = if rows.is_empty() {
        vec![Row::new(vec![
            Cell::from("outcomes pending"),
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
            Constraint::Length(14),
            Constraint::Length(12),
            Constraint::Length(8),
            Constraint::Length(16),
            Constraint::Min(8),
        ],
    )
    .header(
        Row::new(vec!["Market", "Expiry", "Winner", "Token", "Status"])
            .style(Style::default().fg(Color::Cyan)),
    )
    .block(Block::bordered().title("Outcomes"));

    frame.render_widget(table, area);
}

fn optional_as_dash(value: Option<&str>) -> String {
    value
        .filter(|value| !value.is_empty())
        .unwrap_or("-")
        .to_string()
}

fn compact_timestamp(timestamp: Option<&str>) -> String {
    let Some(timestamp) = timestamp.filter(|value| !value.is_empty()) else {
        return "-".to_string();
    };
    if let Ok(parsed) = DateTime::parse_from_rfc3339(timestamp) {
        return parsed.with_timezone(&Local).format("%H:%M %Z").to_string();
    }
    timestamp.to_string()
}

#[cfg(test)]
mod tests {
    use crate::{
        render::outcomes::outcome_rows,
        state::AppState,
        status::{RuntimeOutcomeRow, RuntimeOutcomes},
    };

    #[test]
    fn outcome_rows_show_official_label_only() {
        let app = app_with_outcomes(Some("UP"), Some("up-token"), "resolved");

        let rows = outcome_rows(&app);

        assert_eq!(rows[0].winner, "UP");
        assert_eq!(rows[0].token, "up-token");
        assert_eq!(rows[0].status, "resolved");
    }

    fn app_with_outcomes(
        official_winner: Option<&str>,
        winning_token_id: Option<&str>,
        official_resolution_status: &str,
    ) -> AppState {
        AppState {
            runtime_outcomes: Some(RuntimeOutcomes {
                ok: true,
                state: "OK".to_string(),
                generated_at: Some("2026-06-03T22:00:00Z".to_string()),
                rows: vec![RuntimeOutcomeRow {
                    market: "BTC 5m".to_string(),
                    market_id: "btc-updown-5m-1780521900".to_string(),
                    asset: Some("BTC".to_string()),
                    start_ts: None,
                    expiry_ts: Some("2026-06-03T21:25:00Z".to_string()),
                    computed_winner: None,
                    official_winner: official_winner.map(str::to_string),
                    winning_token_id: winning_token_id.map(str::to_string),
                    official_resolution_status: official_resolution_status.to_string(),
                    mismatch: None,
                }],
            }),
            ..Default::default()
        }
    }
}
