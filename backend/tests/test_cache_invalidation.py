import numpy as np
from PIL import Image

from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, ImageFrame, Node, ProjectGraph, ProjectSettings
from opencomp.nodes import read as read_node_module


def test_upstream_param_change_invalidates_downstream_cache() -> None:
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "builtin://gradient"}),
            "Grade1": Node(id="Grade1", type="Grade", params={"gain": 1.0}),
            "Viewer1": Node(id="Viewer1", type="Viewer"),
        },
        edges=[
            Edge(id="read-grade", source_node="Read1", target_node="Grade1"),
            Edge(id="grade-viewer", source_node="Grade1", target_node="Viewer1"),
        ],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True))

    first = evaluator.evaluate_node(graph, "Viewer1", 1001)
    graph.nodes["Grade1"].params["gain"] = 2.0
    second = evaluator.evaluate_node(graph, "Viewer1", 1001)

    assert not np.allclose(first.data[:, :, :3], second.data[:, :, :3])
    assert evaluator.cache_hits >= 1


def test_read_node_reuses_memory_cache_when_downstream_changes(monkeypatch) -> None:
    calls = 0

    def fake_read_image(path: str, frame: int | None = None, colorspace: str = "ACES2065-1") -> ImageFrame:
        nonlocal calls
        calls += 1
        data = np.ones((2, 2, 4), dtype=np.float32)
        data[:, :, 3] = 1.0
        return ImageFrame(width=2, height=2, data=data, colorspace=colorspace, frame=frame or 1001)

    monkeypatch.setattr(read_node_module, "read_image", fake_read_image)
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "memory://patched"}),
            "Grade1": Node(id="Grade1", type="Grade", params={"gain": 1.0}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="read-grade", source_node="Read1", target_node="Grade1"),
            Edge(id="grade-viewer", source_node="Grade1", target_node="Viewer1", target_socket="0"),
        ],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True))

    evaluator.evaluate_node(graph, "Viewer1", 1001)
    graph.nodes["Grade1"].params["gain"] = 2.0
    evaluator.evaluate_node(graph, "Viewer1", 1001)

    assert calls == 1
    assert evaluator.cache_hits >= 1


def test_file_read_cache_invalidates_when_source_file_changes(tmp_path) -> None:
    image_path = tmp_path / "plate.png"
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(image_path)
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": str(image_path), "colorspace": "ACES2065-1"}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[Edge(id="read-viewer", source_node="Read1", target_node="Viewer1", target_socket="0")],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True))

    first = evaluator.evaluate_node(graph, "Viewer1", 1001)
    Image.new("RGBA", (3, 3), (0, 255, 0, 255)).save(image_path)
    second = evaluator.evaluate_node(graph, "Viewer1", 1001)

    assert first.width == 2
    assert second.width == 3


def test_memory_cache_prunes_to_budget() -> None:
    graph = ProjectGraph(
        nodes={
            "Constant1": Node(id="Constant1", type="Constant", params={"width": 10, "height": 10}),
            "Constant2": Node(id="Constant2", type="Constant", params={"width": 10, "height": 10}),
            "Constant3": Node(id="Constant3", type="Constant", params={"width": 10, "height": 10}),
        },
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True), max_cache_bytes=3500)

    evaluator.evaluate_node(graph, "Constant1", 1001)
    evaluator.evaluate_node(graph, "Constant2", 1001)
    evaluator.evaluate_node(graph, "Constant3", 1001)

    assert len(evaluator.cache) == 2
    assert evaluator.cache_memory_bytes <= evaluator.max_cache_bytes


def test_preview_cache_survives_node_cache_pruning() -> None:
    graph = ProjectGraph(
        nodes={
            "Constant1": Node(id="Constant1", type="Constant", params={"width": 10, "height": 10}),
            "Constant2": Node(id="Constant2", type="Constant", params={"width": 10, "height": 10}),
            "Constant3": Node(id="Constant3", type="Constant", params={"width": 10, "height": 10}),
        },
    )
    evaluator = GraphEvaluator(
        settings=ProjectSettings(cache_enabled=True),
        max_cache_bytes=3500,
        max_preview_cache_bytes=10000,
    )
    preview_key = evaluator.preview_cache_key_for_signature(
        "Viewer1",
        1001,
        "signature",
        "sRGB",
        "default",
        "rgba",
        1280,
        720,
        None,
    )

    evaluator.store_cached_preview(preview_key, b"cached-preview")
    evaluator.evaluate_node(graph, "Constant1", 1001)
    evaluator.evaluate_node(graph, "Constant2", 1001)
    evaluator.evaluate_node(graph, "Constant3", 1001)

    assert evaluator.get_cached_preview(preview_key) == b"cached-preview"
    assert evaluator.cached_frame_numbers({"Viewer1"})["preview_frames"] == [1001]
