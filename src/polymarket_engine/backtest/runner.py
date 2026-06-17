from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from polymarket_engine.backtest.fills import BacktestFillConfig
from polymarket_engine.backtest.fills import simulate_hold_to_expiry_trade
from polymarket_engine.backtest.report import BacktestReport
from polymarket_engine.backtest.report import build_backtest_report
from polymarket_engine.calibration.reports import load_calibration_jsonl


@dataclass(frozen=True)
class BacktestRunConfig:
    input_path: Path
    out_path: Path
    probability_field: str
    stake_usd: float
    min_edge: float
    max_quote_age_ms: int
    fee_rate: float


def run_backtest(config: BacktestRunConfig) -> BacktestReport:
    rows = load_calibration_jsonl(config.input_path)
    fill_config = BacktestFillConfig(
        stake_usd=config.stake_usd,
        min_edge=config.min_edge,
        max_quote_age_ms=config.max_quote_age_ms,
        fee_rate=config.fee_rate,
    )
    trades = tuple(
        trade
        for row in rows
        if (
            trade := simulate_hold_to_expiry_trade(
                row,
                fill_config,
                probability_field=config.probability_field,
            )
        )
        is not None
    )
    report = build_backtest_report(input_row_count=len(rows), trades=trades)
    config.out_path.parent.mkdir(parents=True, exist_ok=True)
    config.out_path.write_text(
        json.dumps(report.to_json_dict(), allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
