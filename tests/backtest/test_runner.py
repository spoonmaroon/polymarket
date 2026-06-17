from __future__ import annotations

import json
from pathlib import Path

from polymarket_engine.backtest.runner import BacktestRunConfig
from polymarket_engine.backtest.runner import run_backtest


def _record(state_id: str, probability: float, label: int) -> dict[str, object]:
    return {
        "state_id": state_id,
        "contract_id": f"contract-{state_id}",
        "asset": "BTC",
        "side": "UP",
        "asof_ts": "2026-06-10T12:00:00+00:00",
        "expiry_ts": "2026-06-10T12:05:00+00:00",
        "p_finish_mc": probability,
        "target_size_ask_vwap": 0.64,
        "target_size_bid_vwap": 0.62,
        "best_ask": 0.64,
        "best_bid": 0.62,
        "quote_age_ms": 250.0,
        "skip_or_block_reason": None,
        "final_label": label,
    }


def test_run_backtest_writes_report_and_trade_rows(tmp_path: Path) -> None:
    input_path = tmp_path / "dataset.jsonl"
    out_path = tmp_path / "backtest.json"
    input_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                _record("state-1", 0.72, 1),
                _record("state-2", 0.73, 0),
                _record("state-3", 0.60, 1),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = run_backtest(
        BacktestRunConfig(
            input_path=input_path,
            out_path=out_path,
            probability_field="p_finish_mc",
            stake_usd=100.0,
            min_edge=0.02,
            max_quote_age_ms=1000,
            fee_rate=0.0,
        )
    )

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert report.trade_count == 2
    assert payload["schema_version"] == "polymarket-backtest-report-v1"
    assert payload["input_row_count"] == 3
    assert payload["trade_count"] == 2
    assert payload["skipped_count"] == 1
    assert payload["win_rate"] == 0.5
    assert round(payload["total_pnl"], 6) == -43.75
    assert [trade["state_id"] for trade in payload["trades"]] == ["state-1", "state-2"]
