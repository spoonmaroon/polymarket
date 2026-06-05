from __future__ import annotations

from pathlib import Path

from polymarket_engine.probability import runtime
from polymarket_engine.probability.runtime import ProbabilityRuntimeCache


def test_runtime_cache_timestamps_after_successful_payload_build(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monotonic_times = iter((100.0, 102.0, 102.1))
    build_calls: list[Path] = []

    def fake_monotonic() -> float:
        return next(monotonic_times)

    def fake_build_probability_payload(*, duckdb_path: Path, limit: int) -> dict[str, object]:
        build_calls.append(duckdb_path)
        return {
            "ok": True,
            "state": "OK",
            "generated_at": "2026-06-05T14:03:00+00:00",
            "cached": False,
            "model_version": None,
            "rows": [],
            "skipped": 0,
            "errors": [],
            "limit": limit,
        }

    monkeypatch.setattr(runtime.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(runtime, "build_probability_payload", fake_build_probability_payload)

    cache = ProbabilityRuntimeCache(min_interval_seconds=1.0)

    first = cache.payload(duckdb_path=tmp_path / "probability.duckdb", limit=4)
    second = cache.payload(duckdb_path=tmp_path / "probability.duckdb", limit=4)

    assert first["cached"] is False
    assert second["cached"] is True
    assert len(build_calls) == 1
