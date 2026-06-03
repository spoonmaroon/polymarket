use crate::status::{RuntimeGates, RuntimeStatus};

#[derive(Debug, Clone)]
pub struct EngineClient {
    base_url: String,
    client: reqwest::Client,
}

impl EngineClient {
    pub fn new(base_url: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into().trim_end_matches('/').to_string(),
            client: reqwest::Client::new(),
        }
    }

    pub async fn status(&self) -> anyhow::Result<RuntimeStatus> {
        self.get_json("/api/runtime/status").await
    }

    pub async fn gates(&self) -> anyhow::Result<RuntimeGates> {
        self.get_json("/api/runtime/gates").await
    }

    async fn get_json<T>(&self, path: &str) -> anyhow::Result<T>
    where
        T: serde::de::DeserializeOwned,
    {
        let url = format!("{}{}", self.base_url, path);
        Ok(self
            .client
            .get(url)
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?)
    }
}
