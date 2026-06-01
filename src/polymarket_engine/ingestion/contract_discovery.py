from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

SUPPORTED_INTERVAL_MINUTES = {"5m": 5, "15m": 15}
GAMMA_REQUEST_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "polymarket-engine/0.1",
}
LAST_MARKET_DISCOVERY_ERRORS: dict[str, str] = {}


@dataclass(frozen=True)
class MarketToken:
    slug: str
    outcome: str
    token_id: str


def floor_to_5m_epoch(now: datetime) -> int:
    return floor_to_interval_epoch(now, interval_minutes=5)


def floor_to_interval_epoch(now: datetime, *, interval_minutes: int) -> int:
    floored = now.replace(second=0, microsecond=0)
    floored = floored.replace(minute=(floored.minute // interval_minutes) * interval_minutes)
    return int(floored.timestamp())


def crypto_5m_slugs(
    now: datetime,
    assets: tuple[str, ...] = ("BTC", "ETH"),
    windows_ahead: int = 3,
) -> tuple[str, ...]:
    return crypto_updown_slugs(
        now,
        assets=assets,
        intervals=("5m",),
        windows_ahead=windows_ahead,
    )


def crypto_updown_slugs(
    now: datetime,
    assets: tuple[str, ...] = ("BTC", "ETH"),
    intervals: tuple[str, ...] = ("5m", "15m"),
    windows_ahead: int = 3,
) -> tuple[str, ...]:
    slugs: list[str] = []
    for interval in intervals:
        interval_minutes = SUPPORTED_INTERVAL_MINUTES.get(interval)
        if interval_minutes is None:
            raise ValueError(f"unsupported interval: {interval}")
        start = datetime.fromtimestamp(
            floor_to_interval_epoch(now, interval_minutes=interval_minutes),
            tz=now.tzinfo,
        )
        for window_index in range(windows_ahead):
            epoch = int((start + timedelta(minutes=interval_minutes * window_index)).timestamp())
            for asset in assets:
                slugs.append(f"{asset.lower()}-updown-{interval}-{epoch}")
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
    return await fetch_crypto_updown_markets(
        client=client,
        base_url=base_url,
        now=now,
        assets=assets,
        intervals=("5m",),
        windows_ahead=windows_ahead,
    )


async def fetch_crypto_updown_markets(
    client: httpx.AsyncClient,
    base_url: str,
    now: datetime,
    assets: tuple[str, ...] = ("BTC", "ETH"),
    intervals: tuple[str, ...] = ("5m", "15m"),
    windows_ahead: int = 3,
) -> tuple[dict[str, Any], ...]:
    slugs = crypto_updown_slugs(
        now,
        assets=assets,
        intervals=intervals,
        windows_ahead=windows_ahead,
    )
    results = await asyncio.gather(
        *(
            _fetch_market_slug(client=client, base_url=base_url, slug=slug)
            for slug in slugs
        ),
        return_exceptions=True,
    )
    markets: list[dict[str, Any]] = []
    errors: list[tuple[str, BaseException]] = []
    partial_errors: dict[str, str] = {}
    for slug, result in zip(slugs, results, strict=True):
        if isinstance(result, BaseException):
            errors.append((slug, result))
            partial_errors[slug] = f"{type(result).__name__}: {result}"
            continue
        markets.extend(result)
    LAST_MARKET_DISCOVERY_ERRORS.clear()
    LAST_MARKET_DISCOVERY_ERRORS.update(partial_errors)
    if not markets and errors:
        raise errors[0][1]
    return tuple(markets)


async def _fetch_market_slug(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    slug: str,
) -> tuple[dict[str, Any], ...]:
    response = await client.get(
        f"{base_url.rstrip('/')}/markets",
        params={"slug": slug},
        headers=GAMMA_REQUEST_HEADERS,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload if isinstance(payload, list) else payload.get("markets", [])
    return tuple(dict(item) for item in items)
