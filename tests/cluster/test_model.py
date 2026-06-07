from polymarket_engine.cluster.model import ClusterArtifact
from polymarket_engine.cluster.model import ClusterManifest
from polymarket_engine.cluster.model import ClusterMirror
from polymarket_engine.cluster.model import ClusterNode


def test_cluster_model_holds_nodes_artifacts_and_mirror() -> None:
    nodes = {"spoon": ClusterNode(host="spoon", role="cpu_authority")}
    artifacts = {
        "probability_inputs.json": ClusterArtifact(
            owner="spoon",
            canonical_path="/home/spoon/polymarket-data/live/probability_inputs.json",
            mirrors={"thepc": "/home/ender/polymarket-data/live/probability_inputs.json"},
        )
    }
    mirror = ClusterMirror(
        source_node="spoon",
        target_node="thepc",
        max_age_seconds=5.0,
    )

    manifest = ClusterManifest(nodes=nodes, artifacts=artifacts, mirror=mirror)

    assert manifest.nodes["spoon"].host == "spoon"
    assert manifest.mirror.max_age_seconds == 5.0
    assert manifest.artifacts["probability_inputs.json"].owner == "spoon"
