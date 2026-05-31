from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from polymarket_engine.domain.contract_rules import NormalizedContractRule


class DuckDbIngestStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def apply_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema.sql")
        with duckdb.connect(str(self.db_path)) as conn:
            conn.sql(schema_path.read_text())

    def register_ingest_file(
        self,
        file_id: str,
        source_key: str,
        stream_key: str,
        partition_date: str,
        partition_hour: int,
        path: str,
        sha256: str,
        row_count: int,
        first_event_ts: datetime,
        last_event_ts: datetime,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into ops.ingest_files
                (file_id, source_key, stream_key, partition_date, partition_hour, path, sha256,
                 row_count, first_event_ts, last_event_ts, written_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    file_id,
                    source_key,
                    stream_key,
                    partition_date,
                    partition_hour,
                    path,
                    sha256,
                    row_count,
                    first_event_ts,
                    last_event_ts,
                    datetime.now(timezone.utc),
                ],
            )

    def upsert_contract_rule(self, rule: NormalizedContractRule) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into core.contract_rules
                (market_id, condition_id, slug, asset, contract_type, start_ts, end_ts,
                 expiry_ts, threshold_type, threshold_price, comparison_operator_up,
                 comparison_operator_down, settlement_source_name, settlement_source_url,
                 settlement_symbol, outcome_token_ids_json, rule_text, rule_hash,
                 parser_version, accepted, reject_reason, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    rule.market_id,
                    rule.condition_id,
                    rule.slug,
                    rule.asset,
                    rule.contract_type,
                    rule.start_ts,
                    rule.end_ts,
                    rule.expiry_ts,
                    rule.threshold_type,
                    rule.threshold_price,
                    rule.comparison_operator_up,
                    rule.comparison_operator_down,
                    rule.settlement_source_name,
                    rule.settlement_source_url,
                    rule.settlement_symbol,
                    json.dumps(rule.outcome_token_ids, sort_keys=True),
                    rule.rule_text,
                    rule.rule_hash,
                    rule.parser_version,
                    rule.accepted,
                    rule.reject_reason,
                    datetime.now(timezone.utc),
                ],
            )
