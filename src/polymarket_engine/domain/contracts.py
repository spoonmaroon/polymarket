from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, cast

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
        if self.side == "UP" and self.comparison_operator not in {">", ">="}:
            raise ValueError("UP side requires greater-than comparison operator")
        if self.side == "DOWN" and self.comparison_operator not in {"<", "<="}:
            raise ValueError("DOWN side requires less-than comparison operator")


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
    return cast(Asset, value)


def _threshold_type(value: str) -> ThresholdType:
    if value not in {"start_price", "fixed_price"}:
        raise ValueError(f"unsupported threshold_type: {value}")
    return cast(ThresholdType, value)


def _comparison(value: str) -> ComparisonOperator:
    if value not in {">", ">=", "<", "<="}:
        raise ValueError(f"unsupported comparison_operator: {value}")
    return cast(ComparisonOperator, value)


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")
