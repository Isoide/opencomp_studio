"""Unit tests for float viewer payload helper functions."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from opencomp.api.viewer_float import float_preview_header
from opencomp.core.models import FrameROI, FrameRequest, ViewerViewport


def test_float_preview_header_marks_roi_as_partial_and_preserves_requested_tile_height() -> None:
    payload = FrameRequest(
        node_id="Viewer1",
        frame=1001,
        channel="rgba",
        precision="float16",
        transfer_mode="float16-rgba",
        roi=FrameROI(x=10, y=12, width=20, height=16),
        viewport=ViewerViewport(x=0, y=0, width=128, height=64),
        tile_height=64,
    )
    result = SimpleNamespace(
        cache_hit=False,
        lookup_ms=0.0,
        evaluate_ms=12.34,
        resize_ms=0.0,
        entry=SimpleNamespace(
            rgba=np.zeros((32, 48, 4), dtype=np.float32),
            display_width=48,
            display_height=32,
            source_width=48,
            source_height=32,
            pixel_aspect=1.0,
            colorspace="ACES2065-1",
            apply_ocio=False,
            format_bbox={"x": 0, "y": 0, "width": 48, "height": 32},
            data_window={"x": 0, "y": 0, "width": 48, "height": 32},
            execution_backend="cpu",
            gpu_kernel_mode="cpu_fallback",
            gpu_upload_ms=0.0,
            gpu_dispatch_ms=0.0,
            gpu_download_ms=0.0,
            gpu_resize_ms=0.0,
            gpu_cache_hit=False,
        ),
    )

    header = float_preview_header(payload, result, stream_tiles=True, tile_height=64, tile_count_value=2, tile_count_total=4)

    assert header["partial"] is True
    assert header["roi"] == {"x": 10, "y": 12, "width": 20, "height": 16}
    assert header["updated_tile"] == {"x": 10, "y": 12, "width": 20, "height": 16}
    assert header["requested_tile_height"] == 64
    assert header["tile_count"] == 2
    assert header["tile_count_total"] == 4
    assert header["viewport"] == {"x": 0.0, "y": 0.0, "width": 128.0, "height": 64.0}
