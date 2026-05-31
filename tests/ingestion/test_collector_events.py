from datetime import datetime, timezone

from polymarket_engine.ingestion.collector_events import (
    CollectorEvent,
    SourceHealth,
    SourceLag,
    SourceQualityFlag,
)


def test_collector_event_calculates_lag_ms() -> None:
    event = CollectorEvent(
        source_key="coinbase_advanced_ws",
        stream_key="ticker",
        symbol="BTC-USD",
        event_ts=datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc),
        observed_ts=datetime(2026, 5, 31, 21, 0, 1, 250000, tzinfo=timezone.utc),
        payload={"price": "104000.0"},
    )

    assert event.lag_ms == 1250
    assert event.to_raw_event().source_key == "coinbase_advanced_ws"


def test_source_lag_flags_stale_and_negative_clock_values() -> None:
    fresh = SourceLag(source_key="coinbase_advanced_ws", lag_ms=900, stale_after_ms=2000)
    stale = SourceLag(source_key="polymarket_rtds_chainlink", lag_ms=6000, stale_after_ms=5000)
    bad_clock = SourceLag(source_key="coinbase_advanced_ws", lag_ms=-50, stale_after_ms=2000)

    assert fresh.quality_flags() == ()
    assert stale.quality_flags() == (SourceQualityFlag.STALE_SOURCE,)
    assert bad_clock.quality_flags() == (SourceQualityFlag.CLOCK_SKEW,)


def test_source_health_is_unhealthy_when_recent_error_exists() -> None:
    health = SourceHealth(
        source_key="binance_spot_ws",
        connected=False,
        last_event_ts=None,
        last_observed_ts=None,
        last_error="HTTP 451",
        quality_flags=(SourceQualityFlag.SOURCE_BLOCKED,),
    )

    assert health.is_healthy is False
