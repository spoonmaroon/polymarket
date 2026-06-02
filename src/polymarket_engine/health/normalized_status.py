from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polymarket_engine.storage.atomic import durable_replace
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


NORMALIZED_HEALTH_SCHEMA_VERSION = "polymarket-normalized-health-v1"


def build_normalized_health_status(
    *,
    store: DuckDbIngestStore,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if generated_at is None:
        generated_at = datetime.now(timezone.utc)
    return {
        "schema_version": NORMALIZED_HEALTH_SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "duckdb_path": str(store.db_path),
        "tables": list(store.normalized_table_health()),
    }


def write_normalized_health_status(
    *,
    store: DuckDbIngestStore,
    out_path: Path,
) -> dict[str, Any]:
    status = build_normalized_health_status(store=store)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(f"{out_path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(status, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    durable_replace(tmp_path, out_path)
    return status
