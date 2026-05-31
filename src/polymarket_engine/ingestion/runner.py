from __future__ import annotations

from dataclasses import dataclass

from polymarket_engine.domain.sources import DataSource, part_one_sources


@dataclass(frozen=True)
class IngestionPlan:
    sources: tuple[DataSource, ...]
    paper_only: bool


def build_part_one_ingestion_plan() -> IngestionPlan:
    return IngestionPlan(sources=part_one_sources(), paper_only=True)
