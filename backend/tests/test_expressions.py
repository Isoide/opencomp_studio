import numpy as np

from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, Node, ProjectGraph, ProjectSettings


def test_expression_updates_transform_across_frames() -> None:
    graph = ProjectGraph(
        nodes={
            "Constant1": Node(id="Constant1", type="Constant", params={"width": 4, "height": 4, "r": 1, "g": 0, "b": 0, "a": 1}),
            "Transform1": Node(
                id="Transform1",
                type="Transform",
                params={"translate_x": 0, "translate_y": 0, "scale": 1, "filter": "nearest"},
                param_expressions={"translate_x": {"source": "frame - 1001", "enabled": True}},
            ),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="const-transform", source_node="Constant1", target_node="Transform1"),
            Edge(id="transform-viewer", source_node="Transform1", target_node="Viewer1", target_socket="0"),
        ],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings())

    first = evaluator.evaluate_node(graph, "Transform1", 1001)
    later = evaluator.evaluate_node(graph, "Transform1", 1005)

    assert first.data_window == {"x": 0, "y": 0, "width": 4, "height": 4}
    assert later.data_window == {"x": 4, "y": 0, "width": 4, "height": 4}
    assert evaluator.node_signature(graph, "Transform1", 1001) != evaluator.node_signature(graph, "Transform1", 1005)


def test_expression_cycle_is_reported() -> None:
    graph = ProjectGraph(
        nodes={
            "Transform1": Node(
                id="Transform1",
                type="Transform",
                params={"translate_x": 0, "translate_y": 0, "scale": 1},
                param_expressions={"translate_x": {"source": 'node("Transform2").translate_x + 1', "enabled": True}},
            ),
            "Transform2": Node(
                id="Transform2",
                type="Transform",
                params={"translate_x": 0, "translate_y": 0, "scale": 1},
                param_expressions={"translate_x": {"source": 'node("Transform1").translate_x + 1', "enabled": True}},
            ),
        }
    )
    evaluator = GraphEvaluator(settings=ProjectSettings())

    errors = evaluator.expression_errors(graph, "Transform1", 1001)

    assert "translate_x" in errors
    assert "cycle" in errors["translate_x"].lower()


def test_framehold_reuses_single_read_source_frame() -> None:
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "builtin://gradient", "frame_start": 1001, "frame_end": 1010}),
            "FrameHold1": Node(id="FrameHold1", type="FrameHold", params={"first_frame": 1001, "increment": 0}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="read-hold", source_node="Read1", target_node="FrameHold1"),
            Edge(id="hold-viewer", source_node="FrameHold1", target_node="Viewer1", target_socket="0"),
        ],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True))

    first = evaluator.evaluate_node(graph, "Viewer1", 1001)
    second = evaluator.evaluate_node(graph, "Viewer1", 1005)
    snapshot = evaluator.cache_snapshot()

    assert np.allclose(first.data, second.data)
    assert snapshot["source_cache_entries"] == 1
    assert snapshot["hits"] >= 1 or snapshot["source_cache_hits"] >= 1
