from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from polymarket_engine.domain.contracts import Asset, ContractSide, ContractSpec
from polymarket_engine.features.state_builder import DecisionStateUnavailable
from polymarket_engine.features.state_replay import build_decision_state_from_store
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


SETTLEMENT_SOURCE_KEY = "polymarket_rtds_chainlink"
LIVE_HEALTH_FRESHNESS_MS = 30_000


@dataclass(frozen=True)
class UnavailableDecisionState:
    contract_id: str
    token_id: str
    reason: str


@dataclass(frozen=True)
class CurrentDecisionStateSnapshotResult:
    asof_ts: datetime
    contracts_upserted: int
    states_written: int
    unavailable: tuple[UnavailableDecisionState, ...]


def build_current_decision_state_snapshots(
    *,
    status_path: Path,
    store: DuckDbIngestStore,
    include_next: bool = False,
) -> CurrentDecisionStateSnapshotResult:
    payload = _read_status(status_path)
    asof_ts = _parse_ts(payload["generated_at"])
    token_metadata = _token_metadata(payload.get("orderbooks", []))
    contracts = _contracts_from_status(
        payload,
        token_metadata=token_metadata,
        include_next=include_next,
    )
    states_written = 0
    unavailable: list[UnavailableDecisionState] = []
    for contract in contracts:
        store.upsert_contract_spec(contract)
        try:
            state = build_decision_state_from_store(
                store=store,
                contract=contract,
                asof_ts=asof_ts,
                resolved_threshold_price=None,
                settlement_source_key=SETTLEMENT_SOURCE_KEY,
                proxy_source_keys=(),
                volatility=None,
                volatility_source_key=SETTLEMENT_SOURCE_KEY,
                volatility_lookback_limit=180,
                stale_source_after_ms=LIVE_HEALTH_FRESHNESS_MS,
                stale_book_after_ms=LIVE_HEALTH_FRESHNESS_MS,
            )
        except DecisionStateUnavailable as exc:
            unavailable.append(
                UnavailableDecisionState(
                    contract_id=contract.contract_id,
                    token_id=contract.token_id,
                    reason=str(exc),
                )
            )
            continue
        store.upsert_asof_state_input(state)
        states_written += 1
    return CurrentDecisionStateSnapshotResult(
        asof_ts=asof_ts,
        contracts_upserted=len(contracts),
        states_written=states_written,
        unavailable=tuple(unavailable),
    )


def _read_status(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("status payload must be an object")
    if payload.get("schema_version") != "rust-live-probe-state-manager-v1":
        raise ValueError("status payload is not a Rust state-manager report")
    return payload


def _contracts_from_status(
    payload: dict[str, Any],
    *,
    token_metadata: dict[str, dict[str, str]],
    include_next: bool,
) -> tuple[ContractSpec, ...]:
    groups = ["current"]
    if include_next:
        groups.append("next")
    contracts: list[ContractSpec] = []
    for group in groups:
        rows = payload.get(group, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            window = _mapping(row.get("window"), f"{group}.window")
            asset = _asset(window["asset"])
            interval = str(window["interval"])
            start_ts = _parse_ts(window["start_ts"])
            expiry_ts = _parse_ts(window["end_ts"])
            for side_key, side in (("up", "UP"), ("down", "DOWN")):
                token = _mapping(row.get(side_key), f"{group}.{side_key}")
                token_id = str(token["token_id"])
                metadata = token_metadata.get(token_id, {})
                slug = metadata.get("market_slug") or _slug(asset, interval, start_ts)
                condition_id = metadata.get("contract_id") or f"unknown:{slug}"
                contract_id = f"{slug}:{side}"
                contracts.append(
                    ContractSpec(
                        contract_id=contract_id,
                        venue="polymarket",
                        market_id=slug,
                        condition_id=condition_id,
                        slug=slug,
                        asset=asset,
                        side=cast(ContractSide, side),
                        token_id=token_id,
                        threshold_type="start_price",
                        threshold_price=None,
                        comparison_operator=">=" if side == "UP" else "<",
                        start_ts=start_ts,
                        expiry_ts=expiry_ts,
                        settlement_source_name=SETTLEMENT_SOURCE_KEY,
                        settlement_source_url=_settlement_source_url(asset),
                        settlement_symbol=f"{asset}/USD",
                        rule_text=_rule_text(asset=asset, interval=interval, slug=slug),
                        rule_hash=_rule_hash(asset=asset, interval=interval, slug=slug),
                        parser_version="rust-state-manager-v1",
                    )
                )
    return tuple(contracts)


def _token_metadata(rows: object) -> dict[str, dict[str, str]]:
    if not isinstance(rows, list):
        return {}
    metadata: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        token_id = row.get("token_id")
        if not isinstance(token_id, str) or not token_id:
            continue
        values: dict[str, str] = {}
        for key in ("market_slug", "contract_id"):
            value = row.get(key)
            if isinstance(value, str) and value:
                values[key] = value
        metadata[token_id] = values
    return metadata


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _asset(value: object) -> Asset:
    normalized = str(value).upper()
    if normalized not in {"BTC", "ETH", "SOL"}:
        raise ValueError(f"unsupported asset: {value}")
    return cast(Asset, normalized)


def _parse_ts(value: object) -> datetime:
    raw = str(value)
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _slug(asset: Asset, interval: str, start_ts: datetime) -> str:
    return f"{asset.lower()}-updown-{interval}-{int(start_ts.timestamp())}"


def _settlement_source_url(asset: Asset) -> str:
    return f"https://data.chain.link/streams/{asset.lower()}-usd"


def _rule_text(*, asset: Asset, interval: str, slug: str) -> str:
    return (
        f"{asset} {interval} Up/Down start-price contract discovered by Rust "
        f"state-manager status: {slug}"
    )


def _rule_hash(*, asset: Asset, interval: str, slug: str) -> str:
    return hashlib.sha256(_rule_text(asset=asset, interval=interval, slug=slug).encode()).hexdigest()
