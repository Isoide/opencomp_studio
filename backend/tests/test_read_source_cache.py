import time

import numpy as np

from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, ImageFrame, Node, ProjectGraph, ProjectSettings
from opencomp.nodes import read as read_node_module


def test_duplicate_read_nodes_share_source_frame_cache(monkeypatch) -> None:
    calls: list[tuple[str, int | None, tuple[str, ...] | None]] = []

    def fake_read_image(
        path: str,
        frame: int | None = None,
        colorspace: str = "ACES2065-1",
        read_channels: list[str] | None = None,
    ) -> ImageFrame:
        calls.append((path, frame, None if read_channels is None else tuple(read_channels)))
        time.sleep(0.02)
        data = np.ones((4, 4, 4), dtype=np.float32)
        data[:, :, 0] = 0.4
        data[:, :, 1] = 0.2
        data[:, :, 2] = 0.1
        return ImageFrame(width=4, height=4, data=data, colorspace=colorspace, frame=frame or 1001)

    monkeypatch.setattr(read_node_module, "read_image", fake_read_image)
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "memory://same-plate"}),
            "Read2": Node(id="Read2", type="Read", params={"path": "memory://same-plate"}),
            "Merge1": Node(id="Merge1", type="Merge", params={"operation": "plus"}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="read1-merge", source_node="Read1", target_node="Merge1", target_socket="a"),
            Edge(id="read2-merge", source_node="Read2", target_node="Merge1", target_socket="b"),
            Edge(id="merge-viewer", source_node="Merge1", target_node="Viewer1", target_socket="0"),
        ],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True, render_workers=4, read_workers=4))

    result = evaluator.evaluate_node(graph, "Viewer1", 1001)
    snapshot = evaluator.cache_snapshot()

    assert result.width == 4
    assert len(calls) == 1
    assert snapshot["source_cache_entries"] == 1
    assert any(timing["phase"] in {"read.source_cache", "read.source_inflight_wait"} for timing in snapshot["phase_timings"])


def test_duplicate_read_source_cache_separates_channel_demands(monkeypatch) -> None:
    calls: list[tuple[str, ...] | None] = []

    def fake_read_image(
        path: str,
        frame: int | None = None,
        colorspace: str = "ACES2065-1",
        read_channels: list[str] | None = None,
    ) -> ImageFrame:
        calls.append(None if read_channels is None else tuple(read_channels))
        data = np.ones((2, 2, 4), dtype=np.float32)
        channel_data = {}
        if read_channels is None or any(str(channel).lower() == "z" for channel in read_channels):
            channel_data["Z"] = np.ones((2, 2), dtype=np.float32)
        return ImageFrame(width=2, height=2, data=data, channel_data=channel_data, colorspace=colorspace, frame=frame or 1001)

    monkeypatch.setattr(read_node_module, "read_image", fake_read_image)
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "memory://same-plate"}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[Edge(id="read-viewer", source_node="Read1", target_node="Viewer1", target_socket="0")],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True))

    evaluator.evaluate_node(graph, "Viewer1", 1001, requested_channel="rgba")
    evaluator.evaluate_node(graph, "Viewer1", 1001, requested_channel="Z")

    assert len(calls) == 2
    assert "Z" not in calls[0]
    assert "Z" in calls[1]
    assert evaluator.cache_snapshot()["source_cache_entries"] == 2
