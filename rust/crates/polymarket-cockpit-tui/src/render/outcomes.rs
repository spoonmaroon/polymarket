use chrono::{DateTime, Local};
use ratatui::{
    Frame,
    layout::{Constraint, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::state::AppState;

const OUTCOME_VISIBLE_ROWS: usize = 20;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutcomeDisplayRow {
    pub marker: String,
    pub market: String,
    pub expiry: String,
    pub winner: String,
    pub token: String,
    pub status: String,
}

pub fn outcome_rows(app: &AppState) -> Vec<OutcomeDisplayRow> {
    outcome_rows_for_visible_count(app, OUTCOME_VISIBLE_ROWS)
}

pub fn outcome_rows_for_visible_count(
    app: &AppState,
    visible_rows: usize,
) -> Vec<OutcomeDisplayRow> {
    let selected_index = app.effective_outcome_index();
    let visible_rows = visible_rows.max(1);

    app.runtime_outcomes
        .as_ref()
        .map(|outcomes| {
            let display_rows = outcomes
                .rows
                .iter()
                .enumerate()
                .map(|(index, row)| OutcomeDisplayRow {
                    marker: if selected_index == Some(index) {
                        ">"
                    } else {
                        " "
                    }
                    .to_string(),
                    market: row.market.clone(),
                    expiry: compact_timestamp(row.expiry_ts.as_deref()),
                    winner: optional_as_dash(row.official_winner.as_deref()),
                    token: optional_as_dash(row.winning_token_id.as_deref()),
                    status: row.official_resolution_status.clone(),
                })
                .collect::<Vec<_>>();
            let selected_display_index = display_rows.iter().position(|row| row.marker == ">");
            let start =
                visible_outcome_start(display_rows.len(), selected_display_index, visible_rows);
            display_rows
                .into_iter()
                .skip(start)
                .take(visible_rows)
                .collect()
        })
        .unwrap_or_default()
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let visible_rows = area.height.saturating_sub(3).max(1) as usize;
    let rows = outcome_rows_for_visible_count(app, visible_rows)
        .into_iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(row.marker),
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
            Cell::from(" "),
            Cell::from("outcomes pending"),
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
            Constraint::Length(2),
            Constraint::Length(14),
            Constraint::Length(12),
            Constraint::Length(8),
            Constraint::Length(16),
            Constraint::Min(8),
        ],
    )
    .header(
        Row::new(vec!["", "Market", "Expiry", "Winner", "Token", "Status"])
            .style(Style::default().fg(Color::Cyan)),
    )
    .block(Block::bordered().title("Outcomes"));

    frame.render_widget(table, area);
}

fn visible_outcome_start(count: usize, selected_index: Option<usize>, visible_rows: usize) -> usize {
    if count <= visible_rows {
        return 0;
    }

    let selected_index = selected_index.unwrap_or_default().min(count - 1);
    if selected_index < visible_rows {
        0
    } else {
        selected_index + 1 - visible_rows
    }
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
        render::outcomes::{outcome_rows, outcome_rows_for_visible_count},
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

    #[test]
    fn outcome_rows_mark_selected_outcome() {
        let mut app = app_with_market_names(vec!["BTC 5m", "ETH 5m"]);
        app.sync_outcome_selection();
        app.select_next_outcome();

        let rows = outcome_rows(&app);

        assert_eq!(rows[0].marker, " ");
        assert_eq!(rows[1].marker, ">");
    }

    #[test]
    fn outcome_rows_keep_selected_outcome_visible() {
        let mut app = app_with_market_names(vec![
            "BTC 5m 16:20",
            "BTC 5m 16:25",
            "ETH 5m 16:20",
            "ETH 5m 16:25",
        ]);
        app.sync_outcome_selection();
        app.select_previous_outcome();

        let rows = outcome_rows_for_visible_count(&app, 2);

        assert_eq!(rows.len(), 2);
        assert_eq!(rows.last().map(|row| row.marker.as_str()), Some(">"));
        assert_eq!(
            rows.last().map(|row| row.market.as_str()),
            Some("ETH 5m 16:25")
        );
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

    fn app_with_market_names(markets: Vec<&str>) -> AppState {
        AppState {
            runtime_outcomes: Some(RuntimeOutcomes {
                ok: true,
                state: "OK".to_string(),
                generated_at: Some("2026-06-03T22:00:00Z".to_string()),
                rows: markets
                    .into_iter()
                    .enumerate()
                    .map(|(index, market)| RuntimeOutcomeRow {
                        market: market.to_string(),
                        market_id: format!("market-{index}"),
                        asset: Some("BTC".to_string()),
                        start_ts: None,
                        expiry_ts: Some("2026-06-03T21:25:00Z".to_string()),
                        computed_winner: None,
                        official_winner: Some("UP".to_string()),
                        winning_token_id: Some(format!("token-{index}")),
                        official_resolution_status: "resolved".to_string(),
                        mismatch: None,
                    })
                    .collect(),
            }),
            ..Default::default()
        }
    }
}
