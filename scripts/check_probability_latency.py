#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--max-total-lag-ms", type=float, required=True)
    parser.add_argument("--require-lane", action="append", default=[])
    args = parser.parse_args()

    payload = json.loads(args.path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "polymarket-probability-runtime-v1":
        print("invalid_probability_schema", file=sys.stderr)
        return 1

    lanes = payload.get("lanes", {})
    if not isinstance(lanes, dict):
        print("invalid_probability_lanes", file=sys.stderr)
        return 1
    for lane in args.require_lane:
        if int(lanes.get(lane, 0)) <= 0:
            print(f"missing_probability_lane lane={lane}", file=sys.stderr)
            return 1

    latency = payload.get("latency", {})
    if not isinstance(latency, dict):
        print("invalid_probability_latency", file=sys.stderr)
        return 1
    max_lag = latency.get("max_total_lag_ms")
    if max_lag is None:
        print("missing_probability_latency", file=sys.stderr)
        return 1
    if float(max_lag) > args.max_total_lag_ms:
        print(
            f"probability_lag_too_high max_total_lag_ms={max_lag}",
            file=sys.stderr,
        )
        return 1

    print(f"probability_latency=ok max_total_lag_ms={max_lag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
