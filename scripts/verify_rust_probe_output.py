from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--max-btc-disagreement-bps", type=float, default=100.0)
    parser.add_argument("--require-orderbooks", action="store_true")
    parser.add_argument("--require-btc-prices", action="store_true")
    parser.add_argument("--require-btc-disagreement", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "rust-live-probe-v1":
        raise SystemExit("wrong schema_version")
    if not isinstance(payload.get("elapsed_ms"), int) or payload["elapsed_ms"] < 0:
        raise SystemExit("elapsed_ms missing or invalid")
    if args.require_orderbooks and not payload.get("orderbooks"):
        raise SystemExit("expected at least one normalized orderbook")
    if args.require_btc_prices:
        symbols = {
            (row.get("source_key"), row.get("symbol"))
            for row in payload.get("prices", [])
        }
        if ("polymarket_rtds_chainlink", "BTC/USD") not in symbols:
            raise SystemExit("missing Chainlink BTC/USD price")
        if ("kraken_rest", "XBT/USD") not in symbols:
            raise SystemExit("missing Kraken XBT/USD price")
    required_marks = {
        "start",
        "contracts_discovered",
        "orderbooks_normalized",
        "chainlink_btc_received",
        "kraken_btc_received",
        "source_disagreement_calculated",
        "report_written",
    }
    if args.require_orderbooks or args.require_btc_prices or args.require_btc_disagreement:
        marks = {row.get("name") for row in payload.get("latency_marks", [])}
        missing_marks = sorted(required_marks - marks)
        if missing_marks:
            raise SystemExit(f"missing latency marks: {', '.join(missing_marks)}")
    btc_disagreement_count = 0
    for row in payload.get("source_disagreements", []):
        if row.get("asset") != "BTC":
            continue
        btc_disagreement_count += 1
        diff_bps = float(row["diff_bps"])
        if not math.isfinite(diff_bps):
            raise SystemExit(f"BTC disagreement is not finite: {row['diff_bps']}")
        if diff_bps > args.max_btc_disagreement_bps:
            raise SystemExit(f"BTC disagreement too high: {row['diff_bps']} bps")
    if (args.require_btc_prices or args.require_btc_disagreement) and btc_disagreement_count == 0:
        raise SystemExit("missing BTC source disagreement")
    print(
        "ok",
        f"elapsed_ms={payload['elapsed_ms']}",
        f"orderbooks={len(payload.get('orderbooks', []))}",
        f"prices={len(payload.get('prices', []))}",
        f"disagreements={len(payload.get('source_disagreements', []))}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
