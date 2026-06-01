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
    args = parser.parse_args()

    payload = json.loads(args.status_path.read_text(encoding="utf-8"))
    generated_at = _parse_timestamp(payload["generated_at"])
    now = datetime.now(timezone.utc)
    status_age = (now - generated_at.astimezone(timezone.utc)).total_seconds()
    if status_age > args.max_status_age_seconds:
        raise SystemExit(f"status file stale: age_seconds={status_age:.2f}")

    prices = payload.get("prices", [])
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

    print(
        {
            "ok": True,
            "status_age_seconds": round(status_age, 3),
            "price_age_ms": price_age_ms,
            "orderbook_age_ms": book_age_ms,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
