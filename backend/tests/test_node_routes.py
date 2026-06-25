"""Integration coverage for node metadata/bindings/Cryptomatte API routes.

These tests keep the extracted node router honest without needing the full
viewer transport stack. They focus on route wiring, response shaping, and the
most common success/error cases for node inspection endpoints.
"""

from fastapi.testclient import TestClient

from opencomp.app import app
from opencomp.core.models import Edge, Node, ProjectGraph


def test_node_catalog_lists_core_nodes() -> None:
    client = TestClient(app)

    response = client.get("/api/nodes/catalog")

    assert response.status_code == 200
    catalog = response.json()
    assert any(item["type"] == "Read" for item in catalog)
    assert any(item["type"] == "Viewer" for item in catalog)


def test_node_bindings_return_expression_surface() -> None:
    client = TestClient(app)
    client.post("/api/projects/new")
    graph = ProjectGraph(
        nodes={
            "Constant1": Node(id="Constant1", type="Constant", params={"width": 8, "height": 6, "r": 0.2, "g": 0.4, "b": 0.6, "a": 1.0}),
        },
        edges=[],
    )
    client.put("/api/graph", json={"graph": graph.model_dump()})

    response = client.get("/api/nodes/Constant1/bindings?frame=1001")

    assert response.status_code == 200
    payload = response.json()
    assert payload["node_id"] == "Constant1"
    assert payload["frame"] == 1001
    assert isinstance(payload["bindable_outputs"], dict)
    assert isinstance(payload["expression_errors"], dict)


def test_node_metadata_unknown_node_returns_404() -> None:
    client = TestClient(app)
    client.post("/api/projects/new")

    response = client.get("/api/nodes/DoesNotExist/metadata?frame=1001")

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown node: DoesNotExist"


def test_node_cryptomatte_returns_layers_payload() -> None:
    client = TestClient(app)
    client.post("/api/projects/new")
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "builtin://gradient", "colorspace": "ACES2065-1"}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[Edge(id="read-viewer", source_node="Read1", target_node="Viewer1", target_socket="0")],
    )
    client.put("/api/graph", json={"graph": graph.model_dump()})

    response = client.get("/api/nodes/Read1/cryptomatte?frame=1001")

    assert response.status_code == 200
    payload = response.json()
    assert payload["node_id"] == "Read1"
    assert payload["frame"] == 1001
    assert payload["layers"] == []
