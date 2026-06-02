use anyhow::{Result, anyhow};
use chrono::{DateTime, TimeZone, Utc};
use futures::{StreamExt as _, future::try_join_all};
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
use tokio::time::timeout;

pub const KRAKEN_TICKER_URL: &str = "https://api.kraken.com/0/public/Ticker";

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

#[derive(Debug, Default, Clone)]
pub struct LatestPrices {
    inner: Arc<RwLock<HashMap<String, NormalizedPriceTick>>>,
}

impl LatestPrices {
    pub async fn update(&self, tick: NormalizedPriceTick) {
        let key = normalize_price_symbol(&tick.symbol);
        let mut inner = self.inner.write().expect("latest price lock poisoned");
        inner.insert(key, tick);
    }

    #[allow(dead_code)]
    pub async fn get(&self, symbol: &str) -> Option<NormalizedPriceTick> {
        let key = normalize_price_symbol(symbol);
        let inner = self.inner.read().expect("latest price lock poisoned");
        inner.get(&key).cloned()
    }

    #[allow(dead_code)]
    pub async fn snapshot(&self) -> Vec<NormalizedPriceTick> {
        let inner = self.inner.read().expect("latest price lock poisoned");
        let mut ticks = inner.values().cloned().collect::<Vec<_>>();
        ticks.sort_by(|left, right| left.symbol.cmp(&right.symbol));
        ticks
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
    let streams = chainlink_stream_symbols(symbols)
        .into_iter()
        .map(|symbol| run_chainlink_symbol_stream(symbol, latest.clone()));
    try_join_all(streams).await?;
    Ok(())
}

#[allow(dead_code)]
async fn run_chainlink_symbol_stream(symbol: String, latest: LatestPrices) -> Result<()> {
    let client = RtdsClient::default();
    let stream = client.subscribe_raw(Subscription::chainlink_prices(Some(symbol.clone())))?;
    let mut stream = Box::pin(stream);

    while let Some(message_result) = stream.next().await {
        let message = message_result?;
        let observed_ts = Utc::now();
        update_latest_from_chainlink_message(&latest, &message, &symbol, observed_ts).await?;
    }

    Err(anyhow!("Chainlink RTDS stream ended for {symbol}"))
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
) -> Result<bool> {
    if let Some(tick) =
        chainlink_snapshot_tick_from_message(message, requested_symbol, observed_ts)?
    {
        latest.update(tick).await;
        return Ok(true);
    }

    if let Some(price) = message.as_chainlink_price()
        && price.symbol.eq_ignore_ascii_case(requested_symbol)
    {
        let tick = chainlink_update_tick(&price.symbol, price.timestamp, price.value, observed_ts)?;
        latest.update(tick).await;
        return Ok(true);
    }

    Ok(false)
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

        let updated = update_latest_from_chainlink_message(&latest, &message, "btc/usd", observed)
            .await
            .unwrap();

        assert!(updated);
        assert_eq!(
            latest.get("BTC/USD").await.unwrap().price.to_string(),
            "71210.25"
        );
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
}
