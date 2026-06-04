use chrono::{DateTime, Local, Utc};
use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{
    market_view,
    render::orderbook,
    state::AppState,
    status::{RuntimeOrderbookRow, RuntimeOutcomeRow, RuntimeOutcomes},
};

#[cfg(test)]
const MARKET_VISIBLE_ROWS: usize = 10;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MarketDisplayRow {
    pub marker: String,
    pub expires: String,
    pub market: String,
    pub k: String,
    pub up: String,
    pub down: String,
    pub spread: String,
    pub tte: String,
    pub outcome: String,
}

pub fn market_header_labels() -> [&'static str; 9] {
    [
        "",
        "Expires",
        "Market",
        "K",
        "UP bid/ask",
        "DOWN bid/ask",
        "Spread",
        "TTE",
        "Outcome",
    ]
}

#[cfg(test)]
pub fn market_rows(app: &AppState) -> Vec<MarketDisplayRow> {
    market_rows_for_visible_count(app, MARKET_VISIBLE_ROWS)
}

pub fn market_rows_for_visible_count(app: &AppState, visible_rows: usize) -> Vec<MarketDisplayRow> {
    let selected_index = app.effective_market_index();
    let visible_rows = visible_rows.max(1);

    app.runtime_monitor
        .as_ref()
        .map(|monitor| {
            let groups = app.visible_market_groups();
            let display_rows = market_display_rows(
                &groups,
                app.runtime_outcomes.as_ref(),
                selected_index,
                &monitor.generated_at,
            );
            let selected_display_index = display_rows.iter().position(|row| row.marker == ">");
            let start =
                visible_market_start(display_rows.len(), selected_display_index, visible_rows);
            display_rows
                .into_iter()
                .skip(start)
                .take(visible_rows)
                .collect()
        })
        .unwrap_or_default()
}

fn market_display_rows(
    groups: &[market_view::MarketGroup<'_>],
    outcomes: Option<&RuntimeOutcomes>,
    selected_index: Option<usize>,
    generated_at: &str,
) -> Vec<MarketDisplayRow> {
    let mut rows = Vec::new();
    let mut last_asset: Option<String> = None;
    for (index, group) in groups.iter().enumerate() {
        let asset = group.asset.clone();
        if last_asset.as_deref() != Some(asset.as_str()) {
            if last_asset.is_some() {
                rows.push(MarketDisplayRow {
                    marker: " ".to_string(),
                    expires: String::new(),
                    market: String::new(),
                    k: String::new(),
                    up: String::new(),
                    down: String::new(),
                    spread: String::new(),
                    tte: String::new(),
                    outcome: String::new(),
                });
            }
            rows.push(MarketDisplayRow {
                marker: " ".to_string(),
                expires: String::new(),
                market: asset.clone(),
                k: String::new(),
                up: String::new(),
                down: String::new(),
                spread: String::new(),
                tte: String::new(),
                outcome: String::new(),
            });
            last_asset = Some(asset);
        }
        rows.push(MarketDisplayRow {
            marker: if selected_index == Some(index) {
                ">"
            } else {
                " "
            }
            .to_string(),
            expires: local_expiry_timestamp(group.expiry_ts),
            market: short_market_label(&group.label),
            k: market_k(group),
            up: top_quote(group.up),
            down: top_quote(group.down),
            spread: tight_spread(group.up, group.down),
            tte: countdown_to_expiry(group.expiry_ts, generated_at),
            outcome: market_outcome_label(group, outcomes),
        });
    }
    rows
}

fn visible_market_start(count: usize, selected_index: Option<usize>, visible_rows: usize) -> usize {
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

fn top_quote(orderbook: Option<&RuntimeOrderbookRow>) -> String {
    let Some(orderbook) = orderbook else {
        return "-".to_string();
    };
    match (
        positive_scalar(orderbook.best_bid.as_deref()),
        positive_scalar(orderbook.best_ask.as_deref()),
    ) {
        (Some(bid), Some(ask)) => format!("{bid}/{ask}"),
        (Some(bid), None) => format!("{bid}/-"),
        (None, Some(ask)) => format!("-/{ask}"),
        (None, None) => "-".to_string(),
    }
}

fn tight_spread(up: Option<&RuntimeOrderbookRow>, down: Option<&RuntimeOrderbookRow>) -> String {
    let mut spreads = [up, down]
        .into_iter()
        .flatten()
        .filter_map(|orderbook| {
            positive_number(orderbook.spread.as_deref()).or_else(|| {
                let bid = positive_number(orderbook.best_bid.as_deref())?;
                let ask = positive_number(orderbook.best_ask.as_deref())?;
                (ask - bid).is_sign_positive().then_some(ask - bid)
            })
        })
        .collect::<Vec<_>>();
    spreads.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
    if let Some(spread) = spreads.first() {
        format!("{spread:.4}")
    } else {
        "-".to_string()
    }
}

fn market_k(group: &market_view::MarketGroup<'_>) -> String {
    group
        .up
        .or(group.down)
        .and_then(|row| row.threshold_price.as_deref())
        .and_then(|price| positive_number(Some(price)))
        .map(format_usd_number)
        .unwrap_or_else(|| "pending".to_string())
}

fn positive_scalar(value: Option<&str>) -> Option<String> {
    let value = value?.trim();
    if value.is_empty() {
        return None;
    }
    let Ok(number) = value.parse::<f64>() else {
        return Some(value.to_string());
    };
    if number <= 0.0 {
        None
    } else {
        Some(value.to_string())
    }
}

fn positive_number(value: Option<&str>) -> Option<f64> {
    let number = value?.trim().parse::<f64>().ok()?;
    if number > 0.0 { Some(number) } else { None }
}

fn format_usd_number(value: f64) -> String {
    add_thousands_separators(&format!("{value:.2}"))
}

fn add_thousands_separators(value: &str) -> String {
    let (whole, fraction) = value.split_once('.').unwrap_or((value, ""));
    let (sign, digits) = whole
        .strip_prefix('-')
        .map_or(("", whole), |rest| ("-", rest));
    let mut grouped = String::new();

    for (index, character) in digits.chars().rev().enumerate() {
        if index > 0 && index % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(character);
    }

    let whole = grouped.chars().rev().collect::<String>();
    if fraction.is_empty() {
        format!("{sign}{whole}")
    } else {
        format!("{sign}{whole}.{fraction}")
    }
}

fn local_expiry_timestamp(expiry_ts: Option<DateTime<Utc>>) -> String {
    expiry_ts
        .map(|timestamp| {
            timestamp
                .with_timezone(&Local)
                .format("%b %d %H:%M %Z")
                .to_string()
        })
        .unwrap_or_else(|| "-".to_string())
}

fn short_market_label(label: &str) -> String {
    let parts = label.split_whitespace().take(2).collect::<Vec<_>>();
    if parts.len() == 2 {
        parts.join(" ")
    } else {
        label.to_string()
    }
}

fn market_outcome_label(
    group: &market_view::MarketGroup<'_>,
    outcomes: Option<&RuntimeOutcomes>,
) -> String {
    let Some(outcome) = outcomes
        .into_iter()
        .flat_map(|outcomes| outcomes.rows.iter())
        .find(|row| outcome_matches_group(row, group))
    else {
        return "-".to_string();
    };

    outcome
        .official_winner
        .as_deref()
        .filter(|winner| !winner.trim().is_empty())
        .map(|winner| winner.trim().to_ascii_uppercase())
        .unwrap_or_else(|| outcome.official_resolution_status.to_ascii_lowercase())
}

fn outcome_matches_group(
    outcome: &RuntimeOutcomeRow,
    group: &market_view::MarketGroup<'_>,
) -> bool {
    outcome.market_id.eq_ignore_ascii_case(&group.market_slug)
        || outcome
            .market_slug
            .as_deref()
            .is_some_and(|slug| slug.eq_ignore_ascii_case(&group.market_slug))
        || outcome_expiry_matches_group(outcome, group)
}

fn outcome_expiry_matches_group(
    outcome: &RuntimeOutcomeRow,
    group: &market_view::MarketGroup<'_>,
) -> bool {
    let Some(group_expiry) = group.expiry_ts else {
        return false;
    };
    let Some(outcome_expiry) = outcome.expiry_ts.as_deref() else {
        return false;
    };
    let Ok(outcome_expiry) = DateTime::parse_from_rfc3339(outcome_expiry) else {
        return false;
    };
    outcome
        .asset
        .as_deref()
        .is_some_and(|asset| asset.eq_ignore_ascii_case(&group.asset))
        && outcome_expiry.with_timezone(&Utc) == group_expiry
}

fn countdown_to_expiry(expiry_ts: Option<DateTime<Utc>>, generated_at: &str) -> String {
    let Some(expiry_ts) = expiry_ts else {
        return "-".to_string();
    };
    let Ok(generated_at) = DateTime::parse_from_rfc3339(generated_at) else {
        return "-".to_string();
    };
    let remaining = expiry_ts
        .signed_duration_since(generated_at.with_timezone(&Utc))
        .num_seconds();
    if remaining < 0 {
        return format!("+{}", duration_label(-remaining));
    }

    duration_label(remaining)
}

fn duration_label(seconds: i64) -> String {
    let hours = seconds / 3600;
    let minutes = (seconds % 3600) / 60;
    let seconds = seconds % 60;
    if hours > 0 {
        format!("{hours:02}:{minutes:02}:{seconds:02}")
    } else {
        format!("{minutes:02}:{seconds:02}")
    }
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let [counts_area, orderbook_area] = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(13), Constraint::Min(3)])
        .areas(area);
    let visible_rows = counts_area.height.saturating_sub(3).max(1) as usize;
    let rows = market_rows_for_visible_count(app, visible_rows)
        .into_iter()
        .map(|row| {
            Row::new(vec![
                Cell::from(row.marker),
                Cell::from(row.expires),
                Cell::from(row.market),
                Cell::from(row.k),
                Cell::from(row.up),
                Cell::from(row.down),
                Cell::from(row.spread),
                Cell::from(row.tte),
                Cell::from(row.outcome),
            ])
        })
        .collect::<Vec<_>>();
    let rows = if rows.is_empty() {
        vec![Row::new(vec![
            Cell::from(" "),
            Cell::from("-"),
            Cell::from("monitor pending"),
            Cell::from("pending"),
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
            Constraint::Length(2),
            Constraint::Length(18),
            Constraint::Length(10),
            Constraint::Length(11),
            Constraint::Length(13),
            Constraint::Length(13),
            Constraint::Length(7),
            Constraint::Length(8),
            Constraint::Min(8),
        ],
    )
    .header(Row::new(market_header_labels().to_vec()).style(Style::default().fg(Color::Cyan)))
    .block(Block::bordered().title("Market"));

    frame.render_widget(table, counts_area);
    orderbook::render(frame, orderbook_area, app);
}

#[cfg(test)]
mod tests {
    use chrono::{Local, TimeZone, Utc};

    use crate::{
        state::AppState,
        status::{
            RuntimeBookLevel, RuntimeMonitor, RuntimeOrderbookRow, RuntimeOutcomeRow,
            RuntimeOutcomes,
        },
    };

    use super::{market_header_labels, market_rows, market_rows_for_visible_count};

    #[test]
    fn market_rows_show_contract_identity_and_top_of_book() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T20:43:20.744215+00:00".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: Some("polymarket_rust_sdk".to_string()),
                    market_slug: Some("eth-updown-5m-1780519500".to_string()),
                    contract_id: "0x0abe644dd79156eeeb5e4e3be9f8f78953d9907316c57e014c3598f2ae99e3cc".to_string(),
                    token_id: Some("100783333159874947931352697222477663764026407100859257224541015812712077669400".to_string()),
                    asset: Some("ETH".to_string()),
                    side: Some("DOWN".to_string()),
                    event_ts: Some("2026-06-03T20:43:12.101Z".to_string()),
                    observed_ts: Some("2026-06-03T20:43:20.616043736Z".to_string()),
                    start_ts: None,
                    expiry_ts: None,
                    threshold_price: None,
                    threshold_event_ts: None,
                    threshold_observed_ts: None,
                    settlement_price: None,
                    settlement_event_ts: None,
                    best_bid: Some("0.86".to_string()),
                    best_ask: Some("0.87".to_string()),
                    spread: Some("0.01".to_string()),
                    bid_size_top: Some("33".to_string()),
                    ask_size_top: Some("14.46".to_string()),
                    bids: vec![RuntimeBookLevel {
                        price: Some("0.86".to_string()),
                        size: Some("33".to_string()),
                    }],
                    asks: vec![RuntimeBookLevel {
                        price: Some("0.87".to_string()),
                        size: Some("14.46".to_string()),
                    }],
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert_eq!(rows[0].market, "ETH");
        assert_eq!(rows[1].expires, local_expiry_timestamp_label(1_780_519_500));
        assert_eq!(rows[1].market, "ETH 5m");
        assert_eq!(rows[1].up, "-");
        assert_eq!(rows[1].down, "0.86/0.87");
        assert_eq!(rows[1].spread, "0.0100");
        assert_eq!(rows[1].outcome, "-");
        assert!(!rows[1].market.ends_with('Z'));
        assert!(!rows[1].expires.ends_with('Z'));
    }

    #[test]
    fn market_rows_fall_back_to_contract_id_when_metadata_is_missing() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T20:43:20.744215+00:00".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: None,
                    market_slug: None,
                    contract_id: "btc-5m-up".to_string(),
                    token_id: Some("token-1".to_string()),
                    asset: None,
                    side: None,
                    event_ts: None,
                    observed_ts: Some("2026-06-03T20:43:20.616043736Z".to_string()),
                    start_ts: None,
                    expiry_ts: None,
                    threshold_price: None,
                    threshold_event_ts: None,
                    threshold_observed_ts: None,
                    settlement_price: None,
                    settlement_event_ts: None,
                    best_bid: Some("0.44".to_string()),
                    best_ask: None,
                    spread: None,
                    bid_size_top: None,
                    ask_size_top: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert_eq!(rows[0].market, "OTHER");
        assert_eq!(rows[1].market, "btc-5m-up");
        assert_eq!(rows[1].up, "0.44/-");
        assert_eq!(rows[1].down, "-");
        assert_eq!(rows[1].spread, "-");
    }

    #[test]
    fn market_rows_hide_nonpositive_top_of_book_values() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: Some("polymarket_rust_sdk".to_string()),
                    market_slug: Some("btc-updown-5m-1780522200".to_string()),
                    contract_id: "btc-up".to_string(),
                    token_id: Some("btc-up-token".to_string()),
                    asset: Some("BTC".to_string()),
                    side: Some("UP".to_string()),
                    event_ts: None,
                    observed_ts: Some("2026-06-03T21:05:58Z".to_string()),
                    start_ts: None,
                    expiry_ts: None,
                    threshold_price: None,
                    threshold_event_ts: None,
                    threshold_observed_ts: None,
                    settlement_price: None,
                    settlement_event_ts: None,
                    best_bid: Some("0".to_string()),
                    best_ask: Some("-0.01".to_string()),
                    spread: Some("0".to_string()),
                    bid_size_top: Some("100".to_string()),
                    ask_size_top: Some("200".to_string()),
                    bids: Vec::new(),
                    asks: Vec::new(),
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert_eq!(rows[1].market, "BTC 5m");
        assert_eq!(rows[1].up, "-");
        assert_eq!(rows[1].down, "-");
        assert_eq!(rows[1].spread, "-");
    }

    #[test]
    fn market_rows_show_expiry_before_market_and_mark_selected_contract() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("eth-updown-5m-1780522200".to_string()),
                        contract_id: "eth-up".to_string(),
                        token_id: Some("eth-up-token".to_string()),
                        asset: Some("ETH".to_string()),
                        side: Some("UP".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:58Z".to_string()),
                        start_ts: None,
                        expiry_ts: None,
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        settlement_price: None,
                        settlement_event_ts: None,
                        best_bid: Some("0.88".to_string()),
                        best_ask: Some("0.89".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: Some("102".to_string()),
                        ask_size_top: Some("26".to_string()),
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("btc-updown-5m-1780522200".to_string()),
                        contract_id: "btc-down".to_string(),
                        token_id: Some("btc-down-token".to_string()),
                        asset: Some("BTC".to_string()),
                        side: Some("DOWN".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:47Z".to_string()),
                        start_ts: None,
                        expiry_ts: None,
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        settlement_price: None,
                        settlement_event_ts: None,
                        best_bid: Some("0.49".to_string()),
                        best_ask: Some("0.50".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: Some("1256.68".to_string()),
                        ask_size_top: Some("702.96".to_string()),
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                ],
            }),
            ..Default::default()
        };
        app.sync_market_selection();

        let rows = market_rows(&app);

        assert_eq!(
            market_header_labels(),
            [
                "",
                "Expires",
                "Market",
                "K",
                "UP bid/ask",
                "DOWN bid/ask",
                "Spread",
                "TTE",
                "Outcome"
            ]
        );
        assert_eq!(rows[0].expires, "");
        assert_eq!(rows[0].market, "BTC");
        assert_eq!(rows[1].marker, ">");
        assert_eq!(rows[1].expires, local_expiry_timestamp_label(1_780_522_200));
        assert_eq!(rows[1].outcome, "-");
        assert_eq!(rows[2].market, "");
        assert_eq!(rows[3].market, "ETH");
        assert_eq!(rows[4].marker, " ");
    }

    #[test]
    fn market_rows_show_threshold_k_from_up_or_down_row() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    threshold_orderbook("BTC", "UP", "btc-updown-5m-1780522200", Some("64000")),
                    threshold_orderbook("ETH", "DOWN", "eth-updown-5m-1780522200", None),
                ],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert_eq!(
            market_header_labels(),
            [
                "",
                "Expires",
                "Market",
                "K",
                "UP bid/ask",
                "DOWN bid/ask",
                "Spread",
                "TTE",
                "Outcome"
            ]
        );
        assert_eq!(rows[1].k, "64,000.00");
        assert_eq!(rows[4].k, "pending");
    }

    #[test]
    fn market_rows_show_countdown_to_expiration_from_monitor_time() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:23:20Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: Some("polymarket_rust_sdk".to_string()),
                    market_slug: Some("btc-updown-5m-1780521900".to_string()),
                    contract_id: "btc-up".to_string(),
                    token_id: Some("btc-up-token".to_string()),
                    asset: Some("BTC".to_string()),
                    side: Some("UP".to_string()),
                    event_ts: None,
                    observed_ts: Some("2026-06-03T21:23:18Z".to_string()),
                    start_ts: None,
                    expiry_ts: None,
                    threshold_price: None,
                    threshold_event_ts: None,
                    threshold_observed_ts: None,
                    settlement_price: None,
                    settlement_event_ts: None,
                    best_bid: Some("0.45".to_string()),
                    best_ask: Some("0.46".to_string()),
                    spread: Some("0.01".to_string()),
                    bid_size_top: None,
                    ask_size_top: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert_eq!(rows[1].tte, "01:40");
    }

    #[test]
    fn market_rows_show_post_expiration_handoff_timer() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:25:05Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: Some("polymarket_rust_sdk".to_string()),
                    market_slug: Some("btc-updown-5m-1780521900".to_string()),
                    contract_id: "btc-up".to_string(),
                    token_id: Some("btc-up-token".to_string()),
                    asset: Some("BTC".to_string()),
                    side: Some("UP".to_string()),
                    event_ts: None,
                    observed_ts: Some("2026-06-03T21:24:59Z".to_string()),
                    start_ts: None,
                    expiry_ts: None,
                    threshold_price: None,
                    threshold_event_ts: None,
                    threshold_observed_ts: None,
                    settlement_price: None,
                    settlement_event_ts: None,
                    best_bid: Some("0.99".to_string()),
                    best_ask: None,
                    spread: None,
                    bid_size_top: None,
                    ask_size_top: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert_eq!(rows[1].tte, "+00:05");
    }

    #[test]
    fn market_rows_show_official_outcome_during_post_expiration_handoff() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:25:45Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: Some("polymarket_rust_sdk".to_string()),
                    market_slug: Some("btc-updown-5m-1780521900".to_string()),
                    contract_id: "btc-up".to_string(),
                    token_id: Some("btc-up-token".to_string()),
                    asset: Some("BTC".to_string()),
                    side: Some("UP".to_string()),
                    event_ts: None,
                    observed_ts: Some("2026-06-03T21:24:59Z".to_string()),
                    start_ts: None,
                    expiry_ts: None,
                    threshold_price: None,
                    threshold_event_ts: None,
                    threshold_observed_ts: None,
                    settlement_price: None,
                    settlement_event_ts: None,
                    best_bid: Some("0.99".to_string()),
                    best_ask: None,
                    spread: None,
                    bid_size_top: None,
                    ask_size_top: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                }],
            }),
            runtime_outcomes: Some(RuntimeOutcomes {
                ok: true,
                state: "OK".to_string(),
                generated_at: Some("2026-06-03T21:25:45Z".to_string()),
                rows: vec![RuntimeOutcomeRow {
                    market: "BTC 5m".to_string(),
                    market_id: "btc-updown-5m-1780521900".to_string(),
                    market_slug: Some("btc-updown-5m-1780521900".to_string()),
                    asset: Some("BTC".to_string()),
                    start_ts: Some("2026-06-03T21:20:00Z".to_string()),
                    expiry_ts: Some("2026-06-03T21:25:00Z".to_string()),
                    computed_winner: None,
                    official_winner: Some("UP".to_string()),
                    winning_token_id: Some("btc-up-token".to_string()),
                    official_resolution_status: "resolved".to_string(),
                    mismatch: None,
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert_eq!(rows[1].tte, "+00:45");
        assert_eq!(rows[1].outcome, "UP");
    }

    #[test]
    fn market_rows_keep_resolved_expired_market_until_outcome_visible_for_30_seconds() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:26:49Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    threshold_orderbook("BTC", "UP", "btc-updown-5m-1780521900", Some("64000")),
                    threshold_orderbook("BTC", "DOWN", "btc-updown-5m-1780521900", Some("64000")),
                ],
            }),
            ..Default::default()
        };
        app.apply_runtime_outcomes(RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-03T21:26:20Z".to_string()),
            rows: vec![RuntimeOutcomeRow {
                market: "BTC 5m".to_string(),
                market_id: "btc-updown-5m-1780521900".to_string(),
                market_slug: Some("btc-updown-5m-1780521900".to_string()),
                asset: Some("BTC".to_string()),
                start_ts: Some("2026-06-03T21:20:00Z".to_string()),
                expiry_ts: Some("2026-06-03T21:25:00Z".to_string()),
                computed_winner: None,
                official_winner: Some("UP".to_string()),
                winning_token_id: Some("btc-up-token".to_string()),
                official_resolution_status: "resolved".to_string(),
                mismatch: None,
            }],
        });

        let rows = market_rows(&app);

        assert_eq!(rows[0].market, "BTC");
        assert_eq!(rows[1].market, "BTC 5m");
        assert_eq!(rows[1].tte, "+01:49");
        assert_eq!(rows[1].outcome, "UP");
    }

    #[test]
    fn market_rows_drop_expired_contract_after_handoff_window() {
        let app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:26:01Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![RuntimeOrderbookRow {
                    venue: Some("polymarket".to_string()),
                    source_key: Some("polymarket_rust_sdk".to_string()),
                    market_slug: Some("btc-updown-5m-1780521900".to_string()),
                    contract_id: "btc-up".to_string(),
                    token_id: Some("btc-up-token".to_string()),
                    asset: Some("BTC".to_string()),
                    side: Some("UP".to_string()),
                    event_ts: None,
                    observed_ts: Some("2026-06-03T21:24:59Z".to_string()),
                    start_ts: None,
                    expiry_ts: None,
                    threshold_price: None,
                    threshold_event_ts: None,
                    threshold_observed_ts: None,
                    settlement_price: None,
                    settlement_event_ts: None,
                    best_bid: Some("0.99".to_string()),
                    best_ask: None,
                    spread: None,
                    bid_size_top: None,
                    ask_size_top: None,
                    bids: Vec::new(),
                    asks: Vec::new(),
                }],
            }),
            ..Default::default()
        };

        let rows = market_rows(&app);

        assert!(rows.is_empty());
    }

    #[test]
    fn market_rows_keep_selected_contract_inside_visible_window() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: (0..8)
                    .map(|index| RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some(format!("btc-updown-5m-1780521{index:03}")),
                        contract_id: format!("btc-row-{index}"),
                        token_id: Some(format!("btc-row-{index}-token")),
                        asset: Some("BTC".to_string()),
                        side: Some(if index % 2 == 0 { "UP" } else { "DOWN" }.to_string()),
                        event_ts: None,
                        observed_ts: Some(format!("2026-06-03T21:05:5{index}Z")),
                        start_ts: None,
                        expiry_ts: None,
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        settlement_price: None,
                        settlement_event_ts: None,
                        best_bid: Some(format!("0.{index}1")),
                        best_ask: Some(format!("0.{index}2")),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    })
                    .collect(),
            }),
            ..Default::default()
        };
        app.sync_market_selection();
        app.select_previous_market();

        let rows = market_rows(&app);

        assert_eq!(app.selected_market_index(), Some(7));
        assert!(rows.len() <= 10);
        assert_eq!(rows.last().map(|row| row.marker.as_str()), Some(">"));
        assert_eq!(rows.last().map(|row| row.down.as_str()), Some("0.71/0.72"));
    }

    #[test]
    fn market_rows_keep_selected_contract_visible_in_short_panels() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: (0..12)
                    .map(|index| RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some(format!("btc-updown-5m-1780522{index:03}")),
                        contract_id: format!("btc-row-{index}"),
                        token_id: Some(format!("btc-row-{index}-token")),
                        asset: Some("BTC".to_string()),
                        side: Some(if index % 2 == 0 { "UP" } else { "DOWN" }.to_string()),
                        event_ts: None,
                        observed_ts: Some(format!("2026-06-03T21:05:{index:02}Z")),
                        start_ts: None,
                        expiry_ts: None,
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        settlement_price: None,
                        settlement_event_ts: None,
                        best_bid: Some(format!("0.{index}1")),
                        best_ask: Some(format!("0.{index}2")),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    })
                    .collect(),
            }),
            ..Default::default()
        };
        app.sync_market_selection();
        app.select_previous_market();

        let rows = market_rows_for_visible_count(&app, 4);

        assert_eq!(app.selected_market_index(), Some(11));
        assert_eq!(rows.len(), 4);
        assert_eq!(rows.last().map(|row| row.marker.as_str()), Some(">"));
        assert_eq!(
            rows.last().map(|row| row.down.as_str()),
            Some("0.111/0.112")
        );
    }

    #[test]
    fn market_rows_keep_selected_contract_visible_with_asset_spacers() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:00Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("btc-updown-5m-1780521900".to_string()),
                        contract_id: "btc-early".to_string(),
                        token_id: Some("btc-early-token".to_string()),
                        asset: Some("BTC".to_string()),
                        side: Some("UP".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:55Z".to_string()),
                        start_ts: None,
                        expiry_ts: None,
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        settlement_price: None,
                        settlement_event_ts: None,
                        best_bid: Some("0.41".to_string()),
                        best_ask: Some("0.42".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("btc-updown-5m-1780522200".to_string()),
                        contract_id: "btc-late".to_string(),
                        token_id: Some("btc-late-token".to_string()),
                        asset: Some("BTC".to_string()),
                        side: Some("UP".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:56Z".to_string()),
                        start_ts: None,
                        expiry_ts: None,
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        settlement_price: None,
                        settlement_event_ts: None,
                        best_bid: Some("0.51".to_string()),
                        best_ask: Some("0.52".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("eth-updown-5m-1780521900".to_string()),
                        contract_id: "eth-early".to_string(),
                        token_id: Some("eth-early-token".to_string()),
                        asset: Some("ETH".to_string()),
                        side: Some("UP".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:57Z".to_string()),
                        start_ts: None,
                        expiry_ts: None,
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        settlement_price: None,
                        settlement_event_ts: None,
                        best_bid: Some("0.61".to_string()),
                        best_ask: Some("0.62".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                    RuntimeOrderbookRow {
                        venue: Some("polymarket".to_string()),
                        source_key: Some("polymarket_rust_sdk".to_string()),
                        market_slug: Some("eth-updown-5m-1780522200".to_string()),
                        contract_id: "eth-late".to_string(),
                        token_id: Some("eth-late-token".to_string()),
                        asset: Some("ETH".to_string()),
                        side: Some("UP".to_string()),
                        event_ts: None,
                        observed_ts: Some("2026-06-03T21:05:58Z".to_string()),
                        start_ts: None,
                        expiry_ts: None,
                        threshold_price: None,
                        threshold_event_ts: None,
                        threshold_observed_ts: None,
                        settlement_price: None,
                        settlement_event_ts: None,
                        best_bid: Some("0.71".to_string()),
                        best_ask: Some("0.72".to_string()),
                        spread: Some("0.01".to_string()),
                        bid_size_top: None,
                        ask_size_top: None,
                        bids: Vec::new(),
                        asks: Vec::new(),
                    },
                ],
            }),
            ..Default::default()
        };
        app.sync_market_selection();
        app.select_previous_market();

        let rows = market_rows_for_visible_count(&app, 3);

        assert_eq!(app.selected_market_index(), Some(3));
        assert_eq!(rows.len(), 3);
        assert_eq!(rows.last().map(|row| row.marker.as_str()), Some(">"));
        assert_eq!(rows.last().map(|row| row.up.as_str()), Some("0.71/0.72"));
    }

    fn local_expiry_timestamp_label(epoch_seconds: i64) -> String {
        Utc.timestamp_opt(epoch_seconds, 0)
            .single()
            .unwrap()
            .with_timezone(&Local)
            .format("%b %d %H:%M %Z")
            .to_string()
    }

    fn threshold_orderbook(
        asset: &str,
        side: &str,
        market_slug: &str,
        threshold_price: Option<&str>,
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
            observed_ts: Some("2026-06-03T21:05:58Z".to_string()),
            start_ts: None,
            expiry_ts: None,
            threshold_price: threshold_price.map(str::to_string),
            threshold_event_ts: None,
            threshold_observed_ts: None,
            settlement_price: None,
            settlement_event_ts: None,
            best_bid: Some("0.45".to_string()),
            best_ask: Some("0.46".to_string()),
            spread: Some("0.01".to_string()),
            bid_size_top: None,
            ask_size_top: None,
            bids: Vec::new(),
            asks: Vec::new(),
        }
    }
}
