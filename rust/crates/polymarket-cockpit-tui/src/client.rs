use std::time::Duration;

use crate::status::{RuntimeGates, RuntimeStatus};

const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Debug, Clone)]
pub struct EngineClient {
    base_url: String,
    client: reqwest::Client,
}

#[cfg(test)]
mod tests {
    use std::{
        net::TcpListener,
        thread,
        time::{Duration, Instant},
    };

    use super::EngineClient;

    #[tokio::test]
    async fn status_request_times_out_on_half_open_api() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let _server = thread::spawn(move || {
            let Ok((_stream, _peer)) = listener.accept() else {
                return;
            };
            thread::sleep(Duration::from_secs(2));
        });

        let client = EngineClient::with_request_timeout(
            format!("http://{address}"),
            Duration::from_millis(50),
        );
        let started = Instant::now();

        let result = client.status().await;

        assert!(result.is_err());
        assert!(started.elapsed() < Duration::from_secs(1));
    }
}

impl EngineClient {
    pub fn new(base_url: impl Into<String>) -> Self {
        Self::with_request_timeout(base_url, DEFAULT_REQUEST_TIMEOUT)
    }

    pub fn with_request_timeout(base_url: impl Into<String>, timeout: Duration) -> Self {
        Self {
            base_url: base_url.into().trim_end_matches('/').to_string(),
            client: reqwest::Client::builder()
                .timeout(timeout)
                .build()
                .expect("request timeout client configuration is valid"),
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
