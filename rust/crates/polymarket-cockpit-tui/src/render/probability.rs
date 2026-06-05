use chrono::{DateTime, Utc};
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
    let Some(probabilities) = app.runtime_probabilities.as_ref() else {
        return Vec::new();
    };

    let rows = match app.selected_market_group() {
        Some(group) if probabilities.rows.iter().any(row_has_market_identity) => probabilities
            .rows
            .iter()
            .filter(|row| probability_matches_group(row, &group))
            .collect::<Vec<_>>(),
        _ => probabilities.rows.iter().collect::<Vec<_>>(),
    };

    rows.into_iter().map(probability_row).collect()
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

fn row_has_market_identity(row: &RuntimeProbabilityRow) -> bool {
    row.asset
        .as_deref()
        .is_some_and(|value| !value.trim().is_empty())
        || row
            .expiry_ts
            .as_deref()
            .is_some_and(|value| !value.trim().is_empty())
        || row
            .contract_id
            .as_deref()
            .is_some_and(|value| !value.trim().is_empty())
}

fn probability_matches_group(
    row: &RuntimeProbabilityRow,
    group: &crate::market_view::MarketGroup<'_>,
) -> bool {
    let asset_matches = row
        .asset
        .as_deref()
        .map(str::trim)
        .filter(|asset| !asset.is_empty())
        .map(|asset| asset.eq_ignore_ascii_case(&group.asset))
        .unwrap_or_else(|| row.contract.to_ascii_uppercase().starts_with(&group.asset));
    if !asset_matches {
        return false;
    }

    if let (Some(row_expiry), Some(group_expiry)) = (
        parse_probability_ts(row.expiry_ts.as_deref()),
        group.expiry_ts,
    ) {
        return row_expiry == group_expiry;
    }

    probability_contract_matches_group(row, group)
        || row
            .expiry_ts
            .as_deref()
            .is_none_or(|value| value.trim().is_empty())
}

fn probability_contract_matches_group(
    row: &RuntimeProbabilityRow,
    group: &crate::market_view::MarketGroup<'_>,
) -> bool {
    let Some(contract_id) = normalized(row.contract_id.as_deref()) else {
        return false;
    };
    let market_slug = group.market_slug.trim().to_ascii_lowercase();
    if !market_slug.is_empty() && contract_id.contains(&market_slug) {
        return true;
    }

    [group.up, group.down]
        .into_iter()
        .flatten()
        .any(|orderbook| {
            normalized(Some(&orderbook.contract_id)).as_deref() == Some(contract_id.as_str())
                || normalized(orderbook.token_id.as_deref()).as_deref()
                    == Some(contract_id.as_str())
        })
}

fn normalized(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_ascii_lowercase)
}

fn parse_probability_ts(value: Option<&str>) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value?)
        .ok()
        .map(|timestamp| timestamp.with_timezone(&Utc))
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
        .block(Block::bordered().title("Monte Carlo Health"));

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
            RuntimeMonitor, RuntimeOrderbookRow, RuntimeProbabilities, RuntimeProbabilityRow,
            RuntimeVolatility, RuntimeVolatilityRow,
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
                    contract_id: Some("btc-updown-5m-1780521900:UP".to_string()),
                    asset: Some("BTC".to_string()),
                    side: Some("UP".to_string()),
                    asof_ts: Some("2026-06-03T21:06:00Z".to_string()),
                    expiry_ts: Some("2026-06-03T21:10:00Z".to_string()),
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
    fn probability_rows_follow_selected_market_group() {
        let expiry_ts = "2026-06-03T21:25:00Z";
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:22:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    orderbook("BTC", "UP", "btc-updown-5m-1780521900", expiry_ts),
                    orderbook("BTC", "DOWN", "btc-updown-5m-1780521900", expiry_ts),
                    orderbook("ETH", "UP", "eth-updown-5m-1780521900", expiry_ts),
                    orderbook("ETH", "DOWN", "eth-updown-5m-1780521900", expiry_ts),
                ],
            }),
            runtime_probabilities: Some(RuntimeProbabilities {
                generated_at: "2026-06-03T21:22:00Z".to_string(),
                cached: false,
                rows: vec![
                    probability("BTC 5m UP", "BTC", "UP", expiry_ts, 0.57),
                    probability("BTC 5m DOWN", "BTC", "DOWN", expiry_ts, 0.43),
                    probability("ETH 5m UP", "ETH", "UP", expiry_ts, 0.61),
                    probability("ETH 5m DOWN", "ETH", "DOWN", expiry_ts, 0.39),
                ],
            }),
            ..Default::default()
        };

        app.sync_market_selection();
        app.select_next_market();
        let rows = probability_rows(&app);

        assert_eq!(
            rows.into_iter().map(|row| row.contract).collect::<Vec<_>>(),
            vec!["ETH 5m UP".to_string(), "ETH 5m DOWN".to_string()]
        );
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

    fn probability(
        contract: &str,
        asset: &str,
        side: &str,
        expiry_ts: &str,
        p_finish: f64,
    ) -> RuntimeProbabilityRow {
        RuntimeProbabilityRow {
            contract: contract.to_string(),
            contract_id: Some(format!(
                "{}:{}",
                contract.to_ascii_lowercase().replace(' ', "-"),
                side
            )),
            asset: Some(asset.to_string()),
            side: Some(side.to_string()),
            asof_ts: Some("2026-06-03T21:22:00Z".to_string()),
            expiry_ts: Some(expiry_ts.to_string()),
            p_finish,
            p_no_touch: 0.31,
            z_path: 0.42,
            sigma_tau: 0.0123,
            age_ms: 850,
            flags: vec!["OK".to_string()],
            mc_dispersion: None,
            uncertainty_buffer: None,
            path_diagnosis: Vec::new(),
            effective_weights: Default::default(),
            decision_hint: None,
            edge_after_costs: None,
            required_edge: None,
            gate_reasons: Vec::new(),
            generator_metadata: Default::default(),
        }
    }

    fn orderbook(
        asset: &str,
        side: &str,
        market_slug: &str,
        expiry_ts: &str,
    ) -> RuntimeOrderbookRow {
        RuntimeOrderbookRow {
            venue: Some("polymarket".to_string()),
            source_key: Some("polymarket_rust_sdk".to_string()),
            market_slug: Some(market_slug.to_string()),
            contract_id: format!("{market_slug}-{side}"),
            token_id: Some(format!("{market_slug}-{side}-token")),
            asset: Some(asset.to_string()),
            side: Some(side.to_string()),
            event_ts: None,
            observed_ts: Some("2026-06-03T21:22:00Z".to_string()),
            start_ts: Some("2026-06-03T21:20:00Z".to_string()),
            expiry_ts: Some(expiry_ts.to_string()),
            threshold_price: None,
            threshold_event_ts: None,
            threshold_observed_ts: None,
            settlement_price: None,
            settlement_event_ts: None,
            best_bid: None,
            best_ask: None,
            spread: None,
            bid_size_top: None,
            ask_size_top: None,
            bids: Vec::new(),
            asks: Vec::new(),
        }
    }
}
