import numpy as np

from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, Node, ProjectGraph, ProjectSettings


def test_viewer_evaluates_only_active_numbered_input() -> None:
    graph = ProjectGraph(
        nodes={
            "Constant1": Node(
                id="Constant1",
                type="Constant",
                params={"width": 2, "height": 2, "r": 0.1, "g": 0.2, "b": 0.3, "a": 1.0},
            ),
            "Broken1": Node(id="Broken1", type="DefinitelyUnsupported"),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "2"}),
        },
        edges=[
            Edge(id="viewer-slot-2", source_node="Constant1", target_node="Viewer1", target_socket="2"),
            Edge(id="viewer-slot-9", source_node="Broken1", target_node="Viewer1", target_socket="9"),
        ],
    )

    image = GraphEvaluator(settings=ProjectSettings()).evaluate_node(graph, "Viewer1", 1001)

    assert image.width == 2
    assert image.height == 2
    np.testing.assert_allclose(image.data[0, 0], [0.1, 0.2, 0.3, 1.0], rtol=0.00001)
