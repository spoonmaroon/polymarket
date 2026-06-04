use std::collections::HashSet;

use chrono::{DateTime, Utc};

use crate::status::{
    RuntimeDisplayLag, RuntimeGates, RuntimeMonitor, RuntimeOrderbookRow, RuntimeOutcomes,
    RuntimeProbabilities, RuntimeStatus,
};

const EXPIRED_MARKET_HANDOFF_SECONDS: i64 = 60;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MainTab {
    Live,
    Systems,
    Market,
    Probability,
    Outcomes,
    Logs,
}

impl MainTab {
    pub fn all() -> &'static [MainTab] {
        &[
            MainTab::Live,
            MainTab::Systems,
            MainTab::Market,
            MainTab::Probability,
            MainTab::Outcomes,
            MainTab::Logs,
        ]
    }

    pub fn label(&self) -> &'static str {
        match self {
            MainTab::Live => "Live",
            MainTab::Systems => "Systems",
            MainTab::Market => "Market",
            MainTab::Probability => "Probability",
            MainTab::Outcomes => "Outcomes",
            MainTab::Logs => "Logs",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct AppState {
    pub active_tab: MainTab,
    pub logs: Vec<String>,
    pub runtime_status: Option<RuntimeStatus>,
    pub runtime_gates: Option<RuntimeGates>,
    pub runtime_monitor: Option<RuntimeMonitor>,
    pub runtime_probabilities: Option<RuntimeProbabilities>,
    pub runtime_outcomes: Option<RuntimeOutcomes>,
    pub runtime_display_lag: Option<RuntimeDisplayLag>,
    pub runtime_error: Option<String>,
    pub selected_market_key: Option<String>,
    pub selected_outcome_index: Option<usize>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            active_tab: MainTab::Live,
            logs: Vec::new(),
            runtime_status: None,
            runtime_gates: None,
            runtime_monitor: None,
            runtime_probabilities: None,
            runtime_outcomes: None,
            runtime_display_lag: None,
            runtime_error: None,
            selected_market_key: None,
            selected_outcome_index: None,
        }
    }
}

impl AppState {
    pub fn next_tab(&mut self) {
        let tabs = MainTab::all();
        let current = tabs
            .iter()
            .position(|tab| *tab == self.active_tab)
            .unwrap_or_default();
        self.active_tab = tabs[(current + 1) % tabs.len()];
    }

    pub fn previous_tab(&mut self) {
        let tabs = MainTab::all();
        let current = tabs
            .iter()
            .position(|tab| *tab == self.active_tab)
            .unwrap_or_default();
        self.active_tab = tabs[(current + tabs.len() - 1) % tabs.len()];
    }

    pub fn sync_market_selection(&mut self) {
        if self.selected_market_index().is_some() {
            return;
        }

        let Some(index) = self.default_market_index() else {
            self.selected_market_key = None;
            return;
        };

        self.set_selected_market_index(index);
    }

    pub fn selected_market_index(&self) -> Option<usize> {
        let key = self.selected_market_key.as_ref()?;
        let monitor = self.runtime_monitor.as_ref()?;

        crate::market_view::market_groups(&monitor.orderbooks)
            .iter()
            .position(|group| group.key == *key)
    }

    pub fn effective_market_index(&self) -> Option<usize> {
        self.selected_market_index()
            .or_else(|| self.default_market_index())
    }

    pub fn selected_market_group(&self) -> Option<crate::market_view::MarketGroup<'_>> {
        let index = self.effective_market_index()?;
        self.runtime_monitor.as_ref().and_then(|monitor| {
            crate::market_view::market_groups(&monitor.orderbooks)
                .get(index)
                .cloned()
        })
    }

    pub fn monitor_with_expiration_handoff(&self, mut next: RuntimeMonitor) -> RuntimeMonitor {
        let Some(previous) = self.runtime_monitor.as_ref() else {
            return next;
        };
        let Some(generated_at) = parse_runtime_timestamp(&next.generated_at) else {
            return next;
        };

        let mut seen = next
            .orderbooks
            .iter()
            .map(orderbook_identity)
            .collect::<HashSet<_>>();
        for orderbook in &previous.orderbooks {
            let identity = orderbook_identity(orderbook);
            if seen.contains(&identity) {
                continue;
            }
            if recently_expired_for_handoff(orderbook, generated_at) {
                seen.insert(identity);
                next.orderbooks.push(orderbook.clone());
            }
        }
        next
    }

    pub fn select_next_market(&mut self) {
        let Some(count) = self.orderbook_count().filter(|count| *count > 0) else {
            self.selected_market_key = None;
            return;
        };
        let current = self.effective_market_index().unwrap_or_default();
        self.set_selected_market_index((current + 1) % count);
    }

    pub fn select_previous_market(&mut self) {
        let Some(count) = self.orderbook_count().filter(|count| *count > 0) else {
            self.selected_market_key = None;
            return;
        };
        let current = self.effective_market_index().unwrap_or_default();
        self.set_selected_market_index((current + count - 1) % count);
    }

    pub fn sync_outcome_selection(&mut self) {
        let Some(count) = self.outcome_count().filter(|count| *count > 0) else {
            self.selected_outcome_index = None;
            return;
        };

        self.selected_outcome_index = Some(
            self.selected_outcome_index
                .unwrap_or_default()
                .min(count - 1),
        );
    }

    pub fn effective_outcome_index(&self) -> Option<usize> {
        let count = self.outcome_count().filter(|count| *count > 0)?;
        Some(
            self.selected_outcome_index
                .unwrap_or_default()
                .min(count - 1),
        )
    }

    pub fn select_next_outcome(&mut self) {
        let Some(count) = self.outcome_count().filter(|count| *count > 0) else {
            self.selected_outcome_index = None;
            return;
        };
        let current = self.effective_outcome_index().unwrap_or_default();
        self.selected_outcome_index = Some((current + 1) % count);
    }

    pub fn select_previous_outcome(&mut self) {
        let Some(count) = self.outcome_count().filter(|count| *count > 0) else {
            self.selected_outcome_index = None;
            return;
        };
        let current = self.effective_outcome_index().unwrap_or_default();
        self.selected_outcome_index = Some((current + count - 1) % count);
    }

    fn orderbook_count(&self) -> Option<usize> {
        self.runtime_monitor
            .as_ref()
            .map(|monitor| crate::market_view::market_groups(&monitor.orderbooks).len())
    }

    fn outcome_count(&self) -> Option<usize> {
        self.runtime_outcomes
            .as_ref()
            .map(|outcomes| outcomes.rows.len())
    }

    fn default_market_index(&self) -> Option<usize> {
        let monitor = self.runtime_monitor.as_ref()?;
        let groups = crate::market_view::market_groups(&monitor.orderbooks);
        if groups.is_empty() {
            return None;
        }

        Some(
            groups
                .iter()
                .position(is_btc_group)
                .unwrap_or_else(|| freshest_group_index(&groups)),
        )
    }

    fn set_selected_market_index(&mut self, index: usize) {
        self.selected_market_key = self
            .runtime_monitor
            .as_ref()
            .and_then(|monitor| {
                crate::market_view::market_groups(&monitor.orderbooks)
                    .get(index)
                    .cloned()
            })
            .map(|group| group.key);
    }
}

fn recently_expired_for_handoff(
    orderbook: &RuntimeOrderbookRow,
    generated_at: DateTime<Utc>,
) -> bool {
    let Some(expiry_ts) = crate::market_view::expiry_ts(orderbook) else {
        return false;
    };
    let elapsed = generated_at.signed_duration_since(expiry_ts).num_seconds();
    (0..=EXPIRED_MARKET_HANDOFF_SECONDS).contains(&elapsed)
}

fn orderbook_identity(orderbook: &RuntimeOrderbookRow) -> String {
    if let Some(token_id) = orderbook
        .token_id
        .as_deref()
        .map(str::trim)
        .filter(|token_id| !token_id.is_empty())
    {
        format!("token={token_id}")
    } else {
        format!("contract={}", orderbook.contract_id.trim())
    }
}

fn parse_runtime_timestamp(timestamp: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(timestamp)
        .ok()
        .map(|timestamp| timestamp.with_timezone(&Utc))
}

fn freshest_group_index(groups: &[crate::market_view::MarketGroup<'_>]) -> usize {
    groups
        .iter()
        .enumerate()
        .filter_map(|(index, group)| {
            group
                .up
                .or(group.down)
                .and_then(|orderbook| orderbook.observed_ts.as_deref())
                .filter(|timestamp| !timestamp.is_empty())
                .map(|timestamp| (index, timestamp))
        })
        .max_by(|(_, left), (_, right)| left.cmp(right))
        .map(|(index, _)| index)
        .unwrap_or(0)
}

fn is_btc_group(group: &crate::market_view::MarketGroup<'_>) -> bool {
    group.asset.eq_ignore_ascii_case("BTC")
        || group.market_slug.to_ascii_lowercase().starts_with("btc-")
}

#[cfg(test)]
mod tests {
    use super::{AppState, MainTab};
    use crate::status::{RuntimeMonitor, RuntimeOrderbookRow, RuntimeOutcomeRow, RuntimeOutcomes};

    #[test]
    fn cockpit_defaults_to_live_read_only_tab() {
        let app = AppState::default();

        assert_eq!(app.active_tab, MainTab::Live);
    }

    #[test]
    fn cockpit_tabs_are_operator_surfaces() {
        let labels: Vec<&'static str> = MainTab::all().iter().map(MainTab::label).collect();

        assert_eq!(
            labels,
            vec![
                "Live",
                "Systems",
                "Market",
                "Probability",
                "Outcomes",
                "Logs"
            ]
        );
    }

    #[test]
    fn market_selection_defaults_to_first_btc_orderbook() {
        let mut app = AppState {
            runtime_monitor: Some(monitor(vec![
                orderbook(
                    "ETH",
                    "UP",
                    "eth-updown-5m-1780519500",
                    "2026-06-03T21:05:58Z",
                ),
                orderbook(
                    "BTC",
                    "DOWN",
                    "btc-updown-5m-1780519800",
                    "2026-06-03T21:05:47Z",
                ),
                orderbook(
                    "BTC",
                    "UP",
                    "btc-updown-5m-1780519500",
                    "2026-06-03T21:05:58Z",
                ),
            ])),
            ..Default::default()
        };

        app.sync_market_selection();

        assert_eq!(app.selected_market_index(), Some(0));
    }

    #[test]
    fn market_selection_falls_back_to_freshest_orderbook_without_btc() {
        let mut app = AppState {
            runtime_monitor: Some(monitor(vec![
                orderbook(
                    "ETH",
                    "UP",
                    "eth-updown-5m-1780519500",
                    "2026-06-03T21:05:58Z",
                ),
                orderbook(
                    "SOL",
                    "DOWN",
                    "sol-updown-5m-1780519800",
                    "2026-06-03T21:06:10Z",
                ),
            ])),
            ..Default::default()
        };

        app.sync_market_selection();

        assert_eq!(app.selected_market_index(), Some(1));
    }

    #[test]
    fn market_selection_moves_with_up_down_and_wraps() {
        let mut app = AppState {
            runtime_monitor: Some(monitor(vec![
                orderbook(
                    "BTC",
                    "UP",
                    "btc-updown-5m-1780519500",
                    "2026-06-03T21:05:58Z",
                ),
                orderbook(
                    "BTC",
                    "DOWN",
                    "btc-updown-5m-1780519500",
                    "2026-06-03T21:05:58Z",
                ),
                orderbook(
                    "ETH",
                    "UP",
                    "eth-updown-5m-1780519500",
                    "2026-06-03T21:05:58Z",
                ),
            ])),
            ..Default::default()
        };

        app.sync_market_selection();
        app.select_next_market();
        assert_eq!(app.selected_market_index(), Some(1));

        app.select_previous_market();
        assert_eq!(app.selected_market_index(), Some(0));

        app.select_previous_market();
        assert_eq!(app.selected_market_index(), Some(1));
    }

    #[test]
    fn market_selection_moves_by_market_group_not_outcome_token() {
        let mut app = AppState {
            runtime_monitor: Some(monitor(vec![
                orderbook(
                    "BTC",
                    "UP",
                    "btc-updown-5m-1780521900",
                    "2026-06-03T21:22:15Z",
                ),
                orderbook(
                    "BTC",
                    "DOWN",
                    "btc-updown-5m-1780521900",
                    "2026-06-03T21:22:15Z",
                ),
                orderbook(
                    "ETH",
                    "UP",
                    "eth-updown-5m-1780521900",
                    "2026-06-03T21:22:15Z",
                ),
                orderbook(
                    "ETH",
                    "DOWN",
                    "eth-updown-5m-1780521900",
                    "2026-06-03T21:22:15Z",
                ),
            ])),
            ..Default::default()
        };

        app.sync_market_selection();
        assert_eq!(app.selected_market_index(), Some(0));
        app.select_next_market();
        assert_eq!(app.selected_market_index(), Some(1));
    }

    #[test]
    fn outcome_selection_moves_with_up_down_and_wraps() {
        let mut app = AppState {
            runtime_outcomes: Some(outcomes(vec!["BTC 5m 16:25", "ETH 5m 16:25"])),
            ..Default::default()
        };

        app.sync_outcome_selection();
        assert_eq!(app.selected_outcome_index, Some(0));

        app.select_next_outcome();
        assert_eq!(app.selected_outcome_index, Some(1));

        app.select_next_outcome();
        assert_eq!(app.selected_outcome_index, Some(0));

        app.select_previous_outcome();
        assert_eq!(app.selected_outcome_index, Some(1));
    }

    #[test]
    fn outcome_selection_clamps_when_outcome_rows_shrink() {
        let mut app = AppState {
            runtime_outcomes: Some(outcomes(vec!["BTC 5m 16:25", "ETH 5m 16:25"])),
            ..Default::default()
        };
        app.sync_outcome_selection();
        app.select_next_outcome();

        app.runtime_outcomes = Some(outcomes(vec!["BTC 5m 16:25"]));
        app.sync_outcome_selection();

        assert_eq!(app.selected_outcome_index, Some(0));
    }

    fn monitor(orderbooks: Vec<RuntimeOrderbookRow>) -> RuntimeMonitor {
        RuntimeMonitor {
            generated_at: "2026-06-03T21:06:00Z".to_string(),
            price_rows: Vec::new(),
            orderbooks,
        }
    }

    fn orderbook(
        asset: &str,
        side: &str,
        market_slug: &str,
        observed_ts: &str,
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
            observed_ts: Some(observed_ts.to_string()),
            best_bid: None,
            best_ask: None,
            spread: None,
            bid_size_top: None,
            ask_size_top: None,
            bids: Vec::new(),
            asks: Vec::new(),
        }
    }

    fn outcomes(markets: Vec<&str>) -> RuntimeOutcomes {
        RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-03T22:00:00Z".to_string()),
            rows: markets
                .into_iter()
                .enumerate()
                .map(|(index, market)| RuntimeOutcomeRow {
                    market: market.to_string(),
                    market_id: format!("market-{index}"),
                    market_slug: Some(format!("market-{index}")),
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
        }
    }
}
