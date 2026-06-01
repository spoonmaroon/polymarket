# Decision State And Normalized Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete build sections 1-3 of the BTC/ETH binary-contract engine by adding normalized contract specs, as-of decision-state construction, normalized DuckDB storage writes, retention metadata hooks, and storage replay queries that prove no future data can enter a decision state.

**Architecture:** Keep probability modeling out of this slice. Contract parsing produces side-level `ContractSpec` objects, normalized market observations feed an in-memory `DecisionState` builder, DuckDB persists normalized rows, and replay helpers query only records timestamped at or before `asof_ts`. Raw collectors remain read-only; this bridge prepares later Monte Carlo and XGBoost layers without allowing leakage.

**Tech Stack:** Python 3.11+, frozen dataclasses, DuckDB, pytest, ruff, mypy strict mode, existing `polymarket_engine` package layout.

---

## Scope

This plan implements Option A plus the safe first part of Option C.

Build:
- side-level `ContractSpec`
- BTC/ETH UP and DOWN specs from parsed Polymarket rules
- fixed-threshold support at the `ContractSpec` level
- full `DecisionState`
- in-memory state builder that rejects or excludes future data
- normalized storage writes for `price_ticks`, `orderbook_snapshots`, `asof_state_inputs`, `decision_snapshots`, and `decision_labels`
- storage replay queries for latest valid records `<= asof_ts`
- data-retention metadata and manifest hooks
- tests proving no future data can enter the as-of state

Do not build yet:
- Monte Carlo probability models
- XGBoost
- live order placement
- automatic retention deletion
- Docker guided setup CLI
- paper/live trading execution

The Docker setup principle is still preserved: later deployment should support `configure`-style guided setup where the operator enters keys and chooses `collect`, `paper`, or `live`; keys existing must not automatically arm live trading.

## File Structure

- Create `src/polymarket_engine/domain/contracts.py`
  - Defines `ContractSpec`, validation helpers, and `contract_specs_from_rule()`.
- Modify `src/polymarket_engine/domain/__init__.py`
  - Exports domain models only if existing style supports it; otherwise leave empty.
- Modify `tests/domain/test_contract_rules.py`
  - Adds ETH fixture and stricter unsupported-rule coverage.
- Create `tests/domain/test_contracts.py`
  - Tests side-level `ContractSpec` objects for BTC/ETH start-price and fixed-threshold contracts.
- Create `src/polymarket_engine/domain/market_state.py`
  - Defines normalized observations and `DecisionState`.
- Create `src/polymarket_engine/features/state_builder.py`
  - Builds `DecisionState` from in-memory observations and enforces as-of rules.
- Create `src/polymarket_engine/features/state_replay.py`
  - Builds `DecisionState` from DuckDB replay queries without importing storage into the in-memory builder.
- Create `tests/features/test_state_builder.py`
  - Tests no-future-data selection/rejection, stale flags, source disagreement, and order-book handling.
- Modify `src/polymarket_engine/storage/schema.sql`
  - Adds retention manifest fields/tables, richer `core.contracts`, `features.decision_snapshots`, and `validation.decision_labels`.
- Modify `src/polymarket_engine/storage/duckdb_store.py`
  - Adds normalized write methods and as-of replay query methods.
- Modify `tests/storage/test_schema.py`
  - Verifies all new tables are present.
- Create `tests/storage/test_normalized_writes.py`
  - Verifies normalized insert/read paths and retention manifest writes.
- Create `tests/storage/test_state_replay.py`
  - Verifies storage replay selects latest records at or before `asof_ts` and never after.

---

### Task 1: Add Side-Level ContractSpec Domain Object

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/domain/contracts.py`
- Test: `/Users/goon/polymarket/tests/domain/test_contracts.py`

- [ ] **Step 1: Write failing contract-spec tests**

Create `/Users/goon/polymarket/tests/domain/test_contracts.py`:

```python
from datetime import datetime, timezone

import pytest

from polymarket_engine.domain.contract_rules import parse_polymarket_crypto_updown_rule
from polymarket_engine.domain.contracts import ContractSpec, contract_specs_from_rule


BTC_DESCRIPTION = """This market will resolve to "Up" if the Bitcoin price at the end of the time range specified in the title is greater than or equal to the price at the beginning of that range. Otherwise, it will resolve to "Down".
The resolution source for this market is information from Chainlink, specifically the BTC/USD data stream available at https://data.chain.link/streams/btc-usd.
Please note that this market is about the price according to Chainlink data stream BTC/USD, not according to other sources or spot exchanges."""


ETH_DESCRIPTION = BTC_DESCRIPTION.replace("Bitcoin", "Ethereum").replace(
    "BTC/USD", "ETH/USD"
).replace("btc-usd", "eth-usd")


def _market(asset: str, description: str) -> dict[str, object]:
    lower = asset.lower()
    return {
        "id": f"{asset}-market-1",
        "conditionId": f"0x{lower}",
        "slug": f"{lower}-updown-5m-1780264500",
        "question": f"{asset} Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": description,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": f"https://data.chain.link/streams/{lower}-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }


def test_contract_specs_from_btc_start_price_rule() -> None:
    rule = parse_polymarket_crypto_updown_rule(_market("btc", BTC_DESCRIPTION))

    up, down = contract_specs_from_rule(rule)

    assert up.contract_id == "btc-market-1:UP"
    assert up.venue == "polymarket"
    assert up.asset == "BTC"
    assert up.side == "UP"
    assert up.token_id == "111"
    assert up.threshold_type == "start_price"
    assert up.threshold_price is None
    assert up.comparison_operator == ">="
    assert up.settlement_symbol == "BTC/USD"
    assert up.start_ts == datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc)
    assert up.expiry_ts == datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc)

    assert down.contract_id == "btc-market-1:DOWN"
    assert down.asset == "BTC"
    assert down.side == "DOWN"
    assert down.token_id == "222"
    assert down.comparison_operator == "<"
    assert down.rule_hash == up.rule_hash


def test_contract_specs_from_eth_start_price_rule() -> None:
    rule = parse_polymarket_crypto_updown_rule(_market("eth", ETH_DESCRIPTION))

    up, down = contract_specs_from_rule(rule)

    assert up.asset == "ETH"
    assert up.settlement_symbol == "ETH/USD"
    assert down.asset == "ETH"
    assert down.settlement_symbol == "ETH/USD"


def test_fixed_threshold_contract_spec_is_supported_at_object_level() -> None:
    spec = ContractSpec(
        contract_id="manual-btc-up",
        venue="polymarket",
        market_id="manual-market",
        condition_id="0xmanual",
        slug="manual-btc-fixed",
        asset="BTC",
        side="UP",
        token_id="token-up",
        threshold_type="fixed_price",
        threshold_price=105_000.0,
        comparison_operator=">",
        start_ts=datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="Manual fixed threshold fixture.",
        rule_hash="abc123",
        parser_version="manual_fixture",
    )

    assert spec.threshold_type == "fixed_price"
    assert spec.threshold_price == 105_000.0


def test_fixed_threshold_requires_threshold_price() -> None:
    with pytest.raises(ValueError, match="fixed_price requires threshold_price"):
        ContractSpec(
            contract_id="bad-fixed",
            venue="polymarket",
            market_id="manual-market",
            condition_id="0xmanual",
            slug="manual-btc-fixed",
            asset="BTC",
            side="UP",
            token_id="token-up",
            threshold_type="fixed_price",
            threshold_price=None,
            comparison_operator=">",
            start_ts=datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc),
            expiry_ts=datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc),
            settlement_source_name="chainlink_data_streams",
            settlement_source_url="https://data.chain.link/streams/btc-usd",
            settlement_symbol="BTC/USD",
            rule_text="Manual fixed threshold fixture.",
            rule_hash="abc123",
            parser_version="manual_fixture",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/domain/test_contracts.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'polymarket_engine.domain.contracts'`.

- [ ] **Step 3: Implement `ContractSpec`**

Create `/Users/goon/polymarket/src/polymarket_engine/domain/contracts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from polymarket_engine.domain.contract_rules import NormalizedContractRule

Asset = Literal["BTC", "ETH", "SOL"]
ContractSide = Literal["UP", "DOWN"]
ThresholdType = Literal["start_price", "fixed_price"]
ComparisonOperator = Literal[">", ">=", "<", "<="]


@dataclass(frozen=True)
class ContractSpec:
    contract_id: str
    venue: str
    market_id: str
    condition_id: str
    slug: str
    asset: Asset
    side: ContractSide
    token_id: str
    threshold_type: ThresholdType
    threshold_price: float | None
    comparison_operator: ComparisonOperator
    start_ts: datetime
    expiry_ts: datetime
    settlement_source_name: str
    settlement_source_url: str
    settlement_symbol: str
    rule_text: str
    rule_hash: str
    parser_version: str

    def __post_init__(self) -> None:
        _require_utc(self.start_ts, "start_ts")
        _require_utc(self.expiry_ts, "expiry_ts")
        if self.start_ts >= self.expiry_ts:
            raise ValueError("start_ts must be before expiry_ts")
        if self.threshold_type == "fixed_price" and self.threshold_price is None:
            raise ValueError("fixed_price requires threshold_price")
        if self.threshold_type == "start_price" and self.threshold_price is not None:
            raise ValueError("start_price threshold_price must be None until resolved")
        if self.threshold_price is not None and self.threshold_price <= 0:
            raise ValueError("threshold_price must be positive")


def contract_specs_from_rule(rule: NormalizedContractRule) -> tuple[ContractSpec, ContractSpec]:
    up = ContractSpec(
        contract_id=f"{rule.market_id}:UP",
        venue="polymarket",
        market_id=rule.market_id,
        condition_id=rule.condition_id,
        slug=rule.slug,
        asset=_asset(rule.asset),
        side="UP",
        token_id=rule.outcome_token_ids["Up"],
        threshold_type=_threshold_type(rule.threshold_type),
        threshold_price=rule.threshold_price,
        comparison_operator=_comparison(rule.comparison_operator_up),
        start_ts=rule.start_ts,
        expiry_ts=rule.expiry_ts,
        settlement_source_name=rule.settlement_source_name,
        settlement_source_url=rule.settlement_source_url,
        settlement_symbol=rule.settlement_symbol,
        rule_text=rule.rule_text,
        rule_hash=rule.rule_hash,
        parser_version=rule.parser_version,
    )
    down = ContractSpec(
        contract_id=f"{rule.market_id}:DOWN",
        venue="polymarket",
        market_id=rule.market_id,
        condition_id=rule.condition_id,
        slug=rule.slug,
        asset=_asset(rule.asset),
        side="DOWN",
        token_id=rule.outcome_token_ids["Down"],
        threshold_type=_threshold_type(rule.threshold_type),
        threshold_price=rule.threshold_price,
        comparison_operator=_comparison(rule.comparison_operator_down),
        start_ts=rule.start_ts,
        expiry_ts=rule.expiry_ts,
        settlement_source_name=rule.settlement_source_name,
        settlement_source_url=rule.settlement_source_url,
        settlement_symbol=rule.settlement_symbol,
        rule_text=rule.rule_text,
        rule_hash=rule.rule_hash,
        parser_version=rule.parser_version,
    )
    return up, down


def _asset(value: str) -> Asset:
    if value not in {"BTC", "ETH", "SOL"}:
        raise ValueError(f"unsupported asset: {value}")
    return value  # type: ignore[return-value]


def _threshold_type(value: str) -> ThresholdType:
    if value not in {"start_price", "fixed_price"}:
        raise ValueError(f"unsupported threshold_type: {value}")
    return value  # type: ignore[return-value]


def _comparison(value: str) -> ComparisonOperator:
    if value not in {">", ">=", "<", "<="}:
        raise ValueError(f"unsupported comparison_operator: {value}")
    return value  # type: ignore[return-value]


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")
```

- [ ] **Step 4: Run contract tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/domain/test_contracts.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/domain/contracts.py tests/domain/test_contracts.py
git commit -m "feat: add normalized contract specs"
```

---

### Task 2: Expand Rule Parser Coverage For BTC/ETH And Unsupported Rules

**Files:**
- Modify: `/Users/goon/polymarket/tests/domain/test_contract_rules.py`

- [ ] **Step 1: Add failing parser coverage**

Append these tests to `/Users/goon/polymarket/tests/domain/test_contract_rules.py`:

```python
ETH_DESCRIPTION = BTC_DESCRIPTION.replace("Bitcoin", "Ethereum").replace(
    "BTC/USD", "ETH/USD"
).replace("btc-usd", "eth-usd")


def test_parse_eth_updown_start_price_rule() -> None:
    market = {
        "id": "2397999",
        "conditionId": "0xeth",
        "slug": "eth-updown-5m-1780264500",
        "question": "Ethereum Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": ETH_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/eth-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["333", "444"]',
    }

    rule = parse_polymarket_crypto_updown_rule(market)

    assert rule.asset == "ETH"
    assert rule.settlement_symbol == "ETH/USD"
    assert rule.settlement_source_url == "https://data.chain.link/streams/eth-usd"
    assert rule.outcome_token_ids == {"Up": "333", "Down": "444"}


def test_parse_rejects_unsupported_slug_before_state_building() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-above-100000",
        "question": "Bitcoin above 100000",
        "description": BTC_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    with pytest.raises(ContractRuleRejected, match="unsupported slug"):
        parse_polymarket_crypto_updown_rule(market)


def test_parse_rejects_missing_end_price_rule() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION.replace("price at the end", "final quote"),
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    with pytest.raises(ContractRuleRejected, match="missing end-price comparison rule"):
        parse_polymarket_crypto_updown_rule(market)


def test_parse_rejects_naive_timestamps() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    with pytest.raises(ContractRuleRejected, match="timestamp must be timezone-aware"):
        parse_polymarket_crypto_updown_rule(market)
```

- [ ] **Step 2: Run parser tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/domain/test_contract_rules.py -v
```

Expected: PASS. If this fails, fix only parser behavior directly related to the failing assertion.

- [ ] **Step 3: Commit**

```bash
cd /Users/goon/polymarket
git add tests/domain/test_contract_rules.py src/polymarket_engine/domain/contract_rules.py
git commit -m "test: cover btc eth contract rule parsing"
```

---

### Task 3: Add Normalized Market Observation And DecisionState Models

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/domain/market_state.py`
- Test: `/Users/goon/polymarket/tests/features/test_state_builder.py`

- [ ] **Step 1: Write failing model smoke test**

Create `/Users/goon/polymarket/tests/features/test_state_builder.py` with this initial test:

```python
from datetime import datetime, timezone

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import (
    DecisionState,
    OrderBookObservation,
    PriceObservation,
    VolatilitySnapshot,
)


def _contract() -> ContractSpec:
    return ContractSpec(
        contract_id="btc-market:UP",
        venue="polymarket",
        market_id="btc-market",
        condition_id="0xbtc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        side="UP",
        token_id="111",
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=">=",
        start_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 5, 31, 20, 5, tzinfo=timezone.utc),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="fixture",
        rule_hash="hash",
        parser_version="test",
    )


def test_decision_state_model_holds_contract_price_book_and_volatility_state() -> None:
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    price = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=asof_ts,
        observed_ts=asof_ts,
        price=104_000.0,
    )
    book = OrderBookObservation(
        venue="polymarket",
        contract_id="btc-market:UP",
        token_id="111",
        event_ts=asof_ts,
        observed_ts=asof_ts,
        best_bid=0.61,
        best_ask=0.64,
        bid_size_top=50.0,
        ask_size_top=40.0,
        spread=0.03,
        depth_json='{"bids":[],"asks":[]}',
    )
    volatility = VolatilitySnapshot(
        event_ts=asof_ts,
        observed_ts=asof_ts,
        realized_returns=(0.001, -0.0005),
        short_realized_vol=0.01,
        medium_realized_vol=0.012,
        long_realized_vol=0.015,
        sigma_tau=0.002,
        regime="normal",
    )

    state = DecisionState(
        state_id="btc-market:UP:2026-05-31T20:03:00+00:00",
        asof_ts=asof_ts,
        contract=_contract(),
        threshold=103_950.0,
        seconds_left=120.0,
        settlement_price=price.price,
        settlement_source_key=price.source_key,
        proxy_prices={"coinbase_advanced_ws": 104_010.0},
        source_disagreement_bps=0.9615384615384616,
        best_bid=book.best_bid,
        best_ask=book.best_ask,
        executable_price=book.best_ask,
        spread=book.spread,
        quote_age_ms=0,
        source_age_ms=0,
        book_age_ms=0,
        realized_returns=volatility.realized_returns,
        short_realized_vol=volatility.short_realized_vol,
        medium_realized_vol=volatility.medium_realized_vol,
        long_realized_vol=volatility.long_realized_vol,
        sigma_tau=volatility.sigma_tau,
        volatility_regime=volatility.regime,
        data_quality_flags=(),
    )

    assert state.contract.asset == "BTC"
    assert state.executable_price == 0.64
    assert state.seconds_left == 120.0
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/features/test_state_builder.py::test_decision_state_model_holds_contract_price_book_and_volatility_state -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'polymarket_engine.domain.market_state'`.

- [ ] **Step 3: Implement market-state dataclasses**

Create `/Users/goon/polymarket/src/polymarket_engine/domain/market_state.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from polymarket_engine.domain.contracts import ContractSpec

DataQualityFlag = Literal[
    "stale_source",
    "stale_orderbook",
    "missing_orderbook",
    "source_disagreement",
    "missing_volatility",
]


@dataclass(frozen=True)
class PriceObservation:
    source_key: str
    symbol: str
    event_ts: datetime
    observed_ts: datetime
    price: float
    bid: float | None = None
    ask: float | None = None
    sequence: str | None = None

    def __post_init__(self) -> None:
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.observed_ts, "observed_ts")
        if self.price <= 0:
            raise ValueError("price must be positive")


@dataclass(frozen=True)
class OrderBookObservation:
    venue: str
    contract_id: str
    token_id: str
    event_ts: datetime
    observed_ts: datetime
    best_bid: float | None
    best_ask: float | None
    bid_size_top: float | None
    ask_size_top: float | None
    spread: float | None
    depth_json: str

    def __post_init__(self) -> None:
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.observed_ts, "observed_ts")


@dataclass(frozen=True)
class VolatilitySnapshot:
    event_ts: datetime
    observed_ts: datetime
    realized_returns: tuple[float, ...]
    short_realized_vol: float | None
    medium_realized_vol: float | None
    long_realized_vol: float | None
    sigma_tau: float | None
    regime: str | None

    def __post_init__(self) -> None:
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.observed_ts, "observed_ts")


@dataclass(frozen=True)
class DecisionState:
    state_id: str
    asof_ts: datetime
    contract: ContractSpec
    threshold: float
    seconds_left: float
    settlement_price: float
    settlement_source_key: str
    proxy_prices: dict[str, float]
    source_disagreement_bps: float | None
    best_bid: float | None
    best_ask: float | None
    executable_price: float | None
    spread: float | None
    quote_age_ms: int | None
    source_age_ms: int | None
    book_age_ms: int | None
    realized_returns: tuple[float, ...]
    short_realized_vol: float | None
    medium_realized_vol: float | None
    long_realized_vol: float | None
    sigma_tau: float | None
    volatility_regime: str | None
    data_quality_flags: tuple[DataQualityFlag, ...]

    def __post_init__(self) -> None:
        _require_utc(self.asof_ts, "asof_ts")
        if self.threshold <= 0:
            raise ValueError("threshold must be positive")
        if self.seconds_left < 0:
            raise ValueError("seconds_left must be nonnegative")
        if self.settlement_price <= 0:
            raise ValueError("settlement_price must be positive")

    def to_json_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        return _json_ready(raw)


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")
```

- [ ] **Step 4: Run model test**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/features/test_state_builder.py::test_decision_state_model_holds_contract_price_book_and_volatility_state -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/domain/market_state.py tests/features/test_state_builder.py
git commit -m "feat: add decision state domain models"
```

---

### Task 4: Build In-Memory DecisionState With No-Future-Data Enforcement

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/features/state_builder.py`
- Modify: `/Users/goon/polymarket/tests/features/test_state_builder.py`

- [ ] **Step 1: Add failing builder tests**

Append these tests to `/Users/goon/polymarket/tests/features/test_state_builder.py`:

```python
import pytest

from polymarket_engine.features.state_builder import (
    DecisionStateUnavailable,
    build_decision_state,
    validate_observation_asof,
)


def test_build_decision_state_uses_latest_price_and_book_at_or_before_asof() -> None:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    price_before = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=datetime(2026, 5, 31, 20, 2, 58, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
        price=104_000.0,
    )
    price_after = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        price=105_000.0,
    )
    book_before = OrderBookObservation(
        venue="polymarket",
        contract_id=contract.contract_id,
        token_id=contract.token_id,
        event_ts=datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
        best_bid=0.61,
        best_ask=0.64,
        bid_size_top=50.0,
        ask_size_top=40.0,
        spread=0.03,
        depth_json='{"bids":[],"asks":[]}',
    )

    state = build_decision_state(
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=103_950.0,
        settlement_prices=(price_before, price_after),
        proxy_prices=(),
        orderbooks=(book_before,),
        volatility=None,
    )

    assert state.settlement_price == 104_000.0
    assert state.best_ask == 0.64
    assert state.source_age_ms == 1000
    assert state.book_age_ms == 1000


def test_build_decision_state_rejects_selected_future_observation() -> None:
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    future_price = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=asof_ts,
        observed_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        price=105_000.0,
    )

    with pytest.raises(ValueError, match="future_price observed_ts timestamp is after asof_ts"):
        validate_observation_asof(future_price, asof_ts, "future_price")


def test_build_decision_state_raises_when_no_settlement_price_exists_before_asof() -> None:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    future_price = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc),
        price=105_000.0,
    )

    with pytest.raises(DecisionStateUnavailable, match="no settlement price"):
        build_decision_state(
            contract=contract,
            asof_ts=asof_ts,
            resolved_threshold_price=103_950.0,
            settlement_prices=(future_price,),
            proxy_prices=(),
            orderbooks=(),
            volatility=None,
        )


def test_build_decision_state_flags_stale_source_missing_book_and_source_disagreement() -> None:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    stale_price = PriceObservation(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        event_ts=datetime(2026, 5, 31, 20, 2, 45, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 2, 45, tzinfo=timezone.utc),
        price=104_000.0,
    )
    proxy = PriceObservation(
        source_key="coinbase_advanced_ws",
        symbol="BTC-USD",
        event_ts=datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc),
        price=104_500.0,
    )

    state = build_decision_state(
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=103_950.0,
        settlement_prices=(stale_price,),
        proxy_prices=(proxy,),
        orderbooks=(),
        volatility=None,
        stale_source_after_ms=5_000,
        source_disagreement_block_bps=10.0,
    )

    assert "stale_source" in state.data_quality_flags
    assert "missing_orderbook" in state.data_quality_flags
    assert "source_disagreement" in state.data_quality_flags
    assert state.best_bid is None
    assert state.executable_price is None
```

- [ ] **Step 2: Run builder tests to verify they fail**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/features/test_state_builder.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'polymarket_engine.features.state_builder'`.

- [ ] **Step 3: Implement state builder**

Create `/Users/goon/polymarket/src/polymarket_engine/features/state_builder.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TypeVar

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import (
    DataQualityFlag,
    DecisionState,
    OrderBookObservation,
    PriceObservation,
    VolatilitySnapshot,
)
from polymarket_engine.features.asof_inputs import (
    calculate_source_disagreement_bps,
    ensure_asof,
)


class DecisionStateUnavailable(ValueError):
    pass


TObservation = TypeVar("TObservation", PriceObservation, OrderBookObservation, VolatilitySnapshot)


def validate_observation_asof(
    observation: PriceObservation | OrderBookObservation | VolatilitySnapshot,
    asof_ts: datetime,
    field_name: str,
) -> None:
    ensure_asof(observation.event_ts, asof_ts, f"{field_name} event_ts")
    ensure_asof(observation.observed_ts, asof_ts, f"{field_name} observed_ts")


def latest_asof(
    observations: Sequence[TObservation],
    asof_ts: datetime,
) -> TObservation | None:
    allowed = [
        observation
        for observation in observations
        if observation.event_ts <= asof_ts and observation.observed_ts <= asof_ts
    ]
    if not allowed:
        return None
    return max(allowed, key=lambda observation: (observation.observed_ts, observation.event_ts))


def build_decision_state(
    *,
    contract: ContractSpec,
    asof_ts: datetime,
    resolved_threshold_price: float | None,
    settlement_prices: Sequence[PriceObservation],
    proxy_prices: Sequence[PriceObservation],
    orderbooks: Sequence[OrderBookObservation],
    volatility: VolatilitySnapshot | None,
    stale_source_after_ms: int = 2_000,
    stale_book_after_ms: int = 2_000,
    source_disagreement_block_bps: float = 10.0,
) -> DecisionState:
    ensure_asof(asof_ts, contract.expiry_ts, "asof_ts")
    threshold = _threshold(contract, resolved_threshold_price)

    settlement_candidates = [
        price
        for price in settlement_prices
        if price.symbol == contract.settlement_symbol
        and price.source_key in {"polymarket_rtds_chainlink", contract.settlement_source_name}
    ]
    settlement_price = latest_asof(settlement_candidates, asof_ts)
    if settlement_price is None:
        raise DecisionStateUnavailable("no settlement price at or before asof_ts")
    validate_observation_asof(settlement_price, asof_ts, "settlement_price")

    proxy_latest: dict[str, PriceObservation] = {}
    for proxy in proxy_prices:
        if proxy.event_ts <= asof_ts and proxy.observed_ts <= asof_ts:
            current = proxy_latest.get(proxy.source_key)
            if current is None or (proxy.observed_ts, proxy.event_ts) > (
                current.observed_ts,
                current.event_ts,
            ):
                proxy_latest[proxy.source_key] = proxy

    book = latest_asof(
        [
            candidate
            for candidate in orderbooks
            if candidate.venue == contract.venue and candidate.token_id == contract.token_id
        ],
        asof_ts,
    )
    if book is not None:
        validate_observation_asof(book, asof_ts, "orderbook")

    if volatility is not None:
        validate_observation_asof(volatility, asof_ts, "volatility")

    proxy_price_values = {source_key: proxy.price for source_key, proxy in proxy_latest.items()}
    source_disagreement = calculate_source_disagreement_bps(
        settlement_price.price,
        list(proxy_price_values.values()),
    )

    source_age_ms = _age_ms(asof_ts, settlement_price.observed_ts)
    book_age_ms = None if book is None else _age_ms(asof_ts, book.observed_ts)
    flags = _flags(
        source_age_ms=source_age_ms,
        book_age_ms=book_age_ms,
        has_book=book is not None,
        source_disagreement_bps=source_disagreement,
        stale_source_after_ms=stale_source_after_ms,
        stale_book_after_ms=stale_book_after_ms,
        source_disagreement_block_bps=source_disagreement_block_bps,
        has_volatility=volatility is not None,
    )
    state_id = f"{contract.contract_id}:{asof_ts.isoformat()}"
    seconds_left = (contract.expiry_ts - asof_ts).total_seconds()

    return DecisionState(
        state_id=state_id,
        asof_ts=asof_ts,
        contract=contract,
        threshold=threshold,
        seconds_left=seconds_left,
        settlement_price=settlement_price.price,
        settlement_source_key=settlement_price.source_key,
        proxy_prices=proxy_price_values,
        source_disagreement_bps=source_disagreement,
        best_bid=None if book is None else book.best_bid,
        best_ask=None if book is None else book.best_ask,
        executable_price=None if book is None else book.best_ask,
        spread=None if book is None else book.spread,
        quote_age_ms=book_age_ms,
        source_age_ms=source_age_ms,
        book_age_ms=book_age_ms,
        realized_returns=() if volatility is None else volatility.realized_returns,
        short_realized_vol=None if volatility is None else volatility.short_realized_vol,
        medium_realized_vol=None if volatility is None else volatility.medium_realized_vol,
        long_realized_vol=None if volatility is None else volatility.long_realized_vol,
        sigma_tau=None if volatility is None else volatility.sigma_tau,
        volatility_regime=None if volatility is None else volatility.regime,
        data_quality_flags=tuple(flags),
    )


def _threshold(contract: ContractSpec, resolved_threshold_price: float | None) -> float:
    if contract.threshold_type == "fixed_price":
        if contract.threshold_price is None:
            raise DecisionStateUnavailable("fixed threshold missing threshold_price")
        return contract.threshold_price
    if resolved_threshold_price is None:
        raise DecisionStateUnavailable("start-price contract requires resolved_threshold_price")
    if resolved_threshold_price <= 0:
        raise DecisionStateUnavailable("resolved_threshold_price must be positive")
    return resolved_threshold_price


def _age_ms(asof_ts: datetime, observed_ts: datetime) -> int:
    return int((asof_ts - observed_ts).total_seconds() * 1000)


def _flags(
    *,
    source_age_ms: int,
    book_age_ms: int | None,
    has_book: bool,
    source_disagreement_bps: float | None,
    stale_source_after_ms: int,
    stale_book_after_ms: int,
    source_disagreement_block_bps: float,
    has_volatility: bool,
) -> list[DataQualityFlag]:
    flags: list[DataQualityFlag] = []
    if source_age_ms > stale_source_after_ms:
        flags.append("stale_source")
    if not has_book:
        flags.append("missing_orderbook")
    elif book_age_ms is not None and book_age_ms > stale_book_after_ms:
        flags.append("stale_orderbook")
    if source_disagreement_bps is not None and source_disagreement_bps > source_disagreement_block_bps:
        flags.append("source_disagreement")
    if not has_volatility:
        flags.append("missing_volatility")
    return flags
```

- [ ] **Step 4: Run builder tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/features/test_state_builder.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/features/state_builder.py tests/features/test_state_builder.py
git commit -m "feat: build as-of decision states"
```

---

### Task 5: Add Retention Metadata And Decision Storage Schema

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/storage/schema.sql`
- Modify: `/Users/goon/polymarket/tests/storage/test_schema.py`

- [ ] **Step 1: Write failing schema test**

Modify `/Users/goon/polymarket/tests/storage/test_schema.py` so the expected table set includes the new tables:

```python
    assert {
        "ops.ingest_files",
        "ops.ingest_checkpoints",
        "ops.retention_manifests",
        "core.contracts",
        "core.contract_rules",
        "core.price_ticks",
        "core.orderbook_snapshots",
        "features.asof_state_inputs",
        "features.decision_snapshots",
        "validation.contract_labels",
        "validation.decision_labels",
    }.issubset(tables)
```

- [ ] **Step 2: Run schema test to verify it fails**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_schema.py -v
```

Expected: FAIL because `ops.retention_manifests`, `features.decision_snapshots`, and `validation.decision_labels` do not exist.

- [ ] **Step 3: Update schema**

Modify `/Users/goon/polymarket/src/polymarket_engine/storage/schema.sql`.

Replace `core.contracts` with this version:

```sql
CREATE TABLE IF NOT EXISTS core.contracts (
    contract_id VARCHAR PRIMARY KEY,
    venue VARCHAR NOT NULL,
    market_id VARCHAR NOT NULL,
    condition_id VARCHAR NOT NULL,
    slug VARCHAR NOT NULL,
    asset VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    token_id VARCHAR NOT NULL,
    threshold_type VARCHAR NOT NULL,
    threshold_price DOUBLE,
    comparison_operator VARCHAR NOT NULL,
    start_ts TIMESTAMPTZ NOT NULL,
    expiry_ts TIMESTAMPTZ NOT NULL,
    settlement_source_name VARCHAR NOT NULL,
    settlement_source_url VARCHAR NOT NULL,
    settlement_symbol VARCHAR NOT NULL,
    rule_text VARCHAR NOT NULL,
    rule_hash VARCHAR NOT NULL,
    parser_version VARCHAR NOT NULL,
    first_seen_ts TIMESTAMPTZ NOT NULL,
    last_seen_ts TIMESTAMPTZ NOT NULL
);
```

Add this table after `ops.ingest_files`:

```sql
CREATE TABLE IF NOT EXISTS ops.retention_manifests (
    manifest_id VARCHAR PRIMARY KEY,
    file_id VARCHAR NOT NULL,
    source_key VARCHAR NOT NULL,
    stream_key VARCHAR NOT NULL,
    partition_date DATE NOT NULL,
    partition_hour UTINYINT NOT NULL,
    path VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    row_count UBIGINT NOT NULL,
    first_event_ts TIMESTAMPTZ,
    last_event_ts TIMESTAMPTZ,
    retention_class VARCHAR NOT NULL,
    archive_after_days USMALLINT,
    delete_after_days USMALLINT,
    archived_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL
);
```

Replace `features.asof_state_inputs` with this expanded version:

```sql
CREATE TABLE IF NOT EXISTS features.asof_state_inputs (
    state_id VARCHAR PRIMARY KEY,
    contract_id VARCHAR NOT NULL,
    asof_ts TIMESTAMPTZ NOT NULL,
    asset VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    threshold DOUBLE NOT NULL,
    seconds_left DOUBLE NOT NULL,
    settlement_price DOUBLE NOT NULL,
    settlement_source_key VARCHAR NOT NULL,
    proxy_prices_json VARCHAR NOT NULL,
    source_disagreement_bps DOUBLE,
    best_bid DOUBLE,
    best_ask DOUBLE,
    executable_price DOUBLE,
    spread DOUBLE,
    quote_age_ms DOUBLE,
    source_age_ms DOUBLE,
    book_age_ms DOUBLE,
    realized_returns_json VARCHAR NOT NULL,
    short_realized_vol DOUBLE,
    medium_realized_vol DOUBLE,
    long_realized_vol DOUBLE,
    sigma_tau DOUBLE,
    volatility_regime VARCHAR,
    data_quality_flags_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

Add these tables after `features.asof_state_inputs`:

```sql
CREATE TABLE IF NOT EXISTS features.decision_snapshots (
    decision_id VARCHAR PRIMARY KEY,
    state_id VARCHAR NOT NULL,
    contract_id VARCHAR NOT NULL,
    asof_ts TIMESTAMPTZ NOT NULL,
    market_id VARCHAR NOT NULL,
    token_id VARCHAR NOT NULL,
    state_json VARCHAR NOT NULL,
    model_json VARCHAR NOT NULL,
    decision VARCHAR NOT NULL,
    block_reason VARCHAR,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS validation.decision_labels (
    decision_id VARCHAR PRIMARY KEY,
    contract_id VARCHAR NOT NULL,
    expiry_ts TIMESTAMPTZ NOT NULL,
    settlement_price DOUBLE NOT NULL,
    did_finish_win BOOLEAN NOT NULL,
    did_no_touch BOOLEAN NOT NULL,
    realized_edge DOUBLE,
    label_source VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

Do not remove `validation.contract_labels`; keep it as contract-level resolution storage.

- [ ] **Step 4: Run schema test**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_schema.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/storage/schema.sql tests/storage/test_schema.py
git commit -m "feat: add decision and retention schema"
```

---

### Task 6: Add Normalized Storage Write Methods

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`
- Create: `/Users/goon/polymarket/tests/storage/test_normalized_writes.py`

- [ ] **Step 1: Write failing normalized write tests**

Create `/Users/goon/polymarket/tests/storage/test_normalized_writes.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState, OrderBookObservation, PriceObservation
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def _contract() -> ContractSpec:
    return ContractSpec(
        contract_id="btc-market:UP",
        venue="polymarket",
        market_id="btc-market",
        condition_id="0xbtc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        side="UP",
        token_id="111",
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=">=",
        start_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 5, 31, 20, 5, tzinfo=timezone.utc),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="fixture",
        rule_hash="hash",
        parser_version="test",
    )


def _state() -> DecisionState:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    return DecisionState(
        state_id="state-1",
        asof_ts=asof_ts,
        contract=contract,
        threshold=103_950.0,
        seconds_left=120.0,
        settlement_price=104_000.0,
        settlement_source_key="polymarket_rtds_chainlink",
        proxy_prices={"coinbase_advanced_ws": 104_010.0},
        source_disagreement_bps=0.96,
        best_bid=0.61,
        best_ask=0.64,
        executable_price=0.64,
        spread=0.03,
        quote_age_ms=1000,
        source_age_ms=1000,
        book_age_ms=1000,
        realized_returns=(0.001, -0.0005),
        short_realized_vol=0.01,
        medium_realized_vol=0.012,
        long_realized_vol=0.015,
        sigma_tau=0.002,
        volatility_regime="normal",
        data_quality_flags=(),
    )


def test_store_writes_contract_price_book_state_decision_and_label(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)

    store.upsert_contract_spec(contract)
    store.insert_price_tick(
        PriceObservation(
            source_key="polymarket_rtds_chainlink",
            symbol="BTC/USD",
            event_ts=asof_ts,
            observed_ts=asof_ts,
            price=104_000.0,
        )
    )
    store.insert_orderbook_snapshot(
        OrderBookObservation(
            venue="polymarket",
            contract_id=contract.contract_id,
            token_id=contract.token_id,
            event_ts=asof_ts,
            observed_ts=asof_ts,
            best_bid=0.61,
            best_ask=0.64,
            bid_size_top=50.0,
            ask_size_top=40.0,
            spread=0.03,
            depth_json='{"bids":[],"asks":[]}',
        )
    )
    state = _state()
    store.upsert_asof_state_input(state)
    store.insert_decision_snapshot(
        decision_id="decision-1",
        state=state,
        model={"model_version": "none"},
        decision="WAIT",
        block_reason="probability_model_not_built",
    )
    store.insert_decision_label(
        decision_id="decision-1",
        contract_id=contract.contract_id,
        expiry_ts=contract.expiry_ts,
        settlement_price=104_100.0,
        did_finish_win=True,
        did_no_touch=True,
        realized_edge=None,
        label_source="fixture",
    )

    with duckdb.connect(str(db_path)) as conn:
        assert conn.sql("select count(*) from core.contracts").fetchone() == (1,)
        assert conn.sql("select count(*) from core.price_ticks").fetchone() == (1,)
        assert conn.sql("select count(*) from core.orderbook_snapshots").fetchone() == (1,)
        assert conn.sql("select count(*) from features.asof_state_inputs").fetchone() == (1,)
        assert conn.sql("select decision, block_reason from features.decision_snapshots").fetchall() == [
            ("WAIT", "probability_model_not_built")
        ]
        assert conn.sql("select did_finish_win, did_no_touch from validation.decision_labels").fetchall() == [
            (True, True)
        ]


def test_register_ingest_file_records_retention_manifest(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.duckdb"
    raw_path = tmp_path / "file.parquet"
    raw_path.write_bytes(b"abc")
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    store.register_ingest_file(
        file_id="file-1",
        source_key="coinbase_advanced_ws",
        stream_key="ticker",
        partition_date="2026-05-31",
        partition_hour=21,
        path=str(raw_path),
        sha256="abc123",
        row_count=2,
        first_event_ts=datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc),
        last_event_ts=datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc),
    )

    with duckdb.connect(str(db_path)) as conn:
        rows = conn.sql(
            "select source_key, stream_key, retention_class, archive_after_days from ops.retention_manifests"
        ).fetchall()

    assert rows == [("coinbase_advanced_ws", "ticker", "raw_hot_90d", 90)]
```

- [ ] **Step 2: Run normalized storage tests to verify they fail**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_normalized_writes.py -v
```

Expected: FAIL with missing methods on `DuckDbIngestStore`.

- [ ] **Step 3: Implement normalized write methods**

Modify `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`.

Add imports:

```python
from typing import Any

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState, OrderBookObservation, PriceObservation
```

Add this helper near the top:

```python
def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
```

Update `register_ingest_file()` after the existing `ops.ingest_files` insert:

```python
            conn.execute(
                """
                insert or replace into ops.retention_manifests
                (manifest_id, file_id, source_key, stream_key, partition_date, partition_hour,
                 path, sha256, row_count, first_event_ts, last_event_ts, retention_class,
                 archive_after_days, delete_after_days, archived_at, deleted_at, recorded_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"{file_id}:raw_hot_90d",
                    file_id,
                    source_key,
                    stream_key,
                    partition_date,
                    partition_hour,
                    path,
                    sha256,
                    row_count,
                    first_event_ts,
                    last_event_ts,
                    "raw_hot_90d",
                    90,
                    None,
                    None,
                    None,
                    datetime.now(timezone.utc),
                ],
            )
```

Add these methods to `DuckDbIngestStore`:

```python
    def upsert_contract_spec(self, contract: ContractSpec) -> None:
        now = datetime.now(timezone.utc)
        with duckdb.connect(str(self.db_path)) as conn:
            existing = conn.execute(
                "select first_seen_ts from core.contracts where contract_id = ?",
                [contract.contract_id],
            ).fetchone()
            first_seen_ts = now if existing is None else existing[0]
            conn.execute(
                """
                insert or replace into core.contracts
                (contract_id, venue, market_id, condition_id, slug, asset, side, token_id,
                 threshold_type, threshold_price, comparison_operator, start_ts, expiry_ts,
                 settlement_source_name, settlement_source_url, settlement_symbol, rule_text,
                 rule_hash, parser_version, first_seen_ts, last_seen_ts)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    contract.contract_id,
                    contract.venue,
                    contract.market_id,
                    contract.condition_id,
                    contract.slug,
                    contract.asset,
                    contract.side,
                    contract.token_id,
                    contract.threshold_type,
                    contract.threshold_price,
                    contract.comparison_operator,
                    contract.start_ts,
                    contract.expiry_ts,
                    contract.settlement_source_name,
                    contract.settlement_source_url,
                    contract.settlement_symbol,
                    contract.rule_text,
                    contract.rule_hash,
                    contract.parser_version,
                    first_seen_ts,
                    now,
                ],
            )

    def insert_price_tick(self, tick: PriceObservation, raw_file_id: str | None = None) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into core.price_ticks
                (source_key, symbol, event_ts, observed_ts, price, bid, ask, sequence, raw_file_id)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    tick.source_key,
                    tick.symbol,
                    tick.event_ts,
                    tick.observed_ts,
                    tick.price,
                    tick.bid,
                    tick.ask,
                    tick.sequence,
                    raw_file_id,
                ],
            )

    def insert_orderbook_snapshot(
        self,
        snapshot: OrderBookObservation,
        raw_file_id: str | None = None,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into core.orderbook_snapshots
                (venue, contract_id, token_id, event_ts, observed_ts, best_bid, best_ask,
                 bid_size_top, ask_size_top, spread, depth_json, raw_file_id)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    snapshot.venue,
                    snapshot.contract_id,
                    snapshot.token_id,
                    snapshot.event_ts,
                    snapshot.observed_ts,
                    snapshot.best_bid,
                    snapshot.best_ask,
                    snapshot.bid_size_top,
                    snapshot.ask_size_top,
                    snapshot.spread,
                    snapshot.depth_json,
                    raw_file_id,
                ],
            )

    def upsert_asof_state_input(self, state: DecisionState) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into features.asof_state_inputs
                (state_id, contract_id, asof_ts, asset, side, threshold, seconds_left,
                 settlement_price, settlement_source_key, proxy_prices_json,
                 source_disagreement_bps, best_bid, best_ask, executable_price, spread,
                 quote_age_ms, source_age_ms, book_age_ms, realized_returns_json,
                 short_realized_vol, medium_realized_vol, long_realized_vol, sigma_tau,
                 volatility_regime, data_quality_flags_json, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    state.state_id,
                    state.contract.contract_id,
                    state.asof_ts,
                    state.contract.asset,
                    state.contract.side,
                    state.threshold,
                    state.seconds_left,
                    state.settlement_price,
                    state.settlement_source_key,
                    _json(state.proxy_prices),
                    state.source_disagreement_bps,
                    state.best_bid,
                    state.best_ask,
                    state.executable_price,
                    state.spread,
                    state.quote_age_ms,
                    state.source_age_ms,
                    state.book_age_ms,
                    _json(list(state.realized_returns)),
                    state.short_realized_vol,
                    state.medium_realized_vol,
                    state.long_realized_vol,
                    state.sigma_tau,
                    state.volatility_regime,
                    _json(list(state.data_quality_flags)),
                    datetime.now(timezone.utc),
                ],
            )

    def insert_decision_snapshot(
        self,
        *,
        decision_id: str,
        state: DecisionState,
        model: dict[str, object],
        decision: str,
        block_reason: str | None,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into features.decision_snapshots
                (decision_id, state_id, contract_id, asof_ts, market_id, token_id,
                 state_json, model_json, decision, block_reason, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    decision_id,
                    state.state_id,
                    state.contract.contract_id,
                    state.asof_ts,
                    state.contract.market_id,
                    state.contract.token_id,
                    _json(state.to_json_dict()),
                    _json(model),
                    decision,
                    block_reason,
                    datetime.now(timezone.utc),
                ],
            )

    def insert_decision_label(
        self,
        *,
        decision_id: str,
        contract_id: str,
        expiry_ts: datetime,
        settlement_price: float,
        did_finish_win: bool,
        did_no_touch: bool,
        realized_edge: float | None,
        label_source: str,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into validation.decision_labels
                (decision_id, contract_id, expiry_ts, settlement_price, did_finish_win,
                 did_no_touch, realized_edge, label_source, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    decision_id,
                    contract_id,
                    expiry_ts,
                    settlement_price,
                    did_finish_win,
                    did_no_touch,
                    realized_edge,
                    label_source,
                    datetime.now(timezone.utc),
                ],
            )
```

- [ ] **Step 4: Run normalized storage tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_normalized_writes.py tests/storage/test_duckdb_store.py tests/storage/test_schema.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/storage/duckdb_store.py tests/storage/test_normalized_writes.py
git commit -m "feat: write normalized decision storage rows"
```

---

### Task 7: Add Storage Replay Queries For Latest Rows At Or Before asof_ts

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py`
- Create: `/Users/goon/polymarket/src/polymarket_engine/features/state_replay.py`
- Create: `/Users/goon/polymarket/tests/storage/test_state_replay.py`

- [ ] **Step 1: Write failing replay tests**

Create `/Users/goon/polymarket/tests/storage/test_state_replay.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import OrderBookObservation, PriceObservation
from polymarket_engine.features.state_builder import DecisionStateUnavailable
from polymarket_engine.features.state_replay import build_decision_state_from_store
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def _contract() -> ContractSpec:
    return ContractSpec(
        contract_id="btc-market:UP",
        venue="polymarket",
        market_id="btc-market",
        condition_id="0xbtc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        side="UP",
        token_id="111",
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=">=",
        start_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 5, 31, 20, 5, tzinfo=timezone.utc),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="fixture",
        rule_hash="hash",
        parser_version="test",
    )


def test_store_latest_price_tick_uses_latest_row_at_or_before_asof(tmp_path: Path) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    before = datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc)
    after = datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", before, before, 104_000.0)
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", after, after, 105_000.0)
    )

    tick = store.latest_price_tick(
        source_key="polymarket_rtds_chainlink",
        symbol="BTC/USD",
        asof_ts=asof_ts,
    )

    assert tick is not None
    assert tick.price == 104_000.0
    assert tick.event_ts == before


def test_build_decision_state_from_store_never_uses_future_price_or_book(tmp_path: Path) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    store.upsert_contract_spec(contract)
    before = datetime(2026, 5, 31, 20, 2, 59, tzinfo=timezone.utc)
    after = datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", before, before, 104_000.0)
    )
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", after, after, 105_000.0)
    )
    store.insert_orderbook_snapshot(
        OrderBookObservation(
            venue="polymarket",
            contract_id=contract.contract_id,
            token_id=contract.token_id,
            event_ts=before,
            observed_ts=before,
            best_bid=0.61,
            best_ask=0.64,
            bid_size_top=50.0,
            ask_size_top=40.0,
            spread=0.03,
            depth_json='{"bids":[],"asks":[]}',
        )
    )

    state = build_decision_state_from_store(
        store=store,
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=103_950.0,
        settlement_source_key="polymarket_rtds_chainlink",
        proxy_source_keys=(),
        volatility=None,
    )

    assert state.settlement_price == 104_000.0
    assert state.best_ask == 0.64


def test_build_decision_state_from_store_raises_when_only_future_price_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "replay.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    after = datetime(2026, 5, 31, 20, 3, 1, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", after, after, 105_000.0)
    )

    with pytest.raises(DecisionStateUnavailable, match="no settlement price"):
        build_decision_state_from_store(
            store=store,
            contract=contract,
            asof_ts=asof_ts,
            resolved_threshold_price=103_950.0,
            settlement_source_key="polymarket_rtds_chainlink",
            proxy_source_keys=(),
            volatility=None,
        )
```

- [ ] **Step 2: Run replay tests to verify they fail**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_state_replay.py -v
```

Expected: FAIL with missing `latest_price_tick()` and `build_decision_state_from_store()`.

- [ ] **Step 3: Add replay query methods to store**

Add these imports to `/Users/goon/polymarket/src/polymarket_engine/storage/duckdb_store.py` if not already present:

```python
from polymarket_engine.domain.market_state import OrderBookObservation, PriceObservation
```

Add methods to `DuckDbIngestStore`:

```python
    def latest_price_tick(
        self,
        *,
        source_key: str,
        symbol: str,
        asof_ts: datetime,
    ) -> PriceObservation | None:
        with duckdb.connect(str(self.db_path)) as conn:
            row = conn.execute(
                """
                select source_key, symbol, event_ts, observed_ts, price, bid, ask, sequence
                from core.price_ticks
                where source_key = ?
                  and symbol = ?
                  and event_ts <= ?
                  and observed_ts <= ?
                order by observed_ts desc, event_ts desc
                limit 1
                """,
                [source_key, symbol, asof_ts, asof_ts],
            ).fetchone()
        if row is None:
            return None
        return PriceObservation(
            source_key=row[0],
            symbol=row[1],
            event_ts=row[2],
            observed_ts=row[3],
            price=row[4],
            bid=row[5],
            ask=row[6],
            sequence=row[7],
        )

    def latest_orderbook_snapshot(
        self,
        *,
        venue: str,
        token_id: str,
        asof_ts: datetime,
    ) -> OrderBookObservation | None:
        with duckdb.connect(str(self.db_path)) as conn:
            row = conn.execute(
                """
                select venue, contract_id, token_id, event_ts, observed_ts, best_bid, best_ask,
                       bid_size_top, ask_size_top, spread, depth_json
                from core.orderbook_snapshots
                where venue = ?
                  and token_id = ?
                  and event_ts <= ?
                  and observed_ts <= ?
                order by observed_ts desc, event_ts desc
                limit 1
                """,
                [venue, token_id, asof_ts, asof_ts],
            ).fetchone()
        if row is None:
            return None
        return OrderBookObservation(
            venue=row[0],
            contract_id=row[1],
            token_id=row[2],
            event_ts=row[3],
            observed_ts=row[4],
            best_bid=row[5],
            best_ask=row[6],
            bid_size_top=row[7],
            ask_size_top=row[8],
            spread=row[9],
            depth_json=row[10],
        )
```

- [ ] **Step 4: Add store replay builder**

Create `/Users/goon/polymarket/src/polymarket_engine/features/state_replay.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState, VolatilitySnapshot
from polymarket_engine.features.state_builder import build_decision_state
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def build_decision_state_from_store(
    *,
    store: DuckDbIngestStore,
    contract: ContractSpec,
    asof_ts: datetime,
    resolved_threshold_price: float | None,
    settlement_source_key: str,
    proxy_source_keys: Sequence[str],
    volatility: VolatilitySnapshot | None,
) -> DecisionState:
    settlement = store.latest_price_tick(
        source_key=settlement_source_key,
        symbol=contract.settlement_symbol,
        asof_ts=asof_ts,
    )
    settlement_prices = () if settlement is None else (settlement,)
    proxy_prices = tuple(
        tick
        for source_key in proxy_source_keys
        if (
            tick := store.latest_price_tick(
                source_key=source_key,
                symbol=contract.settlement_symbol,
                asof_ts=asof_ts,
            )
        )
        is not None
    )
    book = store.latest_orderbook_snapshot(
        venue=contract.venue,
        token_id=contract.token_id,
        asof_ts=asof_ts,
    )
    orderbooks = () if book is None else (book,)
    return build_decision_state(
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=resolved_threshold_price,
        settlement_prices=settlement_prices,
        proxy_prices=proxy_prices,
        orderbooks=orderbooks,
        volatility=volatility,
    )
```

- [ ] **Step 5: Run replay tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/storage/test_state_replay.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/storage/duckdb_store.py src/polymarket_engine/features/state_replay.py tests/storage/test_state_replay.py
git commit -m "feat: replay as-of state from storage"
```

---

### Task 8: Align Live Collector Normalized Writes Without Changing Collector Behavior

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`
- Test: `/Users/goon/polymarket/tests/ingestion/test_live_collector.py`

- [ ] **Step 1: Inspect existing collector test shape**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_live_collector.py -v
```

Expected: PASS before changes.

- [ ] **Step 2: Add a failing test that collector registers side-level contracts**

Append to `/Users/goon/polymarket/tests/ingestion/test_live_collector.py`:

```python
from pathlib import Path

import duckdb


def test_register_market_rules_also_writes_side_level_contracts(tmp_path: Path) -> None:
    from polymarket_engine.ingestion.live_collector import register_market_rules

    db_path = tmp_path / "contracts.duckdb"
    markets = [
        {
            "id": "2397858",
            "conditionId": "0xabc",
            "slug": "btc-updown-5m-1780264500",
            "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
            "description": BTC_DESCRIPTION,
            "eventStartTime": "2026-05-31T21:55:00Z",
            "endDate": "2026-05-31T22:00:00Z",
            "resolutionSource": "https://data.chain.link/streams/btc-usd",
            "outcomes": '["Up", "Down"]',
            "clobTokenIds": '["111", "222"]',
        }
    ]

    errors = register_market_rules(db_path, markets)

    assert errors == ()
    with duckdb.connect(str(db_path)) as conn:
        rows = conn.sql("select contract_id, asset, side, token_id from core.contracts order by side").fetchall()

    assert rows == [
        ("2397858:DOWN", "BTC", "DOWN", "222"),
        ("2397858:UP", "BTC", "UP", "111"),
    ]
```

If `BTC_DESCRIPTION` is not available in this test file, copy the fixture string from `tests/domain/test_contract_rules.py`.

- [ ] **Step 3: Run test to verify it fails**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_live_collector.py::test_register_market_rules_also_writes_side_level_contracts -v
```

Expected: FAIL because `register_market_rules()` writes `core.contract_rules` but not side-level `core.contracts`.

- [ ] **Step 4: Update `register_market_rules()`**

Modify `/Users/goon/polymarket/src/polymarket_engine/ingestion/live_collector.py`.

Add import:

```python
from polymarket_engine.domain.contracts import contract_specs_from_rule
```

Inside the successful parse branch, after `store.upsert_contract_rule(rule)`, add:

```python
            for contract in contract_specs_from_rule(rule):
                store.upsert_contract_spec(contract)
```

- [ ] **Step 5: Run collector test**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/ingestion/test_live_collector.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/goon/polymarket
git add src/polymarket_engine/ingestion/live_collector.py tests/ingestion/test_live_collector.py
git commit -m "feat: persist side-level contracts during collection"
```

---

### Task 9: Document Retention Defaults And Bridge Completion Boundaries

**Files:**
- Modify: `/Users/goon/polymarket/docs/BINARY_CONTRACT_ENGINE_PLAN.md`

- [ ] **Step 1: Add the bridge completion note**

Add this section near the current build-slice or storage section in `/Users/goon/polymarket/docs/BINARY_CONTRACT_ENGINE_PLAN.md`:

```markdown
## Build Slice: Sections 1-3 Bridge Completion

This slice completes the bridge before probability modeling:

- contract rules become side-level `ContractSpec` rows;
- normalized price and order-book observations can be written to DuckDB;
- `DecisionState` joins contract, price, volatility placeholder, and order-book state;
- replay queries select only rows with timestamps `<= asof_ts`;
- future settlement, later BTC/ETH movement, final labels, and later Polymarket quotes remain labels only;
- retention metadata is recorded for raw partitions, but automatic deletion is not enabled.

Retention defaults:

- keep contract rules, rule hashes, decision states, labels, daily/hourly metrics, incident logs, and kill-switch logs forever;
- keep raw tick/event data hot for 90 days if disk allows;
- after the hot window, prefer aggregation/archive over deletion;
- never delete without a retention manifest containing source, stream, partition, row count, sha256, first/last timestamp, retention class, and archive/delete timestamp.

Deployment boundary:

- `collect` mode starts live collection;
- `paper` mode can later start live data plus simulated decisions/orders;
- `live` mode later requires explicit mode selection, valid keys, kill-switch health, clock health, disk health, monitoring health, and an armed confirmation;
- keys existing must not arm live trading by itself.
```

- [ ] **Step 2: Run markdown sanity check**

Run:

```bash
cd /Users/goon/polymarket
git diff --check
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
cd /Users/goon/polymarket
git add docs/BINARY_CONTRACT_ENGINE_PLAN.md
git commit -m "docs: lock bridge completion boundaries"
```

---

### Task 10: Full Verification Before Probability Work

**Files:**
- No new files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest \
  tests/domain/test_contract_rules.py \
  tests/domain/test_contracts.py \
  tests/features/test_asof_inputs.py \
  tests/features/test_state_builder.py \
  tests/storage/test_schema.py \
  tests/storage/test_duckdb_store.py \
  tests/storage/test_normalized_writes.py \
  tests/storage/test_state_replay.py \
  tests/ingestion/test_live_collector.py \
  -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full test suite once**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest
```

Expected: all tests pass.

- [ ] **Step 3: Run lint**

Run:

```bash
cd /Users/goon/polymarket
uv run ruff check .
```

Expected: all checks pass.

- [ ] **Step 4: Run type check**

Run:

```bash
cd /Users/goon/polymarket
uv run mypy src
```

Expected: success with no type errors.

- [ ] **Step 5: Confirm this slice stops before probability models**

Run:

```bash
cd /Users/goon/polymarket
rg -n "p_finish|p_no_touch|Monte Carlo|XGBoost|probability" src tests
```

Expected: no new implementation of probability models. Existing docs or comments may mention these terms; source code should not calculate them yet.

- [ ] **Step 6: Commit verification-only fixes if needed**

If lint, typing, or tests required small fixes, commit them:

```bash
cd /Users/goon/polymarket
git add src tests docs
git commit -m "chore: verify decision-state storage bridge"
```

If no fixes were needed, do not create an empty commit.

---

## Self-Review

Spec coverage:
- Section 1 contract rules and settlement source: Tasks 1, 2, and 8.
- Section 2 BTC/ETH data and as-of state construction: Tasks 3, 4, and 7.
- Section 3 normalized storage and labels: Tasks 5, 6, and 7.
- No-future-data rule: Tasks 4 and 7.
- Retention policy and metadata hooks: Tasks 5, 6, and 9.
- Guided setup context preserved without implementing Docker/auth: Task 9.

Placeholder scan:
- No `TBD`, `TODO`, or vague "handle edge cases" steps are used.
- Each code task includes concrete files, test commands, expected failures, implementation snippets, and commit commands.

Type consistency:
- `ContractSpec.contract_id` is used as the side-level identifier across state, storage, and replay.
- `asof_ts`, `event_ts`, and `observed_ts` remain timezone-aware UTC datetimes.
- `decision_labels` use `did_finish_win`, `did_no_touch`, and `realized_edge` exactly as requested.
