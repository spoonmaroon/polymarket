import json
from pathlib import Path

import pytest

from polymarket_engine.cluster.manifest import load_cluster_manifest


def test_manifest_assigns_spoon_cpu_artifacts_and_thepc_probability_outputs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cluster.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-cluster-manifest-v1",
                "nodes": {
                    "spoon": {"host": "spoon", "role": "cpu_authority"},
                    "thepc": {"host": "thepc-lan", "role": "gpu_api"},
                },
                "artifacts": {
                    "probability_inputs.json": {
                        "owner": "spoon",
                        "canonical_path": "/home/spoon/polymarket-data/live/probability_inputs.json",
                        "mirrors": {
                            "thepc": "/home/ender/polymarket-data/live/probability_inputs.json"
                        },
                    },
                    "probability_fragments.json": {
                        "owner": "spoon",
                        "canonical_path": "/home/spoon/polymarket-data/live/probability_fragments.json",
                        "mirrors": {
                            "thepc": "/home/ender/polymarket-data/live/probability_fragments.json"
                        },
                    },
                    "probabilities.json": {
                        "owner": "thepc",
                        "canonical_path": "/home/ender/polymarket-data/live/probabilities.json",
                        "mirrors": {
                            "spoon": "/home/spoon/polymarket-data/live/probabilities.thepc.json"
                        },
                    },
                },
                "mirror": {
                    "source_node": "spoon",
                    "target_node": "thepc",
                    "max_age_seconds": 5.0,
                },
            }
        ),
        encoding="utf-8",
    )

    manifest = load_cluster_manifest(path)

    assert manifest.artifacts["probability_inputs.json"].owner == "spoon"
    assert manifest.artifacts["probability_fragments.json"].owner == "spoon"
    assert manifest.artifacts["probabilities.json"].owner == "thepc"
    assert manifest.mirror.max_age_seconds == 5.0


def test_manifest_rejects_duplicate_canonical_paths(tmp_path: Path) -> None:
    path = tmp_path / "cluster.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-cluster-manifest-v1",
                "nodes": {
                    "spoon": {"host": "spoon", "role": "cpu_authority"},
                    "thepc": {"host": "thepc-lan", "role": "gpu_api"},
                },
                "artifacts": {
                    "status.json": {
                        "owner": "spoon",
                        "canonical_path": "/live/status.json",
                        "mirrors": {"thepc": "/mirror/status.json"},
                    },
                    "probability_inputs.json": {
                        "owner": "thepc",
                        "canonical_path": "/live/status.json",
                        "mirrors": {"spoon": "/mirror/probability_inputs.json"},
                    },
                },
                "mirror": {
                    "source_node": "spoon",
                    "target_node": "thepc",
                    "max_age_seconds": 5.0,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate canonical_path"):
        load_cluster_manifest(path)
