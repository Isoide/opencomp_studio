from io import BytesIO

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from opencomp.app import app
from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, Node, ProjectGraph, ProjectSettings
from opencomp.core.preview_renderer import PreviewRequest, ViewerProcess, render_difference_preview, render_standard_preview


class IdentityOcio:
    def apply_display_view(self, rgba, src, display=None, view=None):  # noqa: ANN001, ANN201
        return np.asarray(rgba, dtype=np.float32).copy()


def _viewer_graph(nodes: dict[str, Node], edges: list[Edge]) -> ProjectGraph:
    return ProjectGraph(nodes={**nodes, "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "1"})}, edges=edges)


def _png_pixel(png_bytes: bytes) -> tuple[int, int, int, int]:
    image = Image.open(BytesIO(png_bytes)).convert("RGBA")
    return image.getpixel((0, 0))


def test_viewer_fstop_is_applied_before_final_png_clamp() -> None:
    graph = _viewer_graph(
        {
            "Hot1": Node(
                id="Hot1",
                type="Constant",
                params={"width": 2, "height": 2, "r": 4.0, "g": 4.0, "b": 4.0, "a": 1.0},
            )
        },
        [Edge(id="hot-viewer", source_node="Hot1", target_node="Viewer1", target_socket="1")],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True), ocio=IdentityOcio())  # type: ignore[arg-type]

    png = render_standard_preview(
        evaluator,
        graph,
        PreviewRequest(
            cache_node_id="Viewer1",
            eval_node_id="Viewer1",
            frame=1001,
            channel="rgba",
            viewer_process=ViewerProcess(fstop=-4.0),
        ),
    )

    assert _png_pixel(png)[:3] == (64, 64, 64)


def test_viewer_process_changes_reuse_float_cache_without_node_miss() -> None:
    graph = _viewer_graph(
        {
            "Constant1": Node(
                id="Constant1",
                type="Constant",
                params={"width": 2, "height": 2, "r": 1.5, "g": 0.5, "b": 0.25, "a": 1.0},
            )
        },
        [Edge(id="constant-viewer", source_node="Constant1", target_node="Viewer1", target_socket="1")],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True), ocio=IdentityOcio())  # type: ignore[arg-type]

    base_request = PreviewRequest(cache_node_id="Viewer1", eval_node_id="Viewer1", frame=1001, channel="rgba")
    render_standard_preview(evaluator, graph, base_request)
    node_misses = evaluator.cache_misses

    render_standard_preview(
        evaluator,
        graph,
        PreviewRequest(
            cache_node_id="Viewer1",
            eval_node_id="Viewer1",
            frame=1001,
            channel="rgba",
            viewer_process=ViewerProcess(gain=0.5, saturation=0.75, fstop=-1.0),
        ),
    )

    assert evaluator.cache_misses == node_misses
    assert evaluator.float_preview_cache_hits >= 1


def test_difference_preview_compares_float_inputs() -> None:
    graph = _viewer_graph(
        {
            "Black1": Node(id="Black1", type="Constant", params={"width": 2, "height": 2, "r": 0, "g": 0, "b": 0, "a": 1}),
            "White1": Node(id="White1", type="Constant", params={"width": 2, "height": 2, "r": 1, "g": 1, "b": 1, "a": 1}),
        },
        [
            Edge(id="black-viewer", source_node="Black1", target_node="Viewer1", target_socket="1"),
            Edge(id="white-viewer", source_node="White1", target_node="Viewer1", target_socket="2"),
        ],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True), ocio=IdentityOcio())  # type: ignore[arg-type]

    png = render_difference_preview(
        evaluator,
        graph,
        PreviewRequest(
            cache_node_id="Viewer1",
            eval_node_id="Black1",
            frame=1001,
            output_signature=evaluator.node_signature(graph, "Black1", 1001),
        ),
        PreviewRequest(
            cache_node_id="Viewer1",
            eval_node_id="White1",
            frame=1001,
            output_signature=evaluator.node_signature(graph, "White1", 1001),
        ),
    )

    assert _png_pixel(png)[:3] == (255, 255, 255)


def test_transform_data_window_is_reported_by_metadata_endpoint() -> None:
    client = TestClient(app)
    client.post("/api/projects/new")
    graph = ProjectGraph(
        nodes={
            "Constant1": Node(id="Constant1", type="Constant", params={"width": 100, "height": 50, "a": 1.0}),
            "Transform1": Node(id="Transform1", type="Transform", params={"translate_x": 50, "translate_y": -10, "scale": 1}),
        },
        edges=[Edge(id="constant-transform", source_node="Constant1", target_node="Transform1")],
    )
    client.put("/api/graph", json={"graph": graph.model_dump()})

    response = client.get("/api/nodes/Transform1/metadata?frame=1001")

    assert response.status_code == 200
    payload = response.json()
    assert payload["format_bbox"] == {"x": 0, "y": 0, "width": 100, "height": 50}
    assert payload["data_window"] == {"x": 50, "y": -10, "width": 100, "height": 50}
