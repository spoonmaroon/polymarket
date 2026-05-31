from pathlib import Path

from polymarket_engine.storage.atomic import durable_replace


def test_durable_replace_consumes_tmp_and_publishes_final(tmp_path: Path) -> None:
    tmp = tmp_path / "sample.parquet.tmp"
    final = tmp_path / "sample.parquet"
    tmp.write_bytes(b"payload")

    durable_replace(tmp, final)

    assert final.read_bytes() == b"payload"
    assert not tmp.exists()
