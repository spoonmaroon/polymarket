from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def _parse_timestamp(value: object) -> datetime:
    raw = str(value)
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("--max-status-age-seconds", type=float, default=20.0)
    parser.add_argument("--max-price-age-ms", type=int, default=10_000)
    parser.add_argument("--max-orderbook-age-ms", type=int, default=10_000)
    parser.add_argument("--max-websocket-event-age-ms", type=int, default=10_000)
    args = parser.parse_args()

    payload = json.loads(args.status_path.read_text(encoding="utf-8"))
    generated_at = _parse_timestamp(payload["generated_at"])
    now = datetime.now(timezone.utc)
    status_age = (now - generated_at.astimezone(timezone.utc)).total_seconds()
    if status_age > args.max_status_age_seconds:
        raise SystemExit(f"status file stale: age_seconds={status_age:.2f}")

    prices = payload.get("prices") or payload.get("chainlink_prices", [])
    if not prices:
        raise SystemExit("status has no price rows")
    orderbooks = payload.get("orderbooks", [])
    if not orderbooks:
        raise SystemExit("status has no orderbook rows")

    newest_price = max(_parse_timestamp(row["observed_ts"]) for row in prices)
    newest_book = max(_parse_timestamp(row["observed_ts"]) for row in orderbooks)
    price_age_ms = int((now - newest_price.astimezone(timezone.utc)).total_seconds() * 1000)
    book_age_ms = int((now - newest_book.astimezone(timezone.utc)).total_seconds() * 1000)
    if price_age_ms > args.max_price_age_ms:
        raise SystemExit(f"price rows stale: age_ms={price_age_ms}")
    if book_age_ms > args.max_orderbook_age_ms:
        raise SystemExit(f"orderbook rows stale: age_ms={book_age_ms}")
    if payload.get("mode") == "state-manager":
        _reject_state_manager_payload(
            payload,
            max_websocket_event_age_ms=args.max_websocket_event_age_ms,
        )
    else:
        _reject_bad_freshness_rows(payload.get("source_freshness", []), label="source_freshness")
        _reject_bad_freshness_rows(
            payload.get("orderbook_freshness", []),
            label="orderbook_freshness",
            allow_missing_or_stale=True,
        )
        _reject_required_source_errors(
            payload.get("source_errors", {}),
            required_source_keys=(
                "polymarket_market_ws",
                "polymarket_rtds",
            ),
        )

    print(
        {
            "ok": True,
            "status_age_seconds": round(status_age, 3),
            "price_age_ms": price_age_ms,
            "orderbook_age_ms": book_age_ms,
        }
    )
    return 0


def _reject_bad_freshness_rows(
    rows: object,
    *,
    label: str,
    allow_missing_or_stale: bool = False,
) -> None:
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = row.get("symbol", row.get("contract_id", "unknown"))
        source_key = row.get("source_key", "")
        if row.get("required") is False:
            continue
        if row.get("missing"):
            if allow_missing_or_stale:
                continue
            raise SystemExit(f"{label} missing: source={source_key} symbol={identifier}")
        if row.get("stale") or row.get("status") == "STALE":
            if allow_missing_or_stale:
                continue
            raise SystemExit(
                f"{label} stale: source={source_key} symbol={identifier} age_ms={row.get('age_ms')}"
            )


def _reject_required_source_errors(
    source_errors: object,
    *,
    required_source_keys: tuple[str, ...],
) -> None:
    if not isinstance(source_errors, dict):
        return
    for source_key in required_source_keys:
        error = source_errors.get(source_key)
        if error:
            raise SystemExit(f"required source error: source={source_key} error={error}")


def _reject_state_manager_payload(
    payload: dict,
    *,
    max_websocket_event_age_ms: int,
) -> None:
    if payload.get("schema_version") != "rust-live-probe-state-manager-v1":
        raise SystemExit("state-manager status has unexpected schema_version")
    symbols = {
        str(row.get("symbol", "")).upper()
        for row in payload.get("chainlink_prices", [])
        if row.get("source_key") == "polymarket_rtds_chainlink"
    }
    missing_symbols = {"BTC/USD", "ETH/USD"} - symbols
    if missing_symbols:
        raise SystemExit(
            "state-manager missing Chainlink symbols: "
            + ", ".join(sorted(missing_symbols))
        )
    if len(payload.get("current", [])) < 2:
        raise SystemExit("state-manager missing current BTC/ETH contracts")
    if len(payload.get("next", [])) < 2:
        raise SystemExit("state-manager missing next BTC/ETH contracts")
    _reject_bad_websocket_status(
        payload.get("websocket_status", []),
        max_event_age_ms=max_websocket_event_age_ms,
    )
    health_flags = payload.get("health_flags", [])
    if health_flags:
        raise SystemExit("state-manager health flags present: " + ", ".join(health_flags))


def _reject_bad_websocket_status(rows: object, *, max_event_age_ms: int) -> None:
    if not isinstance(rows, list) or not rows:
        raise SystemExit("state-manager missing websocket_status rows")
    required = {"polymarket_rtds_chainlink", "polymarket_clob_market_ws"}
    seen: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SystemExit(f"websocket_status[{idx}] must be an object")
        source_key = str(row.get("source_key", "")).strip()
        channel = str(row.get("channel", "")).strip()
        connection_state = str(row.get("connection_state", "")).strip()
        if not source_key:
            raise SystemExit(f"websocket_status[{idx}] missing source_key")
        if not channel:
            raise SystemExit(f"websocket_status[{idx}] missing channel")
        if not connection_state:
            raise SystemExit(f"websocket_status[{idx}] missing connection_state")
        if not connection_state.startswith("Connected"):
            raise SystemExit(
                f"websocket_status[{idx}] not connected: source={source_key} state={connection_state}"
            )
        seen.add(source_key)
        for key in (
            "reconnect_count",
            "subscription_count",
            "active_token_count",
            "ended_stream_count",
            "stream_error_count",
        ):
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise SystemExit(f"websocket_status[{idx}].{key} must be non-negative")
        if row["subscription_count"] <= 0:
            raise SystemExit(
                f"websocket_status[{idx}].subscription_count must be positive"
            )
        if row["active_token_count"] <= 0:
            raise SystemExit(
                f"websocket_status[{idx}].active_token_count must be positive"
            )
        if row["ended_stream_count"] > 0:
            raise SystemExit(
                f"websocket_status[{idx}] has ended streams: source={source_key} count={row['ended_stream_count']}"
            )
        if row["stream_error_count"] > 0:
            raise SystemExit(
                f"websocket_status[{idx}] has stream errors: source={source_key} count={row['stream_error_count']}"
            )
        age_ms = row.get("last_event_age_ms")
        if age_ms is None:
            raise SystemExit(
                f"websocket_status[{idx}].last_event_age_ms must be present"
            )
        if age_ms is not None and (
            isinstance(age_ms, bool) or not isinstance(age_ms, (int, float)) or age_ms < 0
        ):
            raise SystemExit(
                f"websocket_status[{idx}].last_event_age_ms must be non-negative or null"
            )
        if age_ms > max_event_age_ms:
            raise SystemExit(
                f"websocket_status[{idx}] event stale: source={source_key} age_ms={age_ms}"
            )
    missing = required - seen
    if missing:
        raise SystemExit(
            "state-manager missing websocket_status sources: "
            + ", ".join(sorted(missing))
        )


if __name__ == "__main__":
    raise SystemExit(main())
