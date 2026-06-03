use crate::status::{RuntimeGates, RuntimeStatus};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MainTab {
    Live,
    Systems,
    Market,
    Logs,
}

impl MainTab {
    pub fn all() -> &'static [MainTab] {
        &[
            MainTab::Live,
            MainTab::Systems,
            MainTab::Market,
            MainTab::Logs,
        ]
    }

    pub fn label(&self) -> &'static str {
        match self {
            MainTab::Live => "Live",
            MainTab::Systems => "Systems",
            MainTab::Market => "Market",
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
    pub runtime_error: Option<String>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            active_tab: MainTab::Live,
            logs: Vec::new(),
            runtime_status: None,
            runtime_gates: None,
            runtime_error: None,
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
}

#[cfg(test)]
mod tests {
    use super::{AppState, MainTab};

    #[test]
    fn cockpit_defaults_to_live_read_only_tab() {
        let app = AppState::default();

        assert_eq!(app.active_tab, MainTab::Live);
    }

    #[test]
    fn cockpit_tabs_are_operator_surfaces() {
        let labels: Vec<&'static str> = MainTab::all().iter().map(MainTab::label).collect();

        assert_eq!(labels, vec!["Live", "Systems", "Market", "Logs"]);
    }
}
