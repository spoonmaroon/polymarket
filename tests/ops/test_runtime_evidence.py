from __future__ import annotations

from datetime import UTC, datetime

from polymarket_engine.ops.runtime_keeper import HttpResult
from polymarket_engine.ops.runtime_keeper import KeeperCheck
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
