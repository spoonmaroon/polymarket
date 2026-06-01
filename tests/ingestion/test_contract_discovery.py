import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from polymarket_engine.ingestion.contract_discovery import (
    MarketToken,
    crypto_5m_slugs,
    crypto_updown_slugs,
    extract_market_tokens,
    fetch_crypto_5m_markets,
    fetch_crypto_updown_markets,
    floor_to_5m_epoch,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_floor_to_5m_epoch() -> None:
    now = datetime(2026, 5, 31, 21, 4, 59, tzinfo=timezone.utc)

    assert floor_to_5m_epoch(now) == 1780261200


def test_crypto_5m_slugs_use_polymarket_epoch_pattern() -> None:
    now = datetime(2026, 5, 31, 21, 4, 0, tzinfo=timezone.utc)

    assert crypto_5m_slugs(now, assets=("BTC", "ETH"), windows_ahead=2) == (
        "btc-updown-5m-1780261200",
        "eth-updown-5m-1780261200",
        "btc-updown-5m-1780261500",
        "eth-updown-5m-1780261500",
    )


def test_crypto_updown_slugs_include_5m_and_15m_scopes() -> None:
    now = datetime(2026, 5, 31, 21, 17, 0, tzinfo=timezone.utc)

    assert crypto_updown_slugs(
        now,
        assets=("BTC", "ETH"),
        intervals=("5m", "15m"),
        windows_ahead=2,
    ) == (
        "btc-updown-5m-1780262100",
        "eth-updown-5m-1780262100",
        "btc-updown-5m-1780262400",
        "eth-updown-5m-1780262400",
        "btc-updown-15m-1780262100",
        "eth-updown-15m-1780262100",
        "btc-updown-15m-1780263000",
        "eth-updown-15m-1780263000",
    )


def test_extract_market_tokens_from_gamma_payload() -> None:
    market: dict[str, Any] = {
        "slug": "btc-updown-5m-1780261200",
        "question": "Bitcoin Up or Down - May 31, 5:00PM-5:05PM ET",
        "outcomes": json.dumps(["Up", "Down"]),
        "clobTokenIds": json.dumps(["111", "222"]),
    }

    assert extract_market_tokens(market) == (
        MarketToken(slug="btc-updown-5m-1780261200", outcome="Up", token_id="111"),
        MarketToken(slug="btc-updown-5m-1780261200", outcome="Down", token_id="222"),
    )


@pytest.mark.anyio
async def test_fetch_crypto_5m_markets_fetches_by_slug() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            json=[
                {
                    "slug": request.url.params["slug"],
                    "question": "Bitcoin Up or Down - May 31, 5:00PM-5:05PM ET",
                    "outcomes": json.dumps(["Up", "Down"]),
                    "clobTokenIds": json.dumps(["111", "222"]),
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    markets = await fetch_crypto_5m_markets(
        client=client,
        base_url="https://gamma-api.polymarket.com",
        now=datetime(2026, 5, 31, 21, 4, 0, tzinfo=timezone.utc),
        assets=("BTC",),
        windows_ahead=1,
    )
    await client.aclose()

    assert len(markets) == 1
    assert markets[0]["slug"] == "btc-updown-5m-1780261200"
    assert requested_urls == [
        "https://gamma-api.polymarket.com/markets?slug=btc-updown-5m-1780261200"
    ]


@pytest.mark.anyio
async def test_fetch_crypto_updown_markets_sends_explicit_user_agent() -> None:
    user_agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        user_agents.append(request.headers.get("user-agent", ""))
        return httpx.Response(
            200,
            json=[
                {
                    "slug": request.url.params["slug"],
                    "question": "Bitcoin Up or Down - May 31, 5:00PM-5:05PM ET",
                    "outcomes": json.dumps(["Up", "Down"]),
                    "clobTokenIds": json.dumps(["111", "222"]),
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await fetch_crypto_updown_markets(
        client=client,
        base_url="https://gamma-api.polymarket.com",
        now=datetime(2026, 5, 31, 21, 4, 0, tzinfo=timezone.utc),
        assets=("BTC",),
        intervals=("5m",),
        windows_ahead=1,
    )
    await client.aclose()

    assert user_agents == ["polymarket-engine/0.1"]


@pytest.mark.anyio
async def test_fetch_crypto_updown_markets_fetches_15m_slugs() -> None:
    requested_slugs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_slugs.append(str(request.url.params["slug"]))
        return httpx.Response(
            200,
            json=[
                {
                    "slug": request.url.params["slug"],
                    "question": "Bitcoin Up or Down - May 31, 5:15PM-5:30PM ET",
                    "outcomes": json.dumps(["Up", "Down"]),
                    "clobTokenIds": json.dumps(["111", "222"]),
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    markets = await fetch_crypto_updown_markets(
        client=client,
        base_url="https://gamma-api.polymarket.com",
        now=datetime(2026, 5, 31, 21, 17, 0, tzinfo=timezone.utc),
        assets=("BTC",),
        intervals=("15m",),
        windows_ahead=1,
    )
    await client.aclose()

    assert len(markets) == 1
    assert requested_slugs == ["btc-updown-15m-1780262100"]
