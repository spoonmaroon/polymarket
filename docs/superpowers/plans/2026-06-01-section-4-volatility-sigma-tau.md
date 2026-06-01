# Section 4 Volatility and Sigma Tau Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Section 4 by turning as-of price ticks into `VolatilitySnapshot` objects with realized-volatility windows and `sigma_tau`.

**Architecture:** Add a pure `features/volatility.py` module for log returns, realized-volatility windows, regime classification, and `sigma_tau`. Keep all volatility construction as-of safe: only Chainlink settlement-reference ticks with both `event_ts <= asof_ts` and `observed_ts <= asof_ts` may enter a snapshot. Coinbase, Binance, and other proxies remain source-quality checks; they are not volatility inputs. Thread the snapshot into existing replay/state code; do not build probabilities or trading decisions in this slice.

**Tech Stack:** Python 3.11, existing dataclasses, DuckDB store helpers, pytest, ruff, mypy.

---

## File Structure

- Create `/Users/goon/polymarket/src/polymarket_engine/features/volatility.py`
  - Owns volatility math and `VolatilitySnapshot` construction.
  - Depends only on `PriceObservation` and `VolatilitySnapshot`.
- Create `/Users/goon/polymarket/tests/features/test_volatility.py`
  - Covers log-return construction, realized-vol windows, floor behavior, invalid config, regime labels, and as-of filtering.
- Modify `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`
  - Add `price_ticks_before()` so replay can fetch ordered historical ticks without exposing raw SQL outside storage.
- Modify `/Users/goon/polymarket/src/polymarket_engine/features/state_replay.py`
  - Add optional volatility construction from stored ticks.
- Modify `/Users/goon/polymarket/tests/storage/test_state_replay.py`
  - Prove replay uses only as-of Chainlink ticks for volatility and fills `sigma_tau` in `DecisionState`.
- Modify `/Users/goon/polymarket/docs/BINARY_CONTRACT_ENGINE_PLAN.md`
  - Mark Section 4 as implemented and name the code/test files.

---

### Task 1: Pure Volatility Math

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/features/volatility.py`
- Test: `/Users/goon/polymarket/tests/features/test_volatility.py`

- [ ] **Step 1: Write failing volatility math tests**

Create `/Users/goon/polymarket/tests/features/test_volatility.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.features.volatility import (
    VolatilityConfig,
    build_volatility_snapshot,
    estimate_sigma_tau,
    log_returns_from_prices,
    realized_volatility,
)


def _tick(offset_seconds: int, price: float) -> PriceObservation:
    ts = datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)
    return PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=ts,
        observed_ts=ts,
        price=price,
    )


def test_log_returns_from_prices_are_time_ordered() -> None:
    returns = log_returns_from_prices((_tick(2, 103_000.0), _tick(0, 100_000.0), _tick(1, 101_000.0)))

    assert len(returns) == 2
    assert returns[0] == pytest.approx(0.009950330853168092)
    assert returns[1] == pytest.approx(0.01960847138837618)


def test_realized_volatility_uses_recent_window_root_mean_square() -> None:
    vol = realized_volatility((0.01, -0.01, 0.02), window=2)

    assert vol == pytest.approx(((0.01**2 + 0.02**2) / 2) ** 0.5)


def test_estimate_sigma_tau_increases_with_larger_recent_returns() -> None:
    quiet = estimate_sigma_tau((0.0001, -0.0001, 0.0001), seconds_left=60)
    loud = estimate_sigma_tau((0.001, -0.001, 0.001), seconds_left=60)

    assert loud > quiet


def test_estimate_sigma_tau_keeps_floor_for_flat_tape() -> None:
    sigma = estimate_sigma_tau((0.0, 0.0, 0.0), seconds_left=60, sigma_floor=0.00005)

    assert sigma == 0.00005


def test_estimate_sigma_tau_rejects_bad_weights() -> None:
    with pytest.raises(ValueError, match="weights must sum to 1"):
        estimate_sigma_tau((0.001, -0.001), seconds_left=60, weights=(0.50, 0.50, 0.50))


def test_build_volatility_snapshot_filters_future_ticks_and_labels_regime() -> None:
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    prices = tuple(_tick(i, price) for i, price in enumerate((100_000, 100_010, 100_020, 100_120, 100_240)))
    future = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=asof_ts + timedelta(seconds=1),
        observed_ts=asof_ts + timedelta(seconds=1),
        price=110_000.0,
    )

    snapshot = build_volatility_snapshot(
        prices=prices + (future,),
        asof_ts=asof_ts,
        seconds_left=120,
        config=VolatilityConfig(short_window=2, medium_window=3, long_window=4),
    )

    assert snapshot.event_ts == prices[-1].event_ts
    assert snapshot.observed_ts == prices[-1].observed_ts
    assert snapshot.sigma_tau is not None
    assert snapshot.sigma_tau > 0
    assert snapshot.regime in {"expanding", "normal", "contracting", "unknown"}
```

- [ ] **Step 2: Run volatility tests and verify RED**

Run:

```bash
uv run pytest tests/features/test_volatility.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'polymarket_engine.features.volatility'`.

- [ ] **Step 3: Implement the volatility module**

Create `/Users/goon/polymarket/src/polymarket_engine/features/volatility.py`:

```python
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from polymarket_engine.domain.market_state import PriceObservation, VolatilitySnapshot


@dataclass(frozen=True)
class VolatilityConfig:
    short_window: int = 20
    medium_window: int = 60
    long_window: int = 180
    weights: tuple[float, float, float] = (0.50, 0.30, 0.20)
    sigma_floor: float = 0.00005
    expansion_threshold: float = 1.25
    contraction_threshold: float = 0.80
    expansion_multiplier: float = 1.15
    contraction_multiplier: float = 0.95

    def __post_init__(self) -> None:
        if min(self.short_window, self.medium_window, self.long_window) <= 0:
            raise ValueError("volatility windows must be positive")
        if len(self.weights) != 3:
            raise ValueError("weights must contain three values")
        if abs(sum(self.weights) - 1.0) > 1e-9:
            raise ValueError("weights must sum to 1")
        if self.sigma_floor <= 0:
            raise ValueError("sigma_floor must be positive")


def log_returns_from_prices(prices: Sequence[PriceObservation]) -> tuple[float, ...]:
    ordered = sorted(prices, key=lambda tick: (tick.event_ts, tick.observed_ts))
    returns: list[float] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        returns.append(math.log(current.price / previous.price))
    return tuple(returns)


def realized_volatility(returns: Sequence[float], *, window: int) -> float | None:
    if window <= 0:
        raise ValueError("window must be positive")
    selected = tuple(returns[-window:])
    if not selected:
        return None
    return math.sqrt(sum(value * value for value in selected) / len(selected))


def estimate_sigma_tau(
    returns: Sequence[float],
    seconds_left: float,
    short_window: int = 20,
    medium_window: int = 60,
    long_window: int = 180,
    weights: tuple[float, float, float] = (0.50, 0.30, 0.20),
    sigma_floor: float = 0.00005,
    regime_multiplier: float = 1.0,
) -> float:
    if seconds_left < 0:
        raise ValueError("seconds_left must be nonnegative")
    if len(weights) != 3 or abs(sum(weights) - 1.0) > 1e-9:
        raise ValueError("weights must sum to 1")
    if sigma_floor <= 0:
        raise ValueError("sigma_floor must be positive")
    vols = (
        realized_volatility(returns, window=short_window) or 0.0,
        realized_volatility(returns, window=medium_window) or 0.0,
        realized_volatility(returns, window=long_window) or 0.0,
    )
    blended = sum(weight * vol for weight, vol in zip(weights, vols, strict=True))
    scaled = regime_multiplier * math.sqrt(seconds_left) * blended
    return max(sigma_floor, scaled)


def classify_volatility_regime(
    short_vol: float | None,
    medium_vol: float | None,
    *,
    expansion_threshold: float,
    contraction_threshold: float,
) -> str:
    if short_vol is None or medium_vol is None or medium_vol <= 0:
        return "unknown"
    ratio = short_vol / medium_vol
    if ratio >= expansion_threshold:
        return "expanding"
    if ratio <= contraction_threshold:
        return "contracting"
    return "normal"


def regime_multiplier(regime: str, config: VolatilityConfig) -> float:
    if regime == "expanding":
        return config.expansion_multiplier
    if regime == "contracting":
        return config.contraction_multiplier
    return 1.0


def build_volatility_snapshot(
    *,
    prices: Sequence[PriceObservation],
    asof_ts: datetime,
    seconds_left: float,
    config: VolatilityConfig | None = None,
) -> VolatilitySnapshot:
    _require_utc(asof_ts, "asof_ts")
    cfg = VolatilityConfig() if config is None else config
    allowed = tuple(
        sorted(
            (
                tick
                for tick in prices
                if tick.event_ts <= asof_ts and tick.observed_ts <= asof_ts
            ),
            key=lambda tick: (tick.event_ts, tick.observed_ts),
        )
    )
    if not allowed:
        return VolatilitySnapshot(
            event_ts=asof_ts,
            observed_ts=asof_ts,
            realized_returns=(),
            short_realized_vol=None,
            medium_realized_vol=None,
            long_realized_vol=None,
            sigma_tau=cfg.sigma_floor,
            regime="unknown",
        )
    returns = log_returns_from_prices(allowed)
    short = realized_volatility(returns, window=cfg.short_window)
    medium = realized_volatility(returns, window=cfg.medium_window)
    long = realized_volatility(returns, window=cfg.long_window)
    regime = classify_volatility_regime(
        short,
        medium,
        expansion_threshold=cfg.expansion_threshold,
        contraction_threshold=cfg.contraction_threshold,
    )
    sigma_tau = estimate_sigma_tau(
        returns,
        seconds_left=seconds_left,
        short_window=cfg.short_window,
        medium_window=cfg.medium_window,
        long_window=cfg.long_window,
        weights=cfg.weights,
        sigma_floor=cfg.sigma_floor,
        regime_multiplier=regime_multiplier(regime, cfg),
    )
    return VolatilitySnapshot(
        event_ts=allowed[-1].event_ts,
        observed_ts=allowed[-1].observed_ts,
        realized_returns=returns,
        short_realized_vol=short,
        medium_realized_vol=medium,
        long_realized_vol=long,
        sigma_tau=sigma_tau,
        regime=regime,
    )


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")
```

- [ ] **Step 4: Run volatility tests and verify GREEN**

Run:

```bash
uv run pytest tests/features/test_volatility.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/polymarket_engine/features/volatility.py tests/features/test_volatility.py
git commit -m "feat: add volatility sigma tau estimator"
```

---

### Task 2: Store As-Of Price History

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`
- Test: `/Users/goon/polymarket/tests/storage/test_state_replay.py`

- [ ] **Step 1: Add failing store history test**

Append to `/Users/goon/polymarket/tests/storage/test_state_replay.py`:

```python
def test_store_price_ticks_before_returns_asof_ordered_history(tmp_path: Path) -> None:
    db_path = tmp_path / "volatility.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    before_1 = datetime(2026, 5, 31, 20, 2, 58, tzinfo=timezone.utc)
    before_2 = datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc)
    after = datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc)
    store.insert_price_tick(PriceObservation("polymarket_rtds_chainlink", "BTC/USD", before_2, before_2, 104_010.0))
    store.insert_price_tick(PriceObservation("polymarket_rtds_chainlink", "BTC/USD", before_1, before_1, 104_000.0))
    store.insert_price_tick(PriceObservation("polymarket_rtds_chainlink", "BTC/USD", after, after, 105_000.0))

    rows = store.price_ticks_before(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        asof_ts=asof_ts,
        limit=10,
    )

    assert [row.price for row in rows] == [104_000.0, 104_010.0]
```

- [ ] **Step 2: Run store test and verify RED**

Run:

```bash
uv run pytest tests/storage/test_state_replay.py::test_store_price_ticks_before_returns_asof_ordered_history -q
```

Expected: FAIL with `AttributeError: 'DuckDbIngestStore' object has no attribute 'price_ticks_before'`.

- [ ] **Step 3: Implement ordered as-of price history**

Add this method to `DuckDbIngestStore` in `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`, near `latest_price_tick_before()`:

```python
    def price_ticks_before(
        self,
        *,
        source_key: str,
        symbol: str,
        asof_ts: datetime,
        limit: int,
    ) -> tuple[PriceObservation, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        with duckdb.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """
                select source_key, symbol, event_ts::VARCHAR, observed_ts::VARCHAR,
                       price, bid, ask, sequence
                from (
                    select source_key, symbol, event_ts, observed_ts, price, bid, ask, sequence
                    from core.price_ticks
                    where source_key = ?
                      and symbol = ?
                      and event_ts <= ?
                      and observed_ts <= ?
                    order by event_ts desc, observed_ts desc
                    limit ?
                )
                order by event_ts asc, observed_ts asc
                """,
                [source_key, symbol, asof_ts, asof_ts, limit],
            ).fetchall()
        return tuple(
            PriceObservation(
                source_key=row[0],
                symbol=row[1],
                event_ts=_parse_duckdb_timestamptz(row[2]),
                observed_ts=_parse_duckdb_timestamptz(row[3]),
                price=row[4],
                bid=row[5],
                ask=row[6],
                sequence=row[7],
            )
            for row in rows
        )
```

- [ ] **Step 4: Run store test and verify GREEN**

Run:

```bash
uv run pytest tests/storage/test_state_replay.py::test_store_price_ticks_before_returns_asof_ordered_history -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/polymarket_engine/storage/duckdb_store.py tests/storage/test_state_replay.py
git commit -m "feat: read as-of price history for volatility"
```

---

### Task 3: Replay Builds VolatilitySnapshot

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/features/state_replay.py`
- Test: `/Users/goon/polymarket/tests/storage/test_state_replay.py`

- [ ] **Step 1: Add failing replay volatility test**

Append to `/Users/goon/polymarket/tests/storage/test_state_replay.py`:

```python
def test_build_decision_state_from_store_builds_asof_volatility(tmp_path: Path) -> None:
    db_path = tmp_path / "replay-volatility.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    store.upsert_contract_spec(contract)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    threshold_ts = datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)
    prices = (103_950.0, 103_980.0, 104_000.0, 104_050.0, 104_090.0)
    for index, price in enumerate(prices):
        ts = threshold_ts.replace(second=index)
        store.insert_price_tick(PriceObservation("polymarket_rtds_chainlink", "BTC/USD", ts, ts, price))
    store.insert_price_tick(PriceObservation("coinbase_advanced_ws", "BTC-USD", asof_ts, asof_ts, 104_100.0))

    state = build_decision_state_from_store(
        store=store,
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=103_950.0,
        settlement_source_key="polymarket_rtds_chainlink",
        proxy_source_keys=("coinbase_advanced_ws",),
        volatility=None,
        volatility_source_key="polymarket_rtds_chainlink",
        volatility_lookback_limit=10,
    )

    assert state.sigma_tau is not None
    assert state.sigma_tau > 0
    assert "missing_volatility" not in state.data_quality_flags
```

- [ ] **Step 2: Run replay volatility test and verify RED**

Run:

```bash
uv run pytest tests/storage/test_state_replay.py::test_build_decision_state_from_store_builds_asof_volatility -q
```

Expected: FAIL with `TypeError: build_decision_state_from_store() got an unexpected keyword argument 'volatility_source_key'`.

- [ ] **Step 3: Implement replay volatility construction**

Modify imports in `/Users/goon/polymarket/src/polymarket_engine/features/state_replay.py`:

```python
from polymarket_engine.features.volatility import VolatilityConfig, build_volatility_snapshot
```

Change the function signature:

```python
def build_decision_state_from_store(
    *,
    store: DuckDbIngestStore,
    contract: ContractSpec,
    asof_ts: datetime,
    resolved_threshold_price: float | None,
    settlement_source_key: str,
    proxy_source_keys: Sequence[str],
    volatility: VolatilitySnapshot | None,
    volatility_source_key: str | None = None,
    volatility_lookback_limit: int = 180,
    volatility_config: VolatilityConfig | None = None,
) -> DecisionState:
```

Before `return build_decision_state(...)`, add:

```python
    selected_volatility = volatility
    if selected_volatility is None and volatility_source_key is not None:
        price_history = store.price_ticks_before(
            source_key=volatility_source_key,
            symbol=contract.settlement_symbol,
            asof_ts=asof_ts,
            limit=volatility_lookback_limit,
        )
        selected_volatility = build_volatility_snapshot(
            prices=price_history,
            asof_ts=asof_ts,
            seconds_left=(contract.expiry_ts - asof_ts).total_seconds(),
            config=volatility_config,
        )
```

Then pass:

```python
        volatility=selected_volatility,
```

- [ ] **Step 4: Run replay tests and verify GREEN**

Run:

```bash
uv run pytest tests/storage/test_state_replay.py tests/features/test_volatility.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/polymarket_engine/features/state_replay.py tests/storage/test_state_replay.py
git commit -m "feat: build volatility snapshots during replay"
```

---

### Task 4: Document Section 4 Completion

**Files:**
- Modify: `/Users/goon/polymarket/docs/BINARY_CONTRACT_ENGINE_PLAN.md`

- [ ] **Step 1: Update Section 4 implementation note**

In `/Users/goon/polymarket/docs/BINARY_CONTRACT_ENGINE_PLAN.md`, after line `441` heading `### 4. Volatility and \`sigma_tau\`: How It Builds`, add:

```markdown
Implementation status: this section is implemented by `src/polymarket_engine/features/volatility.py`, replayed through `src/polymarket_engine/features/state_replay.py`, and covered by `tests/features/test_volatility.py` plus `tests/storage/test_state_replay.py`. The implementation is as-of safe: future ticks are labels or ignored, never volatility inputs.
```

- [ ] **Step 2: Run doc grep**

Run:

```bash
rg -n "Implementation status: this section is implemented|features/volatility.py|test_volatility.py" docs/BINARY_CONTRACT_ENGINE_PLAN.md
```

Expected: output includes the new implementation-status line and existing Section 4 references.

- [ ] **Step 3: Commit Task 4**

```bash
git add docs/BINARY_CONTRACT_ENGINE_PLAN.md
git commit -m "docs: mark volatility section implementation path"
```

---

### Task 5: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run focused Section 4 tests**

```bash
uv run pytest tests/features/test_volatility.py tests/storage/test_state_replay.py tests/features/test_state_builder.py tests/storage/test_normalized_writes.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full repo verification once**

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```

Expected:

- `ruff`: `All checks passed!`
- `mypy`: `Success: no issues found`
- `pytest`: all tests pass; the existing FastAPI/httpx deprecation warning may remain.

- [ ] **Step 3: Confirm Section 4 boundary**

Run:

```bash
rg -n "p_finish|p_no_touch|Monte Carlo|probability" src/polymarket_engine tests/features tests/storage
```

Expected: no new probability engine implementation. Existing docs may mention probability, but this slice must only produce volatility state and `sigma_tau`.

---

## Self-Review

Spec coverage:

- Section 4 asks for log returns, short/medium/long realized-volatility windows, weighted blend, volatility floor, regime multiplier, and seconds-left scaling. Tasks 1 and 3 cover those.
- Existing replay/state code already accepts `VolatilitySnapshot`; Task 3 wires automatic as-of snapshot construction from DuckDB history.
- Storage already persists volatility fields inside `features.asof_state_inputs`; no schema migration is needed in this slice.

Scope guard:

- This plan does not implement `p_finish`, `p_no_touch`, Monte Carlo, XGBoost, order placement, or watcher restarts.
- This plan does not use ETF options or historical Polymarket contracts for volatility. Those are later calibration inputs.

Type consistency:

- `VolatilityConfig`, `build_volatility_snapshot`, and `estimate_sigma_tau` are defined before state replay imports them.
- `DuckDbIngestStore.price_ticks_before()` returns `tuple[PriceObservation, ...]`, matching `build_volatility_snapshot(prices=...)`.
