use crate::schema::{ComparisonOperator, ProbabilityInput};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PathScore {
    pub terminal: bool,
    pub no_touch: bool,
}

pub fn price_satisfies_contract(input: &ProbabilityInput, price: f64) -> bool {
    match input.comparison_operator {
        ComparisonOperator::GreaterThan => price > input.threshold,
        ComparisonOperator::GreaterThanOrEqual => price >= input.threshold,
        ComparisonOperator::LessThan => price < input.threshold,
        ComparisonOperator::LessThanOrEqual => price <= input.threshold,
    }
}

pub fn score_path(input: &ProbabilityInput, path: &[f64]) -> PathScore {
    let terminal = path
        .last()
        .is_some_and(|price| price_satisfies_contract(input, *price));
    let no_touch = path
        .iter()
        .all(|price| price_satisfies_contract(input, *price));

    PathScore { terminal, no_touch }
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};

    use super::{price_satisfies_contract, score_path};
    use crate::schema::{Asset, ComparisonOperator, ProbabilityInput, Side};

    fn input(operator: ComparisonOperator) -> ProbabilityInput {
        ProbabilityInput {
            state_id: "state-1".to_string(),
            asof_ts: Utc.with_ymd_and_hms(2026, 6, 5, 12, 0, 0).unwrap(),
            asset: Asset::BTC,
            side: Side::UP,
            comparison_operator: operator,
            seconds_left: 60.0,
            settlement_price: 100.0,
            threshold: 100.0,
            sigma_tau: 0.2,
            executable_price: 0.5,
            source_age_ms: 10,
            book_age_ms: 20,
            z_path: 0.0,
        }
    }

    #[test]
    fn price_satisfies_contract_distinguishes_strict_and_inclusive_greater() {
        assert!(price_satisfies_contract(
            &input(ComparisonOperator::GreaterThan),
            101.0
        ));
        assert!(!price_satisfies_contract(
            &input(ComparisonOperator::GreaterThan),
            100.0
        ));
        assert!(price_satisfies_contract(
            &input(ComparisonOperator::GreaterThanOrEqual),
            100.0
        ));
    }

    #[test]
    fn price_satisfies_contract_distinguishes_strict_and_inclusive_less() {
        assert!(price_satisfies_contract(
            &input(ComparisonOperator::LessThan),
            99.0
        ));
        assert!(!price_satisfies_contract(
            &input(ComparisonOperator::LessThan),
            100.0
        ));
        assert!(price_satisfies_contract(
            &input(ComparisonOperator::LessThanOrEqual),
            100.0
        ));
    }

    #[test]
    fn score_path_reports_terminal_and_no_touch_independently() {
        let score = score_path(
            &ProbabilityInput {
                threshold: 101.0,
                comparison_operator: ComparisonOperator::GreaterThanOrEqual,
                ..input(ComparisonOperator::GreaterThanOrEqual)
            },
            &[100.0, 99.0, 100.5, 101.0],
        );

        assert!(score.terminal);
        assert!(!score.no_touch);
    }

    #[test]
    fn score_path_no_touch_requires_every_price_to_stay_on_winning_side() {
        let score = score_path(
            &ProbabilityInput {
                threshold: 100.0,
                comparison_operator: ComparisonOperator::GreaterThanOrEqual,
                ..input(ComparisonOperator::GreaterThanOrEqual)
            },
            &[100.0, 100.5, 101.0],
        );

        assert!(score.no_touch);
    }
}
