from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields
from datetime import datetime
from typing import Any
import json
import math


@dataclass(frozen=True)
class BugReport:
    bug_id: str
    boot_id: str | None
    timestamp: datetime
    runtime_phase: str
    service: str
    severity: str
    contract_id: str | None
    market_slug: str | None
    asset: str | None
    side: str | None
    tte_seconds: float | None
    k: float | None
    current_price: float | None
    price_age_ms: int | None
    orderbook_age_ms: int | None
    sigma_tau: float | None
    sigma_valid: bool
    probability_state: str
    offload_allowed: bool
    offload_block_reasons: tuple[str, ...]
    api_status: str
    websocket_status: str
    duckdb_status: str
    cpu_percent: float | None
    memory_mb: int | None
    queue_length: int | None
    last_error: str | None
    stack_trace: str | None
    recent_logs: tuple[str, ...]
    suspected_module: str | None
    suggested_files_to_inspect: tuple[str, ...]
    suggested_tests_to_run: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            field.name: _json_safe_value(getattr(self, field.name))
            for field in fields(self)
        }


def render_llm_prompt(report: BugReport) -> str:
    return (
        "A runtime bug occurred in the Polymarket probability engine. "
        "Diagnose the likely cause and propose a minimal safe patch. "
        "Use the bug report, stack trace, recent logs, and relevant source files. "
        "Do not change unrelated architecture. Add or update tests. "
        "Explain the root cause, the fix, and how to verify it.\n\n"
        "Bug report:\n"
        f"{json.dumps(report.to_json_dict(), indent=2, sort_keys=True, allow_nan=False)}\n"
    )


def _json_safe_value(value: object) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    return value
