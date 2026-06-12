use std::time::Duration;

use crate::status::{
    RuntimeBugReports, RuntimeGates, RuntimeLive, RuntimeMonitor, RuntimeOutcomes,
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

    pub async fn outcomes(&self, limit: usize) -> anyhow::Result<RuntimeOutcomes> {
        self.get_json(&format!("/api/runtime/outcomes?limit={limit}"))
            .await
    }

    pub async fn bug_reports(&self, limit: usize) -> anyhow::Result<RuntimeBugReports> {
        self.get_json(&format!("/api/runtime/bug-reports?limit={limit}"))
            .await
    }

    async fn get_json<T>(&self, path: &str) -> anyhow::Result<T>
    where
        T: serde::de::DeserializeOwned,
    {
        let url = format!("{}{}", self.base_url, path);
        let response = self.client.get(url).send().await?;
        let status = response.status();
        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .unwrap_or("")
            .to_string();
        let is_blocked =
            !status.is_success() || !content_type.to_ascii_lowercase().contains("json");
        let body = match response.text().await {
            Ok(body) => body,
            Err(error) if is_blocked => {
                anyhow::bail!(
                    "API_BLOCKED status={} content_type={} body_prefix=<body_read_error: {}>",
                    status.as_u16(),
                    content_type,
                    error
                );
            }
            Err(error) => return Err(error.into()),
        };
        if is_blocked {
            anyhow::bail!(
                "API_BLOCKED status={} content_type={} body_prefix={}",
                status.as_u16(),
                content_type,
                body_prefix(&body)
            );
        }
        Ok(serde_json::from_str(&body)?)
    }
}

fn body_prefix(body: &str) -> String {
    body.chars().take(120).collect()
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
    async fn status_request_classifies_non_json_body() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let _server = thread::spawn(move || {
            let Ok((mut stream, _peer)) = listener.accept() else {
                return;
            };
            let mut buffer = [0; 512];
            let _ = stream.read(&mut buffer).unwrap();
            let body = "<html>blocked</html>";
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\n\r\n{}",
                body.len(),
                body
            );
            stream.write_all(response.as_bytes()).unwrap();
        });

        let client = EngineClient::with_request_timeout(
            format!("http://{address}"),
            Duration::from_millis(500),
        );

        let result = client.status().await;

        assert!(result.is_err());
        let error = format!("{:#}", result.unwrap_err());
        assert!(error.contains("API_BLOCKED"));
        assert!(error.contains("status=200"));
        assert!(error.contains("content_type=text/html"));
        assert!(error.contains("body_prefix=<html>blocked</html>"));
    }

    #[tokio::test]
    async fn status_request_classifies_blocked_body_read_error() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let _server = thread::spawn(move || {
            let Ok((mut stream, _peer)) = listener.accept() else {
                return;
            };
            let mut buffer = [0; 512];
            let _ = stream.read(&mut buffer).unwrap();
            let body = "<html";
            let response = format!(
                "HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/html\r\nContent-Length: {}\r\n\r\n{}",
                body.len() + 10,
                body
            );
            stream.write_all(response.as_bytes()).unwrap();
        });

        let client = EngineClient::with_request_timeout(
            format!("http://{address}"),
            Duration::from_millis(500),
        );

        let result = client.status().await;

        assert!(result.is_err());
        let error = format!("{:#}", result.unwrap_err());
        assert!(error.contains("API_BLOCKED"));
        assert!(error.contains("status=502"));
        assert!(error.contains("content_type=text/html"));
        assert!(error.contains("body_prefix=<body_read_error:"));
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
    async fn bug_reports_request_includes_limit_and_parses_payload() {
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
                "schema_version": "polymarket-runtime-bug-reports-v1",
                "ok": true,
                "state": "OK",
                "path": "/var/lib/polymarket/live/bug-reports",
                "generated_at": "2026-06-12T03:00:00+00:00",
                "reports": [{
                    "bug_id": "BUG-009",
                    "severity": "warning",
                    "title": "offload mismatch",
                    "component": "probability",
                    "source_path": "/var/lib/polymarket/live/bug-reports/bug-009.json"
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

        let reports = client.bug_reports(5).await.unwrap();

        assert_eq!(reports.reports[0].bug_id.as_deref(), Some("BUG-009"));
        assert_eq!(
            request_rx.recv_timeout(Duration::from_secs(1)).unwrap(),
            "GET /api/runtime/bug-reports?limit=5 HTTP/1.1"
        );
    }
}
