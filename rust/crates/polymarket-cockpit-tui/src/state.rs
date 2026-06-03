use crate::status::{
    RuntimeGates, RuntimeMonitor, RuntimeOrderbookRow, RuntimeProbabilities, RuntimeStatus,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MainTab {
    Live,
    Systems,
    Market,
    Probability,
    Logs,
}

impl MainTab {
    pub fn all() -> &'static [MainTab] {
        &[
            MainTab::Live,
            MainTab::Systems,
            MainTab::Market,
            MainTab::Probability,
            MainTab::Logs,
        ]
    }

    pub fn label(&self) -> &'static str {
        match self {
            MainTab::Live => "Live",
            MainTab::Systems => "Systems",
            MainTab::Market => "Market",
            MainTab::Probability => "Probability",
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
    pub runtime_error: Option<String>,
    pub selected_market_key: Option<String>,
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
            runtime_error: None,
            selected_market_key: None,
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

        monitor
            .orderbooks
            .iter()
            .position(|orderbook| orderbook_key(orderbook) == *key)
    }

    pub fn effective_market_index(&self) -> Option<usize> {
        self.selected_market_index()
            .or_else(|| self.default_market_index())
    }

    pub fn selected_orderbook(&self) -> Option<&RuntimeOrderbookRow> {
        let index = self.effective_market_index()?;
        self.runtime_monitor
            .as_ref()
            .and_then(|monitor| monitor.orderbooks.get(index))
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

    fn orderbook_count(&self) -> Option<usize> {
        self.runtime_monitor
            .as_ref()
            .map(|monitor| monitor.orderbooks.len())
    }

    fn default_market_index(&self) -> Option<usize> {
        let monitor = self.runtime_monitor.as_ref()?;
        if monitor.orderbooks.is_empty() {
            return None;
        }

        Some(
            monitor
                .orderbooks
                .iter()
                .position(is_btc_orderbook)
                .unwrap_or_else(|| freshest_orderbook_index(monitor)),
        )
    }

    fn set_selected_market_index(&mut self, index: usize) {
        self.selected_market_key = self
            .runtime_monitor
            .as_ref()
            .and_then(|monitor| monitor.orderbooks.get(index))
            .map(orderbook_key);
    }
}

fn freshest_orderbook_index(monitor: &RuntimeMonitor) -> usize {
    monitor
        .orderbooks
        .iter()
        .enumerate()
        .filter_map(|(index, orderbook)| {
            orderbook
                .observed_ts
                .as_deref()
                .filter(|timestamp| !timestamp.is_empty())
                .map(|timestamp| (index, timestamp))
        })
        .max_by(|(_, left), (_, right)| left.cmp(right))
        .map(|(index, _)| index)
        .unwrap_or(0)
}

fn is_btc_orderbook(orderbook: &RuntimeOrderbookRow) -> bool {
    orderbook
        .asset
        .as_deref()
        .is_some_and(|asset| asset.eq_ignore_ascii_case("BTC"))
        || orderbook.market_slug.as_deref().is_some_and(|slug| {
            slug.to_ascii_lowercase().starts_with("btc-")
                || slug.to_ascii_lowercase().starts_with("btc_")
        })
        || orderbook.contract_id.to_ascii_lowercase().contains("btc")
}

fn orderbook_key(orderbook: &RuntimeOrderbookRow) -> String {
    format!(
        "token={}|contract={}|slug={}|asset={}|side={}",
        normalized_key_part(orderbook.token_id.as_deref()),
        normalized_key_part(Some(orderbook.contract_id.as_str())),
        normalized_key_part(orderbook.market_slug.as_deref()),
        normalized_key_part(orderbook.asset.as_deref()),
        normalized_key_part(orderbook.side.as_deref())
    )
}

fn normalized_key_part(value: Option<&str>) -> String {
    value.unwrap_or_default().trim().to_ascii_lowercase()
}

#[cfg(test)]
mod tests {
    use super::{AppState, MainTab};
    use crate::status::{RuntimeMonitor, RuntimeOrderbookRow};

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
            vec!["Live", "Systems", "Market", "Probability", "Logs"]
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

        assert_eq!(app.selected_market_index(), Some(1));
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
        assert_eq!(app.selected_market_index(), Some(2));
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
}
