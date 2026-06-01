from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
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
    slug_match = re.fullmatch(r"(btc|eth|sol)-updown-(5m|15m)-\d+", slug)
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
    normalized_text = " ".join(description.lower().split())
    for asset_word, asset_details in ASSET_BY_WORD.items():
        if asset_word in normalized_text:
            return asset_details
    raise ContractRuleRejected("unsupported asset")


def _decode_json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        decoded = json.loads(value)
    else:
        decoded = value
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise ContractRuleRejected("expected JSON string list")
    return decoded


def _parse_datetime(value: Any, missing_message: str) -> datetime:
    if value is None:
        raise ContractRuleRejected(missing_message)
    raw_value = str(value)
    parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ContractRuleRejected("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)
