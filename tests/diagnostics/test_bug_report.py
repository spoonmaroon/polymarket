from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
import json
import math

from polymarket_engine.diagnostics.bug_report import BugReport
from polymarket_engine.diagnostics.bug_report import render_llm_prompt


def _sample_bug_report(**overrides: object) -> BugReport:
    values: dict[str, Any] = dict(
        bug_id="bug-001",
        boot_id="boot-1",
        timestamp=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
        runtime_phase="DEGRADED",
        service="tui",
        severity="CRITICAL",
        contract_id="btc-up",
        market_slug="btc-updown-5m",
        asset="BTC",
        side="UP",
        tte_seconds=120.0,
        k=70_000.0,
        current_price=70_050.0,
        price_age_ms=100,
        orderbook_age_ms=100,
        sigma_tau=0.012,
        sigma_valid=True,
        probability_state="OFFLOAD_BLOCKED",
        offload_allowed=False,
        offload_block_reasons=("runtime_not_ready",),
        api_status="OK",
        websocket_status="OK",
        duckdb_status="OK",
        cpu_percent=35.0,
        memory_mb=450,
        queue_length=200,
        last_error="TUI receive lag exceeded threshold",
        stack_trace=None,
        recent_logs=("lag=5000ms",),
        suspected_module="rust/crates/polymarket-cockpit-tui/src/event_loop.rs",
        suggested_files_to_inspect=(
            "rust/crates/polymarket-cockpit-tui/src/event_loop.rs",
        ),
        suggested_tests_to_run=("cargo test -p polymarket-cockpit-tui",),
    )
    values.update(overrides)
    return BugReport(**values)


def test_bug_report_prompt_is_llm_ready() -> None:
    report = _sample_bug_report()

    json_payload = report.to_json_dict()
    prompt = render_llm_prompt(report)

    assert json_payload["timestamp"] == "2026-06-11T12:00:00+00:00"
    assert json_payload["offload_block_reasons"] == ["runtime_not_ready"]
    assert json_payload["stack_trace"] is None
    assert "A runtime bug occurred in the Polymarket probability engine" in prompt
    assert "bug-001" in prompt
    assert "Do not change unrelated architecture" in prompt


def test_bug_report_converts_non_finite_floats_for_strict_json_prompt() -> None:
    report = _sample_bug_report(
        tte_seconds=math.nan,
        k=math.inf,
        current_price=-math.inf,
        sigma_tau=math.nan,
        cpu_percent=math.inf,
    )

    json_payload = report.to_json_dict()
    with patch(
        "polymarket_engine.diagnostics.bug_report.json.dumps",
        wraps=json.dumps,
    ) as json_dumps:
        prompt = render_llm_prompt(report)
    rendered_report = json.loads(prompt.split("Bug report:\n", maxsplit=1)[1])

    assert json_dumps.call_args.kwargs["allow_nan"] is False
    assert json_payload["tte_seconds"] is None
    assert json_payload["k"] is None
    assert json_payload["current_price"] is None
    assert json_payload["sigma_tau"] is None
    assert json_payload["cpu_percent"] is None
    assert rendered_report["tte_seconds"] is None
    assert "NaN" not in prompt
    assert "Infinity" not in prompt
