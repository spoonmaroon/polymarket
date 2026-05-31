from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PolymarketContract:
    contract_id: str
    asset: str
    side: str
    threshold: float
    expiry_ts: datetime
    settlement_source: str
    token_id: str
    rule_text: str
    rule_hash: str


def rule_hash(rule_text: str) -> str:
    return hashlib.sha256(rule_text.strip().encode("utf-8")).hexdigest()


def normalize_contract(raw: dict[str, object]) -> PolymarketContract:
    text = str(raw["rule_text"])
    return PolymarketContract(
        contract_id=str(raw["contract_id"]),
        asset=str(raw["asset"]).upper(),
        side=str(raw["side"]).upper(),
        threshold=float(str(raw["threshold"])),
        expiry_ts=datetime.fromisoformat(str(raw["expiry_ts"]).replace("Z", "+00:00")),
        settlement_source=str(raw["settlement_source"]),
        token_id=str(raw["token_id"]),
        rule_text=text,
        rule_hash=rule_hash(text),
    )
