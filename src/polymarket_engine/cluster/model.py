from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


NodeRole = Literal["cpu_authority", "gpu_api", "standby"]


@dataclass(frozen=True)
class ClusterNode:
    host: str
    role: NodeRole


@dataclass(frozen=True)
class ClusterArtifact:
    owner: str
    canonical_path: str
    mirrors: dict[str, str]


@dataclass(frozen=True)
class ClusterMirror:
    source_node: str
    target_node: str
    max_age_seconds: float


@dataclass(frozen=True)
class ClusterManifest:
    nodes: dict[str, ClusterNode]
    artifacts: dict[str, ClusterArtifact]
    mirror: ClusterMirror
