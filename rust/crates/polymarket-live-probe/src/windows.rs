use anyhow::{Result, anyhow, bail};
use chrono::{DateTime, Duration, Utc};
use polymarket_runtime_types::ContractWindow;

pub fn schedule_windows(
    now: DateTime<Utc>,
    assets: &[&str],
    interval: &str,
    count: u8,
) -> Result<Vec<ContractWindow>> {
    let seconds = match interval {
        "5m" => 300,
        "15m" => 900,
        other => bail!("unsupported interval: {other}"),
    };
    let start_epoch = now.timestamp() - now.timestamp().rem_euclid(seconds);
    let mut out = Vec::with_capacity(assets.len() * usize::from(count));
    for index in 0..i64::from(count) {
        let start = DateTime::<Utc>::from_timestamp(start_epoch + seconds * index, 0)
            .ok_or_else(|| anyhow!("invalid start epoch"))?;
        let end = start + Duration::seconds(seconds);
        for asset in assets {
            out.push(ContractWindow::new(asset, interval, start, end)?);
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};

    #[test]
    fn schedules_current_next_and_next_next_5m_windows() {
        let now = Utc.timestamp_opt(1_780_302_456, 0).unwrap();
        let windows = schedule_windows(now, &["BTC", "ETH"], "5m", 3).unwrap();

        assert_eq!(windows.len(), 6);
        assert_eq!(windows[0].slug(), "btc-updown-5m-1780302300");
        assert_eq!(windows[1].slug(), "eth-updown-5m-1780302300");
        assert_eq!(windows[2].slug(), "btc-updown-5m-1780302600");
    }
}
