import json

from fastapi.testclient import TestClient

from opencomp.app import app
from opencomp.core.models import Edge, Node, ProjectGraph


def _install_gradient_viewer(client: TestClient) -> None:
    client.post("/api/projects/new")
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "builtin://gradient", "colorspace": "ACES2065-1"}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[Edge(id="read-viewer", source_node="Read1", target_node="Viewer1", target_socket="0")],
    )
    client.put("/api/graph", json={"graph": graph.model_dump()})


def test_viewer_frame_websocket_returns_png() -> None:
    client = TestClient(app)
    _install_gradient_viewer(client)

    with client.websocket_connect("/ws/viewer/frame") as websocket:
        websocket.send_json({"node_id": "Viewer1", "frame": 1001, "viewer_input": "0"})
        data = websocket.receive_bytes()

    assert data.startswith(b"\x89PNG")
    status = client.get("/api/cache/status").json()
    assert 1001 in status["cached_frames"]


def test_viewer_float_websocket_returns_pre_display_rgba() -> None:
    client = TestClient(app)
    _install_gradient_viewer(client)

    with client.websocket_connect("/ws/viewer/float") as websocket:
        websocket.send_json({"node_id": "Viewer1", "frame": 1001, "viewer_input": "0", "channel": "rgba"})
        header = json.loads(websocket.receive_text())
        data = websocket.receive_bytes()

    assert header["type"] == "viewer_float_frame"
    assert header["dtype"] == "float32"
    assert header["layout"] == "rgba"
    assert header["width"] > 0
    assert header["height"] > 0
    assert header["byte_length"] == header["width"] * header["height"] * 4 * 4
    assert len(data) == header["byte_length"]
    assert header["colorspace"] == "ACES2065-1"


def test_viewer_float_websocket_can_stream_float16_tiles() -> None:
    client = TestClient(app)
    _install_gradient_viewer(client)

    with client.websocket_connect("/ws/viewer/float") as websocket:
        websocket.send_json(
            {
                "node_id": "Viewer1",
                "frame": 1001,
                "viewer_input": "0",
                "channel": "rgba",
                "precision": "float16",
                "stream_tiles": True,
                "tile_height": 7,
            }
        )
        header = json.loads(websocket.receive_text())
        received = 0
        tile_count = 0
        while True:
            message = json.loads(websocket.receive_text())
            if message["type"] == "viewer_float_tiles_done":
                assert message["tiles"] == tile_count
                break
            assert message["type"] == "viewer_float_tile"
            data = websocket.receive_bytes()
            assert len(data) == message["byte_length"]
            received += len(data)
            tile_count += 1

    assert header["type"] == "viewer_float_frame"
    assert header["dtype"] == "float16"
    assert header["tile_stream"] is True
    assert header["tile_height"] == 7
    assert header["tile_count"] == tile_count
    assert header["byte_length"] == header["width"] * header["height"] * 4 * 2
    assert received == header["byte_length"]


def test_viewer_float_websocket_can_stream_one_tile_lane() -> None:
    client = TestClient(app)
    _install_gradient_viewer(client)

    with client.websocket_connect("/ws/viewer/float") as websocket:
        websocket.send_json(
            {
                "node_id": "Viewer1",
                "frame": 1001,
                "viewer_input": "0",
                "channel": "rgba",
                "precision": "float16",
                "stream_tiles": True,
                "tile_height": 7,
                "tile_lanes": 3,
                "tile_lane": 1,
            }
        )
        header = json.loads(websocket.receive_text())
        received = 0
        tile_count = 0
        while True:
            message = json.loads(websocket.receive_text())
            if message["type"] == "viewer_float_tiles_done":
                assert message["tiles"] == tile_count
                break
            assert message["type"] == "viewer_float_tile"
            data = websocket.receive_bytes()
            assert len(data) == message["byte_length"]
            received += len(data)
            tile_count += 1

    assert header["tile_lanes"] == 3
    assert header["tile_lane"] == 1
    assert header["tile_count"] == tile_count
    assert header["tile_count_total"] > header["tile_count"]
    assert received < header["byte_length"]


def test_viewer_float_websocket_uses_native_tiles_when_proxy_is_off() -> None:
    client = TestClient(app)
    _install_gradient_viewer(client)
    settings = client.get("/api/projects/settings").json()
    settings["proxy_enabled"] = False
    response = client.put("/api/projects/settings", json={"settings": settings})
    assert response.status_code == 200

    with client.websocket_connect("/ws/viewer/float") as websocket:
        websocket.send_json(
            {
                "node_id": "Viewer1",
                "frame": 1001,
                "viewer_input": "0",
                "channel": "rgba",
                "precision": "float16",
                "stream_tiles": True,
                "tile_height": 64,
            }
        )
        header = json.loads(websocket.receive_text())
        tile_message = None
        tile_data = b""
        while True:
            message = json.loads(websocket.receive_text())
            if message["type"] == "viewer_float_tiles_done":
                break
            if tile_message is None:
                tile_message = message
                tile_data = websocket.receive_bytes()
            else:
                websocket.receive_bytes()

    assert header["tile_native"] is True
    assert tile_message is not None
    assert tile_message["type"] == "viewer_float_tile"
    assert len(tile_data) == tile_message["byte_length"]


def test_color_gpu_shader_endpoint_reports_capability() -> None:
    client = TestClient(app)
    client.post("/api/projects/new")

    response = client.get("/api/color/gpu-shader?src=ACES2065-1")

    assert response.status_code == 200
    payload = response.json()
    assert "available" in payload
    assert payload["language"] == "GLSL"
    assert payload["source"] == "ACES2065-1"
