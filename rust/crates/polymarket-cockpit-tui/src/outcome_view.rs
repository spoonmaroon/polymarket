use std::collections::{BTreeMap, BTreeSet};

use chrono::{DateTime, Local, Timelike, Utc};

use crate::status::{RuntimeOutcomeRow, RuntimeOutcomes};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct OutcomeExpansion {
    pub expanded_days: BTreeSet<String>,
    pub initialized_days: BTreeSet<String>,
    pub expanded_sections: BTreeSet<String>,
    pub initialized_sections: BTreeSet<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OutcomeToggleTarget {
    Day(String),
    Section(String),
}

#[derive(Debug, Clone, PartialEq)]
pub enum OutcomeDisplayItem<'a> {
    Day {
        key: String,
        label: String,
        count: usize,
        expanded: bool,
    },
    Section {
        key: String,
        day_key: String,
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
    expansion: &OutcomeExpansion,
) -> Vec<OutcomeDisplayItem<'a>> {
    let Some(outcomes) = outcomes else {
        return Vec::new();
    };
    let groups = grouped_outcomes(&outcomes.rows);
    let mut items = Vec::new();
    for group in groups {
        let expanded = expansion.expanded_days.contains(&group.key);
        items.push(OutcomeDisplayItem::Day {
            key: group.key.clone(),
            label: group.label,
            count: group.count,
            expanded,
        });
        if expanded {
            for section in group.sections {
                let expanded = expansion.expanded_sections.contains(&section.key);
                items.push(OutcomeDisplayItem::Section {
                    key: section.key.clone(),
                    day_key: group.key.clone(),
                    label: section.label.to_string(),
                    count: section.rows.len(),
                    expanded,
                });
                if expanded {
                    items.extend(
                        section
                            .rows
                            .into_iter()
                            .map(|row| OutcomeDisplayItem::Outcome { row }),
                    );
                }
            }
        }
    }
    items
}

pub fn outcome_toggle_target_at(
    outcomes: Option<&RuntimeOutcomes>,
    expansion: &OutcomeExpansion,
    index: usize,
) -> Option<OutcomeToggleTarget> {
    match outcome_display_items(outcomes, expansion).get(index)? {
        OutcomeDisplayItem::Day { key, .. } => Some(OutcomeToggleTarget::Day(key.clone())),
        OutcomeDisplayItem::Section { key, .. } => Some(OutcomeToggleTarget::Section(key.clone())),
        OutcomeDisplayItem::Outcome { .. } => None,
    }
}

pub fn latest_outcome_day_key(outcomes: &RuntimeOutcomes) -> Option<String> {
    grouped_outcomes(&outcomes.rows)
        .first()
        .map(|group| group.key.clone())
}

pub fn default_outcome_section_key(
    outcomes: Option<&RuntimeOutcomes>,
    day_key: &str,
) -> Option<String> {
    let outcomes = outcomes?;
    grouped_outcomes(&outcomes.rows)
        .into_iter()
        .find(|group| group.key == day_key)?
        .sections
        .into_iter()
        .rev()
        .find(|section| !section.rows.is_empty())
        .map(|section| section.key)
}

pub fn outcome_section_key(day_key: &str, section_key: &str) -> String {
    format!("{day_key}#{section_key}")
}

struct OutcomeDayGroup<'a> {
    key: String,
    label: String,
    count: usize,
    sections: Vec<OutcomeSectionGroup<'a>>,
}

struct OutcomeSectionGroup<'a> {
    key: String,
    label: &'static str,
    rows: Vec<&'a RuntimeOutcomeRow>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct OutcomeSection {
    key: &'static str,
    label: &'static str,
    start_hour: u32,
    end_hour: u32,
}

const OUTCOME_SECTIONS: [OutcomeSection; 4] = [
    OutcomeSection {
        key: "overnight",
        label: "Overnight 00:00-05:59",
        start_hour: 0,
        end_hour: 5,
    },
    OutcomeSection {
        key: "morning",
        label: "Morning 06:00-11:59",
        start_hour: 6,
        end_hour: 11,
    },
    OutcomeSection {
        key: "afternoon",
        label: "Afternoon 12:00-17:59",
        start_hour: 12,
        end_hour: 17,
    },
    OutcomeSection {
        key: "evening",
        label: "Evening 18:00-23:59",
        start_hour: 18,
        end_hour: 23,
    },
];
const UNKNOWN_OUTCOME_SECTION_KEY: &str = "unknown";
const UNKNOWN_OUTCOME_SECTION_LABEL: &str = "Unknown expiry";

fn grouped_outcomes(rows: &[RuntimeOutcomeRow]) -> Vec<OutcomeDayGroup<'_>> {
    let mut groups_by_key: BTreeMap<String, Vec<&RuntimeOutcomeRow>> = BTreeMap::new();
    for row in rows {
        let key = outcome_day_key(row);
        groups_by_key.entry(key).or_default().push(row);
    }

    let mut unknown_rows = groups_by_key.remove("unknown");
    let mut groups = groups_by_key
        .into_iter()
        .rev()
        .map(|(key, rows)| {
            let label = rows
                .iter()
                .find_map(|row| outcome_day_label(row))
                .unwrap_or_else(|| key.clone());
            let count = rows.len();
            OutcomeDayGroup {
                sections: grouped_sections(&key, rows),
                key,
                label,
                count,
            }
        })
        .collect::<Vec<_>>();
    if let Some(rows) = unknown_rows.take() {
        let key = "unknown".to_string();
        let count = rows.len();
        groups.push(OutcomeDayGroup {
            sections: grouped_sections(&key, rows),
            key: key.clone(),
            label: key,
            count,
        });
    }
    groups
}

fn grouped_sections<'a>(
    day_key: &str,
    rows: Vec<&'a RuntimeOutcomeRow>,
) -> Vec<OutcomeSectionGroup<'a>> {
    let mut groups = OUTCOME_SECTIONS
        .iter()
        .filter_map(|section| {
            let mut section_rows = rows
                .iter()
                .copied()
                .filter(|row| {
                    parse_local_expiry(row)
                        .map(section_for_local_expiry)
                        .is_some_and(|row_section| row_section.key == section.key)
                })
                .collect::<Vec<_>>();
            if section_rows.is_empty() {
                return None;
            }
            section_rows.sort_by(|left, right| right.expiry_ts.cmp(&left.expiry_ts));
            Some(OutcomeSectionGroup {
                key: outcome_section_key(day_key, section.key),
                label: section.label,
                rows: section_rows,
            })
        })
        .collect::<Vec<_>>();
    let mut unknown_rows = rows
        .into_iter()
        .filter(|row| parse_local_expiry(row).is_none())
        .collect::<Vec<_>>();
    if !unknown_rows.is_empty() {
        unknown_rows.sort_by(|left, right| right.market.cmp(&left.market));
        groups.push(OutcomeSectionGroup {
            key: outcome_section_key(day_key, UNKNOWN_OUTCOME_SECTION_KEY),
            label: UNKNOWN_OUTCOME_SECTION_LABEL,
            rows: unknown_rows,
        });
    }
    groups
}

fn section_for_local_expiry(timestamp: DateTime<Local>) -> OutcomeSection {
    let hour = timestamp.hour();
    OUTCOME_SECTIONS
        .into_iter()
        .find(|section| (section.start_hour..=section.end_hour).contains(&hour))
        .unwrap_or(OUTCOME_SECTIONS[0])
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
    use chrono::{Datelike, Local, NaiveDate, TimeZone};

    use crate::{
        outcome_view::{
            OutcomeDisplayItem, OutcomeExpansion, OutcomeToggleTarget, default_outcome_section_key,
            latest_outcome_day_key, outcome_display_items, outcome_section_key,
            outcome_toggle_target_at,
        },
        status::{RuntimeOutcomeRow, RuntimeOutcomes},
    };

    #[test]
    fn outcome_display_items_allows_latest_day_to_be_closed() {
        let outcomes = RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-04T20:00:00Z".to_string()),
            rows: vec![
                outcome("BTC 5m", &local_expiry("2026-06-04", 20)),
                outcome("ETH 5m", &local_expiry("2026-06-03", 20)),
            ],
        };
        let mut expansion = OutcomeExpansion::default();
        expansion.initialized_days.insert("2026-06-04".to_string());

        let items = outcome_display_items(Some(&outcomes), &expansion);

        assert!(matches!(
            &items[0],
            OutcomeDisplayItem::Day {
                label,
                expanded: false,
                ..
            } if label.contains("Jun 04")
        ));
        assert!(!items.iter().any(|item| {
            matches!(item, OutcomeDisplayItem::Outcome { row } if row.market == "BTC 5m")
        }));
    }

    #[test]
    fn outcome_display_items_splits_expanded_day_into_large_sections() {
        let outcomes = RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-04T20:00:00Z".to_string()),
            rows: vec![
                outcome("BTC 5m overnight", &local_expiry("2026-06-04", 1)),
                outcome("BTC 5m afternoon", &local_expiry("2026-06-04", 13)),
            ],
        };
        let mut expansion = OutcomeExpansion::default();
        expansion.initialized_days.insert("2026-06-04".to_string());
        expansion.expanded_days.insert("2026-06-04".to_string());
        expansion
            .initialized_sections
            .insert("2026-06-04#afternoon".to_string());
        expansion
            .expanded_sections
            .insert("2026-06-04#afternoon".to_string());

        let items = outcome_display_items(Some(&outcomes), &expansion);

        assert!(matches!(
            &items[0],
            OutcomeDisplayItem::Day { expanded: true, .. }
        ));
        assert!(items.iter().any(|item| {
            matches!(
                item,
                OutcomeDisplayItem::Section {
                    key,
                    label,
                    count: 1,
                    expanded: false,
                    ..
                } if key == "2026-06-04#overnight" && label.contains("Overnight")
            )
        }));
        assert!(items.iter().any(|item| {
            matches!(
                item,
                OutcomeDisplayItem::Section {
                    key,
                    label,
                    count: 1,
                    expanded: true,
                    ..
                } if key == "2026-06-04#afternoon" && label.contains("Afternoon")
            )
        }));
        assert!(items.iter().any(|item| {
            matches!(item, OutcomeDisplayItem::Outcome { row } if row.market == "BTC 5m afternoon")
        }));
        assert!(!items.iter().any(|item| {
            matches!(item, OutcomeDisplayItem::Outcome { row } if row.market == "BTC 5m overnight")
        }));
    }

    #[test]
    fn outcome_toggle_target_at_returns_only_selectable_headers() {
        let outcomes = RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-04T20:00:00Z".to_string()),
            rows: vec![outcome("BTC 5m", &local_expiry("2026-06-04", 13))],
        };
        let mut expansion = OutcomeExpansion::default();
        expansion.expanded_days.insert("2026-06-04".to_string());
        expansion
            .expanded_sections
            .insert(outcome_section_key("2026-06-04", "afternoon"));

        assert_eq!(
            outcome_toggle_target_at(Some(&outcomes), &expansion, 0),
            Some(OutcomeToggleTarget::Day("2026-06-04".to_string()))
        );
        assert_eq!(
            outcome_toggle_target_at(Some(&outcomes), &expansion, 1),
            Some(OutcomeToggleTarget::Section(
                "2026-06-04#afternoon".to_string()
            ))
        );
        assert_eq!(
            outcome_toggle_target_at(Some(&outcomes), &expansion, 2),
            None
        );
    }

    #[test]
    fn latest_outcome_day_key_returns_newest_local_day() {
        let outcomes = RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-04T20:00:00Z".to_string()),
            rows: vec![
                outcome("ETH 5m", &local_expiry("2026-06-03", 20)),
                outcome("BTC 5m", &local_expiry("2026-06-04", 20)),
            ],
        };

        assert_eq!(
            latest_outcome_day_key(&outcomes).as_deref(),
            Some("2026-06-04")
        );
    }

    #[test]
    fn default_outcome_section_key_returns_newest_section_for_day() {
        let outcomes = RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-04T20:00:00Z".to_string()),
            rows: vec![
                outcome("BTC 5m morning", &local_expiry("2026-06-04", 8)),
                outcome("BTC 5m evening", &local_expiry("2026-06-04", 20)),
            ],
        };

        assert_eq!(
            default_outcome_section_key(Some(&outcomes), "2026-06-04").as_deref(),
            Some("2026-06-04#evening")
        );
    }

    #[test]
    fn outcome_display_items_keeps_unknown_expiry_rows_visible() {
        let outcomes = RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-04T20:00:00Z".to_string()),
            rows: vec![outcome("BTC malformed", "not-a-timestamp")],
        };
        let mut expansion = OutcomeExpansion::default();
        expansion.expanded_days.insert("unknown".to_string());
        expansion
            .expanded_sections
            .insert("unknown#unknown".to_string());

        let items = outcome_display_items(Some(&outcomes), &expansion);

        assert!(matches!(
            &items[0],
            OutcomeDisplayItem::Day {
                key,
                count: 1,
                expanded: true,
                ..
            } if key == "unknown"
        ));
        assert!(items.iter().any(|item| {
            matches!(
                item,
                OutcomeDisplayItem::Section {
                    key,
                    label,
                    count: 1,
                    expanded: true,
                    ..
                } if key == "unknown#unknown" && label.contains("Unknown")
            )
        }));
        assert!(items.iter().any(|item| {
            matches!(item, OutcomeDisplayItem::Outcome { row } if row.market == "BTC malformed")
        }));
    }

    #[test]
    fn outcome_display_items_orders_unknown_day_after_dated_days() {
        let dated_key = "2026-06-04";
        let outcomes = RuntimeOutcomes {
            ok: true,
            state: "OK".to_string(),
            generated_at: Some("2026-06-04T20:00:00Z".to_string()),
            rows: vec![
                outcome("BTC malformed", "not-a-timestamp"),
                outcome("BTC dated", &local_expiry(dated_key, 13)),
            ],
        };
        let expansion = OutcomeExpansion::default();

        let items = outcome_display_items(Some(&outcomes), &expansion);

        assert!(matches!(
            &items[0],
            OutcomeDisplayItem::Day { key, .. } if key == dated_key
        ));
        assert!(items.iter().skip(1).any(|item| {
            matches!(item, OutcomeDisplayItem::Day { key, .. } if key == "unknown")
        }));
        assert_eq!(
            latest_outcome_day_key(&outcomes).as_deref(),
            Some(dated_key)
        );
    }

    fn local_expiry(day: &str, hour: u32) -> String {
        let date = NaiveDate::parse_from_str(day, "%Y-%m-%d").unwrap();
        Local
            .with_ymd_and_hms(date.year(), date.month(), date.day(), hour, 5, 0)
            .single()
            .unwrap()
            .to_rfc3339()
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
