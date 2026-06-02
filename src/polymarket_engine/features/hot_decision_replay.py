from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from polymarket_engine.domain.contracts import Asset, ContractSide, ContractSpec
from polymarket_engine.features.state_builder import DecisionStateUnavailable
from polymarket_engine.features.state_replay import build_decision_state_from_store
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


HOT_DECISION_SCHEMA_VERSION = "rust-hot-decision-state-v1"
SETTLEMENT_SOURCE_KEY = "polymarket_rtds_chainlink"
LIVE_HEALTH_FRESHNESS_MS = 30_000
PYTHON_ONLY_FLAGS = {"missing_volatility"}
RUST_FLAG_MAP = {
    "MissingOrderbook": "missing_orderbook",
    "IncompleteOrderbook": "incomplete_orderbook",
    "StaleSource": "stale_source",
    "StaleOrderbook": "stale_orderbook",
}


@dataclass(frozen=True)
class HotDecisionReplayMismatch:
    state_id: str
    field: str
    hot_value: str | None
    replay_value: str | None


@dataclass(frozen=True)
class HotDecisionReplayComparison:
    state_id: str
    mismatches: tuple[HotDecisionReplayMismatch, ...]

    @property
    def ok(self) -> bool:
        return not self.mismatches


@dataclass(frozen=True)
class HotDecisionReplayResult:
    rows_checked: int
    comparisons: tuple[HotDecisionReplayComparison, ...]

    @property
    def mismatches(self) -> tuple[HotDecisionReplayMismatch, ...]:
        return tuple(mismatch for comparison in self.comparisons for mismatch in comparison.mismatches)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def recent_hot_decision_rows(raw_root: Path, *, limit: int) -> tuple[dict[str, Any], ...]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    rows: deque[dict[str, Any]] = deque(maxlen=limit)
    decision_root = raw_root / "polymarket_decision_state" / "hot_state"
    for path in sorted(decision_root.rglob("decision-state.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                if isinstance(row, dict) and row.get("schema_version") == HOT_DECISION_SCHEMA_VERSION:
                    rows.append(row)
    return tuple(rows)


def verify_hot_decision_rows(
    *,
    rows: Sequence[dict[str, Any]],
    store: DuckDbIngestStore,
    price_tolerance: Decimal = Decimal("0.000000001"),
) -> HotDecisionReplayResult:
    comparisons = tuple(
        _compare_hot_decision_row(row=row, store=store, price_tolerance=price_tolerance)
        for row in rows
    )
    return HotDecisionReplayResult(rows_checked=len(rows), comparisons=comparisons)


def _compare_hot_decision_row(
    *,
    row: dict[str, Any],
    store: DuckDbIngestStore,
    price_tolerance: Decimal,
) -> HotDecisionReplayComparison:
    state_id = str(row.get("state_id") or "<unknown>")
    contract = _contract_from_hot_row(row)
    asof_ts = _parse_ts(row["asof_ts"])
    mismatches: list[HotDecisionReplayMismatch] = []
    try:
        replay = build_decision_state_from_store(
            store=store,
            contract=contract,
            asof_ts=asof_ts,
            resolved_threshold_price=None,
            settlement_source_key=SETTLEMENT_SOURCE_KEY,
            proxy_source_keys=(),
            volatility=None,
            volatility_source_key=None,
            stale_source_after_ms=LIVE_HEALTH_FRESHNESS_MS,
            stale_book_after_ms=LIVE_HEALTH_FRESHNESS_MS,
        )
    except DecisionStateUnavailable as exc:
        return HotDecisionReplayComparison(
            state_id=state_id,
            mismatches=(
                HotDecisionReplayMismatch(
                    state_id=state_id,
                    field="replay_state",
                    hot_value="available",
                    replay_value=str(exc),
                ),
            ),
        )

    _compare_text(mismatches, state_id, "side", _hot_side(row), replay.contract.side)
    _compare_text(mismatches, state_id, "token_id", str(row["token_id"]), replay.contract.token_id)
    _compare_timestamp(mismatches, state_id, "asof_ts", row.get("asof_ts"), replay.asof_ts)
    _compare_decimal(
        mismatches,
        state_id,
        "threshold_price",
        row.get("threshold_price"),
        replay.threshold,
        price_tolerance,
    )
    _compare_timestamp(
        mismatches,
        state_id,
        "threshold_event_ts",
        row.get("threshold_event_ts"),
        replay.threshold_event_ts,
    )
    _compare_decimal(
        mismatches,
        state_id,
        "settlement_price",
        row.get("settlement_price"),
        replay.settlement_price,
        price_tolerance,
    )
    _compare_timestamp(
        mismatches,
        state_id,
        "settlement_event_ts",
        row.get("settlement_event_ts"),
        replay.settlement_event_ts,
    )
    for field in ("best_bid", "best_ask", "executable_price", "spread"):
        _compare_decimal(mismatches, state_id, field, row.get(field), getattr(replay, field), price_tolerance)
    _compare_number(mismatches, state_id, "source_age_ms", row.get("source_age_ms"), replay.source_age_ms)
    _compare_number(mismatches, state_id, "book_age_ms", row.get("book_age_ms"), replay.book_age_ms)
    _compare_flags(
        mismatches,
        state_id,
        hot_flags=row.get("data_quality_flags"),
        replay_flags=replay.data_quality_flags,
    )
    return HotDecisionReplayComparison(state_id=state_id, mismatches=tuple(mismatches))


def _contract_from_hot_row(row: dict[str, Any]) -> ContractSpec:
    contract = _mapping(row["contract"], "contract")
    window = _mapping(contract["window"], "contract.window")
    asset = _asset(window["asset"])
    interval = str(window["interval"])
    start_ts = _parse_ts(window["start_ts"])
    expiry_ts = _parse_ts(window["end_ts"])
    side = _hot_side(row)
    slug = f"{asset.lower()}-updown-{interval}-{int(start_ts.timestamp())}"
    return ContractSpec(
        contract_id=f"{slug}:{side}",
        venue="polymarket",
        market_id=slug,
        condition_id=f"unknown:{slug}",
        slug=slug,
        asset=asset,
        side=side,
        token_id=str(row["token_id"]),
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=">=" if side == "UP" else "<",
        start_ts=start_ts,
        expiry_ts=expiry_ts,
        settlement_source_name=SETTLEMENT_SOURCE_KEY,
        settlement_source_url=f"https://data.chain.link/streams/{asset.lower()}-usd",
        settlement_symbol=f"{asset}/USD",
        rule_text=f"{asset} {interval} hot decision replay contract: {slug}",
        rule_hash=f"hot-replay:{slug}",
        parser_version="rust-hot-decision-replay-v1",
    )


def _compare_text(
    mismatches: list[HotDecisionReplayMismatch],
    state_id: str,
    field: str,
    hot_value: str | None,
    replay_value: str | None,
) -> None:
    if hot_value != replay_value:
        mismatches.append(_mismatch(state_id, field, hot_value, replay_value))


def _compare_number(
    mismatches: list[HotDecisionReplayMismatch],
    state_id: str,
    field: str,
    hot_value: object,
    replay_value: int | None,
) -> None:
    if hot_value is None or replay_value is None:
        if hot_value != replay_value:
            mismatches.append(_mismatch(state_id, field, hot_value, replay_value))
        return
    if int(str(hot_value)) != replay_value:
        mismatches.append(_mismatch(state_id, field, hot_value, replay_value))


def _compare_decimal(
    mismatches: list[HotDecisionReplayMismatch],
    state_id: str,
    field: str,
    hot_value: object,
    replay_value: float | None,
    tolerance: Decimal,
) -> None:
    if hot_value is None or replay_value is None:
        if hot_value != replay_value:
            mismatches.append(_mismatch(state_id, field, hot_value, replay_value))
        return
    hot_decimal = Decimal(str(hot_value))
    replay_decimal = Decimal(str(replay_value))
    if abs(hot_decimal - replay_decimal) > tolerance:
        mismatches.append(_mismatch(state_id, field, str(hot_decimal), str(replay_decimal)))


def _compare_timestamp(
    mismatches: list[HotDecisionReplayMismatch],
    state_id: str,
    field: str,
    hot_value: object,
    replay_value: datetime | None,
) -> None:
    if hot_value is None or replay_value is None:
        if hot_value != replay_value:
            mismatches.append(_mismatch(state_id, field, hot_value, replay_value))
        return
    hot_ts = _parse_ts(hot_value)
    if hot_ts != replay_value:
        mismatches.append(_mismatch(state_id, field, hot_ts.isoformat(), replay_value.isoformat()))


def _compare_flags(
    mismatches: list[HotDecisionReplayMismatch],
    state_id: str,
    *,
    hot_flags: object,
    replay_flags: Iterable[str],
) -> None:
    expected = sorted(
        RUST_FLAG_MAP[flag]
        for flag in _string_list(hot_flags)
        if flag in RUST_FLAG_MAP
    )
    actual = sorted(flag for flag in replay_flags if flag not in PYTHON_ONLY_FLAGS)
    if expected != actual:
        mismatches.append(_mismatch(state_id, "data_quality_flags", expected, actual))


def _mismatch(
    state_id: str,
    field: str,
    hot_value: object,
    replay_value: object,
) -> HotDecisionReplayMismatch:
    return HotDecisionReplayMismatch(
        state_id=state_id,
        field=field,
        hot_value=None if hot_value is None else str(hot_value),
        replay_value=None if replay_value is None else str(replay_value),
    )


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _hot_side(row: dict[str, Any]) -> ContractSide:
    raw = str(row["side"]).strip().upper()
    if raw in {"UP", "UPPER"}:
        return "UP"
    if raw in {"DOWN", "LOWER"}:
        return "DOWN"
    raise ValueError(f"unsupported hot decision side: {row['side']}")


def _asset(value: object) -> Asset:
    normalized = str(value).strip().upper()
    if normalized not in {"BTC", "ETH", "SOL"}:
        raise ValueError(f"unsupported asset: {value}")
    return cast(Asset, normalized)


def _parse_ts(value: object) -> datetime:
    raw = str(value)
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    if "." in raw:
        head, tail = raw.split(".", 1)
        offset_start = max(tail.rfind("+"), tail.rfind("-"))
        if offset_start > 0:
            fraction = tail[:offset_start]
            offset = tail[offset_start:]
        else:
            fraction = tail
            offset = ""
        if len(fraction) > 6:
            raw = f"{head}.{fraction[:6]}{offset}"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
