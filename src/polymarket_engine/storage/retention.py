from __future__ import annotations

RAW_HOT_RETENTION_DAYS = 90
RAW_HOT_RETENTION_CLASS = "raw_hot_90d"
COMPACT_RESEARCH_RETENTION_CLASS = "compact_research_forever"


def retention_manifest_class(kind: str) -> str:
    if kind == "raw":
        return RAW_HOT_RETENTION_CLASS
    if kind == "compact":
        return COMPACT_RESEARCH_RETENTION_CLASS
    raise ValueError(f"unsupported retention kind: {kind}")
