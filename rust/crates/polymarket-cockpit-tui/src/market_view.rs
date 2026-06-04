use chrono::{DateTime, Duration, Local, TimeZone, Utc};

use crate::status::RuntimeOrderbookRow;

#[derive(Debug, Clone, PartialEq)]
pub struct MarketGroup<'a> {
    pub key: String,
    pub asset: String,
    pub market_slug: String,
    pub label: String,
    pub start_ts: Option<DateTime<Utc>>,
    pub expiry_ts: Option<DateTime<Utc>>,
    pub up: Option<&'a RuntimeOrderbookRow>,
    pub down: Option<&'a RuntimeOrderbookRow>,
}

pub fn market_groups(orderbooks: &[RuntimeOrderbookRow]) -> Vec<MarketGroup<'_>> {
    let mut groups: Vec<MarketGroup<'_>> = Vec::new();

    for orderbook in orderbooks {
        let key = market_key(orderbook);
        if let Some(group) = groups.iter_mut().find(|group| group.key == key) {
            assign_side(group, orderbook);
            continue;
        }

        let asset = orderbook
            .asset
            .as_deref()
            .filter(|asset| !asset.trim().is_empty())
            .map(|asset| asset.trim().to_ascii_uppercase())
            .or_else(|| slug_parts(orderbook.market_slug.as_deref()).map(|parts| parts.asset))
            .unwrap_or_else(|| "OTHER".to_string());
        let market_slug = orderbook
            .market_slug
            .as_deref()
            .filter(|slug| !slug.trim().is_empty())
            .unwrap_or(orderbook.contract_id.as_str())
            .to_string();
        let expiry_ts = expiry_ts(orderbook);
        let start_ts =
            start_ts(orderbook).or_else(|| expiry_ts.map(|ts| ts - Duration::minutes(5)));
        let interval = slug_parts(orderbook.market_slug.as_deref())
            .map(|parts| parts.interval)
            .unwrap_or_else(|| "5m".to_string());
        let label = if expiry_ts.is_some() {
            format!("{asset} {interval} {}", local_expiry_label(expiry_ts))
        } else {
            market_slug.clone()
        };
        let mut group = MarketGroup {
            key,
            asset,
            market_slug,
            label,
            start_ts,
            expiry_ts,
            up: None,
            down: None,
        };
        assign_side(&mut group, orderbook);
        groups.push(group);
    }

    groups.sort_by(|left, right| {
        market_group_sort_key(left)
            .cmp(&market_group_sort_key(right))
            .then_with(|| left.key.cmp(&right.key))
    });
    groups
}

pub fn market_key(orderbook: &RuntimeOrderbookRow) -> String {
    if let Some(slug) = orderbook
        .market_slug
        .as_deref()
        .map(str::trim)
        .filter(|slug| !slug.is_empty())
    {
        return format!("slug={}", slug.to_ascii_lowercase());
    }

    let asset = orderbook
        .asset
        .as_deref()
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    let timestamp = orderbook
        .event_ts
        .as_deref()
        .or(orderbook.observed_ts.as_deref())
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    if !asset.is_empty() && !timestamp.is_empty() {
        return format!("asset={asset}|ts={timestamp}");
    }

    format!(
        "token={}|contract={}",
        orderbook
            .token_id
            .as_deref()
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase(),
        orderbook.contract_id.trim().to_ascii_lowercase()
    )
}

pub fn local_expiry_label(expiry_ts: Option<DateTime<Utc>>) -> String {
    expiry_ts
        .map(|ts| ts.with_timezone(&Local).format("%H:%M %Z").to_string())
        .unwrap_or_else(|| "-".to_string())
}

fn assign_side<'a>(group: &mut MarketGroup<'a>, orderbook: &'a RuntimeOrderbookRow) {
    match orderbook.side.as_deref().map(str::to_ascii_uppercase) {
        Some(side) if side == "UP" => group.up = Some(orderbook),
        Some(side) if side == "DOWN" => group.down = Some(orderbook),
        _ if group.up.is_none() => group.up = Some(orderbook),
        _ if group.down.is_none() => group.down = Some(orderbook),
        _ => {}
    }
}

fn market_group_sort_key(group: &MarketGroup<'_>) -> (u8, i64) {
    let asset_order = match group.asset.as_str() {
        "BTC" => 0,
        "ETH" => 1,
        "SOL" => 2,
        _ => 3,
    };
    let expiry = group.expiry_ts.map_or(i64::MAX, |ts| ts.timestamp());
    (asset_order, expiry)
}

pub fn expiry_ts(orderbook: &RuntimeOrderbookRow) -> Option<DateTime<Utc>> {
    if let Some(expiry_ts) = parse_utc_ts(orderbook.expiry_ts.as_deref()) {
        return Some(expiry_ts);
    }
    let epoch = slug_parts(orderbook.market_slug.as_deref())?.expiry_epoch;
    Utc.timestamp_opt(epoch, 0).single()
}

fn start_ts(orderbook: &RuntimeOrderbookRow) -> Option<DateTime<Utc>> {
    parse_utc_ts(orderbook.start_ts.as_deref())
}

fn parse_utc_ts(value: Option<&str>) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value?)
        .ok()
        .map(|ts| ts.with_timezone(&Utc))
}

#[derive(Debug)]
struct SlugParts {
    asset: String,
    interval: String,
    expiry_epoch: i64,
}

fn slug_parts(market_slug: Option<&str>) -> Option<SlugParts> {
    let parts = market_slug?.split('-').collect::<Vec<_>>();
    if parts.len() >= 4 && parts[1] == "updown" {
        Some(SlugParts {
            asset: parts[0].to_ascii_uppercase(),
            interval: parts[2].to_string(),
            expiry_epoch: parts[3].parse().ok()?,
        })
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use crate::{
        market_view::market_groups,
        status::{RuntimeMonitor, RuntimeOrderbookRow},
    };

    #[test]
    fn market_groups_merge_up_and_down_token_books_for_one_window() {
        let books = vec![
            orderbook("BTC", "UP", "btc-updown-5m-1780521900", "up-token"),
            orderbook("BTC", "DOWN", "btc-updown-5m-1780521900", "down-token"),
        ];

        let groups = market_groups(&books);

        assert_eq!(groups.len(), 1);
        assert_eq!(groups[0].asset, "BTC");
        assert_eq!(groups[0].up.unwrap().token_id.as_deref(), Some("up-token"));
        assert_eq!(
            groups[0].down.unwrap().token_id.as_deref(),
            Some("down-token")
        );
    }

    #[test]
    fn market_groups_sort_btc_before_eth_then_by_expiry() {
        let books = vec![
            orderbook("ETH", "UP", "eth-updown-5m-1780521900", "eth-token"),
            orderbook("BTC", "UP", "btc-updown-5m-1780522200", "btc-late"),
            orderbook("BTC", "UP", "btc-updown-5m-1780521900", "btc-early"),
        ];

        let groups = market_groups(&books);

        assert_eq!(groups[0].market_slug, "btc-updown-5m-1780521900");
        assert_eq!(groups[1].market_slug, "btc-updown-5m-1780522200");
        assert_eq!(groups[2].market_slug, "eth-updown-5m-1780521900");
    }

    #[test]
    fn market_groups_use_runtime_expiry_when_slug_epoch_is_start_time() {
        let mut book = orderbook("BTC", "UP", "btc-updown-5m-1780556100", "btc-token");
        book.start_ts = Some("2026-06-04T06:55:00Z".to_string());
        book.expiry_ts = Some("2026-06-04T07:00:00Z".to_string());

        let books = [book];
        let groups = market_groups(&books);

        assert_eq!(groups[0].start_ts.unwrap().timestamp(), 1_780_556_100);
        assert_eq!(groups[0].expiry_ts.unwrap().timestamp(), 1_780_556_400);
    }

    fn orderbook(
        asset: &str,
        side: &str,
        market_slug: &str,
        token_id: &str,
    ) -> RuntimeOrderbookRow {
        RuntimeOrderbookRow {
            venue: Some("polymarket".to_string()),
            source_key: Some("polymarket_rust_sdk".to_string()),
            market_slug: Some(market_slug.to_string()),
            contract_id: format!("{market_slug}-{side}"),
            token_id: Some(token_id.to_string()),
            asset: Some(asset.to_string()),
            side: Some(side.to_string()),
            event_ts: None,
            observed_ts: Some("2026-06-03T21:22:15Z".to_string()),
            start_ts: None,
            expiry_ts: None,
            best_bid: None,
            best_ask: None,
            spread: None,
            bid_size_top: None,
            ask_size_top: None,
            bids: Vec::new(),
            asks: Vec::new(),
        }
    }

    #[allow(dead_code)]
    fn monitor(orderbooks: Vec<RuntimeOrderbookRow>) -> RuntimeMonitor {
        RuntimeMonitor {
            generated_at: "2026-06-03T21:22:15Z".to_string(),
            price_rows: Vec::new(),
            orderbooks,
        }
    }
}
