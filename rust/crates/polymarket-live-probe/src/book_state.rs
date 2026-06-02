#![allow(dead_code)]

use anyhow::Result;
use chrono::{DateTime, Utc};
use polymarket_runtime_types::{FeedFreshness, NormalizedOrderBook};
use rust_decimal::Decimal;
use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use std::time::Duration;

#[derive(Debug, Default, Clone)]
pub struct LiveBookState {
    inner: Arc<RwLock<HashMap<String, NormalizedOrderBook>>>,
}

impl LiveBookState {
    pub async fn upsert_book(&self, book: NormalizedOrderBook) {
        let mut inner = self.inner.write().expect("live book state lock poisoned");
        inner.insert(book.token_id.clone(), book);
    }

    pub async fn apply_top_of_book(
        &self,
        token_id: &str,
        best_bid: Decimal,
        best_ask: Decimal,
        spread: Decimal,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    ) -> Result<bool> {
        let mut inner = self.inner.write().expect("live book state lock poisoned");
        let Some(book) = inner.get_mut(token_id) else {
            return Ok(false);
        };
        book.best_bid = Some(best_bid);
        book.best_ask = Some(best_ask);
        book.spread = Some(spread);
        book.event_ts = event_ts;
        book.observed_ts = observed_ts;
        Ok(true)
    }

    pub async fn snapshot(&self) -> Vec<NormalizedOrderBook> {
        let inner = self.inner.read().expect("live book state lock poisoned");
        let mut books = inner.values().cloned().collect::<Vec<_>>();
        books.sort_by(|left, right| left.token_id.cmp(&right.token_id));
        books
    }

    pub async fn snapshot_for_token_ids<'a, I>(&self, token_ids: I) -> Vec<NormalizedOrderBook>
    where
        I: IntoIterator<Item = &'a str>,
    {
        let inner = self.inner.read().expect("live book state lock poisoned");
        let mut books = token_ids
            .into_iter()
            .filter_map(|token_id| inner.get(token_id).cloned())
            .collect::<Vec<_>>();
        books.sort_by(|left, right| left.token_id.cmp(&right.token_id));
        books
    }

    pub async fn freshness(&self, now: DateTime<Utc>, max_age: Duration) -> Vec<FeedFreshness> {
        let max_age_ms = i64::try_from(max_age.as_millis()).unwrap_or(i64::MAX);
        self.snapshot()
            .await
            .into_iter()
            .map(|book| {
                let age_ms = now
                    .signed_duration_since(book.observed_ts)
                    .num_milliseconds();
                FeedFreshness {
                    source_key: book.source_key,
                    symbol: book.token_id,
                    age_ms,
                    stale: age_ms < 0 || age_ms > max_age_ms,
                }
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{DateTime, Utc};
    use polymarket_runtime_types::{BookLevel, NormalizedOrderBook, OrderBookMeta};
    use rust_decimal::Decimal;
    use std::time::Duration;

    fn ts(second: u32) -> DateTime<Utc> {
        format!("2026-06-01T20:00:{second:02}Z")
            .parse::<DateTime<Utc>>()
            .unwrap()
    }

    fn sample_book() -> NormalizedOrderBook {
        let mut book = NormalizedOrderBook::from_levels(
            OrderBookMeta {
                market_slug: "btc-updown-5m-1780352700".to_owned(),
                contract_id: "market-1".to_owned(),
                token_id: "token-1".to_owned(),
                asset: "BTC".to_owned(),
                side: "UP".to_owned(),
                event_ts: ts(0),
                observed_ts: ts(0),
            },
            vec![BookLevel {
                price: Decimal::new(49, 2),
                size: Decimal::new(20, 0),
            }],
            vec![BookLevel {
                price: Decimal::new(51, 2),
                size: Decimal::new(30, 0),
            }],
        );
        book.source_key = "polymarket_market_ws".to_owned();
        book
    }

    #[tokio::test]
    async fn full_book_event_initializes_state() {
        let state = LiveBookState::default();

        state.upsert_book(sample_book()).await;

        let snapshot = state.snapshot().await;
        assert_eq!(snapshot.len(), 1);
        assert_eq!(snapshot[0].token_id, "token-1");
        assert_eq!(snapshot[0].best_bid, Some(Decimal::new(49, 2)));
        assert_eq!(snapshot[0].best_ask, Some(Decimal::new(51, 2)));
    }

    #[tokio::test]
    async fn top_of_book_update_preserves_contract_metadata() {
        let state = LiveBookState::default();
        state.upsert_book(sample_book()).await;

        let applied = state
            .apply_top_of_book(
                "token-1",
                Decimal::new(52, 2),
                Decimal::new(54, 2),
                Decimal::new(2, 2),
                ts(1),
                ts(1),
            )
            .await
            .unwrap();

        assert!(applied);
        let snapshot = state.snapshot().await;
        assert_eq!(snapshot[0].asset, "BTC");
        assert_eq!(snapshot[0].side, "UP");
        assert_eq!(snapshot[0].market_slug, "btc-updown-5m-1780352700");
        assert_eq!(snapshot[0].best_bid, Some(Decimal::new(52, 2)));
        assert_eq!(snapshot[0].best_ask, Some(Decimal::new(54, 2)));
        assert_eq!(snapshot[0].spread, Some(Decimal::new(2, 2)));
        assert_eq!(snapshot[0].event_ts, ts(1));
    }

    #[tokio::test]
    async fn snapshot_for_token_ids_clones_only_requested_books() {
        let state = LiveBookState::default();
        let mut first = sample_book();
        first.token_id = "token-1".to_owned();
        let mut second = sample_book();
        second.token_id = "token-2".to_owned();
        state.upsert_book(first).await;
        state.upsert_book(second).await;

        let snapshot = state.snapshot_for_token_ids(["token-2"]).await;

        assert_eq!(snapshot.len(), 1);
        assert_eq!(snapshot[0].token_id, "token-2");
    }

    #[tokio::test]
    async fn freshness_marks_stale_orderbooks() {
        let state = LiveBookState::default();
        state.upsert_book(sample_book()).await;

        let freshness = state.freshness(ts(2), Duration::from_millis(1500)).await;

        assert_eq!(freshness.len(), 1);
        assert_eq!(freshness[0].source_key, "polymarket_market_ws");
        assert_eq!(freshness[0].symbol, "token-1");
        assert_eq!(freshness[0].age_ms, 2_000);
        assert!(freshness[0].stale);
    }
}
