use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    symbols,
    widgets::{Axis, Block, Chart, Dataset, GraphType, Paragraph},
};

use crate::{market_view, state::AppState};

#[cfg(test)]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PricePathDisplayRow {
    pub asset: String,
    pub latest: String,
    pub target: String,
    pub path: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PricePathChartModel {
    pub asset: String,
    pub latest: String,
    pub target: String,
    pub price_points: Vec<(f64, f64)>,
    pub target_line: Vec<(f64, f64)>,
    pub y_bounds: [f64; 2],
}

#[cfg(test)]
pub fn price_path_rows(app: &AppState) -> Vec<PricePathDisplayRow> {
    ["BTC", "ETH"]
        .into_iter()
        .map(|asset| {
            let symbol = format!("{asset}/USD");
            let history = app.price_history_for(&symbol);
            PricePathDisplayRow {
                asset: asset.to_string(),
                latest: history
                    .last()
                    .map(|point| format_usd_number(point.price))
                    .unwrap_or_else(|| "-".to_string()),
                target: current_target_label(app, asset),
                path: compact_path(&history),
            }
        })
        .collect()
}

pub fn price_path_charts(app: &AppState) -> Vec<PricePathChartModel> {
    ["BTC", "ETH"]
        .into_iter()
        .map(|asset| {
            let symbol = format!("{asset}/USD");
            let history = app.price_history_for(&symbol);
            let latest = history
                .last()
                .map(|point| format_usd_number(point.price))
                .unwrap_or_else(|| "-".to_string());
            let target_value = selected_target_value(app, asset);
            let price_points = history
                .into_iter()
                .enumerate()
                .map(|(index, point)| (index as f64, point.price))
                .collect::<Vec<_>>();
            let target_line = target_value
                .map(|price| {
                    let x_max = x_axis_upper_bound(&price_points);
                    vec![(0.0, price), (x_max, price)]
                })
                .unwrap_or_default();

            PricePathChartModel {
                asset: asset.to_string(),
                latest,
                target: target_label(target_value),
                y_bounds: chart_y_bounds(&price_points, target_value),
                price_points,
                target_line,
            }
        })
        .collect()
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let charts = price_path_charts(app);
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(area);

    for (chart, chunk) in charts.iter().zip(chunks.iter()) {
        render_price_chart(frame, *chunk, chart);
    }
}

fn render_price_chart(frame: &mut Frame<'_>, area: Rect, model: &PricePathChartModel) {
    let title = format!(
        "{} Price | {} | {}",
        model.asset, model.latest, model.target
    );
    if model.price_points.is_empty() {
        frame.render_widget(
            Paragraph::new("waiting for price history").block(Block::bordered().title(title)),
            area,
        );
        return;
    }

    let mut datasets = vec![
        Dataset::default()
            .marker(symbols::Marker::Braille)
            .graph_type(GraphType::Line)
            .style(Style::default().fg(Color::Cyan))
            .data(&model.price_points),
    ];

    if !model.target_line.is_empty() {
        datasets.push(
            Dataset::default()
                .marker(symbols::Marker::Braille)
                .graph_type(GraphType::Line)
                .style(Style::default().fg(Color::Yellow))
                .data(&model.target_line),
        );
    }

    let chart = Chart::new(datasets)
        .block(Block::bordered().title(title))
        .x_axis(
            Axis::default()
                .bounds([0.0, x_axis_upper_bound(&model.price_points)])
                .labels(["old", "now"]),
        )
        .y_axis(Axis::default().bounds(model.y_bounds).labels([
            format_axis_number(model.y_bounds[0]),
            format_axis_number(model.y_bounds[1]),
        ]));

    frame.render_widget(chart, area);
}

#[cfg(test)]
fn current_target_label(app: &AppState, asset: &str) -> String {
    target_label(selected_target_value(app, asset))
}

fn selected_target_value(app: &AppState, asset: &str) -> Option<f64> {
    let selected = app.selected_market_group()?;
    if selected.asset.eq_ignore_ascii_case(asset) {
        return threshold_price(&selected);
    }

    let selected_expiry = selected.expiry_ts?;
    app.visible_market_groups()
        .into_iter()
        .find(|group| {
            group.asset.eq_ignore_ascii_case(asset) && group.expiry_ts == Some(selected_expiry)
        })
        .and_then(|group| threshold_price(&group))
}

fn threshold_price(group: &market_view::MarketGroup<'_>) -> Option<f64> {
    [group.up, group.down]
        .into_iter()
        .flatten()
        .find_map(|row| row.threshold_price.as_deref().and_then(parse_positive))
}

#[cfg(test)]
fn compact_path(history: &[&crate::state::PriceHistoryPoint]) -> String {
    let samples = history
        .iter()
        .rev()
        .take(4)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .map(|point| format_usd_number(point.price))
        .collect::<Vec<_>>();
    if samples.is_empty() {
        "-".to_string()
    } else {
        samples.join(" -> ")
    }
}

fn x_axis_upper_bound(points: &[(f64, f64)]) -> f64 {
    points.last().map(|point| point.0.max(1.0)).unwrap_or(1.0)
}

fn chart_y_bounds(points: &[(f64, f64)], target_value: Option<f64>) -> [f64; 2] {
    let mut values = points.iter().map(|point| point.1).collect::<Vec<_>>();
    if let Some(target_value) = target_value {
        values.push(target_value);
    }

    let Some(mut min) = values.iter().copied().reduce(f64::min) else {
        return [0.0, 1.0];
    };
    let Some(mut max) = values.iter().copied().reduce(f64::max) else {
        return [0.0, 1.0];
    };

    if !min.is_finite() || !max.is_finite() {
        return [0.0, 1.0];
    }

    let midpoint = (min + max) / 2.0;
    let minimum_span = (midpoint.abs() * 0.001).max(1.0);
    if (max - min).abs() < minimum_span {
        min = midpoint - (minimum_span / 2.0);
        max = midpoint + (minimum_span / 2.0);
    }

    let span = (max - min).abs();
    let pad = (span * 0.12).max(0.01);
    min -= pad;
    max += pad;
    [min, max]
}

fn parse_positive(value: &str) -> Option<f64> {
    let number = value.trim().parse::<f64>().ok()?;
    (number > 0.0 && number.is_finite()).then_some(number)
}

fn format_usd_number(value: f64) -> String {
    add_thousands_separators(&format!("{value:.2}"))
}

fn target_label(value: Option<f64>) -> String {
    value
        .map(|price| format!("K {}", format_usd_number(price)))
        .unwrap_or_else(|| "K -".to_string())
}

fn format_axis_number(value: f64) -> String {
    add_thousands_separators(&format!("{value:.0}"))
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

#[cfg(test)]
mod tests {
    use ratatui::{Terminal, backend::TestBackend};

    use crate::{
        state::AppState,
        status::{RuntimeMonitor, RuntimeOrderbookRow, RuntimePriceRow},
    };

    use super::{price_path_charts, price_path_rows};

    #[test]
    fn price_path_rows_include_latest_price_and_current_target() {
        let mut app = AppState::default();
        app.apply_runtime_monitor(RuntimeMonitor {
            generated_at: "2026-06-04T07:43:10Z".to_string(),
            price_rows: vec![price_row("BTC/USD", "64050")],
            orderbooks: vec![orderbook_with_threshold("BTC", "64000")],
        });

        let rows = price_path_rows(&app);

        assert_eq!(rows[0].asset, "BTC");
        assert_eq!(rows[0].latest, "64,050.00");
        assert_eq!(rows[0].target, "K 64,000.00");
    }

    #[test]
    fn price_path_uses_current_window_target_not_expired_retained_handoff() {
        let mut app = AppState::default();
        app.apply_runtime_monitor(RuntimeMonitor {
            generated_at: "2026-06-04T07:43:10Z".to_string(),
            price_rows: vec![price_row("BTC/USD", "64050")],
            orderbooks: vec![
                orderbook_with_window(
                    "BTC",
                    "btc-updown-5m-expired",
                    "2026-06-04T07:35:00Z",
                    "2026-06-04T07:40:00Z",
                    "63000",
                ),
                orderbook_with_window(
                    "BTC",
                    "btc-updown-5m-current",
                    "2026-06-04T07:40:00Z",
                    "2026-06-04T07:45:00Z",
                    "64000",
                ),
            ],
        });

        let rows = price_path_rows(&app);

        assert_eq!(rows[0].target, "K 64,000.00");
    }

    #[test]
    fn price_path_charts_build_price_line_and_horizontal_target_line() {
        let mut app = AppState::default();
        for price in ["64000", "64010", "64020"] {
            app.apply_runtime_monitor(RuntimeMonitor {
                generated_at: "2026-06-04T07:43:10Z".to_string(),
                price_rows: vec![price_row("BTC/USD", price)],
                orderbooks: vec![orderbook_with_threshold("BTC", "64005")],
            });
        }

        let charts = price_path_charts(&app);
        let btc = charts
            .iter()
            .find(|chart| chart.asset == "BTC")
            .expect("BTC chart should exist");

        assert_eq!(
            btc.price_points,
            vec![(0.0, 64000.0), (1.0, 64010.0), (2.0, 64020.0)]
        );
        assert_eq!(btc.target_line, vec![(0.0, 64005.0), (2.0, 64005.0)]);
        assert!(btc.y_bounds[0] < 64000.0);
        assert!(btc.y_bounds[1] > 64020.0);
    }

    #[test]
    fn price_path_target_line_follows_selected_contract() {
        let mut app = AppState::default();
        app.apply_runtime_monitor(RuntimeMonitor {
            generated_at: "2026-06-04T07:43:10Z".to_string(),
            price_rows: vec![price_row("BTC/USD", "64050")],
            orderbooks: vec![
                orderbook_with_window(
                    "BTC",
                    "btc-updown-5m-current",
                    "2026-06-04T07:40:00Z",
                    "2026-06-04T07:45:00Z",
                    "64000",
                ),
                orderbook_with_window(
                    "BTC",
                    "btc-updown-5m-next",
                    "2026-06-04T07:45:00Z",
                    "2026-06-04T07:50:00Z",
                    "64100",
                ),
            ],
        });
        app.sync_market_selection();
        app.select_next_market();

        let charts = price_path_charts(&app);
        let btc = charts
            .iter()
            .find(|chart| chart.asset == "BTC")
            .expect("BTC chart should exist");

        assert_eq!(btc.target, "K 64,100.00");
        assert_eq!(btc.target_line, vec![(0.0, 64100.0), (1.0, 64100.0)]);
    }

    #[test]
    fn price_path_omits_target_line_when_selected_contract_has_no_k() {
        let mut app = AppState::default();
        app.apply_runtime_monitor(RuntimeMonitor {
            generated_at: "2026-06-04T07:43:10Z".to_string(),
            price_rows: vec![price_row("BTC/USD", "64050")],
            orderbooks: vec![
                orderbook_with_window(
                    "BTC",
                    "btc-updown-5m-current",
                    "2026-06-04T07:40:00Z",
                    "2026-06-04T07:45:00Z",
                    "64000",
                ),
                orderbook_without_threshold(
                    "BTC",
                    "btc-updown-5m-next",
                    "2026-06-04T07:45:00Z",
                    "2026-06-04T07:50:00Z",
                ),
            ],
        });
        app.sync_market_selection();
        app.select_next_market();

        let charts = price_path_charts(&app);
        let btc = charts
            .iter()
            .find(|chart| chart.asset == "BTC")
            .expect("BTC chart should exist");

        assert_eq!(btc.target, "K -");
        assert!(btc.target_line.is_empty());
    }

    #[test]
    fn price_path_render_hides_dataset_legend_labels() {
        let mut app = AppState::default();
        for price in ["64000", "64010", "64020"] {
            app.apply_runtime_monitor(RuntimeMonitor {
                generated_at: "2026-06-04T07:43:10Z".to_string(),
                price_rows: vec![price_row("BTC/USD", price), price_row("ETH/USD", "1800")],
                orderbooks: vec![
                    orderbook_with_threshold("BTC", "64005"),
                    orderbook_with_threshold("ETH", "1801"),
                ],
            });
        }

        let backend = TestBackend::new(80, 20);
        let mut terminal = Terminal::new(backend).expect("terminal should initialize");
        terminal
            .draw(|frame| super::render(frame, frame.area(), &app))
            .expect("render should complete");
        let rendered = terminal
            .backend()
            .buffer()
            .content
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();

        assert!(!rendered.contains("price"), "{rendered}");
    }

    #[test]
    fn price_path_y_bounds_keep_small_moves_from_filling_chart() {
        let bounds = super::chart_y_bounds(&[(0.0, 64000.0), (1.0, 64000.25)], None);

        assert!(
            bounds[1] - bounds[0] >= 64.0,
            "expected at least a 0.1% BTC-scale viewport, got {:?}",
            bounds
        );
    }

    fn price_row(symbol: &str, price: &str) -> RuntimePriceRow {
        RuntimePriceRow {
            source_key: Some("polymarket_rtds_chainlink".to_string()),
            symbol: symbol.to_string(),
            event_ts: Some("2026-06-04T07:43:09Z".to_string()),
            observed_ts: Some("2026-06-04T07:43:10Z".to_string()),
            price: Some(price.to_string()),
        }
    }

    fn orderbook_with_threshold(asset: &str, threshold_price: &str) -> RuntimeOrderbookRow {
        let market_slug = format!("{}-updown-5m-1780558800", asset.to_ascii_lowercase());
        orderbook_with_window(
            asset,
            &market_slug,
            "2026-06-04T07:40:00Z",
            "2026-06-04T07:45:00Z",
            threshold_price,
        )
    }

    fn orderbook_with_window(
        asset: &str,
        market_slug: &str,
        start_ts: &str,
        expiry_ts: &str,
        threshold_price: &str,
    ) -> RuntimeOrderbookRow {
        let mut orderbook = orderbook_without_threshold(asset, market_slug, start_ts, expiry_ts);
        orderbook.threshold_price = Some(threshold_price.to_string());
        orderbook
    }

    fn orderbook_without_threshold(
        asset: &str,
        market_slug: &str,
        start_ts: &str,
        expiry_ts: &str,
    ) -> RuntimeOrderbookRow {
        RuntimeOrderbookRow {
            venue: Some("polymarket".to_string()),
            source_key: Some("polymarket_rust_sdk".to_string()),
            market_slug: Some(market_slug.to_string()),
            contract_id: format!("{market_slug}-UP"),
            token_id: Some(format!("{market_slug}-UP-token")),
            asset: Some(asset.to_string()),
            side: Some("UP".to_string()),
            event_ts: None,
            observed_ts: Some("2026-06-04T07:43:10Z".to_string()),
            start_ts: Some(start_ts.to_string()),
            expiry_ts: Some(expiry_ts.to_string()),
            threshold_price: None,
            threshold_event_ts: Some("2026-06-04T07:40:00Z".to_string()),
            threshold_observed_ts: Some("2026-06-04T07:40:00.005Z".to_string()),
            settlement_price: Some("64050".to_string()),
            settlement_event_ts: Some("2026-06-04T07:43:09Z".to_string()),
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

#[cfg(test)]
mod cross_asset_target_tests {
    use crate::{
        render::price_path::price_path_charts,
        state::AppState,
        status::{RuntimeBookLevel, RuntimeMonitor, RuntimeOrderbookRow},
    };

    #[test]
    fn price_path_charts_show_matching_expiry_targets_for_btc_and_eth() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-07T16:20:10Z".to_string(),
                price_rows: vec![],
                orderbooks: vec![
                    orderbook("BTC", "UP", "btc-updown-5m-1780849200", "62171.42"),
                    orderbook("ETH", "UP", "eth-updown-5m-1780849200", "1629.69"),
                ],
            }),
            ..Default::default()
        };
        app.sync_market_selection();

        let charts = price_path_charts(&app);

        assert_eq!(charts[0].asset, "BTC");
        assert_eq!(charts[0].target, "K 62,171.42");
        assert_eq!(charts[1].asset, "ETH");
        assert_eq!(charts[1].target, "K 1,629.69");
    }

    fn orderbook(
        asset: &str,
        side: &str,
        market_slug: &str,
        threshold_price: &str,
    ) -> RuntimeOrderbookRow {
        RuntimeOrderbookRow {
            venue: Some("polymarket".to_string()),
            source_key: Some("polymarket_clob".to_string()),
            market_slug: Some(market_slug.to_string()),
            contract_id: format!("{market_slug}-{side}"),
            token_id: Some(format!("{market_slug}-{side}-token")),
            asset: Some(asset.to_string()),
            side: Some(side.to_string()),
            event_ts: Some("2026-06-07T16:20:01Z".to_string()),
            observed_ts: Some("2026-06-07T16:20:01Z".to_string()),
            start_ts: Some("2026-06-07T16:20:00Z".to_string()),
            expiry_ts: Some("2026-06-07T16:25:00Z".to_string()),
            threshold_price: Some(threshold_price.to_string()),
            threshold_event_ts: Some("2026-06-07T16:20:00Z".to_string()),
            threshold_observed_ts: Some("2026-06-07T16:20:01Z".to_string()),
            settlement_price: None,
            settlement_event_ts: None,
            best_bid: Some("0.49".to_string()),
            best_ask: Some("0.51".to_string()),
            spread: Some("0.02".to_string()),
            bid_size_top: Some("10".to_string()),
            ask_size_top: Some("11".to_string()),
            bids: Vec::<RuntimeBookLevel>::new(),
            asks: Vec::<RuntimeBookLevel>::new(),
        }
    }
}
