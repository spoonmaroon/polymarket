use std::collections::{HashMap, HashSet};

use chrono::{DateTime, Utc};

use crate::outcome_view::{
    OutcomeExpansion, OutcomeToggleTarget, default_outcome_section_key, latest_outcome_day_key,
    outcome_display_items, outcome_toggle_target_at,
};
use crate::status::{
    RuntimeDisplayLag, RuntimeGates, RuntimeMonitor, RuntimeOrderbookRow, RuntimeOutcomeRow,
    RuntimeOutcomes, RuntimePriceRow, RuntimeProbabilities, RuntimeStatus, RuntimeVolatility,
};

const EXPIRED_MARKET_HANDOFF_SECONDS: i64 = 60;
const PENDING_OUTCOME_FRESHNESS_SECONDS: i64 = 20;
const RESOLVED_OUTCOME_RETENTION_SECONDS: i64 = 30;
const MAX_PRICE_HISTORY_POINTS: usize = 240;
const MAX_VISIBLE_MARKET_GROUPS_PER_ASSET: usize = 3;

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
pub struct PriceHistoryPoint {
    pub symbol: String,
    pub observed_at: String,
    pub price: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AppState {
    pub active_tab: MainTab,
    pub logs: Vec<String>,
    pub runtime_status: Option<RuntimeStatus>,
    pub runtime_gates: Option<RuntimeGates>,
    pub runtime_monitor: Option<RuntimeMonitor>,
    pub runtime_volatility: Option<RuntimeVolatility>,
    pub runtime_probabilities: Option<RuntimeProbabilities>,
    pub runtime_outcomes: Option<RuntimeOutcomes>,
    pub resolved_outcome_seen_at: HashMap<String, String>,
    pub runtime_display_lag: Option<RuntimeDisplayLag>,
    pub runtime_error: Option<String>,
    pub selected_market_key: Option<String>,
    pub selected_outcome_index: Option<usize>,
    pub selected_outcome_anchors: Vec<OutcomeToggleTarget>,
    pub outcome_expansion: OutcomeExpansion,
    pub display_now: Option<String>,
    pub price_history: Vec<PriceHistoryPoint>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            active_tab: MainTab::Live,
            logs: Vec::new(),
            runtime_status: None,
            runtime_gates: None,
            runtime_monitor: None,
            runtime_volatility: None,
            runtime_probabilities: None,
            runtime_outcomes: None,
            resolved_outcome_seen_at: HashMap::new(),
            runtime_display_lag: None,
            runtime_error: None,
            selected_market_key: None,
            selected_outcome_index: None,
            selected_outcome_anchors: Vec::new(),
            outcome_expansion: OutcomeExpansion::default(),
            display_now: None,
            price_history: Vec::new(),
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
        if let Some(index) = self.selected_market_index() {
            let groups = self.visible_market_groups();
            if let Some(group) = groups.get(index)
                && self.selected_market_group_is_current(group)
            {
                return;
            }
        }

        let Some(index) = self.default_market_index() else {
            self.selected_market_key = None;
            return;
        };

        self.set_selected_market_index(index);
    }

    pub fn selected_market_index(&self) -> Option<usize> {
        let key = self.selected_market_key.as_ref()?;

        self.visible_market_groups()
            .iter()
            .position(|group| group.key == *key)
    }

    pub fn effective_market_index(&self) -> Option<usize> {
        self.selected_market_index()
            .or_else(|| self.default_market_index())
    }

    pub fn selected_market_group(&self) -> Option<crate::market_view::MarketGroup<'_>> {
        let index = self.effective_market_index()?;
        self.visible_market_groups().get(index).cloned()
    }

    fn monitor_with_expiration_handoff(&self, mut next: RuntimeMonitor) -> RuntimeMonitor {
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

        for group in crate::market_view::market_groups(&previous.orderbooks) {
            if !group_expired_at(&group, generated_at)
                || !self.should_retain_group_after_expiry(&group, &next.generated_at)
            {
                continue;
            }

            for orderbook in [group.up, group.down].into_iter().flatten() {
                let identity = orderbook_identity(orderbook);
                if seen.contains(&identity) {
                    continue;
                }
                seen.insert(identity);
                next.orderbooks.push(orderbook.clone());
            }
        }
        next
    }

    pub fn apply_runtime_monitor(&mut self, next: RuntimeMonitor) -> bool {
        let next = self.monitor_with_expiration_handoff(next);
        let history_changed = self.append_price_history(&next);
        let monitor_changed = self.runtime_monitor.as_ref() != Some(&next);
        if monitor_changed {
            self.runtime_monitor = Some(next);
        }

        history_changed || monitor_changed
    }

    pub fn apply_runtime_outcomes(&mut self, next: RuntimeOutcomes) -> bool {
        let mut changed = false;
        if let Some(generated_at) = next
            .generated_at
            .as_deref()
            .filter(|timestamp| parse_runtime_timestamp(timestamp).is_some())
        {
            for outcome in next.rows.iter().filter(|row| has_official_winner(row)) {
                for key in outcome_market_keys(outcome) {
                    if let std::collections::hash_map::Entry::Vacant(entry) =
                        self.resolved_outcome_seen_at.entry(key)
                    {
                        entry.insert(generated_at.to_string());
                        changed = true;
                    }
                }
            }
        }

        if self.runtime_outcomes.as_ref() != Some(&next) {
            self.runtime_outcomes = Some(next);
            self.sync_outcome_expansion_defaults();
            changed = true;
        }

        changed
    }

    pub fn visible_market_groups(&self) -> Vec<crate::market_view::MarketGroup<'_>> {
        let Some(monitor) = self.runtime_monitor.as_ref() else {
            return Vec::new();
        };

        let groups = crate::market_view::market_groups(&monitor.orderbooks)
            .into_iter()
            .filter(|group| self.should_retain_group_after_expiry(group, &monitor.generated_at))
            .collect();
        cap_market_groups_per_asset(groups, &monitor.generated_at)
    }

    pub fn should_retain_group_after_expiry(
        &self,
        group: &crate::market_view::MarketGroup<'_>,
        generated_at: &str,
    ) -> bool {
        let Some(expiry_ts) = group.expiry_ts else {
            return true;
        };
        let Some(generated_at) = parse_runtime_timestamp(generated_at) else {
            return true;
        };

        let elapsed_since_expiry = generated_at.signed_duration_since(expiry_ts).num_seconds();
        if elapsed_since_expiry < 0 {
            return true;
        }

        if let Some(outcome) = self.matching_outcome(group) {
            if !has_official_winner(outcome) {
                return (0..=EXPIRED_MARKET_HANDOFF_SECONDS).contains(&elapsed_since_expiry)
                    || self.pending_outcome_is_fresh_at(generated_at);
            }

            return (0..=EXPIRED_MARKET_HANDOFF_SECONDS).contains(&elapsed_since_expiry)
                || self
                    .resolved_outcome_seen_timestamp(group, outcome)
                    .is_some_and(|seen_at| {
                        let elapsed_since_seen =
                            generated_at.signed_duration_since(seen_at).num_seconds();
                        elapsed_since_seen <= RESOLVED_OUTCOME_RETENTION_SECONDS
                    });
        }

        (0..=EXPIRED_MARKET_HANDOFF_SECONDS).contains(&elapsed_since_expiry)
    }

    pub fn price_history_for(&self, symbol: &str) -> Vec<&PriceHistoryPoint> {
        self.price_history
            .iter()
            .filter(|point| point.symbol == symbol)
            .collect()
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
            self.selected_outcome_anchors.clear();
            return;
        };

        let index = self
            .selected_outcome_index
            .unwrap_or_default()
            .min(count - 1);
        if !self.selected_outcome_anchors.is_empty()
            && self.outcome_anchors_for_index(index) != self.selected_outcome_anchors
            && let Some(anchor_index) =
                self.visible_outcome_anchor_index(&self.selected_outcome_anchors)
        {
            self.set_selected_outcome_index(anchor_index);
            return;
        }
        self.set_selected_outcome_index(index);
    }

    pub fn sync_outcome_expansion_defaults(&mut self) {
        let Some(outcomes) = self.runtime_outcomes.as_ref() else {
            return;
        };
        let Some(day_key) = latest_outcome_day_key(outcomes) else {
            return;
        };

        if self
            .outcome_expansion
            .initialized_days
            .insert(day_key.clone())
        {
            self.outcome_expansion.expanded_days.insert(day_key.clone());
        }
        if self.outcome_expansion.expanded_days.contains(&day_key) {
            self.open_default_outcome_section_once(&day_key);
        }
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
        self.set_selected_outcome_index((current + 1) % count);
    }

    pub fn select_previous_outcome(&mut self) {
        let Some(count) = self.outcome_count().filter(|count| *count > 0) else {
            self.selected_outcome_index = None;
            return;
        };
        let current = self.effective_outcome_index().unwrap_or_default();
        self.set_selected_outcome_index((current + count - 1) % count);
    }

    pub fn toggle_selected_outcome(&mut self) -> bool {
        let Some(index) = self.effective_outcome_index() else {
            return false;
        };
        let Some(target) = outcome_toggle_target_at(
            self.runtime_outcomes.as_ref(),
            &self.outcome_expansion,
            index,
        ) else {
            return false;
        };
        match &target {
            OutcomeToggleTarget::Day(day_key) => {
                if self.outcome_expansion.expanded_days.insert(day_key.clone()) {
                    self.outcome_expansion
                        .initialized_days
                        .insert(day_key.clone());
                    self.open_default_outcome_section_once(day_key);
                } else {
                    self.outcome_expansion.expanded_days.remove(day_key);
                }
            }
            OutcomeToggleTarget::Section(section_key) => {
                if !self
                    .outcome_expansion
                    .expanded_sections
                    .insert(section_key.clone())
                {
                    self.outcome_expansion.expanded_sections.remove(section_key);
                }
                self.outcome_expansion
                    .initialized_sections
                    .insert(section_key.clone());
            }
        }
        self.sync_outcome_selection();
        self.select_outcome_toggle_target(&target);
        true
    }

    #[cfg(test)]
    pub fn selected_outcome_display_row_is_visible(&self) -> bool {
        self.effective_outcome_index()
            .is_some_and(|index| index < self.outcome_count().unwrap_or_default())
    }

    pub fn update_display_now(&mut self, now: DateTime<Utc>) -> bool {
        let Some(floored) = DateTime::<Utc>::from_timestamp(now.timestamp(), 0) else {
            return false;
        };
        let next = floored.to_rfc3339();
        if self.display_now.as_ref() == Some(&next) {
            return false;
        }
        self.display_now = Some(next);
        true
    }

    pub fn display_timestamp<'a>(&'a self, fallback: &'a str) -> &'a str {
        self.display_now.as_deref().unwrap_or(fallback)
    }

    fn orderbook_count(&self) -> Option<usize> {
        self.runtime_monitor
            .as_ref()
            .map(|_| self.visible_market_groups().len())
    }

    fn outcome_count(&self) -> Option<usize> {
        self.runtime_outcomes.as_ref().map(|_| {
            outcome_display_items(self.runtime_outcomes.as_ref(), &self.outcome_expansion).len()
        })
    }

    fn set_selected_outcome_index(&mut self, index: usize) {
        self.selected_outcome_index = Some(index);
        self.selected_outcome_anchors = self.outcome_anchors_for_index(index);
    }

    fn open_default_outcome_section_once(&mut self, day_key: &str) {
        let Some(section_key) =
            default_outcome_section_key(self.runtime_outcomes.as_ref(), day_key)
        else {
            return;
        };
        if self
            .outcome_expansion
            .initialized_sections
            .insert(section_key.clone())
        {
            self.outcome_expansion.expanded_sections.insert(section_key);
        }
    }

    fn select_outcome_toggle_target(&mut self, target: &OutcomeToggleTarget) {
        let items = outcome_display_items(self.runtime_outcomes.as_ref(), &self.outcome_expansion);
        let index = items.iter().position(|item| match (target, item) {
            (
                OutcomeToggleTarget::Day(target_key),
                crate::outcome_view::OutcomeDisplayItem::Day { key, .. },
            ) => key == target_key,
            (
                OutcomeToggleTarget::Section(target_key),
                crate::outcome_view::OutcomeDisplayItem::Section { key, .. },
            ) => key == target_key,
            _ => false,
        });
        if let Some(index) = index {
            self.set_selected_outcome_index(index);
        }
    }

    fn visible_outcome_anchor_index(&self, anchors: &[OutcomeToggleTarget]) -> Option<usize> {
        let items = outcome_display_items(self.runtime_outcomes.as_ref(), &self.outcome_expansion);
        anchors.iter().find_map(|anchor| {
            items.iter().position(|item| match (anchor, item) {
                (
                    OutcomeToggleTarget::Day(target_key),
                    crate::outcome_view::OutcomeDisplayItem::Day { key, .. },
                ) => key == target_key,
                (
                    OutcomeToggleTarget::Section(target_key),
                    crate::outcome_view::OutcomeDisplayItem::Section { key, .. },
                ) => key == target_key,
                _ => false,
            })
        })
    }

    fn outcome_anchors_for_index(&self, index: usize) -> Vec<OutcomeToggleTarget> {
        let mut day_anchor = None;
        let mut section_anchor = None;
        for (item_index, item) in
            outcome_display_items(self.runtime_outcomes.as_ref(), &self.outcome_expansion)
                .into_iter()
                .enumerate()
        {
            match item {
                crate::outcome_view::OutcomeDisplayItem::Day { key, .. } => {
                    day_anchor = Some(OutcomeToggleTarget::Day(key));
                    section_anchor = None;
                }
                crate::outcome_view::OutcomeDisplayItem::Section { key, day_key, .. } => {
                    day_anchor = Some(OutcomeToggleTarget::Day(day_key));
                    section_anchor = Some(OutcomeToggleTarget::Section(key));
                }
                crate::outcome_view::OutcomeDisplayItem::Outcome { .. } => {}
            }
            if item_index == index {
                let mut anchors = Vec::new();
                if let Some(anchor) = section_anchor {
                    anchors.push(anchor);
                }
                if let Some(anchor) = day_anchor {
                    anchors.push(anchor);
                }
                return anchors;
            }
        }
        Vec::new()
    }

    fn default_market_index(&self) -> Option<usize> {
        let groups = self.visible_market_groups();
        if groups.is_empty() {
            return None;
        }

        if let Some(index) = groups
            .iter()
            .position(|group| is_btc_group(group) && self.selected_market_group_is_current(group))
        {
            return Some(index);
        }

        if let Some(index) = groups
            .iter()
            .enumerate()
            .filter(|(_, group)| self.selected_market_group_is_current(group))
            .filter_map(|(index, group)| {
                group_observed_ts(group).map(|timestamp| (index, timestamp))
            })
            .max_by(|(_, left), (_, right)| left.cmp(right))
            .map(|(index, _)| index)
            .or_else(|| {
                groups
                    .iter()
                    .position(|group| self.selected_market_group_is_current(group))
            })
        {
            return Some(index);
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
            .visible_market_groups()
            .get(index)
            .cloned()
            .map(|group| group.key);
    }

    fn selected_market_group_is_current(
        &self,
        group: &crate::market_view::MarketGroup<'_>,
    ) -> bool {
        self.runtime_monitor
            .as_ref()
            .and_then(|monitor| parse_runtime_timestamp(&monitor.generated_at))
            .is_none_or(|generated_at| !group_expired_at(group, generated_at))
    }

    fn append_price_history(&mut self, monitor: &RuntimeMonitor) -> bool {
        let mut changed = false;
        for row in &monitor.price_rows {
            if let Some(point) = price_history_point(row, &monitor.generated_at) {
                let last_price = self
                    .price_history
                    .iter()
                    .rev()
                    .find(|existing| existing.symbol == point.symbol)
                    .map(|existing| existing.price);
                if last_price != Some(point.price) {
                    self.price_history.push(point);
                    changed = true;
                }
            }
        }

        let overflow = self
            .price_history
            .len()
            .saturating_sub(MAX_PRICE_HISTORY_POINTS);
        if overflow > 0 {
            self.price_history.drain(0..overflow);
        }

        changed
    }

    fn matching_outcome(
        &self,
        group: &crate::market_view::MarketGroup<'_>,
    ) -> Option<&RuntimeOutcomeRow> {
        self.runtime_outcomes
            .as_ref()?
            .rows
            .iter()
            .find(|outcome| outcome_matches_group(outcome, group))
    }

    fn resolved_outcome_seen_timestamp(
        &self,
        group: &crate::market_view::MarketGroup<'_>,
        outcome: &RuntimeOutcomeRow,
    ) -> Option<DateTime<Utc>> {
        group_market_keys(group)
            .into_iter()
            .chain(outcome_market_keys(outcome))
            .find_map(|key| {
                self.resolved_outcome_seen_at
                    .get(&key)
                    .and_then(|timestamp| parse_runtime_timestamp(timestamp))
            })
            .or_else(|| {
                self.runtime_outcomes
                    .as_ref()
                    .and_then(|outcomes| outcomes.generated_at.as_deref())
                    .and_then(parse_runtime_timestamp)
            })
    }

    fn pending_outcome_is_fresh_at(&self, generated_at: DateTime<Utc>) -> bool {
        self.runtime_outcomes
            .as_ref()
            .and_then(|outcomes| outcomes.generated_at.as_deref())
            .and_then(parse_runtime_timestamp)
            .is_some_and(|outcomes_generated_at| {
                let age_seconds = generated_at
                    .signed_duration_since(outcomes_generated_at)
                    .num_seconds();
                (-PENDING_OUTCOME_FRESHNESS_SECONDS..=PENDING_OUTCOME_FRESHNESS_SECONDS)
                    .contains(&age_seconds)
            })
    }
}

fn price_history_point(row: &RuntimePriceRow, generated_at: &str) -> Option<PriceHistoryPoint> {
    let price = row.price.as_deref()?.trim().parse::<f64>().ok()?;
    if !price.is_finite() {
        return None;
    }

    let observed_at = row
        .observed_ts
        .as_deref()
        .filter(|timestamp| !timestamp.trim().is_empty())
        .unwrap_or(generated_at)
        .to_string();

    Some(PriceHistoryPoint {
        symbol: row.symbol.clone(),
        observed_at,
        price,
    })
}

fn group_expired_at(
    group: &crate::market_view::MarketGroup<'_>,
    generated_at: DateTime<Utc>,
) -> bool {
    let Some(expiry_ts) = group.expiry_ts else {
        return false;
    };
    generated_at >= expiry_ts
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

fn cap_market_groups_per_asset<'a>(
    groups: Vec<crate::market_view::MarketGroup<'a>>,
    generated_at: &str,
) -> Vec<crate::market_view::MarketGroup<'a>> {
    let Some(generated_at) = parse_runtime_timestamp(generated_at) else {
        return cap_market_groups_by_asset_order(groups);
    };

    let mut assets: Vec<String> = Vec::new();
    for group in &groups {
        if !assets
            .iter()
            .any(|asset| asset.eq_ignore_ascii_case(&group.asset))
        {
            assets.push(group.asset.clone());
        }
    }

    let mut selected_keys: HashSet<String> = HashSet::new();
    for asset in assets {
        let asset_groups = groups
            .iter()
            .filter(|group| group.asset.eq_ignore_ascii_case(&asset))
            .collect::<Vec<_>>();
        let mut selected_for_asset: Vec<String> = asset_groups
            .iter()
            .copied()
            .filter(|group| !group_expired_at(group, generated_at))
            .take(MAX_VISIBLE_MARKET_GROUPS_PER_ASSET)
            .map(|group| group.key.clone())
            .collect();

        if selected_for_asset.len() < MAX_VISIBLE_MARKET_GROUPS_PER_ASSET {
            let mut expired_groups = asset_groups
                .iter()
                .copied()
                .filter(|group| group_expired_at(group, generated_at))
                .collect::<Vec<_>>();
            expired_groups.sort_by(|left, right| {
                market_group_expiry_timestamp(right).cmp(&market_group_expiry_timestamp(left))
            });
            for group in expired_groups {
                if selected_for_asset.len() >= MAX_VISIBLE_MARKET_GROUPS_PER_ASSET {
                    break;
                }
                selected_for_asset.push(group.key.clone());
            }
        }

        selected_keys.extend(selected_for_asset);
    }

    groups
        .into_iter()
        .filter(|group| selected_keys.contains(&group.key))
        .collect()
}

fn cap_market_groups_by_asset_order<'a>(
    groups: Vec<crate::market_view::MarketGroup<'a>>,
) -> Vec<crate::market_view::MarketGroup<'a>> {
    let mut counts: HashMap<String, usize> = HashMap::new();
    groups
        .into_iter()
        .filter(|group| {
            let count = counts.entry(group.asset.to_ascii_uppercase()).or_default();
            if *count >= MAX_VISIBLE_MARKET_GROUPS_PER_ASSET {
                return false;
            }
            *count += 1;
            true
        })
        .collect()
}

fn market_group_expiry_timestamp(group: &crate::market_view::MarketGroup<'_>) -> i64 {
    group
        .expiry_ts
        .map_or(i64::MIN, |timestamp| timestamp.timestamp())
}

fn outcome_matches_group(
    outcome: &RuntimeOutcomeRow,
    group: &crate::market_view::MarketGroup<'_>,
) -> bool {
    let group_keys = group_market_keys(group);
    outcome_market_keys(outcome)
        .iter()
        .any(|key| group_keys.contains(key))
}

fn group_market_keys(group: &crate::market_view::MarketGroup<'_>) -> Vec<String> {
    let mut keys = Vec::new();
    if let Some(slug) = group
        .up
        .or(group.down)
        .and_then(|row| row.market_slug.as_deref())
        .and_then(slug_market_key)
    {
        push_unique_key(&mut keys, slug);
    }
    if let Some(key) = asset_expiry_market_key(Some(group.asset.as_str()), group.expiry_ts) {
        push_unique_key(&mut keys, key);
    }
    if keys.is_empty() {
        push_unique_key(&mut keys, group.key.to_ascii_lowercase());
    }
    keys
}

fn outcome_market_keys(outcome: &RuntimeOutcomeRow) -> Vec<String> {
    let mut keys = Vec::new();
    if let Some(key) = outcome.market_slug.as_deref().and_then(slug_market_key) {
        push_unique_key(&mut keys, key);
    }
    if let Some(key) = slug_market_key(&outcome.market_id) {
        push_unique_key(&mut keys, key);
    }
    let expiry_ts = outcome
        .expiry_ts
        .as_deref()
        .and_then(parse_runtime_timestamp);
    if let Some(key) = asset_expiry_market_key(outcome.asset.as_deref(), expiry_ts) {
        push_unique_key(&mut keys, key);
    }
    keys
}

fn slug_market_key(slug: &str) -> Option<String> {
    let slug = slug.trim();
    if slug.is_empty() {
        None
    } else {
        Some(format!("slug={}", slug.to_ascii_lowercase()))
    }
}

fn asset_expiry_market_key(
    asset: Option<&str>,
    expiry_ts: Option<DateTime<Utc>>,
) -> Option<String> {
    let asset = asset?.trim();
    if asset.is_empty() {
        return None;
    }
    Some(format!(
        "asset={}|expiry={}",
        asset.to_ascii_lowercase(),
        expiry_ts?.timestamp()
    ))
}

fn push_unique_key(keys: &mut Vec<String>, key: String) {
    if !keys.contains(&key) {
        keys.push(key);
    }
}

fn has_official_winner(outcome: &RuntimeOutcomeRow) -> bool {
    outcome
        .official_winner
        .as_deref()
        .is_some_and(|winner| !winner.trim().is_empty())
}

fn freshest_group_index(groups: &[crate::market_view::MarketGroup<'_>]) -> usize {
    groups
        .iter()
        .enumerate()
        .filter_map(|(index, group)| group_observed_ts(group).map(|timestamp| (index, timestamp)))
        .max_by(|(_, left), (_, right)| left.cmp(right))
        .map(|(index, _)| index)
        .unwrap_or(0)
}

fn group_observed_ts<'a>(group: &crate::market_view::MarketGroup<'a>) -> Option<&'a str> {
    group
        .up
        .or(group.down)
        .and_then(|orderbook| orderbook.observed_ts.as_deref())
        .filter(|timestamp| !timestamp.is_empty())
}

fn is_btc_group(group: &crate::market_view::MarketGroup<'_>) -> bool {
    group.asset.eq_ignore_ascii_case("BTC")
        || group.market_slug.to_ascii_lowercase().starts_with("btc-")
}

#[cfg(test)]
mod tests {
    use super::{AppState, MainTab};
    use crate::{
        outcome_view::{default_outcome_section_key, outcome_section_key},
        status::{
            RuntimeMonitor, RuntimeOrderbookRow, RuntimeOutcomeRow, RuntimeOutcomes,
            RuntimePriceRow,
        },
    };

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
    fn market_selection_auto_jumps_from_expired_handoff_to_current_window() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:25:05Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    orderbook_with_expiry(
                        "BTC",
                        "UP",
                        "btc-updown-5m-1780521900",
                        "2026-06-03T21:25:00Z",
                    ),
                    orderbook_with_expiry(
                        "BTC",
                        "DOWN",
                        "btc-updown-5m-1780521900",
                        "2026-06-03T21:25:00Z",
                    ),
                    orderbook_with_expiry(
                        "BTC",
                        "UP",
                        "btc-updown-5m-1780522200",
                        "2026-06-03T21:30:00Z",
                    ),
                    orderbook_with_expiry(
                        "BTC",
                        "DOWN",
                        "btc-updown-5m-1780522200",
                        "2026-06-03T21:30:00Z",
                    ),
                ],
            }),
            ..Default::default()
        };

        app.sync_market_selection();

        assert_eq!(
            app.selected_market_group().unwrap().market_slug,
            "btc-updown-5m-1780522200"
        );
    }

    #[test]
    fn outcome_selection_moves_with_up_down_and_wraps() {
        let mut app = AppState {
            runtime_outcomes: Some(outcomes(vec!["BTC 5m 16:25", "ETH 5m 16:25"])),
            ..Default::default()
        };

        app.sync_outcome_expansion_defaults();
        app.sync_outcome_selection();
        assert_eq!(app.selected_outcome_index, Some(0));

        app.select_next_outcome();
        assert_eq!(app.selected_outcome_index, Some(1));

        app.select_next_outcome();
        assert_eq!(app.selected_outcome_index, Some(2));

        app.select_previous_outcome();
        assert_eq!(app.selected_outcome_index, Some(1));
    }

    #[test]
    fn outcome_selection_clamps_when_outcome_rows_shrink() {
        let mut app = AppState {
            runtime_outcomes: Some(outcomes(vec!["BTC 5m 16:25", "ETH 5m 16:25"])),
            ..Default::default()
        };
        app.sync_outcome_expansion_defaults();
        app.sync_outcome_selection();
        app.select_next_outcome();
        app.select_next_outcome();

        app.runtime_outcomes = Some(outcomes(vec!["BTC 5m 16:25"]));
        app.sync_outcome_expansion_defaults();
        app.sync_outcome_selection();

        assert_eq!(app.selected_outcome_index, Some(2));
        assert!(app.selected_outcome_display_row_is_visible());
    }

    #[test]
    fn outcome_expansion_defaults_expand_latest_day_once() {
        let mut app = AppState {
            runtime_outcomes: Some(outcomes(vec!["BTC 5m 16:25", "ETH 5m 16:25"])),
            ..Default::default()
        };
        let default_section_key =
            default_outcome_section_key(app.runtime_outcomes.as_ref(), "2026-06-03").unwrap();

        app.sync_outcome_expansion_defaults();

        assert!(app.outcome_expansion.expanded_days.contains("2026-06-03"));
        assert!(
            app.outcome_expansion
                .expanded_sections
                .contains(&default_section_key)
        );

        app.outcome_expansion.expanded_days.remove("2026-06-03");
        app.outcome_expansion
            .expanded_sections
            .remove(&default_section_key);
        app.sync_outcome_expansion_defaults();

        assert!(!app.outcome_expansion.expanded_days.contains("2026-06-03"));
        assert!(
            !app.outcome_expansion
                .expanded_sections
                .contains(&default_section_key)
        );
    }

    #[test]
    fn collapse_selected_day_keeps_selection_on_visible_header() {
        let mut app = AppState {
            runtime_outcomes: Some(outcomes(vec!["BTC 5m 16:25", "ETH 5m 16:25"])),
            ..Default::default()
        };
        app.sync_outcome_expansion_defaults();
        app.selected_outcome_index = Some(2);

        app.outcome_expansion.expanded_days.remove("2026-06-03");
        app.sync_outcome_selection();

        assert_eq!(app.selected_outcome_index, Some(0));
        assert!(app.selected_outcome_display_row_is_visible());
    }

    #[test]
    fn collapsing_parent_of_selected_child_keeps_selection_on_parent_header() {
        let mut app = AppState {
            runtime_outcomes: Some(outcomes_with_expiries(vec![
                ("BTC latest", "2026-06-04T21:25:00Z"),
                ("ETH older", "2026-06-03T21:25:00Z"),
            ])),
            ..Default::default()
        };
        app.sync_outcome_expansion_defaults();
        app.outcome_expansion
            .initialized_days
            .insert("2026-06-03".to_string());
        app.outcome_expansion
            .expanded_days
            .insert("2026-06-03".to_string());
        app.outcome_expansion
            .initialized_sections
            .insert(outcome_section_key("2026-06-03", "afternoon"));
        app.outcome_expansion
            .expanded_sections
            .insert(outcome_section_key("2026-06-03", "afternoon"));
        app.sync_outcome_selection();
        app.select_next_outcome();
        app.select_next_outcome();

        app.outcome_expansion.expanded_days.remove("2026-06-04");
        app.sync_outcome_selection();

        assert_eq!(app.selected_outcome_index, Some(0));
        assert!(app.selected_outcome_display_row_is_visible());
    }

    #[test]
    fn apply_runtime_monitor_appends_changed_price_history() {
        let mut app = AppState::default();
        let first = RuntimeMonitor {
            generated_at: "2026-06-04T07:43:10Z".to_string(),
            price_rows: vec![price_row("BTC/USD", "64050")],
            orderbooks: Vec::new(),
        };
        let second = RuntimeMonitor {
            generated_at: "2026-06-04T07:43:11Z".to_string(),
            price_rows: vec![price_row("BTC/USD", "64051")],
            orderbooks: Vec::new(),
        };

        app.apply_runtime_monitor(first);
        app.apply_runtime_monitor(second);

        assert_eq!(app.price_history_for("BTC/USD").len(), 2);
        assert_eq!(
            app.runtime_monitor.as_ref().unwrap().generated_at,
            "2026-06-04T07:43:11Z"
        );
    }

    #[test]
    fn apply_runtime_monitor_skips_unchanged_prices_and_caps_history() {
        let mut app = AppState::default();

        for index in 0..239 {
            app.apply_runtime_monitor(RuntimeMonitor {
                generated_at: format!("2026-06-04T07:{:02}:00Z", index % 60),
                price_rows: vec![
                    price_row("BTC/USD", &format!("{}", 64000 + index)),
                    price_row("ETH/USD", "1800"),
                ],
                orderbooks: Vec::new(),
            });
        }

        assert_eq!(app.price_history.len(), 240);
        assert_eq!(app.price_history_for("BTC/USD").len(), 239);
        assert_eq!(app.price_history_for("ETH/USD").len(), 1);

        for index in 239..245 {
            app.apply_runtime_monitor(RuntimeMonitor {
                generated_at: format!("2026-06-04T08:{:02}:00Z", index % 60),
                price_rows: vec![price_row("BTC/USD", &format!("{}", 64000 + index))],
                orderbooks: Vec::new(),
            });
        }

        assert_eq!(app.price_history.len(), 240);
    }

    #[test]
    fn expired_market_with_fresh_pending_outcome_stays_beyond_handoff_window() {
        let mut app = app_with_expired_market("btc-updown-5m-1780521900");

        app.apply_runtime_outcomes(RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-03T21:26:50Z".to_string()),
            rows: vec![pending_outcome("BTC", "btc-updown-5m-1780521900")],
        });

        let retained = {
            let groups = crate::market_view::market_groups(
                &app.runtime_monitor.as_ref().unwrap().orderbooks,
            );
            app.should_retain_group_after_expiry(&groups[0], "2026-06-03T21:27:00Z")
        };
        assert!(retained);

        app.runtime_monitor.as_mut().unwrap().generated_at = "2026-06-03T21:27:00Z".to_string();
        assert_eq!(app.visible_market_groups().len(), 1);
    }

    #[test]
    fn expired_market_with_stale_pending_outcome_drops_after_handoff_window() {
        let mut app = app_with_expired_market("btc-updown-5m-1780521900");

        app.apply_runtime_outcomes(RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-03T21:26:10Z".to_string()),
            rows: vec![pending_outcome("BTC", "btc-updown-5m-1780521900")],
        });

        let retained = {
            let groups = crate::market_view::market_groups(
                &app.runtime_monitor.as_ref().unwrap().orderbooks,
            );
            app.should_retain_group_after_expiry(&groups[0], "2026-06-03T21:27:00Z")
        };
        assert!(!retained);

        app.runtime_monitor.as_mut().unwrap().generated_at = "2026-06-03T21:27:00Z".to_string();
        assert!(app.visible_market_groups().is_empty());
    }

    #[test]
    fn expired_market_stays_until_resolved_outcome_visible_for_30_seconds() {
        let mut app = app_with_expired_market("btc-updown-5m-1780521900");

        app.apply_runtime_outcomes(RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-03T21:26:10Z".to_string()),
            rows: vec![pending_outcome("BTC", "btc-updown-5m-1780521900")],
        });
        let retained_while_pending = {
            let groups = crate::market_view::market_groups(
                &app.runtime_monitor.as_ref().unwrap().orderbooks,
            );
            app.should_retain_group_after_expiry(&groups[0], "2026-06-03T21:26:10Z")
        };
        assert!(retained_while_pending);

        app.apply_runtime_outcomes(RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-03T21:26:20Z".to_string()),
            rows: vec![resolved_outcome("BTC", "btc-updown-5m-1780521900", "UP")],
        });

        let retain_at = |app: &AppState, generated_at: &str| {
            let groups = crate::market_view::market_groups(
                &app.runtime_monitor.as_ref().unwrap().orderbooks,
            );
            app.should_retain_group_after_expiry(&groups[0], generated_at)
        };
        assert!(retain_at(&app, "2026-06-03T21:26:49Z"));
        assert!(retain_at(&app, "2026-06-03T21:26:50Z"));
        assert!(!retain_at(&app, "2026-06-03T21:26:51Z"));
    }

    #[test]
    fn early_resolved_market_still_stays_for_base_handoff_window() {
        let mut app = app_with_expired_market("btc-updown-5m-1780521900");

        app.apply_runtime_outcomes(RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-03T21:25:10Z".to_string()),
            rows: vec![resolved_outcome("BTC", "btc-updown-5m-1780521900", "UP")],
        });

        let retain_at = |app: &AppState, generated_at: &str| {
            let groups = crate::market_view::market_groups(
                &app.runtime_monitor.as_ref().unwrap().orderbooks,
            );
            app.should_retain_group_after_expiry(&groups[0], generated_at)
        };
        assert!(retain_at(&app, "2026-06-03T21:25:39Z"));
        assert!(retain_at(&app, "2026-06-03T21:25:59Z"));
        assert!(retain_at(&app, "2026-06-03T21:26:00Z"));
        assert!(!retain_at(&app, "2026-06-03T21:26:01Z"));
    }

    #[test]
    fn visible_market_groups_caps_each_asset_to_three_and_keeps_current_windows() {
        let mut app = AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:06:30Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    orderbook_with_expiry("BTC", "UP", "btc-updown-5m-old", "2026-06-03T21:00:00Z"),
                    orderbook_with_expiry(
                        "BTC",
                        "UP",
                        "btc-updown-5m-recent",
                        "2026-06-03T21:05:00Z",
                    ),
                    orderbook_with_expiry(
                        "BTC",
                        "UP",
                        "btc-updown-5m-current",
                        "2026-06-03T21:10:00Z",
                    ),
                    orderbook_with_expiry(
                        "BTC",
                        "UP",
                        "btc-updown-5m-next",
                        "2026-06-03T21:15:00Z",
                    ),
                    orderbook_with_expiry("ETH", "UP", "eth-updown-5m-old", "2026-06-03T21:00:00Z"),
                    orderbook_with_expiry(
                        "ETH",
                        "UP",
                        "eth-updown-5m-recent",
                        "2026-06-03T21:05:00Z",
                    ),
                    orderbook_with_expiry(
                        "ETH",
                        "UP",
                        "eth-updown-5m-current",
                        "2026-06-03T21:10:00Z",
                    ),
                    orderbook_with_expiry(
                        "ETH",
                        "UP",
                        "eth-updown-5m-next",
                        "2026-06-03T21:15:00Z",
                    ),
                ],
            }),
            ..Default::default()
        };
        app.apply_runtime_outcomes(RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-03T21:06:30Z".to_string()),
            rows: vec![
                pending_outcome("BTC", "btc-updown-5m-old"),
                pending_outcome("BTC", "btc-updown-5m-recent"),
                pending_outcome("ETH", "eth-updown-5m-old"),
                pending_outcome("ETH", "eth-updown-5m-recent"),
            ],
        });

        let visible_slugs = app
            .visible_market_groups()
            .into_iter()
            .map(|group| group.market_slug)
            .collect::<Vec<_>>();

        assert_eq!(
            visible_slugs,
            vec![
                "btc-updown-5m-recent",
                "btc-updown-5m-current",
                "btc-updown-5m-next",
                "eth-updown-5m-recent",
                "eth-updown-5m-current",
                "eth-updown-5m-next",
            ]
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

    fn app_with_expired_market(market_slug: &str) -> AppState {
        AppState {
            runtime_monitor: Some(RuntimeMonitor {
                generated_at: "2026-06-03T21:25:59Z".to_string(),
                price_rows: Vec::new(),
                orderbooks: vec![
                    orderbook("BTC", "UP", market_slug, "2026-06-03T21:24:59Z"),
                    orderbook("BTC", "DOWN", market_slug, "2026-06-03T21:24:59Z"),
                ],
            }),
            ..Default::default()
        }
    }

    fn pending_outcome(asset: &str, market_slug: &str) -> RuntimeOutcomeRow {
        RuntimeOutcomeRow {
            market: format!("{asset} 5m"),
            market_id: market_slug.to_string(),
            market_slug: Some(market_slug.to_string()),
            asset: Some(asset.to_string()),
            start_ts: Some("2026-06-03T21:20:00Z".to_string()),
            expiry_ts: Some("2026-06-03T21:25:00Z".to_string()),
            threshold_price: None,
            threshold_event_ts: None,
            threshold_observed_ts: None,
            computed_winner: None,
            official_winner: None,
            winning_token_id: None,
            official_resolution_status: "pending".to_string(),
            mismatch: None,
        }
    }

    fn resolved_outcome(asset: &str, market_slug: &str, winner: &str) -> RuntimeOutcomeRow {
        RuntimeOutcomeRow {
            official_winner: Some(winner.to_string()),
            winning_token_id: Some(format!("{market_slug}-{winner}-token")),
            official_resolution_status: "resolved".to_string(),
            ..pending_outcome(asset, market_slug)
        }
    }

    fn monitor(orderbooks: Vec<RuntimeOrderbookRow>) -> RuntimeMonitor {
        RuntimeMonitor {
            generated_at: "2026-06-03T20:44:00Z".to_string(),
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
            start_ts: None,
            expiry_ts: None,
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

    fn orderbook_with_expiry(
        asset: &str,
        side: &str,
        market_slug: &str,
        expiry_ts: &str,
    ) -> RuntimeOrderbookRow {
        let mut row = orderbook(asset, side, market_slug, "2026-06-03T21:06:29Z");
        row.expiry_ts = Some(expiry_ts.to_string());
        row
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
                    threshold_price: None,
                    threshold_event_ts: None,
                    threshold_observed_ts: None,
                    computed_winner: None,
                    official_winner: Some("UP".to_string()),
                    winning_token_id: Some(format!("token-{index}")),
                    official_resolution_status: "resolved".to_string(),
                    mismatch: None,
                })
                .collect(),
        }
    }

    fn outcomes_with_expiries(markets: Vec<(&str, &str)>) -> RuntimeOutcomes {
        RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-04T22:00:00Z".to_string()),
            rows: markets
                .into_iter()
                .enumerate()
                .map(|(index, (market, expiry_ts))| RuntimeOutcomeRow {
                    market: market.to_string(),
                    market_id: format!("market-{index}"),
                    market_slug: Some(format!("market-{index}")),
                    asset: Some("BTC".to_string()),
                    start_ts: None,
                    expiry_ts: Some(expiry_ts.to_string()),
                    threshold_price: None,
                    threshold_event_ts: None,
                    threshold_observed_ts: None,
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
