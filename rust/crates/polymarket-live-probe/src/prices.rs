use anyhow::{Result, anyhow};
use chrono::{DateTime, TimeZone, Utc};
use futures::StreamExt as _;
use polymarket_client_sdk_v2::rtds::{Client as RtdsClient, RtdsMessage, Subscription};
use polymarket_runtime_types::{FeedFreshness, NormalizedPriceTick, PriceDisagreement};
use rust_decimal::Decimal;
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::str::FromStr;
use std::sync::{Arc, RwLock};
use std::time::Duration;
use tokio::task::JoinHandle;
use tokio::time::timeout;

use crate::raw_event_journal::{RawEventRecord, RawEventSink};
use crate::report::WebSocketStatus;

pub const KRAKEN_TICKER_URL: &str = "https://api.kraken.com/0/public/Ticker";
const DEFAULT_PRICE_HISTORY_LIMIT: usize = 4096;

#[derive(Debug, Deserialize)]
struct KrakenTickerResponse {
    error: Vec<String>,
    result: HashMap<String, KrakenTickerPair>,
}

#[derive(Debug, Deserialize)]
struct KrakenTickerPair {
    c: [String; 2],
}

#[derive(Debug)]
pub struct ChainlinkFetchResult {
    pub tick: NormalizedPriceTick,
    pub cache_hit: bool,
}

#[derive(Debug, Clone)]
pub struct LatestPrices {
    inner: Arc<RwLock<LatestPricesInner>>,
    history_limit: usize,
}

#[derive(Debug, Default)]
struct LatestPricesInner {
    latest: HashMap<String, NormalizedPriceTick>,
    history: Vec<NormalizedPriceTick>,
}

impl LatestPrices {
    pub fn with_history_limit(history_limit: usize) -> Self {
        Self {
            inner: Arc::new(RwLock::new(LatestPricesInner::default())),
            history_limit: history_limit.max(1),
        }
    }

    pub async fn update(&self, tick: NormalizedPriceTick) {
        let key = normalize_price_symbol(&tick.symbol);
        let mut inner = self.inner.write().expect("latest price lock poisoned");
        inner.latest.insert(key, tick.clone());
        inner.history.push(tick);
        inner.history.sort_by_key(|tick| {
            (
                normalize_price_symbol(&tick.symbol),
                tick.event_ts,
                tick.observed_ts,
            )
        });
        let overflow = inner.history.len().saturating_sub(self.history_limit);
        if overflow > 0 {
            inner.history.drain(0..overflow);
        }
    }

    #[allow(dead_code)]
    pub async fn get(&self, symbol: &str) -> Option<NormalizedPriceTick> {
        let key = normalize_price_symbol(symbol);
        let inner = self.inner.read().expect("latest price lock poisoned");
        inner.latest.get(&key).cloned()
    }

    #[allow(dead_code)]
    pub async fn snapshot(&self) -> Vec<NormalizedPriceTick> {
        let inner = self.inner.read().expect("latest price lock poisoned");
        let mut ticks = inner.latest.values().cloned().collect::<Vec<_>>();
        ticks.sort_by(|left, right| left.symbol.cmp(&right.symbol));
        ticks
    }

    pub async fn history_snapshot(&self) -> Vec<NormalizedPriceTick> {
        let inner = self.inner.read().expect("latest price lock poisoned");
        inner.history.clone()
    }

    pub async fn latest_at_or_before(
        &self,
        symbol: &str,
        event_ts_lte: DateTime<Utc>,
        observed_ts_lte: DateTime<Utc>,
    ) -> Option<NormalizedPriceTick> {
        let key = normalize_price_symbol(symbol);
        let inner = self.inner.read().expect("latest price lock poisoned");
        inner
            .history
            .iter()
            .filter(|tick| normalize_price_symbol(&tick.symbol) == key)
            .filter(|tick| tick.event_ts <= event_ts_lte && tick.observed_ts <= observed_ts_lte)
            .max_by_key(|tick| (tick.event_ts, tick.observed_ts))
            .cloned()
    }
}

impl Default for LatestPrices {
    fn default() -> Self {
        Self::with_history_limit(DEFAULT_PRICE_HISTORY_LIMIT)
    }
}

#[derive(Debug, Default)]
struct ChainlinkWebSocketTelemetry {
    reconnect_count: u64,
    last_event_observed_ts: Option<DateTime<Utc>>,
    stream_error_count: u64,
}

pub struct ChainlinkStreamManager {
    client: RtdsClient,
    symbols: Vec<String>,
    tasks: Vec<JoinHandle<Result<()>>>,
    telemetry: Arc<RwLock<ChainlinkWebSocketTelemetry>>,
    connection_monitor_task: JoinHandle<()>,
}

impl ChainlinkStreamManager {
    #[allow(dead_code)]
    pub fn start(symbols: Vec<String>, latest: LatestPrices) -> Self {
        Self::start_with_raw_events(symbols, latest, None)
    }

    pub fn start_with_raw_events(
        symbols: Vec<String>,
        latest: LatestPrices,
        raw_event_sink: Option<RawEventSink>,
    ) -> Self {
        let client = RtdsClient::default();
        let symbols = chainlink_stream_symbols(symbols);
        let telemetry = Arc::new(RwLock::new(ChainlinkWebSocketTelemetry::default()));
        let tasks = vec![tokio::spawn(run_chainlink_symbols_stream_with_client(
            client.clone(),
            symbols.clone(),
            latest,
            telemetry.clone(),
            raw_event_sink,
        ))];
        let connection_monitor_task = tokio::spawn(monitor_chainlink_connection_state(
            client.clone(),
            telemetry.clone(),
        ));

        Self {
            client,
            symbols,
            tasks,
            telemetry,
            connection_monitor_task,
        }
    }

    #[cfg(test)]
    pub fn inactive_for_tests(symbols: Vec<String>) -> Self {
        let client = RtdsClient::default();
        let telemetry = Arc::new(RwLock::new(ChainlinkWebSocketTelemetry::default()));
        let connection_monitor_task = tokio::spawn(monitor_chainlink_connection_state(
            client.clone(),
            telemetry.clone(),
        ));

        Self {
            client,
            symbols: chainlink_stream_symbols(symbols),
            tasks: vec![],
            telemetry,
            connection_monitor_task,
        }
    }

    pub fn websocket_status(&self, now: DateTime<Utc>) -> WebSocketStatus {
        let telemetry = self.telemetry.read().expect("RTDS telemetry poisoned");
        let last_event_age_ms = telemetry
            .last_event_observed_ts
            .map(|observed_ts| now.signed_duration_since(observed_ts).num_milliseconds());
        let ended_stream_count = self.tasks.iter().filter(|task| task.is_finished()).count();

        WebSocketStatus {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            channel: "crypto_prices_chainlink".to_owned(),
            connection_state: format!("{:?}", self.client.connection_state()),
            reconnect_count: telemetry.reconnect_count,
            subscription_count: self.client.subscription_count(),
            active_token_count: self.symbols.len(),
            ended_stream_count,
            stream_error_count: telemetry.stream_error_count,
            last_event_age_ms,
        }
    }

    pub fn shutdown(&mut self) {
        for task in std::mem::take(&mut self.tasks) {
            task.abort();
        }
        self.connection_monitor_task.abort();
    }
}

fn normalize_price_symbol(symbol: &str) -> String {
    symbol.trim().to_ascii_uppercase()
}

pub fn parse_kraken_xbtusd_ticker(
    json: &str,
    observed_ts: DateTime<Utc>,
) -> Result<NormalizedPriceTick> {
    let response: KrakenTickerResponse = serde_json::from_str(json)?;
    if !response.error.is_empty() {
        return Err(anyhow!(
            "kraken returned error: {}",
            response.error.join(",")
        ));
    }
    let pair = response
        .result
        .get("XXBTZUSD")
        .or_else(|| response.result.get("XBTUSD"))
        .ok_or_else(|| anyhow!("missing Kraken XBT/USD ticker"))?;
    Ok(NormalizedPriceTick {
        source_key: "kraken_rest".to_owned(),
        symbol: "XBT/USD".to_owned(),
        event_ts: observed_ts,
        observed_ts,
        price: Decimal::from_str(&pair.c[0])?,
    })
}

pub async fn fetch_kraken_btc_usd(client: &reqwest::Client) -> Result<NormalizedPriceTick> {
    let text = client
        .get(KRAKEN_TICKER_URL)
        .query(&[("pair", "XBTUSD")])
        .send()
        .await?
        .error_for_status()?
        .text()
        .await?;
    let observed_ts = Utc::now();
    parse_kraken_xbtusd_ticker(&text, observed_ts)
}

pub async fn fetch_chainlink_btc_usd(timeout_seconds: u64) -> Result<NormalizedPriceTick> {
    let client = RtdsClient::default();
    let requested_symbol = "btc/usd";
    let stream = client.subscribe_raw(Subscription::chainlink_prices(Some(
        requested_symbol.to_owned(),
    )))?;
    let mut stream = Box::pin(stream);
    let deadline = Duration::from_secs(timeout_seconds);

    loop {
        let result = timeout(deadline, stream.next())
            .await
            .map_err(|_| anyhow!("timed out waiting for Chainlink BTC/USD RTDS tick"))?;
        let Some(price_result) = result else {
            return Err(anyhow!("Chainlink BTC/USD RTDS stream ended before a tick"));
        };
        let message = price_result?;
        let observed_ts = Utc::now();
        if let Some(tick) =
            chainlink_snapshot_tick_from_message(&message, requested_symbol, observed_ts)?
        {
            return Ok(tick);
        }
        if let Some(price) = message.as_chainlink_price()
            && price.symbol.eq_ignore_ascii_case(requested_symbol)
        {
            return chainlink_update_tick(&price.symbol, price.timestamp, price.value, observed_ts);
        }
    }
}

pub async fn fetch_chainlink_btc_usd_cached(
    timeout_seconds: u64,
    cache_path: PathBuf,
    max_cache_age: Duration,
) -> Result<ChainlinkFetchResult> {
    if let Some(tick) = read_fresh_chainlink_cache(&cache_path, max_cache_age, Utc::now())? {
        return Ok(ChainlinkFetchResult {
            tick,
            cache_hit: true,
        });
    }

    let tick = fetch_chainlink_btc_usd(timeout_seconds).await?;
    write_chainlink_cache(&cache_path, &tick)?;
    Ok(ChainlinkFetchResult {
        tick,
        cache_hit: false,
    })
}

#[allow(dead_code)]
pub async fn run_chainlink_stream(symbols: Vec<String>, latest: LatestPrices) -> Result<()> {
    run_chainlink_symbols_stream_with_client(
        RtdsClient::default(),
        chainlink_stream_symbols(symbols),
        latest,
        Arc::new(RwLock::new(ChainlinkWebSocketTelemetry::default())),
        None,
    )
    .await
}

#[allow(dead_code)]
async fn run_chainlink_symbol_stream(symbol: String, latest: LatestPrices) -> Result<()> {
    run_chainlink_symbols_stream_with_client(
        RtdsClient::default(),
        vec![symbol],
        latest,
        Arc::new(RwLock::new(ChainlinkWebSocketTelemetry::default())),
        None,
    )
    .await
}

async fn run_chainlink_symbols_stream_with_client(
    client: RtdsClient,
    symbols: Vec<String>,
    latest: LatestPrices,
    telemetry: Arc<RwLock<ChainlinkWebSocketTelemetry>>,
    raw_event_sink: Option<RawEventSink>,
) -> Result<()> {
    let symbols = chainlink_stream_symbols(symbols);
    let stream = client.subscribe_raw(Subscription::chainlink_prices(None))?;
    let mut stream = Box::pin(stream);

    while let Some(message_result) = stream.next().await {
        let message = match message_result {
            Ok(message) => message,
            Err(error) => {
                record_chainlink_stream_error(&telemetry);
                return Err(error.into());
            }
        };
        let observed_ts = Utc::now();
        let mut updated = false;
        for symbol in &symbols {
            if let Some(tick) =
                update_latest_from_chainlink_message(&latest, &message, symbol, observed_ts).await?
            {
                if let Some(sink) = &raw_event_sink {
                    sink.try_record(chainlink_raw_event_record_from_tick(&message, &tick))?;
                }
                updated = true;
            }
        }
        if updated {
            record_chainlink_event(&telemetry, observed_ts);
        }
    }

    record_chainlink_stream_error(&telemetry);
    Err(anyhow!(
        "Chainlink RTDS stream ended for {}",
        symbols.join(",")
    ))
}

async fn monitor_chainlink_connection_state(
    client: RtdsClient,
    telemetry: Arc<RwLock<ChainlinkWebSocketTelemetry>>,
) {
    let mut was_connected = false;
    loop {
        let connected = client.connection_state().is_connected();
        if was_connected && !connected {
            let mut telemetry = telemetry.write().expect("RTDS telemetry poisoned");
            telemetry.reconnect_count = telemetry.reconnect_count.saturating_add(1);
        }
        was_connected = connected;
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
}

fn record_chainlink_event(
    telemetry: &Arc<RwLock<ChainlinkWebSocketTelemetry>>,
    observed_ts: DateTime<Utc>,
) {
    let mut telemetry = telemetry.write().expect("RTDS telemetry poisoned");
    telemetry.last_event_observed_ts = Some(observed_ts);
}

fn record_chainlink_stream_error(telemetry: &Arc<RwLock<ChainlinkWebSocketTelemetry>>) {
    let mut telemetry = telemetry.write().expect("RTDS telemetry poisoned");
    telemetry.stream_error_count = telemetry.stream_error_count.saturating_add(1);
}

fn chainlink_stream_symbols(symbols: Vec<String>) -> Vec<String> {
    let raw_symbols = if symbols.is_empty() {
        vec!["btc/usd".to_owned(), "eth/usd".to_owned()]
    } else {
        symbols
    };
    let mut normalized = Vec::new();
    for symbol in raw_symbols {
        let symbol = symbol.trim().to_ascii_lowercase();
        if !symbol.is_empty() && !normalized.contains(&symbol) {
            normalized.push(symbol);
        }
    }
    normalized
}

pub fn compare_btc_sources(
    chainlink: &NormalizedPriceTick,
    kraken: &NormalizedPriceTick,
) -> PriceDisagreement {
    PriceDisagreement::calculate("BTC", chainlink, kraken)
}

#[allow(dead_code)]
pub fn chainlink_freshness(
    ticks: &[NormalizedPriceTick],
    now: DateTime<Utc>,
    max_age: Duration,
) -> Vec<FeedFreshness> {
    let max_age_ms = i64::try_from(max_age.as_millis()).unwrap_or(i64::MAX);
    ticks
        .iter()
        .filter(|tick| tick.source_key == "polymarket_rtds_chainlink")
        .map(|tick| {
            let age_ms = now
                .signed_duration_since(tick.observed_ts)
                .num_milliseconds();
            FeedFreshness {
                source_key: tick.source_key.clone(),
                symbol: normalize_price_symbol(&tick.symbol),
                age_ms,
                stale: age_ms < 0 || age_ms > max_age_ms,
            }
        })
        .collect()
}

async fn update_latest_from_chainlink_message(
    latest: &LatestPrices,
    message: &RtdsMessage,
    requested_symbol: &str,
    observed_ts: DateTime<Utc>,
) -> Result<Option<NormalizedPriceTick>> {
    if let Some(tick) =
        chainlink_snapshot_tick_from_message(message, requested_symbol, observed_ts)?
    {
        latest.update(tick.clone()).await;
        return Ok(Some(tick));
    }

    if let Some(price) = message.as_chainlink_price()
        && price.symbol.eq_ignore_ascii_case(requested_symbol)
    {
        let tick = chainlink_update_tick(&price.symbol, price.timestamp, price.value, observed_ts)?;
        latest.update(tick.clone()).await;
        return Ok(Some(tick));
    }

    Ok(None)
}

#[allow(dead_code)]
fn chainlink_raw_event_record_from_tick(
    message: &RtdsMessage,
    tick: &NormalizedPriceTick,
) -> RawEventRecord {
    RawEventRecord {
        source_key: "polymarket_rtds_chainlink".to_owned(),
        stream_key: "price_update".to_owned(),
        symbol: tick.symbol.clone(),
        event_type: "chainlink_price".to_owned(),
        event_ts: tick.event_ts,
        observed_ts: tick.observed_ts,
        payload: serde_json::json!({
            "topic": message.topic,
            "payload": message.payload.clone(),
        }),
    }
}

fn chainlink_update_tick(
    symbol: &str,
    timestamp: i64,
    price: Decimal,
    observed_ts: DateTime<Utc>,
) -> Result<NormalizedPriceTick> {
    let event_ts = Utc
        .timestamp_millis_opt(timestamp)
        .single()
        .ok_or_else(|| anyhow!("invalid Chainlink BTC/USD timestamp"))?;
    Ok(NormalizedPriceTick {
        source_key: "polymarket_rtds_chainlink".to_owned(),
        symbol: symbol.to_uppercase(),
        event_ts,
        observed_ts,
        price,
    })
}

fn chainlink_snapshot_tick_from_message(
    message: &RtdsMessage,
    requested_symbol: &str,
    observed_ts: DateTime<Utc>,
) -> Result<Option<NormalizedPriceTick>> {
    if message.topic != "crypto_prices_chainlink" {
        return Ok(None);
    }
    chainlink_snapshot_tick_from_payload(&message.payload, requested_symbol, observed_ts)
}

fn chainlink_snapshot_tick_from_payload(
    payload: &Value,
    requested_symbol: &str,
    observed_ts: DateTime<Utc>,
) -> Result<Option<NormalizedPriceTick>> {
    let Some(points) = payload.get("data").and_then(Value::as_array) else {
        return Ok(None);
    };
    let symbol = payload
        .get("symbol")
        .and_then(Value::as_str)
        .unwrap_or(requested_symbol);
    if !symbol.eq_ignore_ascii_case(requested_symbol) {
        return Ok(None);
    }
    let latest = points
        .iter()
        .filter_map(snapshot_point)
        .max_by_key(|point| point.timestamp);
    match latest {
        Some(point) => {
            chainlink_update_tick(symbol, point.timestamp, point.price, observed_ts).map(Some)
        }
        None => Ok(None),
    }
}

#[cfg(test)]
fn chainlink_snapshot_tick(
    message: &Value,
    requested_symbol: &str,
    observed_ts: DateTime<Utc>,
) -> Result<NormalizedPriceTick> {
    if message.get("topic").and_then(Value::as_str) != Some("crypto_prices_chainlink") {
        return Err(anyhow!("message is not a Chainlink RTDS message"));
    }
    chainlink_snapshot_tick_from_payload(
        message
            .get("payload")
            .ok_or_else(|| anyhow!("Chainlink snapshot missing payload"))?,
        requested_symbol,
        observed_ts,
    )?
    .ok_or_else(|| anyhow!("Chainlink snapshot missing data points"))
}

#[derive(Debug)]
struct SnapshotPoint {
    timestamp: i64,
    price: Decimal,
}

fn snapshot_point(value: &Value) -> Option<SnapshotPoint> {
    let timestamp = json_i64(value.get("timestamp")?)?;
    let price = json_decimal(value.get("value")?).ok()?;
    Some(SnapshotPoint { timestamp, price })
}

fn json_i64(value: &Value) -> Option<i64> {
    value
        .as_i64()
        .or_else(|| value.as_str().and_then(|text| text.parse().ok()))
}

fn json_decimal(value: &Value) -> Result<Decimal> {
    match value {
        Value::String(text) => Ok(Decimal::from_str(text)?),
        Value::Number(number) => Ok(Decimal::from_str(&number.to_string())?),
        _ => Err(anyhow!("expected decimal-compatible JSON value")),
    }
}

fn read_fresh_chainlink_cache(
    path: &Path,
    max_age: Duration,
    now: DateTime<Utc>,
) -> Result<Option<NormalizedPriceTick>> {
    let bytes = match std::fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    let tick: NormalizedPriceTick = serde_json::from_slice(&bytes)?;
    if tick.source_key != "polymarket_rtds_chainlink" || tick.symbol != "BTC/USD" {
        return Ok(None);
    }
    let age_ms = now
        .signed_duration_since(tick.observed_ts)
        .num_milliseconds();
    if age_ms < 0 {
        return Ok(None);
    }
    if u128::try_from(age_ms).is_ok_and(|age| age <= max_age.as_millis()) {
        Ok(Some(tick))
    } else {
        Ok(None)
    }
}

fn write_chainlink_cache(path: &Path, tick: &NormalizedPriceTick) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp_path = path.with_extension("json.tmp");
    std::fs::write(&tmp_path, serde_json::to_vec(tick)?)?;
    std::fs::rename(tmp_path, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_kraken_xbtusd_ticker() {
        let observed = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let tick = parse_kraken_xbtusd_ticker(
            include_str!("../tests/fixtures/kraken_xbtusd_ticker.json"),
            observed,
        )
        .unwrap();

        assert_eq!(tick.source_key, "kraken_rest");
        assert_eq!(tick.symbol, "XBT/USD");
        assert_eq!(tick.price.to_string(), "100100.0");
    }

    #[test]
    fn compares_chainlink_to_kraken() {
        let observed = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let chainlink = NormalizedPriceTick {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            symbol: "BTC/USD".to_owned(),
            event_ts: observed,
            observed_ts: observed,
            price: Decimal::new(100_000, 0),
        };
        let kraken = NormalizedPriceTick {
            source_key: "kraken_rest".to_owned(),
            symbol: "XBT/USD".to_owned(),
            event_ts: observed,
            observed_ts: observed,
            price: Decimal::new(100_100, 0),
        };

        let row = compare_btc_sources(&chainlink, &kraken);
        assert!((row.diff_bps - 10.0).abs() < 0.0001);
    }

    #[test]
    fn parses_chainlink_snapshot_latest_point_without_waiting_for_next_update() {
        let observed = "2026-06-01T20:00:01Z".parse::<DateTime<Utc>>().unwrap();
        let message = serde_json::json!({
            "topic": "crypto_prices_chainlink",
            "type": "snapshot",
            "timestamp": 1780352939000_i64,
            "payload": {
                "data": [
                    {"timestamp": 1780352937000_i64, "value": 71209.81171643556},
                    {"timestamp": 1780352939000_i64, "value": 71209.78951739693}
                ]
            }
        });

        let tick = chainlink_snapshot_tick(&message, "btc/usd", observed).unwrap();

        assert_eq!(tick.source_key, "polymarket_rtds_chainlink");
        assert_eq!(tick.symbol, "BTC/USD");
        assert_eq!(tick.event_ts.timestamp_millis(), 1780352939000_i64);
        assert_eq!(tick.price.to_string(), "71209.78951739693");
    }

    #[test]
    fn parses_chainlink_eth_snapshot_latest_point() {
        let observed = "2026-06-01T20:00:01Z".parse::<DateTime<Utc>>().unwrap();
        let message = serde_json::json!({
            "topic": "crypto_prices_chainlink",
            "type": "snapshot",
            "payload": {
                "symbol": "eth/usd",
                "data": [
                    {"timestamp": 1780352937000_i64, "value": "1990.25"},
                    {"timestamp": 1780352939000_i64, "value": "1991.50"}
                ]
            }
        });

        let tick = chainlink_snapshot_tick(&message, "eth/usd", observed).unwrap();

        assert_eq!(tick.source_key, "polymarket_rtds_chainlink");
        assert_eq!(tick.symbol, "ETH/USD");
        assert_eq!(tick.event_ts.timestamp_millis(), 1780352939000_i64);
        assert_eq!(tick.price.to_string(), "1991.50");
    }

    #[test]
    fn ignores_chainlink_snapshot_for_different_requested_symbol() {
        let observed = "2026-06-01T20:00:01Z".parse::<DateTime<Utc>>().unwrap();
        let message: RtdsMessage = serde_json::from_value(serde_json::json!({
            "topic": "crypto_prices_chainlink",
            "type": "snapshot",
            "timestamp": 1780352939000_i64,
            "payload": {
                "symbol": "btc/usd",
                "data": [
                    {"timestamp": 1780352939000_i64, "value": "71210.25"}
                ]
            }
        }))
        .unwrap();

        assert!(
            chainlink_snapshot_tick_from_message(&message, "eth/usd", observed)
                .unwrap()
                .is_none()
        );
    }

    #[tokio::test]
    async fn latest_prices_store_updates_and_snapshots_ticks() {
        let observed = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let store = LatestPrices::default();
        store
            .update(NormalizedPriceTick {
                source_key: "polymarket_rtds_chainlink".to_owned(),
                symbol: "BTC/USD".to_owned(),
                event_ts: observed,
                observed_ts: observed,
                price: Decimal::new(100_000, 0),
            })
            .await;

        assert_eq!(
            store.get("btc/usd").await.unwrap().price.to_string(),
            "100000"
        );
        assert_eq!(store.snapshot().await.len(), 1);
    }

    #[tokio::test]
    async fn latest_prices_keeps_bounded_history_for_start_thresholds() {
        let store = LatestPrices::with_history_limit(3);
        let t0 = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        store
            .update(chainlink_tick("BTC/USD", t0, Decimal::new(70_000, 0)))
            .await;
        store
            .update(chainlink_tick(
                "BTC/USD",
                t0 + chrono::Duration::seconds(1),
                Decimal::new(70_100, 0),
            ))
            .await;
        store
            .update(chainlink_tick(
                "BTC/USD",
                t0 + chrono::Duration::seconds(2),
                Decimal::new(70_200, 0),
            ))
            .await;
        store
            .update(chainlink_tick(
                "BTC/USD",
                t0 + chrono::Duration::seconds(3),
                Decimal::new(70_300, 0),
            ))
            .await;

        let history = store.history_snapshot().await;

        assert_eq!(history.len(), 3);
        assert_eq!(history[0].price, Decimal::new(70_100, 0));
        assert_eq!(
            store
                .latest_at_or_before(
                    "BTC/USD",
                    t0 + chrono::Duration::seconds(2),
                    t0 + chrono::Duration::seconds(2),
                )
                .await
                .unwrap()
                .price,
            Decimal::new(70_200, 0)
        );
    }

    #[test]
    fn chainlink_freshness_marks_stale_ticks() {
        let observed = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let now = "2026-06-01T20:00:02Z".parse::<DateTime<Utc>>().unwrap();
        let tick = NormalizedPriceTick {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            symbol: "ETH/USD".to_owned(),
            event_ts: observed,
            observed_ts: observed,
            price: Decimal::new(2_000, 0),
        };

        let freshness = chainlink_freshness(&[tick], now, Duration::from_millis(1500));

        assert_eq!(freshness.len(), 1);
        assert_eq!(freshness[0].source_key, "polymarket_rtds_chainlink");
        assert_eq!(freshness[0].symbol, "ETH/USD");
        assert_eq!(freshness[0].age_ms, 2_000);
        assert!(freshness[0].stale);
    }

    #[tokio::test]
    async fn updates_latest_prices_from_matching_chainlink_message() {
        let observed = "2026-06-01T20:00:01Z".parse::<DateTime<Utc>>().unwrap();
        let message: RtdsMessage = serde_json::from_value(serde_json::json!({
            "topic": "crypto_prices_chainlink",
            "type": "snapshot",
            "timestamp": 1780352939000_i64,
            "payload": {
                "symbol": "btc/usd",
                "data": [
                    {"timestamp": 1780352937000_i64, "value": "71209.81"},
                    {"timestamp": 1780352939000_i64, "value": "71210.25"}
                ]
            }
        }))
        .unwrap();
        let latest = LatestPrices::default();

        let tick = update_latest_from_chainlink_message(&latest, &message, "btc/usd", observed)
            .await
            .unwrap()
            .unwrap();

        assert_eq!(tick.source_key, "polymarket_rtds_chainlink");
        assert_eq!(tick.symbol, "BTC/USD");
        assert_eq!(tick.event_ts.timestamp_millis(), 1780352939000_i64);
        assert_eq!(tick.observed_ts, observed);
        assert_eq!(tick.price.to_string(), "71210.25");
        assert_eq!(
            latest.get("BTC/USD").await.unwrap().price.to_string(),
            "71210.25"
        );
    }

    #[tokio::test]
    async fn converts_chainlink_price_update_to_raw_event_record() {
        let observed = "2026-06-01T20:00:01Z".parse::<DateTime<Utc>>().unwrap();
        let message: RtdsMessage = serde_json::from_value(serde_json::json!({
            "topic": "crypto_prices_chainlink",
            "type": "price_update",
            "timestamp": 1780352939000_i64,
            "payload": {
                "symbol": "eth/usd",
                "timestamp": 1780352939000_i64,
                "value": "1991.50"
            }
        }))
        .unwrap();
        let tick = update_latest_from_chainlink_message(
            &LatestPrices::default(),
            &message,
            "eth/usd",
            observed,
        )
        .await
        .unwrap()
        .unwrap();

        let record = chainlink_raw_event_record_from_tick(&message, &tick);

        assert_eq!(record.source_key, "polymarket_rtds_chainlink");
        assert_eq!(record.stream_key, "price_update");
        assert_eq!(record.symbol, "ETH/USD");
        assert_eq!(record.event_type, "chainlink_price");
        assert_eq!(record.event_ts.timestamp_millis(), 1780352939000_i64);
        assert_eq!(record.observed_ts, observed);
        assert_eq!(record.payload["topic"], "crypto_prices_chainlink");
        assert_eq!(record.payload["payload"]["symbol"], "eth/usd");
        assert_eq!(record.payload["payload"]["timestamp"], 1780352939000_i64);
        assert_eq!(record.payload["payload"]["value"], "1991.50");
    }

    #[test]
    fn chainlink_stream_symbols_default_and_normalize() {
        assert_eq!(
            chainlink_stream_symbols(vec![]),
            vec!["btc/usd".to_owned(), "eth/usd".to_owned()]
        );
        assert_eq!(
            chainlink_stream_symbols(vec![
                " BTC/USD ".to_owned(),
                "eth/usd".to_owned(),
                "BTC/USD".to_owned(),
            ]),
            vec!["btc/usd".to_owned(), "eth/usd".to_owned()]
        );
    }

    #[test]
    fn reads_fresh_chainlink_cache_and_rejects_stale_cache() {
        let path = std::env::temp_dir().join(format!(
            "polymarket-chainlink-cache-{}.json",
            std::process::id()
        ));
        let observed = "2026-06-01T20:00:00Z".parse::<DateTime<Utc>>().unwrap();
        let tick = NormalizedPriceTick {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            symbol: "BTC/USD".to_owned(),
            event_ts: observed,
            observed_ts: observed,
            price: Decimal::new(100_000, 0),
        };

        write_chainlink_cache(&path, &tick).unwrap();

        let fresh_now = "2026-06-01T20:00:01Z".parse::<DateTime<Utc>>().unwrap();
        let stale_now = "2026-06-01T20:00:03Z".parse::<DateTime<Utc>>().unwrap();
        assert!(
            read_fresh_chainlink_cache(&path, Duration::from_millis(1500), fresh_now)
                .unwrap()
                .is_some()
        );
        assert!(
            read_fresh_chainlink_cache(&path, Duration::from_millis(1500), stale_now)
                .unwrap()
                .is_none()
        );

        let _ = std::fs::remove_file(path);
    }

    fn chainlink_tick(
        symbol: &str,
        timestamp: DateTime<Utc>,
        price: Decimal,
    ) -> NormalizedPriceTick {
        NormalizedPriceTick {
            source_key: "polymarket_rtds_chainlink".to_owned(),
            symbol: symbol.to_owned(),
            event_ts: timestamp,
            observed_ts: timestamp,
            price,
        }
    }
}
