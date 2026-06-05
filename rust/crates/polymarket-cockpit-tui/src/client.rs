use std::time::Duration;

use crate::status::{
    RuntimeGates, RuntimeLive, RuntimeMonitor, RuntimeMonteCarloStatus, RuntimeOutcomes,
    RuntimeProbabilities, RuntimeStatus,
};

const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Debug, Clone)]
pub struct EngineClient {
    base_url: String,
    client: reqwest::Client,
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

    pub async fn monitor(&self, limit: usize) -> anyhow::Result<RuntimeMonitor> {
        self.get_json(&format!("/api/runtime/monitor?limit={limit}"))
            .await
    }

    pub async fn live(&self, limit: usize) -> anyhow::Result<RuntimeLive> {
        self.get_json(&format!("/api/runtime/live?limit={limit}"))
            .await
    }

    pub async fn live_stream_response(
        &self,
        limit: usize,
        interval_ms: u64,
    ) -> anyhow::Result<reqwest::Response> {
        let url = format!(
            "{}/api/runtime/live/stream?limit={limit}&interval_ms={interval_ms}",
            self.base_url
        );
        Ok(self.client.get(url).send().await?.error_for_status()?)
    }

    pub async fn probabilities(&self, limit: usize) -> anyhow::Result<RuntimeProbabilities> {
        self.get_json(&format!("/api/runtime/probabilities?limit={limit}"))
            .await
    }

    pub async fn monte_carlo_status(
        &self,
        limit: usize,
    ) -> anyhow::Result<RuntimeMonteCarloStatus> {
        self.get_json(&format!("/api/runtime/monte-carlo/status?limit={limit}"))
            .await
    }

    pub async fn outcomes(&self, limit: usize) -> anyhow::Result<RuntimeOutcomes> {
        self.get_json(&format!("/api/runtime/outcomes?limit={limit}"))
            .await
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

#[cfg(test)]
mod tests {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::mpsc,
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

    #[tokio::test]
    async fn monitor_request_includes_limit_and_parses_payload() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (request_tx, request_rx) = mpsc::channel();
        let _server = thread::spawn(move || {
            let Ok((mut stream, _peer)) = listener.accept() else {
                return;
            };

            let mut buffer = [0; 512];
            let bytes_read = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..bytes_read]).to_string();
            let first_line = request.lines().next().unwrap_or_default().to_string();
            request_tx.send(first_line).unwrap();

            let body = r#"{
                "generated_at": "2026-06-03T20:43:20.744215+00:00",
                "price_rows": [{
                    "source_key": "polymarket_rtds_chainlink",
                    "symbol": "BTC/USD",
                    "event_ts": "2026-06-03T20:43:16Z",
                    "observed_ts": "2026-06-03T20:43:19.789163241Z",
                    "price": "65185.18675916348"
                }],
                "orderbooks": []
            }"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                body.len(),
                body
            );
            stream.write_all(response.as_bytes()).unwrap();
        });

        let client = EngineClient::with_request_timeout(
            format!("http://{address}"),
            Duration::from_millis(500),
        );

        let monitor = client.monitor(8).await.unwrap();

        assert_eq!(monitor.price_rows[0].symbol, "BTC/USD");
        assert_eq!(
            request_rx.recv_timeout(Duration::from_secs(1)).unwrap(),
            "GET /api/runtime/monitor?limit=8 HTTP/1.1"
        );
    }

    #[tokio::test]
    async fn monte_carlo_status_request_includes_limit_and_parses_payload() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (request_tx, request_rx) = mpsc::channel();
        let _server = thread::spawn(move || {
            let Ok((mut stream, _peer)) = listener.accept() else {
                return;
            };

            let mut buffer = [0; 512];
            let bytes_read = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..bytes_read]).to_string();
            let first_line = request.lines().next().unwrap_or_default().to_string();
            request_tx.send(first_line).unwrap();

            let body = r#"{
                "ok": true,
                "state": "OK",
                "generated_at": "2026-06-05T12:00:00Z",
                "rows": [{
                    "contract": "BTC 5m UP",
                    "p_finish": 0.57,
                    "p_no_touch": 0.31,
                    "z_path": 0.42,
                    "sigma_tau": 0.0123,
                    "backend": "cpu-rayon",
                    "path_count": 65536,
                    "model_version": "rust-mc-v1",
                    "age_ms": 850,
                    "flags": ["cached"],
                    "artifact_id": "artifact-1"
                }],
                "errors": []
            }"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                body.len(),
                body
            );
            stream.write_all(response.as_bytes()).unwrap();
        });

        let client = EngineClient::with_request_timeout(
            format!("http://{address}"),
            Duration::from_millis(500),
        );

        let status = client.monte_carlo_status(8).await.unwrap();

        assert_eq!(status.rows[0].contract, "BTC 5m UP");
        assert_eq!(status.rows[0].artifact_id.as_deref(), Some("artifact-1"));
        assert_eq!(
            request_rx.recv_timeout(Duration::from_secs(1)).unwrap(),
            "GET /api/runtime/monte-carlo/status?limit=8 HTTP/1.1"
        );
    }
}
