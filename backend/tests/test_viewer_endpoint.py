from fastapi.testclient import TestClient

from opencomp.app import app
from opencomp.api.viewer_float import float_preview_payload
from opencomp.core.defaults import create_default_project
from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, FrameROI, FrameRequest, Node, ProjectGraph


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


def test_viewer_frame_reports_structured_node_error_and_stops_on_missing_read() -> None:
    client = TestClient(app)
    client.post("/api/projects/new")
    graph = ProjectGraph(
        nodes={
            "Read1": Node(
                id="Read1",
                type="Read",
                params={"path": "Z:/opencomp-tests/missing_plate.####.exr", "colorspace": "ACES2065-1"},
            ),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[Edge(id="read-viewer", source_node="Read1", target_node="Viewer1", target_socket="0")],
    )
    client.put("/api/graph", json={"graph": graph.model_dump()})

    response = client.post("/api/viewer/frame", json={"node_id": "Viewer1", "frame": 1001})

    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["node_id"] == "Read1"
    assert body["detail"]["kind"] == "node_evaluation_error"
    status = client.get("/api/cache/status").json()
    assert status["node_errors"]["Read1"]["message"]


def test_disabled_broken_branch_does_not_block_viewer_render() -> None:
    client = TestClient(app)
    client.post("/api/projects/new")
    graph = ProjectGraph(
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
    client.put("/api/graph", json={"graph": graph.model_dump()})

    response = client.post("/api/viewer/frame", json={"node_id": "Viewer1", "frame": 1001})

    assert response.status_code == 200
    status = client.get("/api/cache/status").json()
    assert "BrokenA" not in status["node_errors"]


def test_float_preview_payload_marks_roi_as_partial_tile() -> None:
    project = create_default_project()
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "builtin://gradient", "colorspace": "ACES2065-1"}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[Edge(id="read-viewer", source_node="Read1", target_node="Viewer1", target_socket="0")],
    )
    evaluator = GraphEvaluator(settings=project.settings)
    payload = FrameRequest(
        node_id="Viewer1",
        frame=1001,
        display=project.settings.viewer_display,
        view=project.settings.viewer_view,
        channel="rgba",
        precision="float16",
        stream_tiles=False,
        transfer_mode="float16-rgba",
        roi=FrameROI(x=10, y=12, width=20, height=16),
    )

    header, data = float_preview_payload(project, graph, evaluator, payload)

    assert header["partial"] is True
    assert header["roi"] == {"x": 10, "y": 12, "width": 20, "height": 16}
    assert header["updated_tile"] == {"x": 10, "y": 12, "width": 20, "height": 16}
    assert header["byte_length"] == len(data)
