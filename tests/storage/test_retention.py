from polymarket_engine.storage.retention import RAW_HOT_RETENTION_DAYS, retention_manifest_class


def test_raw_hot_retention_is_90_days() -> None:
    assert RAW_HOT_RETENTION_DAYS == 90
    assert retention_manifest_class("raw") == "raw_hot_90d"
