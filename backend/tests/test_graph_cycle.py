import pytest

from opencomp.core.evaluator import GraphCycleError, GraphEvaluator
from opencomp.core.models import Edge, Node, ProjectGraph


def test_graph_cycle_detection() -> None:
    graph = ProjectGraph(
        nodes={
            "A": Node(id="A", type="Viewer"),
            "B": Node(id="B", type="Grade"),
        },
        edges=[
            Edge(id="ab", source_node="A", target_node="B"),
            Edge(id="ba", source_node="B", target_node="A"),
        ],
    )
    with pytest.raises(GraphCycleError):
        GraphEvaluator().evaluate_node(graph, "A", 1001)
