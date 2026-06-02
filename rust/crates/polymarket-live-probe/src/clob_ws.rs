#![allow(dead_code)]

use anyhow::{Context, Result, anyhow, bail};
use chrono::{DateTime, TimeZone, Utc};
use futures::StreamExt as _;
use polymarket_client_sdk_v2::clob::ws::subscription::ChannelType;
use polymarket_client_sdk_v2::clob::ws::{BestBidAsk, Client as ClobWsClient, WsMessage};
use polymarket_client_sdk_v2::types::U256;
use polymarket_runtime_types::{BookLevel, NormalizedOrderBook, OrderBookMeta};
use rust_decimal::Decimal;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::str::FromStr;
use std::sync::{Arc, RwLock};
use std::time::Duration;
use tokio::task::JoinHandle;

use crate::book_state::LiveBookState;
use crate::report::WebSocketStatus;

#[derive(Debug, Clone, PartialEq)]
pub enum ClobMarketEvent {
    Book(Box<NormalizedOrderBook>),
    TopOfBook {
        contract_id: String,
        token_id: String,
        best_bid: Decimal,
        best_ask: Decimal,
        spread: Decimal,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    },
    PriceChange {
        contract_id: String,
        token_id: String,
        price: Decimal,
        side: String,
        size: Option<Decimal>,
        best_bid: Option<Decimal>,
        best_ask: Option<Decimal>,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    },
    LastTradePrice {
        contract_id: String,
        token_id: String,
        price: Decimal,
        side: Option<String>,
        size: Option<Decimal>,
        fee_rate_bps: Option<Decimal>,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    },
    TickSizeChange {
        contract_id: String,
        token_id: String,
        old_tick_size: Decimal,
        new_tick_size: Decimal,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    },
    NewMarket {
        market_id: String,
        contract_id: String,
        slug: String,
        question: String,
        token_ids: Vec<String>,
        outcomes: Vec<String>,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    },
    MarketResolved {
        market_id: String,
        contract_id: String,
        slug: Option<String>,
        token_ids: Vec<String>,
        outcomes: Vec<String>,
        winning_token_id: String,
        winning_outcome: String,
        event_ts: DateTime<Utc>,
        observed_ts: DateTime<Utc>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BestBidAskSubscriptionDelta {
    pub desired: Vec<String>,
    pub to_subscribe: Vec<String>,
    pub to_unsubscribe: Vec<String>,
}

struct BestBidAskTokenStream {
    asset_id: U256,
    task: JoinHandle<Result<()>>,
}

#[derive(Debug, Default)]
struct WebSocketTelemetry {
    reconnect_count: u64,
    last_event_observed_ts: Option<DateTime<Utc>>,
    stream_error_count: u64,
}

pub struct BestBidAskStreamManager {
    client: ClobWsClient,
    book_state: LiveBookState,
    token_streams: BTreeMap<String, BestBidAskTokenStream>,
    telemetry: Arc<RwLock<WebSocketTelemetry>>,
    connection_monitor_task: JoinHandle<()>,
}

impl BestBidAskStreamManager {
    pub fn new(book_state: LiveBookState) -> Self {
        let client = ClobWsClient::default();
        let telemetry = Arc::new(RwLock::new(WebSocketTelemetry::default()));
        Self {
            connection_monitor_task: tokio::spawn(monitor_market_connection_state(
                client.clone(),
                telemetry.clone(),
            )),
            client,
            book_state,
            token_streams: BTreeMap::new(),
            telemetry,
        }
    }

    pub fn active_token_ids(&self) -> BTreeSet<String> {
        self.token_streams.keys().cloned().collect()
    }

    pub fn update_tokens(&mut self, token_ids: &[String]) -> Result<BestBidAskSubscriptionDelta> {
        self.prune_finished_streams();
        let current = self.active_token_ids();
        let delta = plan_best_bid_ask_subscriptions(&current, token_ids)?;

        for token_id in &delta.to_unsubscribe {
            if let Some(token_stream) = self.token_streams.remove(token_id) {
                self.client
                    .unsubscribe_orderbook(&[token_stream.asset_id])?;
                token_stream.task.abort();
            }
        }

        for token_id in &delta.to_subscribe {
            let asset_id = parse_asset_id(token_id)?;
            let stream = self.client.subscribe_best_bid_ask(vec![asset_id])?;
            let book_state = self.book_state.clone();
            let telemetry = self.telemetry.clone();
            let token_label = token_id.clone();
            let task = tokio::spawn(async move {
                let mut stream = Box::pin(stream);
                while let Some(update_result) = stream.next().await {
                    let update = match update_result {
                        Ok(update) => update,
                        Err(error) => {
                            record_market_stream_error(&telemetry);
                            return Err(error.into());
                        }
                    };
                    let observed_ts = Utc::now();
                    record_market_event(&telemetry, observed_ts);
                    let applied =
                        apply_best_bid_ask_update(update, &book_state, observed_ts).await?;
                    if !applied {
                        tracing::warn!("received CLOB best_bid_ask for unseeded orderbook");
                    }
                }

                record_market_stream_error(&telemetry);
                Err(anyhow!("CLOB best_bid_ask stream ended for {token_label}"))
            });
            self.token_streams
                .insert(token_id.clone(), BestBidAskTokenStream { asset_id, task });
        }

        Ok(delta)
    }

    pub fn websocket_status(&self, now: DateTime<Utc>) -> WebSocketStatus {
        let telemetry = self.telemetry.read().expect("websocket telemetry poisoned");
        let last_event_age_ms = telemetry
            .last_event_observed_ts
            .map(|observed_ts| now.signed_duration_since(observed_ts).num_milliseconds());
        let ended_stream_count = self
            .token_streams
            .values()
            .filter(|stream| stream.task.is_finished())
            .count();
        WebSocketStatus {
            source_key: "polymarket_clob_market_ws".to_owned(),
            channel: "market".to_owned(),
            connection_state: format!("{:?}", self.client.connection_state(ChannelType::Market)),
            reconnect_count: telemetry.reconnect_count,
            subscription_count: self.client.subscription_count(),
            active_token_count: self.token_streams.len(),
            ended_stream_count,
            stream_error_count: telemetry.stream_error_count,
            last_event_age_ms,
        }
    }

    pub fn shutdown(&mut self) {
        for (_, token_stream) in std::mem::take(&mut self.token_streams) {
            token_stream.task.abort();
        }
        self.connection_monitor_task.abort();
    }

    fn prune_finished_streams(&mut self) {
        let finished = self
            .token_streams
            .iter()
            .filter(|(_, token_stream)| token_stream.task.is_finished())
            .map(|(token_id, _)| token_id.clone())
            .collect::<Vec<_>>();
        for token_id in finished {
            if let Some(token_stream) = self.token_streams.remove(&token_id) {
                let _ = self.client.unsubscribe_orderbook(&[token_stream.asset_id]);
                token_stream.task.abort();
            }
        }
    }
}

async fn monitor_market_connection_state(
    client: ClobWsClient,
    telemetry: Arc<RwLock<WebSocketTelemetry>>,
) {
    let mut was_connected = false;
    loop {
        let connected = client.connection_state(ChannelType::Market).is_connected();
        if was_connected && !connected {
            let mut telemetry = telemetry.write().expect("websocket telemetry poisoned");
            telemetry.reconnect_count = telemetry.reconnect_count.saturating_add(1);
        }
        was_connected = connected;
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
}

fn record_market_event(telemetry: &Arc<RwLock<WebSocketTelemetry>>, observed_ts: DateTime<Utc>) {
    let mut telemetry = telemetry.write().expect("websocket telemetry poisoned");
    telemetry.last_event_observed_ts = Some(observed_ts);
}

fn record_market_stream_error(telemetry: &Arc<RwLock<WebSocketTelemetry>>) {
    let mut telemetry = telemetry.write().expect("websocket telemetry poisoned");
    telemetry.stream_error_count = telemetry.stream_error_count.saturating_add(1);
}

pub fn plan_best_bid_ask_subscriptions(
    current: &BTreeSet<String>,
    desired: &[String],
) -> Result<BestBidAskSubscriptionDelta> {
    let mut desired_set = BTreeSet::new();
    for token_id in desired {
        let token_id = token_id.trim();
        if token_id.is_empty() {
            continue;
        }
        parse_asset_id(token_id)?;
        desired_set.insert(token_id.to_owned());
    }

    Ok(BestBidAskSubscriptionDelta {
        desired: desired_set.iter().cloned().collect(),
        to_subscribe: desired_set.difference(current).cloned().collect(),
        to_unsubscribe: current.difference(&desired_set).cloned().collect(),
    })
}

pub fn market_subscription_payload(token_ids: &[String]) -> Value {
    let mut token_ids = token_ids
        .iter()
        .map(|token_id| token_id.trim().to_owned())
        .filter(|token_id| !token_id.is_empty())
        .collect::<Vec<_>>();
    token_ids.sort();
    token_ids.dedup();
    serde_json::json!({
        "type": "market",
        "assets_ids": token_ids,
        "operation": "subscribe",
        "initial_dump": true,
        "custom_feature_enabled": true
    })
}

pub fn parse_asset_ids_for_subscription(token_ids: &[String]) -> Result<Vec<U256>> {
    let mut asset_ids = token_ids
        .iter()
        .map(|token_id| token_id.trim())
        .filter(|token_id| !token_id.is_empty())
        .map(|token_id| {
            U256::from_str(token_id)
                .with_context(|| format!("invalid CLOB asset id for websocket: {token_id}"))
        })
        .collect::<Result<Vec<_>>>()?;
    asset_ids.sort();
    asset_ids.dedup();
    Ok(asset_ids)
}

fn parse_asset_id(token_id: &str) -> Result<U256> {
    U256::from_str(token_id)
        .with_context(|| format!("invalid CLOB asset id for websocket: {token_id}"))
}

pub async fn run_best_bid_ask_stream(
    token_ids: Vec<String>,
    book_state: LiveBookState,
) -> Result<()> {
    let asset_ids = parse_asset_ids_for_subscription(&token_ids)?;
    if asset_ids.is_empty() {
        bail!("cannot subscribe to CLOB best_bid_ask stream without token ids");
    }

    let client = ClobWsClient::default();
    let stream = client.subscribe_best_bid_ask(asset_ids)?;
    let mut stream = Box::pin(stream);

    while let Some(update_result) = stream.next().await {
        let update = update_result?;
        let observed_ts = Utc::now();
        let applied = apply_best_bid_ask_update(update, &book_state, observed_ts).await?;
        if !applied {
            tracing::warn!("received CLOB best_bid_ask for unseeded orderbook");
        }
    }

    Err(anyhow!("CLOB best_bid_ask stream ended"))
}

pub async fn apply_best_bid_ask_update(
    update: BestBidAsk,
    book_state: &LiveBookState,
    observed_ts: DateTime<Utc>,
) -> Result<bool> {
    match top_of_book_event_from_best_bid_ask(update, observed_ts)? {
        ClobMarketEvent::TopOfBook {
            token_id,
            best_bid,
            best_ask,
            spread,
            event_ts,
            observed_ts,
            ..
        } => {
            book_state
                .apply_top_of_book(&token_id, best_bid, best_ask, spread, event_ts, observed_ts)
                .await
        }
        _ => Ok(false),
    }
}

pub fn parse_clob_market_events(
    message: &Value,
    observed_ts: DateTime<Utc>,
) -> Result<Vec<ClobMarketEvent>> {
    let event_type = message
        .get("event_type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !matches!(
        event_type,
        "book"
            | "best_bid_ask"
            | "price_change"
            | "last_trade_price"
            | "tick_size_change"
            | "new_market"
            | "market_resolved"
    ) {
        return Ok(vec![]);
    }

    let ws_message: WsMessage = serde_json::from_value(message.clone())?;
    match ws_message {
        WsMessage::Book(book) => Ok(vec![ClobMarketEvent::Book(Box::new(normalized_book(
            book,
            observed_ts,
        )?))]),
        WsMessage::BestBidAsk(top) => Ok(vec![ClobMarketEvent::TopOfBook {
            contract_id: top.market.to_string(),
            token_id: top.asset_id.to_string(),
            best_bid: top.best_bid,
            best_ask: top.best_ask,
            spread: top.spread,
            event_ts: timestamp_millis(top.timestamp)?,
            observed_ts,
        }]),
        WsMessage::PriceChange(price_change) => {
            let event_ts = timestamp_millis(price_change.timestamp)?;
            let contract_id = price_change.market.to_string();
            let events = price_change
                .price_changes
                .into_iter()
                .map(|change| ClobMarketEvent::PriceChange {
                    contract_id: contract_id.clone(),
                    token_id: change.asset_id.to_string(),
                    price: change.price,
                    side: format!("{:?}", change.side).to_ascii_uppercase(),
                    size: change.size,
                    best_bid: change.best_bid,
                    best_ask: change.best_ask,
                    event_ts,
                    observed_ts,
                })
                .collect();
            Ok(events)
        }
        WsMessage::LastTradePrice(last_trade) => Ok(vec![ClobMarketEvent::LastTradePrice {
            contract_id: last_trade.market.to_string(),
            token_id: last_trade.asset_id.to_string(),
            price: last_trade.price,
            side: last_trade
                .side
                .map(|side| format!("{side:?}").to_ascii_uppercase()),
            size: last_trade.size,
            fee_rate_bps: last_trade.fee_rate_bps,
            event_ts: timestamp_millis(last_trade.timestamp)?,
            observed_ts,
        }]),
        WsMessage::TickSizeChange(tick_size) => Ok(vec![ClobMarketEvent::TickSizeChange {
            contract_id: tick_size.market.to_string(),
            token_id: tick_size.asset_id.to_string(),
            old_tick_size: tick_size.old_tick_size,
            new_tick_size: tick_size.new_tick_size,
            event_ts: timestamp_millis(tick_size.timestamp)?,
            observed_ts,
        }]),
        WsMessage::NewMarket(new_market) => Ok(vec![ClobMarketEvent::NewMarket {
            market_id: new_market.id,
            contract_id: new_market.market.to_string(),
            slug: new_market.slug,
            question: new_market.question,
            token_ids: new_market
                .asset_ids
                .into_iter()
                .map(|asset_id| asset_id.to_string())
                .collect(),
            outcomes: new_market.outcomes,
            event_ts: timestamp_millis(new_market.timestamp)?,
            observed_ts,
        }]),
        WsMessage::MarketResolved(resolved) => Ok(vec![ClobMarketEvent::MarketResolved {
            market_id: resolved.id,
            contract_id: resolved.market.to_string(),
            slug: resolved.slug,
            token_ids: resolved
                .asset_ids
                .into_iter()
                .map(|asset_id| asset_id.to_string())
                .collect(),
            outcomes: resolved.outcomes,
            winning_token_id: resolved.winning_asset_id.to_string(),
            winning_outcome: resolved.winning_outcome,
            event_ts: timestamp_millis(resolved.timestamp)?,
            observed_ts,
        }]),
        _ => Ok(vec![]),
    }
}

pub fn top_of_book_event_from_best_bid_ask(
    update: BestBidAsk,
    observed_ts: DateTime<Utc>,
) -> Result<ClobMarketEvent> {
    Ok(ClobMarketEvent::TopOfBook {
        contract_id: update.market.to_string(),
        token_id: update.asset_id.to_string(),
        best_bid: update.best_bid,
        best_ask: update.best_ask,
        spread: update.spread,
        event_ts: timestamp_millis(update.timestamp)?,
        observed_ts,
    })
}

fn normalized_book(
    book: polymarket_client_sdk_v2::clob::ws::BookUpdate,
    observed_ts: DateTime<Utc>,
) -> Result<NormalizedOrderBook> {
    let event_ts = timestamp_millis(book.timestamp)?;
    let bids = book
        .bids
        .into_iter()
        .map(|level| BookLevel {
            price: level.price,
            size: level.size,
        })
        .collect::<Vec<_>>();
    let asks = book
        .asks
        .into_iter()
        .map(|level| BookLevel {
            price: level.price,
            size: level.size,
        })
        .collect::<Vec<_>>();
    let mut normalized = NormalizedOrderBook::from_levels(
        OrderBookMeta {
            market_slug: String::new(),
            contract_id: book.market.to_string(),
            token_id: book.asset_id.to_string(),
            asset: String::new(),
            side: String::new(),
            event_ts,
            observed_ts,
        },
        bids,
        asks,
    );
    normalized.source_key = "polymarket_market_ws".to_owned();
    Ok(normalized)
}

fn timestamp_millis(timestamp: i64) -> Result<DateTime<Utc>> {
    Utc.timestamp_millis_opt(timestamp)
        .single()
        .ok_or_else(|| anyhow!("invalid CLOB websocket timestamp: {timestamp}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{DateTime, Utc};
    use rust_decimal::Decimal;

    fn observed_ts() -> DateTime<Utc> {
        "2026-06-01T20:00:01Z".parse::<DateTime<Utc>>().unwrap()
    }

    #[test]
    fn market_subscription_payload_sorts_and_deduplicates_tokens() {
        let payload =
            market_subscription_payload(&["222".to_owned(), "111".to_owned(), "222".to_owned()]);

        assert_eq!(
            payload,
            serde_json::json!({
                "type": "market",
                "assets_ids": ["111", "222"],
                "operation": "subscribe",
                "initial_dump": true,
                "custom_feature_enabled": true
            })
        );
    }

    #[test]
    fn parses_book_event_into_normalized_orderbook() {
        let message = serde_json::json!({
            "event_type": "book",
            "asset_id": "111",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000001",
            "timestamp": "1780352939000",
            "bids": [{"price": "0.49", "size": "20"}],
            "asks": [{"price": "0.51", "size": "30"}]
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert_eq!(events.len(), 1);
        match &events[0] {
            ClobMarketEvent::Book(book) => {
                assert_eq!(book.source_key, "polymarket_market_ws");
                assert_eq!(
                    book.contract_id,
                    "0x0000000000000000000000000000000000000000000000000000000000000001"
                );
                assert_eq!(book.token_id, "111");
                assert_eq!(book.best_bid, Some(Decimal::new(49, 2)));
                assert_eq!(book.best_ask, Some(Decimal::new(51, 2)));
                assert_eq!(book.spread, Some(Decimal::new(2, 2)));
                assert_eq!(book.event_ts.timestamp_millis(), 1780352939000);
            }
            other => panic!("expected book event, got {other:?}"),
        }
    }

    #[test]
    fn parses_best_bid_ask_event() {
        let message = serde_json::json!({
            "event_type": "best_bid_ask",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000002",
            "asset_id": "222",
            "best_bid": "0.73",
            "best_ask": "0.77",
            "spread": "0.04",
            "timestamp": "1780352939001"
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert_eq!(events.len(), 1);
        match &events[0] {
            ClobMarketEvent::TopOfBook {
                token_id,
                best_bid,
                best_ask,
                observed_ts: event_observed_ts,
                ..
            } => {
                assert_eq!(token_id, "222");
                assert_eq!(*best_bid, Decimal::new(73, 2));
                assert_eq!(*best_ask, Decimal::new(77, 2));
                assert_eq!(*event_observed_ts, observed_ts());
            }
            other => panic!("expected top-of-book event, got {other:?}"),
        }
    }

    #[test]
    fn parses_price_change_batch_events() {
        let message = serde_json::json!({
            "event_type": "price_change",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000003",
            "timestamp": "1780352939002",
            "price_changes": [{
                "asset_id": "333",
                "price": "0.52",
                "size": "10",
                "side": "BUY",
                "best_bid": "0.51",
                "best_ask": "0.53"
            }]
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert_eq!(events.len(), 1);
        match &events[0] {
            ClobMarketEvent::PriceChange {
                token_id,
                price,
                side,
                size,
                observed_ts: event_observed_ts,
                ..
            } => {
                assert_eq!(token_id, "333");
                assert_eq!(*price, Decimal::new(52, 2));
                assert_eq!(side, "BUY");
                assert_eq!(*size, Some(Decimal::new(10, 0)));
                assert_eq!(*event_observed_ts, observed_ts());
            }
            other => panic!("expected price-change event, got {other:?}"),
        }
    }

    #[test]
    fn parses_last_trade_price_event() {
        let message = serde_json::json!({
            "event_type": "last_trade_price",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000004",
            "asset_id": "444",
            "price": "0.456",
            "side": "BUY",
            "size": "219.217767",
            "fee_rate_bps": "0",
            "timestamp": "1780352939003"
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert_eq!(events.len(), 1);
        match &events[0] {
            ClobMarketEvent::LastTradePrice {
                contract_id,
                token_id,
                price,
                side,
                size,
                fee_rate_bps,
                event_ts,
                observed_ts: event_observed_ts,
            } => {
                assert_eq!(
                    contract_id,
                    "0x0000000000000000000000000000000000000000000000000000000000000004"
                );
                assert_eq!(token_id, "444");
                assert_eq!(*price, Decimal::from_str("0.456").unwrap());
                assert_eq!(side.as_deref(), Some("BUY"));
                assert_eq!(*size, Some(Decimal::from_str("219.217767").unwrap()));
                assert_eq!(*fee_rate_bps, Some(Decimal::ZERO));
                assert_eq!(event_ts.timestamp_millis(), 1780352939003);
                assert_eq!(*event_observed_ts, observed_ts());
            }
            other => panic!("expected last-trade-price event, got {other:?}"),
        }
    }

    #[test]
    fn parses_tick_size_change_event() {
        let message = serde_json::json!({
            "event_type": "tick_size_change",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000005",
            "asset_id": "555",
            "old_tick_size": "0.01",
            "new_tick_size": "0.001",
            "timestamp": "1780352939004"
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert_eq!(events.len(), 1);
        match &events[0] {
            ClobMarketEvent::TickSizeChange {
                contract_id,
                token_id,
                old_tick_size,
                new_tick_size,
                event_ts,
                observed_ts: event_observed_ts,
            } => {
                assert_eq!(
                    contract_id,
                    "0x0000000000000000000000000000000000000000000000000000000000000005"
                );
                assert_eq!(token_id, "555");
                assert_eq!(*old_tick_size, Decimal::new(1, 2));
                assert_eq!(*new_tick_size, Decimal::new(1, 3));
                assert_eq!(event_ts.timestamp_millis(), 1780352939004);
                assert_eq!(*event_observed_ts, observed_ts());
            }
            other => panic!("expected tick-size-change event, got {other:?}"),
        }
    }

    #[test]
    fn parses_new_market_event() {
        let message = serde_json::json!({
            "event_type": "new_market",
            "id": "1031769",
            "question": "BTC Up or Down - 5m",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000006",
            "slug": "btc-updown-5m-1780352700",
            "description": "Resolves using the listed BTC/USD source.",
            "assets_ids": ["666", "667"],
            "outcomes": ["Up", "Down"],
            "timestamp": "1780352939005"
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert_eq!(events.len(), 1);
        match &events[0] {
            ClobMarketEvent::NewMarket {
                market_id,
                contract_id,
                slug,
                question,
                token_ids,
                outcomes,
                event_ts,
                observed_ts: event_observed_ts,
            } => {
                assert_eq!(market_id, "1031769");
                assert_eq!(
                    contract_id,
                    "0x0000000000000000000000000000000000000000000000000000000000000006"
                );
                assert_eq!(slug, "btc-updown-5m-1780352700");
                assert_eq!(question, "BTC Up or Down - 5m");
                assert_eq!(token_ids, &vec!["666".to_owned(), "667".to_owned()]);
                assert_eq!(outcomes, &vec!["Up".to_owned(), "Down".to_owned()]);
                assert_eq!(event_ts.timestamp_millis(), 1780352939005);
                assert_eq!(*event_observed_ts, observed_ts());
            }
            other => panic!("expected new-market event, got {other:?}"),
        }
    }

    #[test]
    fn parses_market_resolved_event() {
        let message = serde_json::json!({
            "event_type": "market_resolved",
            "id": "1031770",
            "question": "ETH Up or Down - 5m",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000007",
            "slug": "eth-updown-5m-1780352700",
            "description": "Resolves using the listed ETH/USD source.",
            "assets_ids": ["777", "778"],
            "outcomes": ["Up", "Down"],
            "winning_asset_id": "777",
            "winning_outcome": "Up",
            "timestamp": "1780352939006"
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert_eq!(events.len(), 1);
        match &events[0] {
            ClobMarketEvent::MarketResolved {
                market_id,
                contract_id,
                slug,
                token_ids,
                outcomes,
                winning_token_id,
                winning_outcome,
                event_ts,
                observed_ts: event_observed_ts,
            } => {
                assert_eq!(market_id, "1031770");
                assert_eq!(
                    contract_id,
                    "0x0000000000000000000000000000000000000000000000000000000000000007"
                );
                assert_eq!(slug.as_deref(), Some("eth-updown-5m-1780352700"));
                assert_eq!(token_ids, &vec!["777".to_owned(), "778".to_owned()]);
                assert_eq!(outcomes, &vec!["Up".to_owned(), "Down".to_owned()]);
                assert_eq!(winning_token_id, "777");
                assert_eq!(winning_outcome, "Up");
                assert_eq!(event_ts.timestamp_millis(), 1780352939006);
                assert_eq!(*event_observed_ts, observed_ts());
            }
            other => panic!("expected market-resolved event, got {other:?}"),
        }
    }

    #[test]
    fn unknown_event_type_is_ignored() {
        let message = serde_json::json!({
            "event_type": "comment",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000008"
        });

        let events = parse_clob_market_events(&message, observed_ts()).unwrap();

        assert!(events.is_empty());
    }

    #[test]
    fn maps_typed_best_bid_ask_without_json_roundtrip() {
        let message = serde_json::json!({
            "event_type": "best_bid_ask",
            "market": "0x0000000000000000000000000000000000000000000000000000000000000002",
            "asset_id": "222",
            "best_bid": "0.73",
            "best_ask": "0.77",
            "spread": "0.04",
            "timestamp": "1780352939001"
        });
        let update = match serde_json::from_value::<WsMessage>(message).unwrap() {
            WsMessage::BestBidAsk(update) => update,
            _ => panic!("expected BestBidAsk"),
        };

        let event = top_of_book_event_from_best_bid_ask(update, observed_ts()).unwrap();

        match event {
            ClobMarketEvent::TopOfBook {
                token_id,
                best_bid,
                best_ask,
                ..
            } => {
                assert_eq!(token_id, "222");
                assert_eq!(best_bid, Decimal::new(73, 2));
                assert_eq!(best_ask, Decimal::new(77, 2));
            }
            other => panic!("expected top-of-book event, got {other:?}"),
        }
    }

    #[test]
    fn parses_unique_u256_asset_ids_for_sdk_subscription() {
        let ids = parse_asset_ids_for_subscription(&[
            "222".to_owned(),
            "111".to_owned(),
            "222".to_owned(),
        ])
        .unwrap();

        assert_eq!(ids.len(), 2);
        assert_eq!(ids[0].to_string(), "111");
        assert_eq!(ids[1].to_string(), "222");
    }

    #[test]
    fn rejects_invalid_asset_id_for_sdk_subscription() {
        let error = parse_asset_ids_for_subscription(&["not-a-number".to_owned()])
            .expect_err("invalid asset id should fail");

        assert!(error.to_string().contains("invalid CLOB asset id"));
    }

    #[test]
    fn plans_subscription_delta_without_reconnecting_existing_tokens() {
        let current = ["222".to_owned(), "333".to_owned()].into_iter().collect();
        let desired = vec![
            "333".to_owned(),
            "111".to_owned(),
            "333".to_owned(),
            "444".to_owned(),
        ];

        let delta = plan_best_bid_ask_subscriptions(&current, &desired).unwrap();

        assert_eq!(delta.desired, vec!["111", "333", "444"]);
        assert_eq!(delta.to_subscribe, vec!["111", "444"]);
        assert_eq!(delta.to_unsubscribe, vec!["222"]);
    }
}
