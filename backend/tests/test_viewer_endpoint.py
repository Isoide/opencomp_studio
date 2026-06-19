from fastapi.testclient import TestClient

from opencomp.app import app
from opencomp.core.models import Edge, Node, ProjectGraph


def test_viewer_frame_returns_png() -> None:
    client = TestClient(app)
    client.post("/api/projects/new")
    graph = ProjectGraph(
        nodes={
            "Read1": Node(
                id="Read1",
                type="Read",
                params={"path": "builtin://gradient", "colorspace": "ACES2065-1"},
            ),
            "Viewer1": Node(id="Viewer1", type="Viewer"),
        },
        edges=[Edge(id="read-viewer", source_node="Read1", target_node="Viewer1")],
    )
    client.put("/api/graph", json={"graph": graph.model_dump()})
    response = client.post("/api/viewer/frame", json={"node_id": "Viewer1", "frame": 1001})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_repeated_frontend_sync_keeps_viewer_cache_warm() -> None:
    client = TestClient(app)
    project = client.post("/api/projects/new").json()
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "builtin://gradient", "colorspace": "ACES2065-1"}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[Edge(id="read-viewer", source_node="Read1", target_node="Viewer1", target_socket="0")],
    )

    client.put("/api/projects/settings", json={"settings": project["settings"]})
    client.put("/api/graph", json={"graph": graph.model_dump()})
    first = client.post("/api/viewer/frame", json={"node_id": "Viewer1", "frame": 1001})
    assert first.status_code == 200

    client.put("/api/projects/settings", json={"settings": project["settings"]})
    client.put("/api/graph", json={"graph": graph.model_dump()})
    second = client.post("/api/viewer/frame", json={"node_id": "Viewer1", "frame": 1001})
    assert second.status_code == 200

    status = client.get("/api/cache/status").json()
    assert status["preview_hits"] >= 1
    assert status["entries"] >= 1
    assert status["preview_entries"] >= 1
    assert 1001 in status["cached_frames"]


def test_cache_status_reports_cached_viewer_frames() -> None:
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

    response = client.post("/api/viewer/frame", json={"node_id": "Viewer1", "frame": 1007})
    assert response.status_code == 200

    status = client.get("/api/cache/status").json()
    assert 1007 in status["cached_frames"]
    assert status["max_preview_memory_bytes"] > 0
