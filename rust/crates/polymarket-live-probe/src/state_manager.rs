use anyhow::Result;
use chrono::{DateTime, Utc};
use polymarket_runtime_types::{
    FeedFreshness, NormalizedOrderBook, NormalizedPriceTick, WarmStateSnapshot, WarmedContract,
};
use std::sync::{Arc, RwLock};
use std::time::Duration as StdDuration;
use tokio::task::JoinHandle;

use crate::decision_journal::HotDecisionSink;
use crate::hot_decision::{
    HotDecisionBuilder, HotDecisionConfig, HotDecisionTelemetry, HotDecisionTelemetrySnapshot,
    HotPathEvent, HotPathEventSink,
};
use crate::raw_event_journal::RawEventSink;
use crate::report::{StateManagerSubscription, WebSocketStatus};
use crate::{book_state::LiveBookState, clob_ws, polymarket, prices, windows};

type SharedWarmedContracts = Arc<RwLock<Vec<WarmedContract>>>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StateManagerConfig {
    pub assets: Vec<String>,
    pub interval: String,
    pub windows: u8,
    pub prewarm_before_expiry_ms: i64,
    pub stale_chainlink_after_ms: i64,
    pub stale_orderbook_after_ms: i64,
    pub rest_backup_interval_ms: i64,
}

pub struct StateManagerRuntime {
    config: StateManagerConfig,
    latest_prices: prices::LatestPrices,
    book_state: LiveBookState,
    orderbook_streams: clob_ws::BestBidAskStreamManager,
    warmed: SharedWarmedContracts,
    token_ids: Vec<String>,
    last_refresh: DateTime<Utc>,
    chainlink_streams: prices::ChainlinkStreamManager,
    hot_decision_worker: Option<JoinHandle<Result<()>>>,
    hot_decision_telemetry: Option<HotDecisionTelemetry>,
}

impl StateManagerRuntime {
    pub async fn start(config: StateManagerConfig) -> Result<Self> {
        Self::start_with_raw_events(config, None).await
    }

    pub async fn start_with_raw_events(
        config: StateManagerConfig,
        raw_event_sink: Option<RawEventSink>,
    ) -> Result<Self> {
        Self::start_with_raw_events_and_hot_decisions(config, raw_event_sink, None).await
    }

    pub async fn start_with_raw_events_and_hot_decisions(
        config: StateManagerConfig,
        raw_event_sink: Option<RawEventSink>,
        decision_sink: Option<HotDecisionSink>,
    ) -> Result<Self> {
        let runtime_started_at = Utc::now();
        let latest_prices = prices::LatestPrices::default();
        let book_state = LiveBookState::default();
        let warmed = Arc::new(RwLock::new(Vec::new()));
        let hot_decision_telemetry = decision_sink
            .as_ref()
            .map(|_| HotDecisionTelemetry::default());
        let (hot_event_sink, hot_event_receiver) =
            HotPathEventSink::channel_with_telemetry(16_384, hot_decision_telemetry.clone());
        let telemetry_for_worker = hot_decision_telemetry.clone();
        let hot_decision_worker = decision_sink.map(|sink| {
            start_hot_decision_worker(
                hot_decision_config_for_runtime_start(runtime_started_at),
                latest_prices.clone(),
                book_state.clone(),
                warmed.clone(),
                hot_event_receiver,
                sink,
                telemetry_for_worker.expect("hot decision telemetry missing for worker"),
            )
        });
        let hot_event_sink = hot_decision_worker.as_ref().map(|_| hot_event_sink);
        let chainlink_streams = prices::ChainlinkStreamManager::start_with_raw_events(
            chainlink_symbols_for_assets(&config.assets),
            latest_prices.clone(),
            raw_event_sink.clone(),
            hot_event_sink.clone(),
        );
        let mut runtime = Self {
            config,
            latest_prices,
            orderbook_streams: clob_ws::BestBidAskStreamManager::new_with_raw_events(
                book_state.clone(),
                raw_event_sink,
                hot_event_sink,
            ),
            book_state,
            warmed,
            token_ids: Vec::new(),
            last_refresh: Utc::now(),
            chainlink_streams,
            hot_decision_worker,
            hot_decision_telemetry,
        };
        runtime.refresh_contracts().await?;
        Ok(runtime)
    }

    pub async fn maybe_refresh(&mut self, now: DateTime<Utc>) -> Result<()> {
        if self.needs_contract_refresh(now) || self.needs_rest_backup(now) {
            self.refresh_contracts().await?;
        }
        Ok(())
    }

    pub async fn snapshot(&self, _now: DateTime<Utc>) -> Result<WarmStateSnapshot> {
        let warmed = self
            .warmed
            .read()
            .expect("warmed contracts lock poisoned")
            .clone();
        let chainlink_prices = self.latest_prices.snapshot().await;
        let orderbooks = self
            .book_state
            .snapshot_for_token_ids(warmed_token_ids(&warmed))
            .await;
        let snapshot_now = Utc::now();
        build_snapshot_from_warmed(
            snapshot_now,
            &self.config,
            &warmed,
            chainlink_prices,
            orderbooks,
        )
    }

    pub fn subscriptions(&self) -> Vec<StateManagerSubscription> {
        let warmed = self
            .warmed
            .read()
            .expect("warmed contracts lock poisoned")
            .clone();
        subscriptions_from_warmed_contracts(&warmed)
    }

    pub fn websocket_status(&self, now: DateTime<Utc>) -> Vec<WebSocketStatus> {
        vec![
            self.chainlink_streams.websocket_status(now),
            self.orderbook_streams.websocket_status(now),
        ]
    }

    pub fn hot_decision_telemetry(&self) -> Option<HotDecisionTelemetrySnapshot> {
        self.hot_decision_telemetry
            .as_ref()
            .map(HotDecisionTelemetry::snapshot)
    }

    pub fn shutdown(&mut self) {
        self.chainlink_streams.shutdown();
        self.orderbook_streams.shutdown();
        if let Some(worker) = self.hot_decision_worker.take() {
            worker.abort();
        }
    }

    async fn refresh_contracts(&mut self) -> Result<()> {
        let now = Utc::now();
        let asset_refs = self
            .config
            .assets
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>();
        let windows = windows::schedule_windows(
            now,
            &asset_refs,
            &self.config.interval,
            self.config.windows,
        )?;
        let tokens = polymarket::discover_window_tokens(&windows).await?;
        let warmed = polymarket::warmed_contracts_from_tokens(&tokens)?;

        for book in polymarket::fetch_orderbooks(&tokens).await? {
            self.book_state.upsert_book(book).await;
        }

        let mut token_ids = tokens
            .iter()
            .map(|token| token.token_id.to_string())
            .collect::<Vec<_>>();
        token_ids.sort();
        token_ids.dedup();

        *self.warmed.write().expect("warmed contracts lock poisoned") = warmed;
        self.orderbook_streams.update_tokens(&token_ids)?;
        self.token_ids = token_ids;

        self.last_refresh = now;
        Ok(())
    }

    fn needs_contract_refresh(&self, now: DateTime<Utc>) -> bool {
        let warmed = self
            .warmed
            .read()
            .expect("warmed contracts lock poisoned")
            .clone();
        if warmed.is_empty() {
            return true;
        }
        self.config.assets.iter().any(|asset| {
            let mut asset_contracts = warmed
                .iter()
                .filter(|contract| contract.window.asset == asset.to_ascii_uppercase())
                .filter(|contract| contract.window.end_ts > now)
                .collect::<Vec<_>>();
            asset_contracts.sort_by_key(|contract| contract.window.start_ts);
            asset_contracts.first().is_none_or(|contract| {
                contract
                    .window
                    .end_ts
                    .signed_duration_since(now)
                    .num_milliseconds()
                    <= self.config.prewarm_before_expiry_ms
            }) || asset_contracts.len() < usize::from(self.config.windows.min(2))
        })
    }

    fn needs_rest_backup(&self, now: DateTime<Utc>) -> bool {
        self.config.rest_backup_interval_ms > 0
            && now
                .signed_duration_since(self.last_refresh)
                .num_milliseconds()
                >= self.config.rest_backup_interval_ms
    }
}

fn hot_decision_config_for_runtime_start(runtime_started_at: DateTime<Utc>) -> HotDecisionConfig {
    HotDecisionConfig {
        restart_started_at: Some(runtime_started_at),
        ..HotDecisionConfig::default()
    }
}

fn start_hot_decision_worker(
    config: HotDecisionConfig,
    latest_prices: prices::LatestPrices,
    book_state: LiveBookState,
    warmed: SharedWarmedContracts,
    mut receiver: tokio::sync::mpsc::Receiver<HotPathEvent>,
    decision_sink: HotDecisionSink,
    telemetry: HotDecisionTelemetry,
) -> JoinHandle<Result<()>> {
    tokio::spawn(async move {
        let builder = HotDecisionBuilder::new(config);
        while let Some(event) = receiver.recv().await {
            let asof_ts = Utc::now();
            let warmed_snapshot = warmed
                .read()
                .expect("warmed contracts lock poisoned")
                .clone();
            let price_assets = hot_event_price_assets(&event, &warmed_snapshot);
            let prices = latest_prices
                .history_snapshot_for_assets(price_assets.iter().map(String::as_str))
                .await;
            let orderbook_token_ids =
                hot_event_orderbook_token_ids(&event, &warmed_snapshot, asof_ts);
            let orderbooks = book_state.snapshot_for_token_ids(orderbook_token_ids).await;
            for state in
                builder.build_for_event(&event, &warmed_snapshot, &prices, &orderbooks, asof_ts)
            {
                telemetry.record_state_built(state.asof_ts, state.latency.observed_to_state_us);
                if let Err(error) = decision_sink.try_record(state) {
                    telemetry.record_dropped_event();
                    tracing::warn!(error = %error, "dropped hot decision state before persistence");
                } else {
                    telemetry.record_state_persist_queued();
                }
            }
        }
        Ok(())
    })
}

pub fn build_snapshot_from_warmed(
    now: DateTime<Utc>,
    config: &StateManagerConfig,
    warmed_contracts: &[WarmedContract],
    chainlink_prices: Vec<NormalizedPriceTick>,
    orderbooks: Vec<NormalizedOrderBook>,
) -> Result<WarmStateSnapshot> {
    let mut current = Vec::new();
    let mut next = Vec::new();
    let mut next_next = Vec::new();
    let mut health_flags = Vec::new();

    for asset in &config.assets {
        let mut asset_contracts = warmed_contracts
            .iter()
            .filter(|contract| contract.window.asset == asset.to_ascii_uppercase())
            .filter(|contract| contract.window.end_ts > now)
            .cloned()
            .collect::<Vec<_>>();
        asset_contracts.sort_by_key(|contract| contract.window.start_ts);

        if let Some(contract) = asset_contracts.first() {
            let remaining_ms = contract
                .window
                .end_ts
                .signed_duration_since(now)
                .num_milliseconds();
            if remaining_ms <= config.prewarm_before_expiry_ms && asset_contracts.len() < 2 {
                health_flags.push(format!("next_contract_not_warmed:{asset}"));
            }
        } else {
            health_flags.push(format!("current_contract_not_warmed:{asset}"));
        }

        if let Some(contract) = asset_contracts.first() {
            current.push(contract.clone());
        }
        if let Some(contract) = asset_contracts.get(1) {
            next.push(contract.clone());
        }
        if let Some(contract) = asset_contracts.get(2) {
            next_next.push(contract.clone());
        }
    }

    let freshness = feed_freshness(
        now,
        config,
        &chainlink_prices,
        &orderbooks,
        &mut health_flags,
    );
    add_missing_feed_flags(
        config,
        &chainlink_prices,
        [&current, &next, &next_next]
            .into_iter()
            .flat_map(|contracts| contracts.iter())
            .collect::<Vec<_>>()
            .as_slice(),
        &orderbooks,
        &mut health_flags,
    );

    Ok(WarmStateSnapshot {
        observed_ts: now,
        current,
        next,
        next_next,
        chainlink_prices,
        proxy_prices: vec![],
        orderbooks,
        freshness,
        health_flags,
    })
}

pub fn subscriptions_from_warmed_contracts(
    warmed_contracts: &[WarmedContract],
) -> Vec<StateManagerSubscription> {
    let mut subscriptions = warmed_contracts
        .iter()
        .flat_map(|contract| {
            [
                StateManagerSubscription {
                    source_key: "polymarket_clob_market_ws".to_owned(),
                    channel: "best_bid_ask".to_owned(),
                    asset: contract.window.asset.clone(),
                    token_id: contract.down.token_id.clone(),
                },
                StateManagerSubscription {
                    source_key: "polymarket_clob_market_ws".to_owned(),
                    channel: "best_bid_ask".to_owned(),
                    asset: contract.window.asset.clone(),
                    token_id: contract.up.token_id.clone(),
                },
            ]
        })
        .collect::<Vec<_>>();
    subscriptions.sort_by(|left, right| {
        left.asset
            .cmp(&right.asset)
            .then_with(|| left.token_id.cmp(&right.token_id))
    });
    subscriptions
        .dedup_by(|left, right| left.asset == right.asset && left.token_id == right.token_id);
    subscriptions
}

fn warmed_token_ids(warmed_contracts: &[WarmedContract]) -> impl Iterator<Item = &str> {
    warmed_contracts.iter().flat_map(|contract| {
        [
            contract.up.token_id.as_str(),
            contract.down.token_id.as_str(),
        ]
    })
}

fn hot_event_price_assets(
    event: &HotPathEvent,
    warmed_contracts: &[WarmedContract],
) -> Vec<String> {
    let mut assets = match event {
        HotPathEvent::ChainlinkPrice { symbol, .. } => vec![price_asset_from_symbol(symbol)],
        HotPathEvent::OrderBookTopOfBook { token_id, .. } => warmed_contracts
            .iter()
            .filter(|contract| {
                token_id == &contract.up.token_id || token_id == &contract.down.token_id
            })
            .map(|contract| contract.window.asset.clone())
            .collect(),
    };
    assets.retain(|asset| !asset.is_empty());
    assets.sort();
    assets.dedup();
    assets
}

fn price_asset_from_symbol(symbol: &str) -> String {
    symbol
        .split_once('/')
        .map(|(asset, _)| asset)
        .unwrap_or(symbol)
        .trim()
        .to_ascii_uppercase()
}

fn hot_event_orderbook_token_ids<'a>(
    event: &'a HotPathEvent,
    warmed_contracts: &'a [WarmedContract],
    asof_ts: DateTime<Utc>,
) -> Vec<&'a str> {
    let asof_ts = asof_ts.max(event.observed_ts());
    match event {
        HotPathEvent::ChainlinkPrice { symbol, .. } => {
            let asset = price_asset_from_symbol(symbol);
            warmed_contracts
                .iter()
                .filter(|contract| {
                    contract.window.start_ts <= asof_ts && asof_ts < contract.window.end_ts
                })
                .filter(|contract| contract.window.asset.eq_ignore_ascii_case(&asset))
                .flat_map(|contract| {
                    [
                        contract.up.token_id.as_str(),
                        contract.down.token_id.as_str(),
                    ]
                })
                .collect::<Vec<_>>()
        }
        HotPathEvent::OrderBookTopOfBook { token_id, .. } => warmed_contracts
            .iter()
            .filter(|contract| {
                contract.window.start_ts <= asof_ts && asof_ts < contract.window.end_ts
            })
            .any(|contract| {
                token_id == &contract.up.token_id || token_id == &contract.down.token_id
            })
            .then(|| vec![token_id.as_str()])
            .unwrap_or_default(),
    }
}

fn feed_freshness(
    now: DateTime<Utc>,
    config: &StateManagerConfig,
    chainlink_prices: &[NormalizedPriceTick],
    orderbooks: &[NormalizedOrderBook],
    health_flags: &mut Vec<String>,
) -> Vec<FeedFreshness> {
    let mut freshness = Vec::new();
    for tick in chainlink_prices
        .iter()
        .filter(|tick| tick.source_key == "polymarket_rtds_chainlink")
    {
        let age_ms = now
            .signed_duration_since(tick.observed_ts)
            .num_milliseconds();
        let stale = age_ms < 0 || age_ms > config.stale_chainlink_after_ms;
        if stale {
            health_flags.push(format!("chainlink_price_stale:{}", tick.symbol));
        }
        freshness.push(FeedFreshness {
            source_key: tick.source_key.clone(),
            symbol: tick.symbol.to_ascii_uppercase(),
            age_ms,
            stale,
        });
    }

    for book in orderbooks {
        let age_ms = now
            .signed_duration_since(book.observed_ts)
            .num_milliseconds();
        let stale = age_ms < 0 || age_ms > config.stale_orderbook_after_ms;
        if stale {
            health_flags.push(format!("orderbook_stale:{}", book.token_id));
        }
        freshness.push(FeedFreshness {
            source_key: book.source_key.clone(),
            symbol: book.token_id.clone(),
            age_ms,
            stale,
        });
    }
    freshness
}

fn add_missing_feed_flags(
    config: &StateManagerConfig,
    chainlink_prices: &[NormalizedPriceTick],
    warmed_contracts: &[&WarmedContract],
    orderbooks: &[NormalizedOrderBook],
    health_flags: &mut Vec<String>,
) {
    for asset in &config.assets {
        let expected_symbol = format!("{}/USD", asset.to_ascii_uppercase());
        if !chainlink_prices
            .iter()
            .any(|tick| tick.symbol.eq_ignore_ascii_case(&expected_symbol))
        {
            health_flags.push(format!("chainlink_price_missing:{expected_symbol}"));
        }
    }

    for contract in warmed_contracts {
        for token_id in contract.token_ids() {
            if !orderbooks.iter().any(|book| book.token_id == token_id) {
                health_flags.push(format!("orderbook_missing:{token_id}"));
            }
        }
    }
}

#[allow(dead_code)]
pub async fn run_until_report(
    config: StateManagerConfig,
    run_for: StdDuration,
) -> Result<WarmStateSnapshot> {
    let mut runtime = StateManagerRuntime::start(config).await?;
    tokio::time::sleep(run_for).await;
    runtime.maybe_refresh(Utc::now()).await?;
    let snapshot = runtime.snapshot(Utc::now()).await;
    runtime.shutdown();
    snapshot
}

fn chainlink_symbols_for_assets(assets: &[String]) -> Vec<String> {
    assets
        .iter()
        .map(|asset| format!("{}/usd", asset.trim().to_ascii_lowercase()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Duration, TimeZone, Utc};
    use polymarket_runtime_types::{
        BookLevel, ContractSide, ContractToken, ContractWindow, NormalizedOrderBook,
        NormalizedPriceTick, OrderBookMeta, WarmedContract,
    };
    use rust_decimal::Decimal;

    fn config() -> StateManagerConfig {
        StateManagerConfig {
            assets: vec!["BTC".to_owned()],
            interval: "5m".to_owned(),
            windows: 3,
            prewarm_before_expiry_ms: 30_000,
            stale_chainlink_after_ms: 2_500,
            stale_orderbook_after_ms: 2_000,
            rest_backup_interval_ms: 15_000,
        }
    }

    fn warmed(asset: &str, start_epoch: i64, token_prefix: &str) -> WarmedContract {
        let start = Utc.timestamp_opt(start_epoch, 0).unwrap();
        let end = start + Duration::seconds(300);
        let window = ContractWindow::new(asset, "5m", start, end).unwrap();
        WarmedContract::new(
            window,
            ContractToken::new(asset, ContractSide::Up, &format!("{token_prefix}-up")),
            ContractToken::new(asset, ContractSide::Down, &format!("{token_prefix}-down")),
        )
        .unwrap()
    }

    fn book(
        asset: &str,
        side: &str,
        token_id: &str,
        observed_epoch_ms: i64,
    ) -> NormalizedOrderBook {
        let observed_ts = Utc.timestamp_millis_opt(observed_epoch_ms).unwrap();
        NormalizedOrderBook::from_levels(
            OrderBookMeta {
                market_slug: format!("{}-updown-5m-1780302400", asset.to_ascii_lowercase()),
                contract_id: format!("market-{token_id}"),
                token_id: token_id.to_owned(),
                asset: asset.to_owned(),
                side: side.to_owned(),
                event_ts: observed_ts,
                observed_ts,
            },
            vec![BookLevel {
                price: Decimal::new(49, 2),
                size: Decimal::new(10, 0),
            }],
            vec![BookLevel {
                price: Decimal::new(51, 2),
                size: Decimal::new(12, 0),
            }],
        )
    }

    fn chainlink(asset: &str, observed_epoch_ms: i64) -> NormalizedPriceTick {
        let observed_ts = Utc.timestamp_millis_opt(observed_epoch_ms).unwrap();
        NormalizedPriceTick {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            symbol: format!("{asset}/USD"),
            event_ts: observed_ts,
            observed_ts,
            price: Decimal::new(100_000, 0),
        }
    }

    #[test]
    fn hot_decision_worker_config_marks_runtime_restart_start() {
        let runtime_started_at = Utc.timestamp_opt(1_780_302_445, 0).unwrap();

        let config = hot_decision_config_for_runtime_start(runtime_started_at);

        assert_eq!(config.restart_started_at, Some(runtime_started_at));
    }

    #[test]
    fn warmed_contracts_roll_forward_without_resolver_call() {
        let start = 1_780_302_400;
        let warmed = vec![
            warmed("BTC", start, "current"),
            warmed("BTC", start + 300, "next"),
            warmed("BTC", start + 600, "next-next"),
        ];

        let before_rollover = build_snapshot_from_warmed(
            Utc.timestamp_opt(start + 295, 0).unwrap(),
            &config(),
            &warmed,
            vec![],
            vec![],
        )
        .unwrap();
        let after_rollover = build_snapshot_from_warmed(
            Utc.timestamp_opt(start + 305, 0).unwrap(),
            &config(),
            &warmed,
            vec![],
            vec![],
        )
        .unwrap();

        assert_eq!(before_rollover.current[0].up.token_id, "current-up");
        assert_eq!(before_rollover.next[0].up.token_id, "next-up");
        assert_eq!(after_rollover.current[0].up.token_id, "next-up");
        assert_eq!(after_rollover.next[0].up.token_id, "next-next-up");
        assert!(
            !after_rollover
                .health_flags
                .contains(&"next_contract_not_warmed:BTC".to_owned())
        );
    }

    #[test]
    fn missing_next_contract_before_cutoff_adds_health_flag() {
        let start = 1_780_302_400;
        let warmed = vec![warmed("BTC", start, "current")];

        let snapshot = build_snapshot_from_warmed(
            Utc.timestamp_opt(start + 295, 0).unwrap(),
            &config(),
            &warmed,
            vec![],
            vec![],
        )
        .unwrap();

        assert_eq!(snapshot.current.len(), 1);
        assert!(snapshot.next.is_empty());
        assert!(
            snapshot
                .health_flags
                .contains(&"next_contract_not_warmed:BTC".to_owned())
        );
    }

    #[tokio::test]
    async fn runtime_snapshot_grades_freshness_at_state_capture_time() {
        let observed = Utc::now();
        let stale_caller_now = observed - Duration::seconds(5);
        let start = observed.timestamp() - 60;
        let latest_prices = crate::prices::LatestPrices::default();
        latest_prices
            .update(chainlink("BTC", observed.timestamp_millis()))
            .await;
        let book_state = LiveBookState::default();
        book_state
            .upsert_book(book("BTC", "UP", "current-up", observed.timestamp_millis()))
            .await;
        book_state
            .upsert_book(book(
                "BTC",
                "DOWN",
                "current-down",
                observed.timestamp_millis(),
            ))
            .await;
        let runtime = StateManagerRuntime {
            config: config(),
            latest_prices,
            orderbook_streams: clob_ws::BestBidAskStreamManager::new(book_state.clone()),
            book_state,
            warmed: std::sync::Arc::new(std::sync::RwLock::new(vec![warmed(
                "BTC", start, "current",
            )])),
            token_ids: vec![],
            last_refresh: observed,
            chainlink_streams: prices::ChainlinkStreamManager::inactive_for_tests(vec![
                "btc/usd".to_owned(),
            ]),
            hot_decision_worker: None,
            hot_decision_telemetry: None,
        };

        let snapshot = runtime.snapshot(stale_caller_now).await.unwrap();

        assert!(
            !snapshot
                .health_flags
                .iter()
                .any(|flag| flag.starts_with("chainlink_price_stale:")),
            "unexpected health flags: {:?}",
            snapshot.health_flags
        );
        assert!(
            !snapshot
                .health_flags
                .iter()
                .any(|flag| flag.starts_with("orderbook_stale:")),
            "unexpected health flags: {:?}",
            snapshot.health_flags
        );
    }

    #[test]
    fn subscriptions_cover_every_warmed_token() {
        let start = 1_780_302_400;
        let warmed = vec![warmed("BTC", start, "btc"), warmed("ETH", start, "eth")];

        let subscriptions = subscriptions_from_warmed_contracts(&warmed);

        assert_eq!(subscriptions.len(), 4);
        assert_eq!(subscriptions[0].source_key, "polymarket_clob_market_ws");
        assert_eq!(subscriptions[0].channel, "best_bid_ask");
        assert_eq!(subscriptions[0].asset, "BTC");
        assert_eq!(subscriptions[0].token_id, "btc-down");
        assert_eq!(subscriptions[3].asset, "ETH");
        assert_eq!(subscriptions[3].token_id, "eth-up");
    }

    #[test]
    fn snapshot_reports_feed_freshness_and_missing_orderbooks() {
        let start = 1_780_302_400;
        let now = Utc.timestamp_opt(start + 10, 0).unwrap();
        let config = StateManagerConfig {
            assets: vec!["BTC".to_owned(), "ETH".to_owned()],
            ..config()
        };
        let warmed = vec![warmed("BTC", start, "btc"), warmed("ETH", start, "eth")];

        let snapshot = build_snapshot_from_warmed(
            now,
            &config,
            &warmed,
            vec![chainlink("BTC", now.timestamp_millis())],
            vec![
                book("BTC", "UP", "btc-up", now.timestamp_millis()),
                book("BTC", "DOWN", "btc-down", now.timestamp_millis() - 3_000),
            ],
        )
        .unwrap();

        assert!(
            snapshot
                .freshness
                .iter()
                .any(|row| row.source_key == "polymarket_rtds_chainlink"
                    && row.symbol == "BTC/USD"
                    && !row.stale)
        );
        assert!(
            snapshot
                .freshness
                .iter()
                .any(|row| row.source_key == "polymarket_rust_sdk"
                    && row.symbol == "btc-down"
                    && row.stale)
        );
        assert!(
            snapshot
                .health_flags
                .contains(&"chainlink_price_missing:ETH/USD".to_owned())
        );
        assert!(
            snapshot
                .health_flags
                .contains(&"orderbook_missing:eth-down".to_owned())
        );
    }

    #[test]
    fn warmed_token_ids_include_only_live_up_down_tokens() {
        let start = 1_780_302_400;
        let warmed = vec![warmed("BTC", start, "btc")];

        let token_ids = warmed_token_ids(&warmed).collect::<Vec<_>>();

        assert_eq!(token_ids, vec!["btc-up", "btc-down"]);
    }

    #[test]
    fn hot_event_price_assets_include_only_triggered_asset() {
        let start = 1_780_302_400;
        let warmed = vec![warmed("BTC", start, "btc"), warmed("ETH", start, "eth")];
        let ts = Utc.timestamp_opt(start + 10, 0).unwrap();

        let chainlink_assets = hot_event_price_assets(
            &HotPathEvent::ChainlinkPrice {
                symbol: "eth/usd".to_owned(),
                event_ts: ts,
                observed_ts: ts,
            },
            &warmed,
        );
        let orderbook_assets = hot_event_price_assets(
            &HotPathEvent::OrderBookTopOfBook {
                token_id: "btc-up".to_owned(),
                event_ts: ts,
                observed_ts: ts,
            },
            &warmed,
        );
        let unknown_assets = hot_event_price_assets(
            &HotPathEvent::OrderBookTopOfBook {
                token_id: "unknown-token".to_owned(),
                event_ts: ts,
                observed_ts: ts,
            },
            &warmed,
        );

        assert_eq!(chainlink_assets, vec!["ETH"]);
        assert_eq!(orderbook_assets, vec!["BTC"]);
        assert!(unknown_assets.is_empty());
    }

    #[test]
    fn hot_event_orderbook_token_ids_include_only_impacted_current_tokens() {
        let start = 1_780_302_400;
        let warmed = vec![
            warmed("BTC", start, "btc-current"),
            warmed("BTC", start + 300, "btc-next"),
            warmed("ETH", start, "eth-current"),
        ];
        let ts = Utc.timestamp_opt(start + 10, 0).unwrap();

        let chainlink_event = HotPathEvent::ChainlinkPrice {
            symbol: "btc/usd".to_owned(),
            event_ts: ts,
            observed_ts: ts,
        };
        let orderbook_event = HotPathEvent::OrderBookTopOfBook {
            token_id: "btc-current-up".to_owned(),
            event_ts: ts,
            observed_ts: ts,
        };
        let unknown_event = HotPathEvent::OrderBookTopOfBook {
            token_id: "unknown-token".to_owned(),
            event_ts: ts,
            observed_ts: ts,
        };

        let chainlink_tokens = hot_event_orderbook_token_ids(&chainlink_event, &warmed, ts);
        let orderbook_tokens = hot_event_orderbook_token_ids(&orderbook_event, &warmed, ts);
        let unknown_tokens = hot_event_orderbook_token_ids(&unknown_event, &warmed, ts);

        assert_eq!(chainlink_tokens, vec!["btc-current-up", "btc-current-down"]);
        assert_eq!(orderbook_tokens, vec!["btc-current-up"]);
        assert!(unknown_tokens.is_empty());

        let rollover_ts = Utc.timestamp_opt(start + 300, 0).unwrap();
        let early_worker_now = rollover_ts - Duration::milliseconds(1);
        let rollover_event = HotPathEvent::OrderBookTopOfBook {
            token_id: "btc-next-up".to_owned(),
            event_ts: rollover_ts,
            observed_ts: rollover_ts,
        };
        let observed_asof_tokens =
            hot_event_orderbook_token_ids(&rollover_event, &warmed, early_worker_now);

        assert_eq!(observed_asof_tokens, vec!["btc-next-up"]);
    }
}
