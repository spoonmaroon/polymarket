# Contract Rule Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a strict Polymarket crypto Up/Down rule parser that normalizes BTC/ETH/SOL market rules before any probability model uses them.

**Architecture:** Add a focused domain parser that converts Gamma market payloads into accepted or rejected normalized rule records. Store the parser output in a dedicated DuckDB `core.contract_rules` table while continuing to preserve raw Gamma payloads in immutable Parquet.

**Tech Stack:** Python dataclasses, regex, DuckDB schema migration, pytest, ruff, mypy.

---

## File Structure

- Create `src/polymarket_engine/domain/contract_rules.py`: normalized rule dataclasses, parser error type, rule hash helper, strict Polymarket crypto Up/Down parser.
- Modify `src/polymarket_engine/storage/schema.sql`: add `core.contract_rules` table with start-price fields, comparison operators, parser version, accepted flag, and reject reason.
- Modify `src/polymarket_engine/storage/duckdb_store.py`: add `upsert_contract_rule()` for accepted and rejected parser outputs.
- Modify `src/polymarket_engine/ingestion/live_collector.py`: after fetching Gamma markets, parse and store contract rules before CLOB book collection.
- Add `tests/domain/test_contract_rules.py`: accepted BTC/ETH/SOL fixtures, equivalent phrase fixtures, and rejection fixtures.
- Add `tests/storage/test_contract_rule_store.py`: schema/store persistence test.
- Modify `tests/storage/test_schema.py`: assert new `core.contract_rules` table exists.
- Modify `tests/ingestion/test_live_collector.py`: assert fake/live orchestration can parse and register market rule records.

## Task 1: Add Strict Contract Rule Domain Parser

**Files:**
- Create: `src/polymarket_engine/domain/contract_rules.py`
- Test: `tests/domain/test_contract_rules.py`

- [ ] **Step 1: Write accepted BTC fixture test**

Add this test file:

```python
from datetime import datetime, timezone

import pytest

from polymarket_engine.domain.contract_rules import (
    ContractRuleRejected,
    parse_polymarket_crypto_updown_rule,
    rule_text_hash,
)


BTC_DESCRIPTION = """This market will resolve to "Up" if the Bitcoin price at the end of the time range specified in the title is greater than or equal to the price at the beginning of that range. Otherwise, it will resolve to "Down".
The resolution source for this market is information from Chainlink, specifically the BTC/USD data stream available at https://data.chain.link/streams/btc-usd.
Please note that this market is about the price according to Chainlink data stream BTC/USD, not according to other sources or spot exchanges."""


def test_parse_btc_updown_start_price_rule() -> None:
    market = {
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

    rule = parse_polymarket_crypto_updown_rule(market)

    assert rule.accepted is True
    assert rule.reject_reason is None
    assert rule.market_id == "2397858"
    assert rule.condition_id == "0xabc"
    assert rule.slug == "btc-updown-5m-1780264500"
    assert rule.asset == "BTC"
    assert rule.contract_type == "crypto_up_down_start_price"
    assert rule.start_ts == datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc)
    assert rule.end_ts == datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc)
    assert rule.expiry_ts == rule.end_ts
    assert rule.threshold_type == "start_price"
    assert rule.threshold_price is None
    assert rule.comparison_operator_up == ">="
    assert rule.comparison_operator_down == "<"
    assert rule.settlement_source_name == "chainlink_data_streams"
    assert rule.settlement_source_url == "https://data.chain.link/streams/btc-usd"
    assert rule.settlement_symbol == "BTC/USD"
    assert rule.outcome_token_ids == {"Up": "111", "Down": "222"}
    assert rule.rule_hash == rule_text_hash(BTC_DESCRIPTION)
    assert rule.parser_version == "polymarket_crypto_updown_v1"
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
uv run pytest tests/domain/test_contract_rules.py::test_parse_btc_updown_start_price_rule -q
```

Expected: FAIL because `polymarket_engine.domain.contract_rules` does not exist.

- [ ] **Step 3: Implement the minimal parser**

Create `src/polymarket_engine/domain/contract_rules.py`:

```python
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

PARSER_VERSION = "polymarket_crypto_updown_v1"

ASSET_BY_WORD = {
    "bitcoin": ("BTC", "BTC/USD", "https://data.chain.link/streams/btc-usd"),
    "ethereum": ("ETH", "ETH/USD", "https://data.chain.link/streams/eth-usd"),
    "solana": ("SOL", "SOL/USD", "https://data.chain.link/streams/sol-usd"),
}

SUPPORTED_TIE_PHRASES = (
    "greater than or equal to",
    "at or above",
    "not below",
)


class ContractRuleRejected(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedContractRule:
    market_id: str
    condition_id: str
    slug: str
    asset: str
    contract_type: str
    start_ts: datetime
    end_ts: datetime
    expiry_ts: datetime
    threshold_type: str
    threshold_price: float | None
    comparison_operator_up: str
    comparison_operator_down: str
    settlement_source_name: str
    settlement_source_url: str
    settlement_symbol: str
    outcome_token_ids: dict[str, str]
    rule_text: str
    rule_hash: str
    parser_version: str
    accepted: bool
    reject_reason: str | None


def rule_text_hash(rule_text: str) -> str:
    return hashlib.sha256(rule_text.strip().encode("utf-8")).hexdigest()


def parse_polymarket_crypto_updown_rule(market: dict[str, Any]) -> NormalizedContractRule:
    description = str(market.get("description", "")).strip()
    if not description:
        raise ContractRuleRejected("missing rule text")

    slug = str(market.get("slug", ""))
    slug_match = re.fullmatch(r"(btc|eth|sol)-updown-5m-\d+", slug)
    if slug_match is None:
        raise ContractRuleRejected("unsupported slug")

    asset_from_slug = slug_match.group(1).upper()
    asset, settlement_symbol, expected_source_url = _asset_from_description(description)
    if asset != asset_from_slug:
        raise ContractRuleRejected("asset mismatch between slug and rule text")

    outcomes = _decode_json_list(market.get("outcomes"))
    token_ids = _decode_json_list(market.get("clobTokenIds"))
    if outcomes != ["Up", "Down"]:
        raise ContractRuleRejected("unsupported outcomes")
    if len(token_ids) != 2:
        raise ContractRuleRejected("expected two token ids")

    source_url = str(market.get("resolutionSource") or expected_source_url)
    if source_url != expected_source_url or expected_source_url not in description:
        raise ContractRuleRejected("unsupported settlement source")

    normalized_text = " ".join(description.lower().split())
    if not any(phrase in normalized_text for phrase in SUPPORTED_TIE_PHRASES):
        raise ContractRuleRejected("ambiguous tie rule")
    if "price at the beginning" not in normalized_text:
        raise ContractRuleRejected("missing start-price threshold rule")
    if "price at the end" not in normalized_text:
        raise ContractRuleRejected("missing end-price comparison rule")

    start_ts = _parse_datetime(market.get("eventStartTime"), "missing eventStartTime")
    end_ts = _parse_datetime(market.get("endDate"), "missing endDate")
    if start_ts >= end_ts:
        raise ContractRuleRejected("start time must be before end time")

    return NormalizedContractRule(
        market_id=str(market["id"]),
        condition_id=str(market["conditionId"]),
        slug=slug,
        asset=asset,
        contract_type="crypto_up_down_start_price",
        start_ts=start_ts,
        end_ts=end_ts,
        expiry_ts=end_ts,
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator_up=">=",
        comparison_operator_down="<",
        settlement_source_name="chainlink_data_streams",
        settlement_source_url=source_url,
        settlement_symbol=settlement_symbol,
        outcome_token_ids={"Up": token_ids[0], "Down": token_ids[1]},
        rule_text=description,
        rule_hash=rule_text_hash(description),
        parser_version=PARSER_VERSION,
        accepted=True,
        reject_reason=None,
    )


def _asset_from_description(description: str) -> tuple[str, str, str]:
    lower = description.lower()
    for asset_word, values in ASSET_BY_WORD.items():
        if asset_word in lower:
            return values
    raise ContractRuleRejected("unsupported asset")


def _decode_json_list(value: object) -> list[str]:
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ContractRuleRejected("expected JSON list")
    return [str(item) for item in parsed]


def _parse_datetime(value: object, reject_reason: str) -> datetime:
    if value is None:
        raise ContractRuleRejected(reject_reason)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
```

- [ ] **Step 4: Run the accepted BTC test and verify GREEN**

Run:

```bash
uv run pytest tests/domain/test_contract_rules.py::test_parse_btc_updown_start_price_rule -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/polymarket_engine/domain/contract_rules.py tests/domain/test_contract_rules.py
git commit -m "Add Polymarket crypto rule parser"
```

## Task 2: Add Equivalent Phrase And Rejection Coverage

**Files:**
- Modify: `tests/domain/test_contract_rules.py`
- Modify: `src/polymarket_engine/domain/contract_rules.py`

- [ ] **Step 1: Add equivalent phrase tests**

Append:

```python
@pytest.mark.parametrize(
    "phrase",
    ["greater than or equal to", "at or above", "not below"],
)
def test_parse_accepts_supported_tie_phrases(phrase: str) -> None:
    description = BTC_DESCRIPTION.replace("greater than or equal to", phrase)
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": description,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    assert parse_polymarket_crypto_updown_rule(market).comparison_operator_up == ">="
```

- [ ] **Step 2: Add rejection tests**

Append:

```python
def test_parse_rejects_ambiguous_tie_rule() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION.replace("greater than or equal to", "higher than"),
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    with pytest.raises(ContractRuleRejected, match="ambiguous tie rule"):
        parse_polymarket_crypto_updown_rule(market)


def test_parse_rejects_wrong_settlement_source() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://example.com/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    with pytest.raises(ContractRuleRejected, match="unsupported settlement source"):
        parse_polymarket_crypto_updown_rule(market)
```

- [ ] **Step 3: Run rejection tests**

Run:

```bash
uv run pytest tests/domain/test_contract_rules.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit Task 2**

```bash
git add tests/domain/test_contract_rules.py src/polymarket_engine/domain/contract_rules.py
git commit -m "Harden crypto rule parser fixtures"
```

## Task 3: Store Parsed Contract Rules

**Files:**
- Modify: `src/polymarket_engine/storage/schema.sql`
- Modify: `src/polymarket_engine/storage/duckdb_store.py`
- Modify: `tests/storage/test_schema.py`
- Create: `tests/storage/test_contract_rule_store.py`

- [ ] **Step 1: Add schema test expectation**

Modify `tests/storage/test_schema.py` so its table expectation includes:

```python
"core.contract_rules",
```

- [ ] **Step 2: Add store test**

Create `tests/storage/test_contract_rule_store.py`:

```python
from datetime import datetime, timezone

import duckdb

from polymarket_engine.domain.contract_rules import NormalizedContractRule
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_store_upserts_contract_rule(tmp_path) -> None:
    db_path = tmp_path / "test.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    rule = NormalizedContractRule(
        market_id="2397858",
        condition_id="0xabc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        contract_type="crypto_up_down_start_price",
        start_ts=datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc),
        end_ts=datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc),
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator_up=">=",
        comparison_operator_down="<",
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        outcome_token_ids={"Up": "111", "Down": "222"},
        rule_text="rule",
        rule_hash="hash",
        parser_version="polymarket_crypto_updown_v1",
        accepted=True,
        reject_reason=None,
    )

    store.upsert_contract_rule(rule)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            "select asset, threshold_type, comparison_operator_up, settlement_symbol, accepted "
            "from core.contract_rules where market_id = ?",
            ["2397858"],
        ).fetchone()

    assert row == ("BTC", "start_price", ">=", "BTC/USD", True)
```

- [ ] **Step 3: Run storage tests and verify RED**

Run:

```bash
uv run pytest tests/storage/test_schema.py tests/storage/test_contract_rule_store.py -q
```

Expected: FAIL because `core.contract_rules` and `upsert_contract_rule()` do not exist.

- [ ] **Step 4: Add `core.contract_rules` table**

Append this table to `src/polymarket_engine/storage/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS core.contract_rules (
    market_id VARCHAR PRIMARY KEY,
    condition_id VARCHAR NOT NULL,
    slug VARCHAR NOT NULL,
    asset VARCHAR NOT NULL,
    contract_type VARCHAR NOT NULL,
    start_ts TIMESTAMPTZ NOT NULL,
    end_ts TIMESTAMPTZ NOT NULL,
    expiry_ts TIMESTAMPTZ NOT NULL,
    threshold_type VARCHAR NOT NULL,
    threshold_price DOUBLE,
    comparison_operator_up VARCHAR NOT NULL,
    comparison_operator_down VARCHAR NOT NULL,
    settlement_source_name VARCHAR NOT NULL,
    settlement_source_url VARCHAR NOT NULL,
    settlement_symbol VARCHAR NOT NULL,
    outcome_token_ids_json VARCHAR NOT NULL,
    rule_text VARCHAR NOT NULL,
    rule_hash VARCHAR NOT NULL,
    parser_version VARCHAR NOT NULL,
    accepted BOOLEAN NOT NULL,
    reject_reason VARCHAR,
    updated_at TIMESTAMPTZ NOT NULL
);
```

- [ ] **Step 5: Add store method**

Modify `src/polymarket_engine/storage/duckdb_store.py`:

```python
import json
```

Add:

```python
from polymarket_engine.domain.contract_rules import NormalizedContractRule
```

Add this method to `DuckDbIngestStore`:

```python
    def upsert_contract_rule(self, rule: NormalizedContractRule) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into core.contract_rules
                (market_id, condition_id, slug, asset, contract_type, start_ts, end_ts,
                 expiry_ts, threshold_type, threshold_price, comparison_operator_up,
                 comparison_operator_down, settlement_source_name, settlement_source_url,
                 settlement_symbol, outcome_token_ids_json, rule_text, rule_hash,
                 parser_version, accepted, reject_reason, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    rule.market_id,
                    rule.condition_id,
                    rule.slug,
                    rule.asset,
                    rule.contract_type,
                    rule.start_ts,
                    rule.end_ts,
                    rule.expiry_ts,
                    rule.threshold_type,
                    rule.threshold_price,
                    rule.comparison_operator_up,
                    rule.comparison_operator_down,
                    rule.settlement_source_name,
                    rule.settlement_source_url,
                    rule.settlement_symbol,
                    json.dumps(rule.outcome_token_ids, sort_keys=True),
                    rule.rule_text,
                    rule.rule_hash,
                    rule.parser_version,
                    rule.accepted,
                    rule.reject_reason,
                    datetime.now(timezone.utc),
                ],
            )
```

- [ ] **Step 6: Run storage tests and verify GREEN**

Run:

```bash
uv run pytest tests/storage/test_schema.py tests/storage/test_contract_rule_store.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/polymarket_engine/storage/schema.sql src/polymarket_engine/storage/duckdb_store.py tests/storage/test_schema.py tests/storage/test_contract_rule_store.py
git commit -m "Store normalized contract rules"
```

## Task 4: Wire Parser Into Live Collection

**Files:**
- Modify: `src/polymarket_engine/ingestion/live_collector.py`
- Modify: `tests/ingestion/test_live_collector.py`

- [ ] **Step 1: Add fake Gamma market runner test**

Modify `tests/ingestion/test_live_collector.py` imports:

```python
import duckdb

from polymarket_engine.ingestion.live_collector import (
    LiveCollectorConfig,
    run_fake_collection,
    register_market_rules,
)
```

Append this test:

```python
BTC_DESCRIPTION = """This market will resolve to "Up" if the Bitcoin price at the end of the time range specified in the title is greater than or equal to the price at the beginning of that range. Otherwise, it will resolve to "Down".
The resolution source for this market is information from Chainlink, specifically the BTC/USD data stream available at https://data.chain.link/streams/btc-usd.
Please note that this market is about the price according to Chainlink data stream BTC/USD, not according to other sources or spot exchanges."""


def test_register_market_rules_stores_accepted_contract_rule(tmp_path: Path) -> None:
    config = LiveCollectorConfig(
        assets=("BTC",),
        duration_seconds=1,
        raw_root=tmp_path / "raw",
        duckdb_path=tmp_path / "collector.duckdb",
        max_batch_size=2,
    )
    market = {
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

    source_errors = register_market_rules(config.duckdb_path, (market,))

    assert source_errors == {}
    with duckdb.connect(str(config.duckdb_path), read_only=True) as conn:
        row = conn.execute(
            "select asset, threshold_type, comparison_operator_up, comparison_operator_down, "
            "settlement_symbol, accepted from core.contract_rules where slug = ?",
            ["btc-updown-5m-1780264500"],
        ).fetchone()

    assert row == ("BTC", "start_price", ">=", "<", "BTC/USD", True)
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
uv run pytest tests/ingestion/test_live_collector.py::test_register_market_rules_stores_accepted_contract_rule -q
```

Expected: FAIL because `register_market_rules` does not exist.

- [ ] **Step 3: Add market-rule registration helper**

In `src/polymarket_engine/ingestion/live_collector.py`, import:

```python
from polymarket_engine.domain.contract_rules import (
    ContractRuleRejected,
    parse_polymarket_crypto_updown_rule,
)
```

Add this function near `_register_file()`:

```python
def register_market_rules(
    duckdb_path: Path,
    markets: tuple[dict[str, object], ...],
) -> dict[str, str]:
    store = DuckDbIngestStore(duckdb_path)
    store.apply_schema()
    source_errors: dict[str, str] = {}
    for market in markets:
        slug = str(market.get("slug", "unknown"))
        try:
            store.upsert_contract_rule(parse_polymarket_crypto_updown_rule(market))
        except ContractRuleRejected as exc:
            source_errors[f"contract_rule:{slug}"] = str(exc)
    return source_errors
```

- [ ] **Step 4: Run helper test and verify GREEN**

Run:

```bash
uv run pytest tests/ingestion/test_live_collector.py::test_register_market_rules_stores_accepted_contract_rule -q
```

Expected: PASS.

- [ ] **Step 5: Wire helper into live collector**

Inside `run_live_collection()`, immediately after `markets = await fetch_crypto_5m_markets(...)`, add:

```python
            source_errors.update(register_market_rules(config.duckdb_path, markets))
```

- [ ] **Step 6: Run live collector tests and verify GREEN**

Run:

```bash
uv run pytest tests/ingestion/test_live_collector.py tests/domain/test_contract_rules.py tests/storage/test_contract_rule_store.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/polymarket_engine/ingestion/live_collector.py tests/ingestion/test_live_collector.py
git commit -m "Parse contract rules during live collection"
```

## Task 5: Verify End To End

- [ ] **Step 1: Run full test suite**

Run:

```bash
uv run pytest
```

Expected: all tests pass.

- [ ] **Step 2: Run linter**

Run:

```bash
uv run ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 3: Run type checker**

Run:

```bash
uv run mypy src
```

Expected: `Success: no issues found`.

- [ ] **Step 4: Run short live smoke**

Run:

```bash
mkdir -p data/raw
touch data/raw/.polymarket_archive_root
uv run polymarket-engine collect --assets BTC,ETH --duration 5 --max-batch-size 10
```

Expected: `source_errors` contains no `polymarket` or `coinbase_advanced_ws` failures. `polymarket_rtds_chainlink` should write rows when RTDS emits messages.

- [ ] **Step 5: Inspect parsed rules**

Run:

```bash
uv run python - <<'PY'
import duckdb
con = duckdb.connect("data/db/polymarket.duckdb", read_only=True)
print(con.execute("""
    select slug, asset, threshold_type, comparison_operator_up, comparison_operator_down,
           settlement_symbol, accepted, reject_reason
    from core.contract_rules
    order by updated_at desc
    limit 10
""").fetchall())
PY
```

Expected: recent BTC/ETH Up/Down markets show `threshold_type='start_price'`, `comparison_operator_up='>='`, `comparison_operator_down='<'`, and accepted `true`.

- [ ] **Step 6: Confirm raw data remains ignored**

Run:

```bash
git status --ignored --short data
```

Expected: output starts with `!! data/`, confirming raw live data is ignored.
