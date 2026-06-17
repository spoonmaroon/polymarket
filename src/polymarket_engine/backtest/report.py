from __future__ import annotations

from dataclasses import dataclass

from polymarket_engine.backtest.fills import BacktestTrade


@dataclass(frozen=True)
class BacktestReportProvenance:
    probability_field: str
    stake_usd: float
    min_edge: float
    max_quote_age_ms: int
    fee_rate: float

    def to_json_dict(self) -> dict[str, object]:
        return {
            "probability_field": self.probability_field,
            "fee_rate": self.fee_rate,
            "fill_config": {
                "stake_usd": self.stake_usd,
                "min_edge": self.min_edge,
                "max_quote_age_ms": self.max_quote_age_ms,
                "fee_rate": self.fee_rate,
            },
        }


@dataclass(frozen=True)
class BacktestReport:
    schema_version: str
    input_row_count: int
    trade_count: int
    skipped_count: int
    win_rate: float | None
    total_staked: float
    total_pnl: float
    roi: float | None
    mean_edge: float | None
    provenance: BacktestReportProvenance
    trades: tuple[BacktestTrade, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "input_row_count": self.input_row_count,
            "trade_count": self.trade_count,
            "skipped_count": self.skipped_count,
            "win_rate": self.win_rate,
            "total_staked": self.total_staked,
            "total_pnl": self.total_pnl,
            "roi": self.roi,
            "mean_edge": self.mean_edge,
            "provenance": self.provenance.to_json_dict(),
            "trades": [trade.to_json_dict() for trade in self.trades],
        }


def build_backtest_report(
    *,
    input_row_count: int,
    provenance: BacktestReportProvenance,
    trades: tuple[BacktestTrade, ...],
) -> BacktestReport:
    trade_count = len(trades)
    total_staked = sum(trade.stake_usd for trade in trades)
    total_pnl = sum(trade.pnl for trade in trades)
    wins = sum(1 for trade in trades if trade.final_label == 1)
    return BacktestReport(
        schema_version="polymarket-backtest-report-v1",
        input_row_count=input_row_count,
        trade_count=trade_count,
        skipped_count=input_row_count - trade_count,
        win_rate=(wins / trade_count) if trade_count else None,
        total_staked=total_staked,
        total_pnl=total_pnl,
        roi=(total_pnl / total_staked) if total_staked else None,
        mean_edge=(sum(trade.edge_at_entry for trade in trades) / trade_count)
        if trade_count
        else None,
        provenance=provenance,
        trades=trades,
    )
