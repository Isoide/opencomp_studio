"""Unit tests for viewer request and warm-up helper functions."""

from __future__ import annotations

from opencomp.api.viewer_requests import (
    ensure_frame_request_id,
    read_preload_node_ids,
    render_request_from_frame,
    viewer_request_scope,
    viewer_warm_inputs,
)
from opencomp.core.models import Edge, FrameROI, FrameRequest, Node, ProjectGraph


def test_ensure_frame_request_id_is_stable_once_assigned() -> None:
    payload = FrameRequest(node_id="Viewer1", frame=1001)

    first = ensure_frame_request_id(payload)
    second = ensure_frame_request_id(payload)

    assert first
    assert second == first


def test_viewer_request_scope_encodes_branch_and_compare_mode() -> None:
    payload = FrameRequest(
        node_id="Viewer1",
        frame=1001,
        viewer_input="2",
        channel="alpha",
        compare_mode="difference",
    )

    assert viewer_request_scope(payload) == "Viewer1:2:alpha:difference"


def test_render_request_from_frame_carries_roi_and_channels() -> None:
    payload = FrameRequest(
        node_id="Viewer1",
        frame=1001,
        channel="rgba",
        channels=["rgba", "depth"],
        roi=FrameROI(x=10, y=20, width=300, height=200),
        precision="float16",
        storage="frontend",
    )

    request = render_request_from_frame(payload, node_id="Grade1")

    assert request.node_id == "Grade1"
    assert request.frame == 1001
    assert request.channels == ["rgba", "depth"]
    assert request.roi is not None
    assert request.roi.x == 10
    assert request.roi.y == 20
    assert request.roi.width == 300
    assert request.roi.height == 200


def test_read_preload_node_ids_collects_all_upstream_reads_for_viewer_branch() -> None:
    graph = ProjectGraph(
        nodes={
            "ReadA": Node(id="ReadA", type="Read", params={"path": "builtin://gradient"}),
            "ReadB": Node(id="ReadB", type="Read", params={"path": "builtin://gradient"}),
            "Merge1": Node(id="Merge1", type="Merge"),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="a-merge", source_node="ReadA", target_node="Merge1", target_socket="a"),
            Edge(id="b-merge", source_node="ReadB", target_node="Merge1", target_socket="b"),
            Edge(id="merge-viewer", source_node="Merge1", target_node="Viewer1", target_socket="0"),
        ],
    )

    assert read_preload_node_ids(graph, "Viewer1", "0") == ["ReadA", "ReadB"]


def test_viewer_warm_inputs_uses_compare_inputs_or_active_input() -> None:
    graph = ProjectGraph(
        nodes={
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "3"}),
        }
    )

    difference_payload = FrameRequest(
        node_id="Viewer1",
        frame=1001,
        compare_mode="difference",
        viewer_input="1",
        compare_input="2",
    )
    active_payload = FrameRequest(node_id="Viewer1", frame=1001)

    assert viewer_warm_inputs(graph, difference_payload) == {"1", "2"}
    assert viewer_warm_inputs(graph, active_payload) == {"3"}
