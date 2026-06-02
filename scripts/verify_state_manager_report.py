#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


REQUIRED_ASSETS = {"BTC", "ETH"}
REQUIRED_CHAINLINK_SYMBOLS = {"BTC/USD", "ETH/USD"}
STATE_MANAGER_SCHEMA_VERSION = "rust-live-probe-state-manager-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a state-manager smoke report JSON file."
    )
    parser.add_argument("report", type=Path, help="Path to state-manager report JSON")
    return parser.parse_args()


def fail(message: str) -> None:
    raise SystemExit(f"state-manager report invalid: {message}")


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{name} must be an object")
    return value


def require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        fail(f"{key} must be a list")
    return value


def require_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{name} must be an object")
    return value


def require_non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{name} must be a non-empty string")
    return value.strip()


def require_timestamp(value: Any, name: str) -> datetime:
    text = require_non_empty_string(value, name)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        fail(f"{name} must be an RFC3339 timestamp")


def require_non_negative_number(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{name} must be a non-negative number")
    if value < 0:
        fail(f"{name} must be non-negative")


def require_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        fail(f"{name} must be a decimal value")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        fail(f"{name} must be a decimal value")
    if parsed <= 0:
        fail(f"{name} must be positive")
    return parsed


def validate_contract(row: Any, name: str) -> str:
    contract = require_object(row, name)
    window = require_object(contract.get("window"), f"{name}.window")
    asset = require_non_empty_string(window.get("asset"), f"{name}.window.asset").upper()
    interval = require_non_empty_string(window.get("interval"), f"{name}.window.interval")
    if interval != "5m":
        fail(f"{name}.window.interval must be 5m")
    start_ts = require_timestamp(window.get("start_ts"), f"{name}.window.start_ts")
    end_ts = require_timestamp(window.get("end_ts"), f"{name}.window.end_ts")
    if end_ts <= start_ts:
        fail(f"{name}.window.end_ts must be after start_ts")

    for side_name, expected_side in (("up", "Up"), ("down", "Down")):
        token = require_object(contract.get(side_name), f"{name}.{side_name}")
        token_asset = require_non_empty_string(
            token.get("asset"), f"{name}.{side_name}.asset"
        ).upper()
        if token_asset != asset:
            fail(f"{name}.{side_name}.asset must match window asset")
        token_side = require_non_empty_string(token.get("side"), f"{name}.{side_name}.side")
        if token_side != expected_side:
            fail(f"{name}.{side_name}.side must be {expected_side}")
        require_non_empty_string(token.get("token_id"), f"{name}.{side_name}.token_id")
    return asset


def validate_contracts(rows: list[Any], key: str, require_assets: bool) -> set[str]:
    assets = {validate_contract(row, f"{key}[{idx}]") for idx, row in enumerate(rows)}
    if require_assets:
        missing = sorted(REQUIRED_ASSETS - assets)
        if missing:
            fail(f"{key} missing assets: {', '.join(missing)}")
    return assets


def validate_price_tick(row: Any, name: str) -> tuple[str, str]:
    tick = require_object(row, name)
    source_key = require_non_empty_string(tick.get("source_key"), f"{name}.source_key")
    symbol = require_non_empty_string(tick.get("symbol"), f"{name}.symbol").upper()
    require_timestamp(tick.get("event_ts"), f"{name}.event_ts")
    require_timestamp(tick.get("observed_ts"), f"{name}.observed_ts")
    require_decimal(tick.get("price"), f"{name}.price")
    return source_key, symbol


def chainlink_symbols(payload: dict[str, Any]) -> set[str]:
    prices = require_list(payload, "chainlink_prices")
    symbols: set[str] = set()
    for idx, row in enumerate(prices):
        source_key, symbol = validate_price_tick(row, f"chainlink_prices[{idx}]")
        if source_key == "polymarket_rtds_chainlink":
            symbols.add(symbol)
    return symbols


def validate_optional_price_list(payload: dict[str, Any], key: str) -> None:
    for idx, row in enumerate(require_list(payload, key)):
        validate_price_tick(row, f"{key}[{idx}]")


def validate_orderbook(row: Any, name: str) -> str:
    book = require_object(row, name)
    for key in (
        "venue",
        "source_key",
        "market_slug",
        "contract_id",
        "token_id",
        "asset",
        "side",
    ):
        require_non_empty_string(book.get(key), f"{name}.{key}")
    require_timestamp(book.get("event_ts"), f"{name}.event_ts")
    require_timestamp(book.get("observed_ts"), f"{name}.observed_ts")
    if not isinstance(book.get("bids"), list):
        fail(f"{name}.bids must be a list")
    if not isinstance(book.get("asks"), list):
        fail(f"{name}.asks must be a list")
    return str(book["token_id"])


def validate_freshness(payload: dict[str, Any]) -> None:
    rows = require_list(payload, "freshness")
    for idx, row in enumerate(rows):
        freshness = require_object(row, f"freshness[{idx}]")
        require_non_empty_string(freshness.get("source_key"), f"freshness[{idx}].source_key")
        require_non_empty_string(freshness.get("symbol"), f"freshness[{idx}].symbol")
        require_non_negative_number(freshness.get("age_ms"), f"freshness[{idx}].age_ms")
        if not isinstance(freshness.get("stale"), bool):
            fail(f"freshness[{idx}].stale must be a boolean")


def validate_subscriptions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = require_list(payload, "subscriptions")
    subscriptions: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        subscription = require_object(row, f"subscriptions[{idx}]")
        for key in ("source_key", "channel", "asset", "token_id"):
            require_non_empty_string(subscription.get(key), f"subscriptions[{idx}].{key}")
        subscriptions.append(subscription)
    return subscriptions


def validate_websocket_status(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = require_list(payload, "websocket_status")
    statuses: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        status = require_object(row, f"websocket_status[{idx}]")
        for key in ("source_key", "channel", "connection_state"):
            require_non_empty_string(status.get(key), f"websocket_status[{idx}].{key}")
        for key in (
            "reconnect_count",
            "subscription_count",
            "active_token_count",
            "ended_stream_count",
            "stream_error_count",
        ):
            require_non_negative_number(status.get(key), f"websocket_status[{idx}].{key}")
        age_ms = status.get("last_event_age_ms")
        if age_ms is not None:
            require_non_negative_number(age_ms, f"websocket_status[{idx}].last_event_age_ms")
        statuses.append(status)
    return statuses


def validate_latency_marks(payload: dict[str, Any]) -> None:
    rows = require_list(payload, "latency_marks")
    required = {
        "chainlink_observed_age_ms",
        "chainlink_event_to_observed_ms",
        "orderbook_observed_age_ms",
        "orderbook_event_to_observed_ms",
    }
    names: set[str] = set()
    for idx, row in enumerate(rows):
        mark = require_object(row, f"latency_marks[{idx}]")
        name = require_non_empty_string(mark.get("name"), f"latency_marks[{idx}].name")
        require_non_negative_number(
            mark.get("elapsed_ms"),
            f"latency_marks[{idx}].elapsed_ms",
        )
        names.add(name)
    missing = sorted(required - names)
    if missing:
        fail("latency_marks missing: " + ", ".join(missing))


def validate_hot_decision_telemetry(payload: dict[str, Any]) -> None:
    telemetry = payload.get("hot_decision_telemetry")
    if telemetry is None:
        return
    telemetry = require_object(telemetry, "hot_decision_telemetry")
    for key in (
        "states_built",
        "states_persist_queued",
        "dropped_events",
        "last_state_age_ms",
        "last_observed_to_state_us",
    ):
        if key not in telemetry:
            fail(f"hot_decision_telemetry missing {key}")
    for key in ("states_built", "states_persist_queued", "dropped_events"):
        require_non_negative_number(telemetry.get(key), f"hot_decision_telemetry.{key}")
    for key in ("last_state_age_ms", "last_observed_to_state_us"):
        value = telemetry.get(key)
        if value is not None:
            require_non_negative_number(value, f"hot_decision_telemetry.{key}")
    if telemetry["states_built"] < telemetry["states_persist_queued"]:
        fail("hot_decision_telemetry states_built is less than states_persist_queued")


def validate(payload: dict[str, Any]) -> list[str]:
    if payload.get("schema_version") != STATE_MANAGER_SCHEMA_VERSION:
        fail(f'schema_version must be "{STATE_MANAGER_SCHEMA_VERSION}"')
    if payload.get("mode") != "state-manager":
        fail('mode must be "state-manager"')
    require_timestamp(payload.get("generated_at"), "generated_at")
    require_non_negative_number(payload.get("elapsed_ms"), "elapsed_ms")

    validate_contracts(require_list(payload, "current"), "current", require_assets=True)
    validate_contracts(require_list(payload, "next"), "next", require_assets=True)
    validate_contracts(require_list(payload, "next_next"), "next_next", require_assets=True)
    validate_optional_price_list(payload, "proxy_prices")
    validate_freshness(payload)
    validate_latency_marks(payload)
    validate_hot_decision_telemetry(payload)

    orderbooks = require_list(payload, "orderbooks")
    orderbook_tokens = {
        validate_orderbook(row, f"orderbooks[{idx}]") for idx, row in enumerate(orderbooks)
    }
    subscriptions = validate_subscriptions(payload)
    websocket_status = validate_websocket_status(payload)
    status_sources = {
        str(status["source_key"])
        for status in websocket_status
    }
    missing_status_sources = sorted(
        {"polymarket_rtds_chainlink", "polymarket_clob_market_ws"} - status_sources
    )
    if missing_status_sources:
        fail(
            "missing websocket_status sources: "
            + ", ".join(missing_status_sources)
        )
    if subscriptions and len(orderbooks) < 4:
        fail(f"expected at least 4 orderbooks, found {len(orderbooks)}")
    if subscriptions:
        missing_book_tokens = sorted(
            str(subscription["token_id"])
            for subscription in subscriptions
            if str(subscription["token_id"]) not in orderbook_tokens
        )
        if missing_book_tokens:
            fail(
                "subscriptions missing matching orderbooks: "
                + ", ".join(missing_book_tokens)
            )

    missing_symbols = sorted(REQUIRED_CHAINLINK_SYMBOLS - chainlink_symbols(payload))
    if missing_symbols:
        fail(f"missing Chainlink prices: {', '.join(missing_symbols)}")

    health_flags = payload.get("health_flags")
    if health_flags is None:
        fail("health_flags must be present")
    if not isinstance(health_flags, list):
        fail("health_flags must be a list")
    bad_flags = [flag for flag in health_flags if not isinstance(flag, str)]
    if bad_flags:
        fail("health_flags entries must be strings")
    return health_flags


def main() -> int:
    args = parse_args()
    try:
        payload = json.loads(args.report.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"report not found: {args.report}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON: {exc}")

    payload = require_mapping(payload, "report")
    health_flags = validate(payload)
    print(
        "ok",
        "mode=state-manager",
        f"current={len(payload['current'])}",
        f"next={len(payload['next'])}",
        f"next_next={len(payload['next_next'])}",
        f"orderbooks={len(payload['orderbooks'])}",
        f"subscriptions={len(payload['subscriptions'])}",
        f"websocket_status={len(payload['websocket_status'])}",
        f"health_flags={len(health_flags)}",
    )
    if health_flags:
        print("health_flags:")
        for flag in health_flags:
            print(f"- {flag}")
    else:
        print("health_flags: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
