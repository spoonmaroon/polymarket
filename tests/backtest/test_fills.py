from __future__ import annotations

from polymarket_engine.backtest.fills import BacktestFillConfig
from polymarket_engine.backtest.fills import simulate_hold_to_expiry_trade


def _row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "state_id": "state-1",
        "contract_id": "contract-1",
        "asset": "BTC",
        "side": "UP",
        "asof_ts": "2026-06-10T12:00:00+00:00",
        "expiry_ts": "2026-06-10T12:05:00+00:00",
        "p_finish_mc": 0.72,
        "target_size_ask_vwap": 0.64,
        "target_size_bid_vwap": 0.62,
        "best_ask": 0.63,
        "best_bid": 0.61,
        "quote_age_ms": 250.0,
        "skip_or_block_reason": None,
        "final_label": 1,
    }
    values.update(overrides)
    return values


def test_simulate_hold_to_expiry_trade_uses_ask_vwap_for_entry() -> None:
    trade = simulate_hold_to_expiry_trade(
        _row(),
        BacktestFillConfig(
            stake_usd=100.0,
            min_edge=0.02,
            max_quote_age_ms=1000,
            fee_rate=0.0,
        ),
        probability_field="p_finish_mc",
    )

    assert trade is not None
    assert trade.state_id == "state-1"
    assert trade.entry_price == 0.64
    assert round(trade.shares, 6) == 156.25
    assert round(trade.pnl, 6) == 56.25
    assert round(trade.edge_at_entry, 6) == 0.08


def test_simulate_hold_to_expiry_trade_blocks_stale_quotes() -> None:
    trade = simulate_hold_to_expiry_trade(
        _row(quote_age_ms=2000.0),
        BacktestFillConfig(
            stake_usd=100.0,
            min_edge=0.02,
            max_quote_age_ms=1000,
            fee_rate=0.0,
        ),
        probability_field="p_finish_mc",
    )

    assert trade is None


def test_simulate_hold_to_expiry_trade_blocks_negative_quote_age() -> None:
    trade = simulate_hold_to_expiry_trade(
        _row(quote_age_ms=-1.0),
        BacktestFillConfig(
            stake_usd=100.0,
            min_edge=0.02,
            max_quote_age_ms=1000,
            fee_rate=0.0,
        ),
        probability_field="p_finish_mc",
    )

    assert trade is None


def test_simulate_hold_to_expiry_trade_requires_positive_edge() -> None:
    trade = simulate_hold_to_expiry_trade(
        _row(p_finish_mc=0.65),
        BacktestFillConfig(
            stake_usd=100.0,
            min_edge=0.02,
            max_quote_age_ms=1000,
            fee_rate=0.0,
        ),
        probability_field="p_finish_mc",
    )

    assert trade is None


def test_simulate_hold_to_expiry_trade_loss_loses_stake() -> None:
    trade = simulate_hold_to_expiry_trade(
        _row(final_label=0),
        BacktestFillConfig(
            stake_usd=100.0,
            min_edge=0.02,
            max_quote_age_ms=1000,
            fee_rate=0.0,
        ),
        probability_field="p_finish_mc",
    )

    assert trade is not None
    assert trade.pnl == -100.0
