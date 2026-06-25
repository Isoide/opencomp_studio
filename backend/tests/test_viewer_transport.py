"""Unit tests for pure viewer transport helper functions."""

from __future__ import annotations

from opencomp.api.viewer_transport import lane_tile_ranges, normalized_frame_roi, ordered_tile_ranges, resolved_tile_lanes
from opencomp.core.models import FrameROI, FrameRequest, ViewerViewport


def test_normalized_frame_roi_clips_to_image_bounds() -> None:
    roi = normalized_frame_roi(FrameROI(x=-5, y=10, width=30, height=100), 20, 40)

    assert roi is not None
    assert roi.x == 0
    assert roi.y == 10
    assert roi.width == 20
    assert roi.height == 30


def test_lane_tile_ranges_and_viewport_ordering_stay_deterministic() -> None:
    payload = FrameRequest(
        node_id="Viewer1",
        frame=1001,
        viewport=ViewerViewport(x=0, y=120, width=100, height=80),
        tile_lanes=3,
        tile_lane=1,
    )
    ordered = ordered_tile_ranges(payload, 100, 400, 100)
    lane_tiles = lane_tile_ranges(payload, ordered)

    assert resolved_tile_lanes(payload) == 3
    assert ordered[0] == (0, 100, 100, 100)
    assert lane_tiles == [ordered[1]]
