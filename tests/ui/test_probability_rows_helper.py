import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_probability_row_filter_helper_handles_partial_payloads(tmp_path: Path) -> None:
    bundled = tmp_path / "probability_rows_test.cjs"
    subprocess.run(
        [
            str(ROOT / "ui/node_modules/esbuild/bin/esbuild"),
            str(ROOT / "tests/ui/probability_rows_test.ts"),
            "--bundle",
            "--platform=node",
            "--format=cjs",
            f"--outfile={bundled}",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["node", str(bundled)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )


def test_probability_value_helper_handles_p_hat_and_path_metadata(tmp_path: Path) -> None:
    bundled = tmp_path / "probability_value_test.cjs"
    subprocess.run(
        [
            str(ROOT / "ui/node_modules/esbuild/bin/esbuild"),
            str(ROOT / "tests/ui/probability_value_test.ts"),
            "--bundle",
            "--platform=node",
            "--format=cjs",
            f"--outfile={bundled}",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["node", str(bundled)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )


def test_market_rows_helper_merges_active_windows(tmp_path: Path) -> None:
    bundled = tmp_path / "market_rows_test.cjs"
    subprocess.run(
        [
            str(ROOT / "ui/node_modules/esbuild/bin/esbuild"),
            str(ROOT / "tests/ui/market_rows_test.ts"),
            "--bundle",
            "--platform=node",
            "--format=cjs",
            f"--outfile={bundled}",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["node", str(bundled)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
