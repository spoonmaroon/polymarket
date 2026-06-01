use anyhow::{Result, anyhow};
use chrono::{DateTime, TimeZone, Utc};
use futures::StreamExt as _;
use polymarket_client_sdk_v2::rtds::{Client as RtdsClient, RtdsMessage, Subscription};
use polymarket_runtime_types::{NormalizedPriceTick, PriceDisagreement};
use rust_decimal::Decimal;
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::str::FromStr;
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

pub fn compare_btc_sources(
    chainlink: &NormalizedPriceTick,
    kraken: &NormalizedPriceTick,
) -> PriceDisagreement {
    PriceDisagreement::calculate("BTC", chainlink, kraken)
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
