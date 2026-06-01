use anyhow::{Result, bail};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ContractSide {
    Up,
    Down,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContractWindow {
    pub asset: String,
    pub interval: String,
    pub start_ts: DateTime<Utc>,
    pub end_ts: DateTime<Utc>,
}

impl ContractWindow {
    pub fn new(
        asset: &str,
        interval: &str,
        start_ts: DateTime<Utc>,
        end_ts: DateTime<Utc>,
    ) -> Result<Self> {
        if end_ts <= start_ts {
            bail!("contract window end must be after start");
        }
        let asset = asset.trim().to_ascii_uppercase();
        if asset != "BTC" && asset != "ETH" {
            bail!("unsupported asset for warmed contract window: {asset}");
        }
        Ok(Self {
            asset,
            interval: interval.to_owned(),
            start_ts,
            end_ts,
        })
    }

    pub fn slug(&self) -> String {
        format!(
            "{}-updown-{}-{}",
            self.asset.to_ascii_lowercase(),
            self.interval,
            self.start_ts.timestamp()
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ContractToken {
    pub asset: String,
    pub side: ContractSide,
    pub token_id: String,
}

impl ContractToken {
    pub fn new(asset: &str, side: ContractSide, token_id: &str) -> Self {
        Self {
            asset: asset.to_ascii_uppercase(),
            side,
            token_id: token_id.to_owned(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WarmedContract {
    pub window: ContractWindow,
    pub up: ContractToken,
    pub down: ContractToken,
}

impl WarmedContract {
    pub fn new(window: ContractWindow, up: ContractToken, down: ContractToken) -> Result<Self> {
        if up.side != ContractSide::Up || down.side != ContractSide::Down {
            bail!("warmed contract requires one UP token and one DOWN token");
        }
        if up.asset != window.asset || down.asset != window.asset {
            bail!("warmed contract token assets must match window asset");
        }
        Ok(Self { window, up, down })
    }

    pub fn token_ids(&self) -> Vec<String> {
        vec![self.up.token_id.clone(), self.down.token_id.clone()]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};

    #[test]
    fn contract_window_tracks_start_end_and_slug() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let end = Utc.timestamp_opt(1_780_302_700, 0).unwrap();
        let window = ContractWindow::new("BTC", "5m", start, end).unwrap();

        assert_eq!(window.asset, "BTC");
        assert_eq!(window.interval, "5m");
        assert_eq!(window.slug(), "btc-updown-5m-1780302400");
    }

    #[test]
    fn warmed_contract_requires_up_and_down_tokens() {
        let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
        let end = Utc.timestamp_opt(1_780_302_700, 0).unwrap();
        let window = ContractWindow::new("ETH", "5m", start, end).unwrap();
        let contract = WarmedContract::new(
            window,
            ContractToken::new("ETH", ContractSide::Up, "111"),
            ContractToken::new("ETH", ContractSide::Down, "222"),
        )
        .unwrap();

        assert_eq!(
            contract.token_ids(),
            vec!["111".to_owned(), "222".to_owned()]
        );
    }
}
