"""Unit tests for shared API context helpers."""

from __future__ import annotations

from opencomp.api.context import get_active_graph
from opencomp.core.defaults import create_default_project


def test_get_active_graph_returns_current_script_graph() -> None:
    project = create_default_project()

    graph = get_active_graph(project)

    assert graph is project.graph
    assert "Viewer1" in graph.nodes
