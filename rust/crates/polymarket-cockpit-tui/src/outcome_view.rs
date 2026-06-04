use std::collections::{BTreeSet, HashMap};

use chrono::{DateTime, Local, Utc};

use crate::status::{RuntimeOutcomeRow, RuntimeOutcomes};

#[derive(Debug, Clone, PartialEq)]
pub enum OutcomeDisplayItem<'a> {
    Day {
        key: String,
        label: String,
        count: usize,
        expanded: bool,
    },
    Outcome {
        row: &'a RuntimeOutcomeRow,
    },
}

pub fn outcome_display_items<'a>(
    outcomes: Option<&'a RuntimeOutcomes>,
    expanded_days: &BTreeSet<String>,
) -> Vec<OutcomeDisplayItem<'a>> {
    let Some(outcomes) = outcomes else {
        return Vec::new();
    };
    let groups = grouped_outcomes(&outcomes.rows);
    let default_expanded = groups.first().map(|group| group.key.clone());
    let mut items = Vec::new();
    for group in groups {
        let expanded = default_expanded.as_deref() == Some(group.key.as_str())
            || expanded_days.contains(&group.key);
        items.push(OutcomeDisplayItem::Day {
            key: group.key,
            label: group.label,
            count: group.rows.len(),
            expanded,
        });
        if expanded {
            items.extend(
                group
                    .rows
                    .into_iter()
                    .map(|row| OutcomeDisplayItem::Outcome { row }),
            );
        }
    }
    items
}

pub fn outcome_day_key_at(
    outcomes: Option<&RuntimeOutcomes>,
    expanded_days: &BTreeSet<String>,
    index: usize,
) -> Option<String> {
    match outcome_display_items(outcomes, expanded_days).get(index)? {
        OutcomeDisplayItem::Day { key, .. } => Some(key.clone()),
        OutcomeDisplayItem::Outcome { .. } => None,
    }
}

struct OutcomeDayGroup<'a> {
    key: String,
    label: String,
    rows: Vec<&'a RuntimeOutcomeRow>,
}

fn grouped_outcomes(rows: &[RuntimeOutcomeRow]) -> Vec<OutcomeDayGroup<'_>> {
    let mut groups: Vec<OutcomeDayGroup<'_>> = Vec::new();
    let mut index_by_key: HashMap<String, usize> = HashMap::new();
    for row in rows {
        let key = outcome_day_key(row);
        let index = if let Some(index) = index_by_key.get(&key).copied() {
            index
        } else {
            let label = outcome_day_label(row).unwrap_or_else(|| key.clone());
            groups.push(OutcomeDayGroup {
                key: key.clone(),
                label,
                rows: Vec::new(),
            });
            let index = groups.len() - 1;
            index_by_key.insert(key, index);
            index
        };
        groups[index].rows.push(row);
    }
    groups
}

fn outcome_day_key(row: &RuntimeOutcomeRow) -> String {
    parse_local_expiry(row)
        .map(|timestamp| timestamp.format("%Y-%m-%d").to_string())
        .unwrap_or_else(|| "unknown".to_string())
}

fn outcome_day_label(row: &RuntimeOutcomeRow) -> Option<String> {
    Some(parse_local_expiry(row)?.format("%b %d %Y").to_string())
}

fn parse_local_expiry(row: &RuntimeOutcomeRow) -> Option<DateTime<Local>> {
    let parsed = DateTime::parse_from_rfc3339(row.expiry_ts.as_deref()?).ok()?;
    Some(parsed.with_timezone(&Utc).with_timezone(&Local))
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use crate::{
        outcome_view::{OutcomeDisplayItem, outcome_day_key_at, outcome_display_items},
        status::{RuntimeOutcomeRow, RuntimeOutcomes},
    };

    #[test]
    fn outcome_display_items_expand_latest_day_by_default() {
        let outcomes = RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-04T20:00:00Z".to_string()),
            rows: vec![
                outcome("BTC 5m", "2026-06-04T20:00:00Z"),
                outcome("ETH 5m", "2026-06-03T20:00:00Z"),
            ],
        };

        let items = outcome_display_items(Some(&outcomes), &BTreeSet::new());

        assert!(matches!(
            &items[0],
            OutcomeDisplayItem::Day {
                label,
                expanded: true,
                ..
            } if label.contains("Jun 04")
        ));
        assert!(matches!(&items[1], OutcomeDisplayItem::Outcome { row } if row.market == "BTC 5m"));
        assert!(matches!(
            &items[2],
            OutcomeDisplayItem::Day {
                label,
                expanded: false,
                ..
            } if label.contains("Jun 03")
        ));
        assert_eq!(items.len(), 3);
    }

    #[test]
    fn outcome_day_key_at_returns_only_selectable_day_headers() {
        let outcomes = RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-04T20:00:00Z".to_string()),
            rows: vec![outcome("BTC 5m", "2026-06-04T20:00:00Z")],
        };

        assert!(outcome_day_key_at(Some(&outcomes), &BTreeSet::new(), 0).is_some());
        assert_eq!(
            outcome_day_key_at(Some(&outcomes), &BTreeSet::new(), 1),
            None
        );
    }

    fn outcome(market: &str, expiry_ts: &str) -> RuntimeOutcomeRow {
        RuntimeOutcomeRow {
            market: market.to_string(),
            market_id: market.to_ascii_lowercase().replace(' ', "-"),
            market_slug: Some(market.to_ascii_lowercase().replace(' ', "-")),
            asset: market.split_whitespace().next().map(str::to_string),
            start_ts: None,
            expiry_ts: Some(expiry_ts.to_string()),
            threshold_price: Some("63500.12".to_string()),
            threshold_event_ts: Some("2026-06-04T20:00:00Z".to_string()),
            threshold_observed_ts: Some("2026-06-04T20:00:03Z".to_string()),
            computed_winner: None,
            official_winner: Some("UP".to_string()),
            winning_token_id: Some("up-token".to_string()),
            official_resolution_status: "resolved".to_string(),
            mismatch: None,
        }
    }
}
