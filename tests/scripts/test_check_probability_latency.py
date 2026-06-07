from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_check_probability_latency_passes_for_fresh_payload(tmp_path: Path) -> None:
    path = tmp_path / "probabilities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-probability-runtime-v1",
                "latency": {"max_total_lag_ms": 850.0},
                "lanes": {"NOWCAST": 4, "MC": 4},
                "rows": [{"contract_id": "btc:UP"}],
            }
        )
    )

    result = subprocess.run(
        [
            "python3",
            "scripts/check_probability_latency.py",
            "--path",
            str(path),
            "--max-total-lag-ms",
            "1000",
            "--require-lane",
            "NOWCAST",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "probability_latency=ok" in result.stdout


def test_check_probability_latency_fails_when_lag_is_high(tmp_path: Path) -> None:
    path = tmp_path / "probabilities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-probability-runtime-v1",
                "latency": {"max_total_lag_ms": 1500.0},
                "lanes": {"NOWCAST": 4},
                "rows": [{"contract_id": "btc:UP"}],
            }
        )
    )

    result = subprocess.run(
        [
            "python3",
            "scripts/check_probability_latency.py",
            "--path",
            str(path),
            "--max-total-lag-ms",
            "1000",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "probability_lag_too_high" in result.stderr
