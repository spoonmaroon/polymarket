from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx


@dataclass(frozen=True)
class MarketToken:
    slug: str
    outcome: str
    token_id: str


def floor_to_5m_epoch(now: datetime) -> int:
    floored = now.replace(second=0, microsecond=0)
    floored = floored.replace(minute=(floored.minute // 5) * 5)
    return int(floored.timestamp())


def crypto_5m_slugs(
    now: datetime,
    assets: tuple[str, ...] = ("BTC", "ETH"),
    windows_ahead: int = 3,
) -> tuple[str, ...]:
    start = datetime.fromtimestamp(floor_to_5m_epoch(now), tz=now.tzinfo)
    slugs: list[str] = []
    for window_index in range(windows_ahead):
        epoch = int((start + timedelta(minutes=5 * window_index)).timestamp())
        for asset in assets:
            slugs.append(f"{asset.lower()}-updown-5m-{epoch}")
    return tuple(slugs)


def _decode_json_list(value: object) -> list[str]:
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError("expected JSON list")
    return [str(item) for item in parsed]


def extract_market_tokens(market: dict[str, Any]) -> tuple[MarketToken, ...]:
    slug = str(market["slug"])
    outcomes = _decode_json_list(market["outcomes"])
    token_ids = _decode_json_list(market["clobTokenIds"])
    if len(outcomes) != len(token_ids):
        raise ValueError("outcomes and clobTokenIds length mismatch")
    return tuple(
        MarketToken(slug=slug, outcome=outcome, token_id=token_id)
        for outcome, token_id in zip(outcomes, token_ids, strict=True)
    )


async def fetch_crypto_5m_markets(
    client: httpx.AsyncClient,
    base_url: str,
    now: datetime,
    assets: tuple[str, ...] = ("BTC", "ETH"),
    windows_ahead: int = 3,
) -> tuple[dict[str, Any], ...]:
    markets: list[dict[str, Any]] = []
    for slug in crypto_5m_slugs(now, assets=assets, windows_ahead=windows_ahead):
        response = await client.get(f"{base_url.rstrip('/')}/markets", params={"slug": slug})
        response.raise_for_status()
        payload = response.json()
        items = payload if isinstance(payload, list) else payload.get("markets", [])
        markets.extend(dict(item) for item in items)
    return tuple(markets)
