from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from polymarket_engine.probability.generator_fragments import (
    GENERATOR_FRAGMENTS_SCHEMA_VERSION,
    GeneratorFragment,
    read_probability_fragments,
    select_fragments_for_input,
    write_probability_fragments,
)
from polymarket_engine.probability.schema import ProbabilityInput


def _input() -> ProbabilityInput:
    return ProbabilityInput(
        state_id="state-btc-up",
        asof_ts=datetime(2026, 6, 7, 12, 3, tzinfo=UTC),
        asset="BTC",
        side="UP",
        comparison_operator=">=",
        seconds_left=120.0,
        settlement_price=100.0,
        threshold=101.0,
        sigma_tau=0.01,
        executable_price=0.52,
        source_age_ms=100,
        book_age_ms=100,
        z_path=-0.4,
    )


def test_probability_fragments_snapshot_round_trips_and_filters_asof(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "probability_fragments.json"
    prior = GeneratorFragment(
        fragment_id="btc-prior",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        prices=(100.0, 100.5, 101.0),
        horizon_seconds=120,
        z_path_bucket="near",
        quality_bucket="OK",
    )
    future = GeneratorFragment(
        fragment_id="btc-future",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 4, tzinfo=UTC),
        prices=(100.0, 99.5, 99.0),
        horizon_seconds=120,
        z_path_bucket="near",
        quality_bucket="OK",
    )

    write_probability_fragments(
        out_path=out_path,
        fragments=(prior, future),
        generated_at=datetime(2026, 6, 7, 12, 3, tzinfo=UTC),
    )

    raw_text = out_path.read_text(encoding="utf-8")
    payload = read_probability_fragments(
        out_path=out_path,
        max_age_seconds=60 * 60 * 24 * 365,
    )
    selected = select_fragments_for_input(
        payload.fragments,
        probability_input=_input(),
        min_fragment_count=1,
        max_fragment_count=10,
    )

    assert raw_text.endswith("\n")
    assert payload.schema_version == GENERATOR_FRAGMENTS_SCHEMA_VERSION
    assert [fragment.fragment_id for fragment in selected.fragments] == ["btc-prior"]
    assert selected.sparse is False
    assert selected.reason == "exact"


def test_probability_fragments_retention_keeps_bounded_asof_safe_history(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "probability_fragments.json"
    retained = GeneratorFragment(
        fragment_id="btc-retained",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 1, tzinfo=UTC),
        prices=(100.0, 100.5, 101.0),
        horizon_seconds=180,
        z_path_bucket="near",
        quality_bucket="OK",
    )
    stale = GeneratorFragment(
        fragment_id="btc-stale",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 11, 55, tzinfo=UTC),
        prices=(100.0, 99.5, 99.0),
        horizon_seconds=180,
        z_path_bucket="near",
        quality_bucket="OK",
    )
    current = GeneratorFragment(
        fragment_id="btc-current",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 3, tzinfo=UTC),
        prices=(100.0, 101.5, 102.0),
        horizon_seconds=180,
        z_path_bucket="near",
        quality_bucket="OK",
    )

    write_probability_fragments(
        out_path=out_path,
        fragments=(retained, stale),
        generated_at=datetime(2026, 6, 7, 12, 1, tzinfo=UTC),
    )
    write_probability_fragments(
        out_path=out_path,
        fragments=(current,),
        generated_at=datetime(2026, 6, 7, 12, 3, tzinfo=UTC),
        retain_existing=True,
        max_retained_fragments=4,
        max_retained_age_seconds=180,
    )

    payload = read_probability_fragments(
        out_path=out_path,
        max_age_seconds=60 * 60 * 24 * 365,
    )

    assert [fragment.fragment_id for fragment in payload.fragments] == [
        "btc-current",
        "btc-retained",
    ]


def test_select_fragments_marks_sparse_when_bucket_is_thin() -> None:
    selected = select_fragments_for_input(
        (),
        probability_input=_input(),
        min_fragment_count=2,
        max_fragment_count=10,
    )

    assert selected.fragments == ()
    assert selected.sparse is True
    assert selected.reason == "missing"


def test_select_fragments_excludes_future_exact_bucket_match() -> None:
    future_exact = GeneratorFragment(
        fragment_id="btc-future-exact",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 4, tzinfo=UTC),
        prices=(100.0, 100.5, 101.0),
        horizon_seconds=120,
        z_path_bucket="near",
        quality_bucket="OK",
    )
    prior_coarse = GeneratorFragment(
        fragment_id="btc-prior-coarse",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        prices=(100.0, 98.0, 97.0),
        horizon_seconds=120,
        z_path_bucket="deep_down",
        quality_bucket="OK",
    )

    selected = select_fragments_for_input(
        (future_exact, prior_coarse),
        probability_input=_input(),
        min_fragment_count=1,
        max_fragment_count=10,
    )

    assert [fragment.fragment_id for fragment in selected.fragments] == [
        "btc-prior-coarse"
    ]
    assert selected.sparse is False
    assert selected.reason == "coarse"


def test_select_fragments_keeps_thin_exact_bucket_sparse() -> None:
    exact = GeneratorFragment(
        fragment_id="btc-prior-exact",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        prices=(100.0, 100.5, 101.0),
        horizon_seconds=120,
        z_path_bucket="near",
        quality_bucket="OK",
    )
    other_bucket = GeneratorFragment(
        fragment_id="btc-prior-other-bucket",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 11, 59, tzinfo=UTC),
        prices=(100.0, 98.0, 97.0),
        horizon_seconds=120,
        z_path_bucket="deep_down",
        quality_bucket="OK",
    )

    selected = select_fragments_for_input(
        (exact, other_bucket),
        probability_input=_input(),
        min_fragment_count=2,
        max_fragment_count=10,
    )

    assert [fragment.fragment_id for fragment in selected.fragments] == ["btc-prior-exact"]
    assert selected.sparse is True
    assert selected.reason == "exact"


def test_select_fragments_deduplicates_same_price_path_for_sparse_count() -> None:
    first = GeneratorFragment(
        fragment_id="btc-prior-up",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        prices=(100.0, 100.5, 101.0),
        horizon_seconds=120,
        z_path_bucket="near",
        quality_bucket="OK",
    )
    duplicate = GeneratorFragment(
        fragment_id="btc-prior-down",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        prices=(100.0, 100.5, 101.0),
        horizon_seconds=120,
        z_path_bucket="near",
        quality_bucket="OK",
    )

    selected = select_fragments_for_input(
        (first, duplicate),
        probability_input=_input(),
        min_fragment_count=2,
        max_fragment_count=10,
    )

    assert [fragment.fragment_id for fragment in selected.fragments] == ["btc-prior-up"]
    assert selected.sparse is True
    assert selected.reason == "exact"


def test_select_fragments_excludes_quality_blocked_rows() -> None:
    blocked = GeneratorFragment(
        fragment_id="btc-blocked",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        prices=(100.0, 100.5, 101.0),
        horizon_seconds=120,
        z_path_bucket="near",
        quality_bucket="BLOCKED",
    )

    selected = select_fragments_for_input(
        (blocked,),
        probability_input=_input(),
        min_fragment_count=1,
        max_fragment_count=10,
    )

    assert selected.fragments == ()
    assert selected.sparse is True
    assert selected.reason == "missing"
