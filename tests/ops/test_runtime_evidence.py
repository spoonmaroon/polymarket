from __future__ import annotations

from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import cast

from polymarket_engine.ops.runtime_keeper import HttpResult
from polymarket_engine.ops.runtime_keeper import KeeperCheck
from polymarket_engine.ops.runtime_keeper import UrlHttpClient
from polymarket_engine.ops.runtime_keeper import evaluate_http_checks
from polymarket_engine.ops.runtime_keeper import report_payload


def test_keeper_report_includes_evidence_fields() -> None:
    payload = report_payload(
        checks=[
            KeeperCheck(
                name="api:/api/runtime/live",
                ok=False,
                detail="status=502 content_type=text/html body_prefix=<html",
            )
        ],
        actions=["compose up api"],
        generated_at=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
    )

    assert payload["ok"] is False
    assert payload["generated_at"] == "2026-06-11T12:00:00+00:00"
    assert payload["checks"][0]["name"] == "api:/api/runtime/live"
    assert payload["checks"][0]["ok"] is False
    assert payload["checks"][0]["detail"] == "status=502 content_type=text/html body_prefix=<html"
    assert payload["actions"] == ["compose up api"]


def test_failed_http_checks_include_response_metadata() -> None:
    checks = evaluate_http_checks(
        health=HttpResult(200, {"status": "ok"}, "", "application/json"),
        ui=HttpResult(200, {}, "<title>Probability Runtime</title>", "text/html"),
        live=HttpResult(
            502,
            {},
            "<html>" + ("x" * 160),
            "text/html",
        ),
        probabilities=HttpResult(200, {"ok": True, "state": "OK", "rows": [{"id": 1}]}, "", "application/json"),
    )

    live_check = checks[2]
    assert live_check.ok is False
    assert live_check.detail.startswith("status=502 content_type=text/html body_prefix=<html")
    assert len(live_check.detail.split("body_prefix=", 1)[1]) == 120


def test_url_http_client_preserves_malformed_json_response_evidence() -> None:
    class MalformedJsonHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":')

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), MalformedJsonHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        host, port = cast(tuple[str, int], server.server_address)
        result = UrlHttpClient().get(f"http://{host}:{port}/bad-json", timeout_seconds=1.0)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert result.status_code == 200
    assert result.json_payload == {}
    assert result.text == '{"ok":'
    assert result.content_type == "application/json"


def test_url_http_client_parses_http_error_json_response_evidence() -> None:
    class ErrorJsonHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(500)
            self.send_header("Content-Type", "Application/JSON")
            self.end_headers()
            self.wfile.write(b'{"detail":"duckdb locked"}')

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), ErrorJsonHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        host, port = cast(tuple[str, int], server.server_address)
        result = UrlHttpClient().get(f"http://{host}:{port}/error-json", timeout_seconds=1.0)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert result.status_code == 500
    assert result.content_type == "Application/JSON"
    assert result.text == '{"detail":"duckdb locked"}'
    assert result.json_payload["detail"] == "duckdb locked"


def test_semantic_json_failures_include_error_message() -> None:
    checks = evaluate_http_checks(
        health=HttpResult(200, {"status": "ok"}, "", "application/json"),
        ui=HttpResult(200, {}, "<title>Probability Runtime</title>", "text/html"),
        live=HttpResult(
            200,
            {"ok": False, "error": "duckdb locked"},
            '{"ok":false,"error":"duckdb locked"}',
            "application/json",
        ),
        probabilities=HttpResult(
            200,
            {"ok": False, "state": "BLOCKED", "message": "inputs stale"},
            '{"ok":false,"message":"inputs stale"}',
            "application/json",
        ),
    )

    assert checks[2].ok is False
    assert checks[2].detail == (
        'status=200 content_type=application/json error=duckdb locked '
        'body_prefix={"ok":false,"error":"duckdb locked"}'
    )
    assert checks[3].ok is False
    assert checks[3].detail == (
        'status=200 content_type=application/json message=inputs stale '
        'body_prefix={"ok":false,"message":"inputs stale"}'
    )

    detail_checks = evaluate_http_checks(
        health=HttpResult(200, {"status": "ok"}, "", "application/json"),
        ui=HttpResult(200, {}, "<title>Probability Runtime</title>", "text/html"),
        live=HttpResult(
            200,
            {"ok": False, "detail": "runtime cache cold"},
            '{"ok":false,"detail":"runtime cache cold"}',
            "application/json",
        ),
        probabilities=HttpResult(
            200,
            {"ok": True, "state": "OK", "rows": [{"id": 1}]},
            "",
            "application/json",
        ),
    )

    assert detail_checks[2].ok is False
    assert detail_checks[2].detail == (
        'status=200 content_type=application/json detail=runtime cache cold '
        'body_prefix={"ok":false,"detail":"runtime cache cold"}'
    )


def test_health_failure_detail_includes_semantic_json_message_and_body_prefix() -> None:
    checks = evaluate_http_checks(
        health=HttpResult(
            500,
            {"detail": "duckdb locked"},
            '{"detail":"duckdb locked"}',
            "application/json",
        ),
        ui=HttpResult(200, {}, "<title>Probability Runtime</title>", "text/html"),
        live=HttpResult(
            200,
            {"ok": True, "monitor": {"orderbooks": [{"id": 1}]}},
            "",
            "application/json",
        ),
        probabilities=HttpResult(
            200,
            {"ok": True, "state": "OK", "rows": [{"id": 1}]},
            "",
            "application/json",
        ),
    )

    health_check = checks[0]
    assert health_check.ok is False
    assert "detail=duckdb locked" in health_check.detail
    assert 'body_prefix={"detail":"duckdb locked"}' in health_check.detail


def test_live_ok_false_with_rows_reports_semantic_failure_not_missing_rows() -> None:
    checks = evaluate_http_checks(
        health=HttpResult(200, {"status": "ok"}, "", "application/json"),
        ui=HttpResult(200, {}, "<title>Probability Runtime</title>", "text/html"),
        live=HttpResult(
            200,
            {"ok": False, "monitor": {"orderbooks": [{"id": 1}]}},
            '{"ok":false,"monitor":{"orderbooks":[{"id":1}]}}',
            "application/json",
        ),
        probabilities=HttpResult(
            200,
            {"ok": True, "state": "OK", "rows": [{"id": 1}]},
            "",
            "application/json",
        ),
    )

    live_check = checks[2]
    assert live_check.ok is False
    assert "missing live rows" not in live_check.detail
    assert "ok_not_true" in live_check.detail
    assert 'body_prefix={"ok":false,"monitor":{"orderbooks":[{"id":1}]}}' in live_check.detail


def test_probability_blocked_state_with_rows_reports_state_not_missing_rows() -> None:
    checks = evaluate_http_checks(
        health=HttpResult(200, {"status": "ok"}, "", "application/json"),
        ui=HttpResult(200, {}, "<title>Probability Runtime</title>", "text/html"),
        live=HttpResult(
            200,
            {"ok": True, "monitor": {"orderbooks": [{"id": 1}]}},
            "",
            "application/json",
        ),
        probabilities=HttpResult(
            200,
            {"ok": True, "state": "BLOCKED", "rows": [{"id": 1}]},
            '{"ok":true,"state":"BLOCKED","rows":[{"id":1}]}',
            "application/json",
        ),
    )

    probability_check = checks[3]
    assert probability_check.ok is False
    assert "missing probability rows" not in probability_check.detail
    assert "state=BLOCKED" in probability_check.detail
    assert 'body_prefix={"ok":true,"state":"BLOCKED","rows":[{"id":1}]}' in (
        probability_check.detail
    )
