from pathlib import Path


def test_deploy_script_has_configurable_long_smoke_window() -> None:
    script = Path(__file__).parents[2] / "scripts" / "deploy.sh"
    text = script.read_text(encoding="utf-8")

    assert 'DEPLOY_SMOKE_ATTEMPTS="${DEPLOY_SMOKE_ATTEMPTS:-90}"' in text
    assert 'seq 1 "$DEPLOY_SMOKE_ATTEMPTS"' in text
