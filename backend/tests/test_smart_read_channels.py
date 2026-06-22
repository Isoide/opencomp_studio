import numpy as np

from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, ImageFrame, Node, Project, ProjectGraph, ProjectSettings, ReadWarmRequest
from opencomp.nodes import read as read_node_module
from opencomp.api.routes import _read_preload_node_ids, _warm_read_frames


def _fake_reader(captured: list[list[str] | None]):
    def fake_read_image(
        path: str,
        frame: int | None = None,
        colorspace: str = "ACES2065-1",
        read_channels: list[str] | None = None,
    ) -> ImageFrame:
        captured.append(read_channels)
        data = np.zeros((2, 2, 4), dtype=np.float32)
        data[:, :, :3] = 0.25
        data[:, :, 3] = 1.0
        channel_data = {}
        if read_channels is None or any(str(channel).lower() == "z" for channel in read_channels):
            channel_data["Z"] = np.full((2, 2), 0.75, dtype=np.float32)
        return ImageFrame(
            width=2,
            height=2,
            data=data,
            channels=["rgba", "rgb", "r", "g", "b", "a", "Z"],
            channel_data=channel_data,
            colorspace=colorspace,
            frame=frame or 1001,
        )

    return fake_read_image


def test_read_defaults_to_rgba_channel_demand(monkeypatch) -> None:
    captured: list[list[str] | None] = []
    monkeypatch.setattr(read_node_module, "read_image", _fake_reader(captured))
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "memory://plate"}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[Edge(id="read-viewer", source_node="Read1", target_node="Viewer1", target_socket="0")],
    )

    GraphEvaluator(settings=ProjectSettings(cache_enabled=True)).evaluate_node(graph, "Viewer1", 1001)

    assert captured[0] is not None
    assert {channel.lower() for channel in captured[0]} >= {"rgba", "r", "g", "b", "a"}
    assert "Z" not in captured[0]


def test_viewer_requested_channel_loads_on_demand_and_uses_separate_cache(monkeypatch) -> None:
    captured: list[list[str] | None] = []
    monkeypatch.setattr(read_node_module, "read_image", _fake_reader(captured))
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "memory://plate"}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[Edge(id="read-viewer", source_node="Read1", target_node="Viewer1", target_socket="0")],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True))

    evaluator.evaluate_node(graph, "Viewer1", 1001)
    evaluator.evaluate_node(graph, "Viewer1", 1001, requested_channel="Z")

    assert len(captured) == 2
    assert "Z" not in captured[0]
    assert "Z" in captured[1]


def test_downstream_channel_node_allows_aux_channel_into_read(monkeypatch) -> None:
    captured: list[list[str] | None] = []
    monkeypatch.setattr(read_node_module, "read_image", _fake_reader(captured))
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "memory://plate"}),
            "Shuffle1": Node(
                id="Shuffle1",
                type="Shuffle",
                params={"out_r": "Z", "out_g": "Z", "out_b": "Z", "out_a": "a"},
            ),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="read-shuffle", source_node="Read1", target_node="Shuffle1", target_socket="in"),
            Edge(id="shuffle-viewer", source_node="Shuffle1", target_node="Viewer1", target_socket="0"),
        ],
    )

    result = GraphEvaluator(settings=ProjectSettings(cache_enabled=True)).evaluate_node(graph, "Viewer1", 1001)

    assert "Z" in captured[0]
    assert np.allclose(result.data[:, :, 0], 0.75)


def test_read_preload_warms_only_upstream_reads_with_channel_demand(monkeypatch) -> None:
    captured: list[list[str] | None] = []
    monkeypatch.setattr(read_node_module, "read_image", _fake_reader(captured))
    graph = ProjectGraph(
        nodes={
            "Read1": Node(id="Read1", type="Read", params={"path": "memory://plate"}),
            "Shuffle1": Node(
                id="Shuffle1",
                type="Shuffle",
                params={"out_r": "Z", "out_g": "Z", "out_b": "Z", "out_a": "a"},
            ),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="read-shuffle", source_node="Read1", target_node="Shuffle1", target_socket="in"),
            Edge(id="shuffle-viewer", source_node="Shuffle1", target_node="Viewer1", target_socket="0"),
        ],
    )
    project = Project(settings=ProjectSettings(frame_start=1001, frame_end=1010, read_workers=2))
    evaluator = GraphEvaluator(settings=project.settings)

    _warm_read_frames(
        project,
        graph,
        evaluator,
        ReadWarmRequest(node_id="Viewer1", frames=[1001, 1002], viewer_input="0", channel="rgba"),
        [1001, 1002],
    )

    assert _read_preload_node_ids(graph, "Viewer1", "0") == ["Read1"]
    assert len(captured) == 2
    assert all(channels is not None and "Z" in channels for channels in captured)
    assert "Shuffle1" not in evaluator.node_timings
