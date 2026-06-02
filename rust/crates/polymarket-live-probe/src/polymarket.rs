use anyhow::{Context, Result, bail};
use chrono::{DateTime, Duration, Utc};
use futures::future::try_join_all;
use polymarket_client_sdk_v2::clob::types::request::OrderBookSummaryRequest;
use polymarket_client_sdk_v2::clob::types::response::OrderBookSummaryResponse;
use polymarket_client_sdk_v2::clob::{Client as ClobClient, Config as ClobConfig};
use polymarket_client_sdk_v2::gamma::Client as GammaClient;
use polymarket_client_sdk_v2::gamma::types::request::MarketsRequest;
use polymarket_client_sdk_v2::gamma::types::response::Market;
use polymarket_client_sdk_v2::types::U256;
use polymarket_runtime_types::{
    BookLevel, ContractSide, ContractToken, ContractWindow, NormalizedOrderBook, OrderBookMeta,
    WarmedContract,
};
use std::collections::BTreeMap;

pub const CLOB_HOST: &str = "https://clob-v2.polymarket.com";
pub const GAMMA_HOST: &str = "https://gamma-api.polymarket.com";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MarketToken {
    pub slug: String,
    pub asset: String,
    pub side: String,
    pub token_id: U256,
}

pub fn current_window_slugs(
    now: DateTime<Utc>,
    assets: &[&str],
    interval: &str,
    windows: u8,
) -> Result<Vec<String>> {
    let interval_seconds = interval_seconds(interval)?;
    let start_epoch = floor_to_interval_epoch(now, interval_seconds);
    let mut slugs = Vec::with_capacity(assets.len() * usize::from(windows));

    for window_index in 0..i64::from(windows) {
        let epoch = start_epoch + interval_seconds * window_index;
        for asset in assets {
            let normalized_asset = normalize_asset(asset)?;
            slugs.push(format!(
                "{}-updown-{interval}-{epoch}",
                normalized_asset.to_lowercase()
            ));
        }
    }

    Ok(slugs)
}

pub fn market_tokens_from_gamma_market(market: &Market) -> Result<Vec<MarketToken>> {
    let slug = market.slug.clone().context("Gamma market missing slug")?;
    let asset = asset_from_slug(&slug)?;
    let outcomes = market
        .outcomes
        .as_ref()
        .context("Gamma market missing outcomes")?;
    let token_ids = market
        .clob_token_ids
        .as_ref()
        .context("Gamma market missing clob_token_ids")?;
    if outcomes.len() != token_ids.len() {
        bail!(
            "Gamma outcomes/token id length mismatch for {slug}: {} outcomes, {} token ids",
            outcomes.len(),
            token_ids.len()
        );
    }

    Ok(outcomes
        .iter()
        .zip(token_ids.iter())
        .map(|(outcome, token_id)| MarketToken {
            slug: slug.clone(),
            asset: asset.clone(),
            side: normalize_outcome_side(outcome),
            token_id: *token_id,
        })
        .collect())
}

pub fn warmed_contracts_from_tokens(tokens: &[MarketToken]) -> Result<Vec<WarmedContract>> {
    let mut by_slug: BTreeMap<&str, Vec<&MarketToken>> = BTreeMap::new();
    for token in tokens {
        by_slug.entry(&token.slug).or_default().push(token);
    }

    by_slug
        .into_iter()
        .map(|(slug, slug_tokens)| warmed_contract_from_slug_tokens(slug, &slug_tokens))
        .collect()
}

fn warmed_contract_from_slug_tokens(slug: &str, tokens: &[&MarketToken]) -> Result<WarmedContract> {
    let window = window_from_slug(slug)?;
    let mut up: Option<ContractToken> = None;
    let mut down: Option<ContractToken> = None;

    for token in tokens {
        match token.side.as_str() {
            "UP" => {
                up = Some(ContractToken::new(
                    &token.asset,
                    ContractSide::Up,
                    &token.token_id.to_string(),
                ));
            }
            "DOWN" => {
                down = Some(ContractToken::new(
                    &token.asset,
                    ContractSide::Down,
                    &token.token_id.to_string(),
                ));
            }
            other => bail!("unsupported token side for warmed contract {slug}: {other}"),
        }
    }

    WarmedContract::new(
        window,
        up.with_context(|| format!("missing UP token for {slug}"))?,
        down.with_context(|| format!("missing DOWN token for {slug}"))?,
    )
}

fn window_from_slug(slug: &str) -> Result<ContractWindow> {
    let parts = slug.split('-').collect::<Vec<_>>();
    if parts.len() != 4 || parts[1] != "updown" {
        bail!("unsupported Polymarket up/down slug: {slug}");
    }
    let asset = normalize_asset(parts[0])?;
    let interval = parts[2];
    let start_epoch = parts[3]
        .parse::<i64>()
        .with_context(|| format!("invalid Polymarket up/down slug epoch: {slug}"))?;
    let start = DateTime::<Utc>::from_timestamp(start_epoch, 0)
        .with_context(|| format!("invalid Polymarket up/down slug timestamp: {slug}"))?;
    let end = start + Duration::seconds(interval_seconds(interval)?);
    ContractWindow::new(&asset, interval, start, end)
}

#[cfg(test)]
pub fn parse_orderbook_summary(json: &str) -> Result<OrderBookSummaryResponse> {
    serde_json::from_str(json).context("failed to parse CLOB orderbook summary")
}

pub fn normalize_orderbook_summary(
    book: &OrderBookSummaryResponse,
    token: &MarketToken,
    observed_ts: DateTime<Utc>,
) -> Result<NormalizedOrderBook> {
    if book.asset_id != token.token_id {
        bail!(
            "orderbook asset_id {} does not match discovered token_id {}",
            book.asset_id,
            token.token_id
        );
    }

    Ok(NormalizedOrderBook::from_levels(
        OrderBookMeta {
            market_slug: token.slug.clone(),
            contract_id: book.market.to_string(),
            token_id: book.asset_id.to_string(),
            asset: token.asset.clone(),
            side: token.side.clone(),
            event_ts: book.timestamp,
            observed_ts,
        },
        book.bids
            .iter()
            .map(|level| BookLevel {
                price: level.price,
                size: level.size,
            })
            .collect(),
        book.asks
            .iter()
            .map(|level| BookLevel {
                price: level.price,
                size: level.size,
            })
            .collect(),
    ))
}

pub async fn discover_current_markets(
    now: DateTime<Utc>,
    assets: &[&str],
    interval: &str,
    windows: u8,
) -> Result<Vec<MarketToken>> {
    let gamma = GammaClient::new(GAMMA_HOST)?;
    let slugs = current_window_slugs(now, assets, interval, windows)?;
    let request = MarketsRequest::builder().slug(slugs).closed(false).build();
    let markets = gamma.markets(&request).await?;
    let mut tokens = Vec::new();

    for market in markets {
        tokens.extend(market_tokens_from_gamma_market(&market)?);
    }

    Ok(tokens)
}

#[allow(dead_code)]
pub async fn discover_windows(windows: &[ContractWindow]) -> Result<Vec<WarmedContract>> {
    let gamma = GammaClient::new(GAMMA_HOST)?;
    let slugs = windows.iter().map(ContractWindow::slug).collect::<Vec<_>>();
    let request = MarketsRequest::builder().slug(slugs).closed(false).build();
    let markets = gamma.markets(&request).await?;
    let mut tokens = Vec::new();

    for market in markets {
        tokens.extend(market_tokens_from_gamma_market(&market)?);
    }

    warmed_contracts_from_tokens(&tokens)
}

pub async fn fetch_orderbooks(tokens: &[MarketToken]) -> Result<Vec<NormalizedOrderBook>> {
    if tokens.is_empty() {
        return Ok(Vec::new());
    }
    let clob = ClobClient::new(CLOB_HOST, ClobConfig::default())?;
    let requests = tokens
        .iter()
        .map(|token| {
            OrderBookSummaryRequest::builder()
                .token_id(token.token_id)
                .build()
        })
        .collect::<Vec<_>>();
    let books = try_join_all(requests.iter().map(|request| clob.order_book(request))).await?;
    let observed_ts = Utc::now();

    if books.len() != tokens.len() {
        bail!(
            "CLOB orderbook length mismatch: {} requests, {} responses",
            tokens.len(),
            books.len()
        );
    }

    tokens
        .iter()
        .zip(books.iter())
        .map(|(token, book)| normalize_orderbook_summary(book, token, observed_ts))
        .collect()
}

fn interval_seconds(interval: &str) -> Result<i64> {
    match interval {
        "5m" => Ok(5 * 60),
        "15m" => Ok(15 * 60),
        _ => bail!("unsupported Polymarket up/down interval: {interval}"),
    }
}

fn floor_to_interval_epoch(now: DateTime<Utc>, interval_seconds: i64) -> i64 {
    now.timestamp() - now.timestamp().rem_euclid(interval_seconds)
}

fn normalize_asset(asset: &str) -> Result<String> {
    let asset = asset.trim();
    if asset.is_empty() {
        bail!("asset cannot be empty");
    }
    Ok(asset.to_uppercase())
}

fn asset_from_slug(slug: &str) -> Result<String> {
    let asset = slug
        .split('-')
        .next()
        .filter(|value| !value.is_empty())
        .context("market slug does not start with an asset")?;
    normalize_asset(asset)
}

fn normalize_outcome_side(outcome: &str) -> String {
    match outcome.trim().to_ascii_lowercase().as_str() {
        "up" => "UP".to_owned(),
        "down" => "DOWN".to_owned(),
        other => other.to_uppercase(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use polymarket_client_sdk_v2::gamma::types::response::Market;
    use rust_decimal::Decimal;

    #[test]
    fn current_window_slugs_use_polymarket_epoch_pattern() {
        let now = "2026-05-31T21:17:00Z".parse::<DateTime<Utc>>().unwrap();

        let slugs = current_window_slugs(now, &["BTC", "eth"], "5m", 2).unwrap();

        assert_eq!(
            slugs,
            vec![
                "btc-updown-5m-1780262100",
                "eth-updown-5m-1780262100",
                "btc-updown-5m-1780262400",
                "eth-updown-5m-1780262400",
            ]
        );
    }

    #[test]
    fn current_window_slugs_floor_15m_windows() {
        let now = "2026-05-31T21:17:00Z".parse::<DateTime<Utc>>().unwrap();

        let slugs = current_window_slugs(now, &["BTC"], "15m", 2).unwrap();

        assert_eq!(
            slugs,
            vec!["btc-updown-15m-1780262100", "btc-updown-15m-1780263000",]
        );
    }

    #[test]
    fn extracts_tokens_from_typed_gamma_market() {
        let market = gamma_market_fixture();

        let tokens = market_tokens_from_gamma_market(&market).unwrap();

        assert_eq!(
            tokens,
            vec![
                MarketToken {
                    slug: "btc-updown-5m-1780262100".to_owned(),
                    asset: "BTC".to_owned(),
                    side: "UP".to_owned(),
                    token_id: U256::from(111_u64),
                },
                MarketToken {
                    slug: "btc-updown-5m-1780262100".to_owned(),
                    asset: "BTC".to_owned(),
                    side: "DOWN".to_owned(),
                    token_id: U256::from(222_u64),
                },
            ]
        );
    }

    #[test]
    fn converts_up_down_tokens_into_warmed_contract() {
        let tokens = vec![
            MarketToken {
                slug: "btc-updown-5m-1780262100".to_owned(),
                asset: "BTC".to_owned(),
                side: "UP".to_owned(),
                token_id: U256::from(111_u64),
            },
            MarketToken {
                slug: "btc-updown-5m-1780262100".to_owned(),
                asset: "BTC".to_owned(),
                side: "DOWN".to_owned(),
                token_id: U256::from(222_u64),
            },
        ];

        let warmed = warmed_contracts_from_tokens(&tokens).unwrap();

        assert_eq!(warmed.len(), 1);
        assert_eq!(warmed[0].window.slug(), "btc-updown-5m-1780262100");
        assert_eq!(
            warmed[0].token_ids(),
            vec!["111".to_owned(), "222".to_owned()]
        );
    }

    #[test]
    fn parses_orderbook_summary_fixture_with_sdk_types() {
        let book =
            parse_orderbook_summary(include_str!("../tests/fixtures/orderbook_summary.json"))
                .unwrap();

        assert_eq!(
            book.market.to_string(),
            "0x00000000000000000000000000000000000000000000000000000000aabbcc00"
        );
        assert_eq!(book.asset_id, U256::from(111_u64));
        assert_eq!(book.bids.len(), 2);
        assert_eq!(book.asks.len(), 2);
    }

    #[test]
    fn normalizes_orderbook_summary_into_runtime_shape() {
        let observed_ts = "2026-05-31T21:17:03Z".parse::<DateTime<Utc>>().unwrap();
        let book =
            parse_orderbook_summary(include_str!("../tests/fixtures/orderbook_summary.json"))
                .unwrap();
        let token = MarketToken {
            slug: "btc-updown-5m-1780262100".to_owned(),
            asset: "BTC".to_owned(),
            side: "UP".to_owned(),
            token_id: U256::from(111_u64),
        };

        let normalized = normalize_orderbook_summary(&book, &token, observed_ts).unwrap();

        assert_eq!(normalized.venue, "polymarket");
        assert_eq!(normalized.source_key, "polymarket_rust_sdk");
        assert_eq!(normalized.market_slug, "btc-updown-5m-1780262100");
        assert_eq!(normalized.asset, "BTC");
        assert_eq!(normalized.side, "UP");
        assert_eq!(normalized.token_id, "111");
        assert_eq!(normalized.best_bid, Some(Decimal::new(50, 2)));
        assert_eq!(normalized.best_ask, Some(Decimal::new(52, 2)));
        assert_eq!(normalized.spread, Some(Decimal::new(2, 2)));
        assert_eq!(normalized.bid_size_top, Some(Decimal::new(15, 0)));
        assert_eq!(normalized.ask_size_top, Some(Decimal::new(25, 0)));
        assert_eq!(normalized.bids.len(), 2);
        assert_eq!(normalized.asks.len(), 2);
    }

    fn gamma_market_fixture() -> Market {
        serde_json::from_value(serde_json::json!({
            "id": "123",
            "slug": "btc-updown-5m-1780262100",
            "question": "Bitcoin Up or Down - May 31, 5:15PM-5:20PM ET",
            "outcomes": "[\"Up\",\"Down\"]",
            "clobTokenIds": "[\"111\",\"222\"]",
            "active": true,
            "closed": false,
            "enableOrderBook": true
        }))
        .unwrap()
    }
}
