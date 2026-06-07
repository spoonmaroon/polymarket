from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from polymarket_engine.cluster.model import ClusterArtifact
from polymarket_engine.cluster.model import ClusterManifest
from polymarket_engine.cluster.model import ClusterMirror
from polymarket_engine.cluster.model import ClusterNode
from polymarket_engine.cluster.model import NodeRole


MANIFEST_SCHEMA_VERSION = "polymarket-cluster-manifest-v1"


def load_cluster_manifest(path: Path) -> ClusterManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("cluster manifest root must be an object")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("cluster manifest schema_version invalid")

    nodes = {
        name: ClusterNode(host=_str(row.get("host"), f"nodes.{name}.host"), role=_role(row.get("role")))
        for name, row in _object_rows(payload.get("nodes"), "nodes").items()
    }
    artifacts = {
        name: ClusterArtifact(
            owner=_str(row.get("owner"), f"artifacts.{name}.owner"),
            canonical_path=_str(row.get("canonical_path"), f"artifacts.{name}.canonical_path"),
            mirrors=_string_map(row.get("mirrors"), f"artifacts.{name}.mirrors"),
        )
        for name, row in _object_rows(payload.get("artifacts"), "artifacts").items()
    }
    mirror_payload = _dict(payload.get("mirror"), "mirror")
    mirror = ClusterMirror(
        source_node=_str(mirror_payload.get("source_node"), "mirror.source_node"),
        target_node=_str(mirror_payload.get("target_node"), "mirror.target_node"),
        max_age_seconds=_positive_float(mirror_payload.get("max_age_seconds"), "mirror.max_age_seconds"),
    )

    _validate_manifest(nodes=nodes, artifacts=artifacts, mirror=mirror)
    return ClusterManifest(nodes=nodes, artifacts=artifacts, mirror=mirror)


def _validate_manifest(
    *,
    nodes: dict[str, ClusterNode],
    artifacts: dict[str, ClusterArtifact],
    mirror: ClusterMirror,
) -> None:
    for node_name in (mirror.source_node, mirror.target_node):
        if node_name not in nodes:
            raise ValueError(f"mirror node {node_name} is not defined")
    canonical_paths = [artifact.canonical_path for artifact in artifacts.values()]
    if len(canonical_paths) != len(set(canonical_paths)):
        raise ValueError("duplicate canonical_path")
    for artifact_name, artifact in artifacts.items():
        if artifact.owner not in nodes:
            raise ValueError(f"artifact {artifact_name} owner is not defined")
        for mirror_node in artifact.mirrors:
            if mirror_node not in nodes:
                raise ValueError(f"artifact {artifact_name} mirror node is not defined")


def _object_rows(value: Any, label: str) -> dict[str, dict[str, Any]]:
    raw = _dict(value, label)
    rows: dict[str, dict[str, Any]] = {}
    for key, row in raw.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{label} key must be a non-empty string")
        rows[key] = _dict(row, f"{label}.{key}")
    return rows


def _dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _string_map(value: Any, label: str) -> dict[str, str]:
    raw = _dict(value, label)
    return {key: _str(nested, f"{label}.{key}") for key, nested in raw.items()}


def _str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _role(value: Any) -> NodeRole:
    role = _str(value, "role")
    if role not in {"cpu_authority", "gpu_api", "standby"}:
        raise ValueError("role invalid")
    return cast(NodeRole, role)


def _positive_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{label} must be positive")
    return float(value)
