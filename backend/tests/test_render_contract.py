import numpy as np

from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, Node, ProjectGraph, ProjectSettings, TileWindow
from opencomp.core.render_contract import RenderROI, RenderRequest


def _disabled_merge_graph() -> ProjectGraph:
    return ProjectGraph(
        nodes={
            "BrokenA": Node(id="BrokenA", type="DefinitelyUnsupported"),
            "B": Node(id="B", type="Constant", params={"width": 8, "height": 6, "r": 0.2, "g": 0.4, "b": 0.6, "a": 1.0}),
            "Merge1": Node(id="Merge1", type="Merge", params={"operation": "over", "disabled": True}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="a-merge", source_node="BrokenA", target_node="Merge1", target_socket="a"),
            Edge(id="b-merge", source_node="B", target_node="Merge1", target_socket="b"),
            Edge(id="merge-viewer", source_node="Merge1", target_node="Viewer1", target_socket="0"),
        ],
    )


def test_execution_plan_resolves_disabled_bypass_tree() -> None:
    graph = _disabled_merge_graph()
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True, tile_rendering_enabled=True))
    request = RenderRequest(node_id="Viewer1", frame=1001, channels=["rgba"], precision="float16", storage="frontend")

    plan = evaluator.execution_plan_for(graph, request, eval_node_id="Viewer1", output_signature="test")
    cached_plan = evaluator.execution_plan_for(graph, request, eval_node_id="Viewer1", output_signature="test")

    assert plan.cache_hit is False
    assert cached_plan.cache_hit is True
    assert "B" in plan.upstream_nodes
    assert "Merge1" in plan.upstream_nodes
    assert "BrokenA" not in plan.upstream_nodes
    merge_plan_node = next(item for item in plan.nodes if item.node_id == "Merge1")
    assert merge_plan_node.disabled is True
    assert merge_plan_node.bypass_socket == "b"


def test_render_request_tile_reuses_tile_cache() -> None:
    graph = ProjectGraph(
        nodes={
            "A": Node(id="A", type="Constant", params={"width": 64, "height": 32, "r": 0.2, "g": 0.4, "b": 0.6, "a": 1.0}),
            "Grade1": Node(id="Grade1", type="Grade", params={"gain": 1.5, "offset": 0.05}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="a-grade", source_node="A", target_node="Grade1", target_socket="in"),
            Edge(id="grade-viewer", source_node="Grade1", target_node="Viewer1", target_socket="0"),
        ],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True, tile_rendering_enabled=True, tile_workers=2))
    request = RenderRequest(
        node_id="Viewer1",
        frame=1001,
        roi=RenderROI(x=7, y=5, width=19, height=11),
        channels=["rgba"],
        precision="float16",
        storage="frontend",
    )

    first = evaluator.evaluate_render_request(graph, request)
    before = evaluator.cache_snapshot()
    second = evaluator.evaluate_render_request(graph, request)
    after = evaluator.cache_snapshot()

    assert np.allclose(first.data, second.data)
    assert first.metadata["tile/full_width"] == 64
    assert first.metadata["tile/full_height"] == 32
    assert after["tile_cache_entries"] >= before["tile_cache_entries"] >= 1
    assert after["tile_cache_hits"] > before["tile_cache_hits"]


def test_direct_tile_evaluator_reports_tile_cache() -> None:
    graph = _disabled_merge_graph()
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True, tile_rendering_enabled=True, tile_workers=2))

    tile_a = evaluator.evaluate_node_tile(graph, "Viewer1", 1001, TileWindow(2, 1, 3, 2))
    hits_before = evaluator.cache_snapshot()["tile_cache_hits"]
    tile_b = evaluator.evaluate_node_tile(graph, "Viewer1", 1001, TileWindow(2, 1, 3, 2))
    hits_after = evaluator.cache_snapshot()["tile_cache_hits"]

    assert np.allclose(tile_a.data, tile_b.data)
    assert hits_after > hits_before


def test_activity_scope_reports_foreground_and_background_nodes_separately() -> None:
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True, tile_rendering_enabled=True))

    with evaluator.node_runtime("Viewer1", "Viewer"):
        snapshot = evaluator.cache_snapshot()
        assert "Viewer1" in snapshot["node_activity"]["foreground_active_nodes"]
        assert "Viewer1" not in snapshot["node_activity"]["background_active_nodes"]

    with evaluator.activity_scope("background"):
        with evaluator.node_runtime("Read1", "Read"):
            snapshot = evaluator.cache_snapshot()
            assert "Read1" in snapshot["node_activity"]["background_active_nodes"]
            assert "Read1" not in snapshot["node_activity"]["foreground_active_nodes"]
