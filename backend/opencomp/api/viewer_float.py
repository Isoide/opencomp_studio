"""Float viewer payload and timing helpers for the backend API layer.

This module owns the float-stream formatting logic used by the viewer routes:
frame headers, encoded payload generation, tile-source selection, native tile
probing, and request timing shaping. Keeping these helpers separate leaves the
route module focused on transport flow rather than payload bookkeeping.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from opencomp.api.viewer_requests import build_preview_request, ensure_frame_request_id, float_preview_entry
from opencomp.api.viewer_transport import (
    encode_float_rgba,
    encoded_rgba_byte_length,
    float_precision,
    lane_tile_ranges,
    normalized_frame_roi,
    ordered_tile_ranges,
    resolved_tile_height,
    resolved_tile_lanes,
    tile_count,
    tile_native_supported,
)
from opencomp.core.evaluator import TILE_FULL_HEIGHT, TILE_FULL_WIDTH, GraphEvaluator
from opencomp.core.models import FrameRequest, Project, TileWindow
from opencomp.io.preview import MAIN_CHANNEL_ALIASES, preview_rgba_for_channel


def float_preview_header(
    payload: FrameRequest,
    result,
    *,
    stream_tiles: bool,
    tile_height: int | None = None,
    tile_count_value: int | None = None,
    tile_count_total: int | None = None,
) -> dict[str, Any]:
    """Build the metadata header describing one float viewer frame response."""

    entry = result.entry
    rgba = np.asarray(entry.rgba)
    precision = float_precision(payload)
    requested_tile_height = tile_height
    tile_height = int(rgba.shape[0])
    tile_width = int(rgba.shape[1])
    roi = normalized_frame_roi(payload.roi, entry.display_width, entry.display_height)
    height = entry.display_height if roi is not None else tile_height
    width = entry.display_width if roi is not None else tile_width
    resolved_height = resolved_tile_height(payload, height) if stream_tiles else None
    lane_count = resolved_tile_lanes(payload)
    header = {
        "type": "viewer_float_frame",
        "request_id": ensure_frame_request_id(payload),
        "node_id": payload.node_id,
        "frame": payload.frame,
        "viewer_input": payload.viewer_input,
        "channel": payload.channel or "rgba",
        "width": width,
        "height": height,
        "source_width": entry.source_width,
        "source_height": entry.source_height,
        "pixel_aspect": entry.pixel_aspect,
        "colorspace": entry.colorspace,
        "apply_ocio": entry.apply_ocio,
        "format_bbox": entry.format_bbox,
        "data_window": entry.data_window,
        "dtype": precision,
        "layout": "rgba",
        "byte_length": encoded_rgba_byte_length(tile_width, tile_height, precision),
        "cache_hit": result.cache_hit,
        "float_cache_lookup_ms": round(result.lookup_ms, 2),
        "evaluate_ms": round(result.evaluate_ms, 2),
        "node_eval_ms": round(result.evaluate_ms, 2),
        "resize_ms": round(result.resize_ms, 2),
        "execution_backend": entry.execution_backend,
        "gpu_kernel_mode": entry.gpu_kernel_mode,
        "gpu_upload_ms": round(entry.gpu_upload_ms, 2),
        "gpu_dispatch_ms": round(entry.gpu_dispatch_ms, 2),
        "gpu_download_ms": round(entry.gpu_download_ms, 2),
        "gpu_resize_ms": round(entry.gpu_resize_ms, 2),
        "gpu_cache_hit": entry.gpu_cache_hit,
        "tile_stream": stream_tiles,
        "tile_height": resolved_height,
        "tile_width": int(payload.tile_width or width),
        "tile_count": tile_count_value if tile_count_value is not None else (tile_count(height, resolved_height) if resolved_height else 0),
        "tile_count_total": tile_count_total if tile_count_total is not None else (tile_count(height, resolved_height) if resolved_height else 0),
        "tile_lanes": lane_count,
        "tile_lane": payload.tile_lane,
        "transfer_mode": payload.transfer_mode,
        "zoom": payload.zoom,
        "render_scale": payload.render_scale,
        "mipmap_level": payload.mipmap_level,
        "channels": payload.channels or [payload.channel or "rgba"],
        "layers": payload.layers,
        "priority": payload.priority,
        "cache_policy": payload.cache_policy,
        "storage": payload.storage,
    }
    if roi is not None:
        header["roi"] = roi.model_dump()
        header["partial"] = True
        header["updated_tile"] = roi.model_dump()
        header["tiles_received"] = 1
        header["tile_revision"] = payload.frame
    if payload.viewport is not None:
        header["viewport"] = payload.viewport.model_dump()
    if requested_tile_height is not None:
        header["requested_tile_height"] = requested_tile_height
    return header


def float_preview_payload(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest) -> tuple[dict[str, Any], bytes]:
    """Return the float-frame header and encoded RGBA payload for one request."""

    result = float_preview_entry(project, graph, evaluator, payload)
    encode_started = time.perf_counter()
    precision = float_precision(payload)
    data = encode_float_rgba(result.entry.rgba, precision)
    encode_ms = (time.perf_counter() - encode_started) * 1000.0
    evaluator.record_phase_timing(
        payload.node_id,
        "viewer.float_encode",
        encode_ms,
        {
            "width": int(result.entry.rgba.shape[1]),
            "height": int(result.entry.rgba.shape[0]),
            "dtype": precision,
            "bytes": len(data),
        },
    )
    header = float_preview_header(payload, result, stream_tiles=False)
    header["tile_encode_ms"] = round(encode_ms, 2)
    return header, data


def float_preview_tile_source(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest):
    """Return a tiled float-preview source, preferring native tile evaluation when possible."""

    native = float_preview_native_tile_source(project, graph, evaluator, payload)
    if native is not None:
        return native

    result = float_preview_entry(project, graph, evaluator, payload)
    tile_height_value = resolved_tile_height(payload, int(result.entry.rgba.shape[0]))
    tiles_total = ordered_tile_ranges(payload, int(result.entry.rgba.shape[1]), int(result.entry.rgba.shape[0]), tile_height_value)
    tiles = lane_tile_ranges(payload, tiles_total)
    header = float_preview_header(
        payload,
        result,
        stream_tiles=True,
        tile_height=tile_height_value,
        tile_count_value=len(tiles),
        tile_count_total=len(tiles_total),
    )
    header["tile_native"] = False
    return header, result.entry.rgba, tiles, None


def float_preview_native_tile_source(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest):
    """Return native tile-stream metadata when the graph can be evaluated tile-by-tile."""

    preview_request = build_preview_request(project, graph, evaluator, payload, payload.viewer_input)
    channel_name = (preview_request.channel or "rgba").strip().lower()
    if MAIN_CHANNEL_ALIASES.get(channel_name) is None:
        return None
    if preview_request.max_width is not None or preview_request.max_height is not None:
        return None
    if not tile_native_supported(graph, preview_request.eval_node_id):
        return None

    probe_started = time.perf_counter()
    probe = evaluator.evaluate_node_tile(
        graph,
        preview_request.eval_node_id,
        preview_request.frame,
        TileWindow(0, 0, 1, 1),
        preview_request.channel,
    )
    probe_ms = (time.perf_counter() - probe_started) * 1000.0
    width = int(probe.metadata.get(TILE_FULL_WIDTH) or probe.width)
    height = int(probe.metadata.get(TILE_FULL_HEIGHT) or probe.height)
    if width <= 0 or height <= 0:
        return None
    tile_height_value = resolved_tile_height(payload, height)
    tiles_total = ordered_tile_ranges(payload, width, height, tile_height_value)
    tiles = lane_tile_ranges(payload, tiles_total)
    _probe_rgba, apply_ocio = preview_rgba_for_channel(probe, preview_request.channel)
    precision = float_precision(payload)
    lane_count = resolved_tile_lanes(payload)
    header = {
        "type": "viewer_float_frame",
        "request_id": ensure_frame_request_id(payload),
        "node_id": payload.node_id,
        "frame": payload.frame,
        "viewer_input": payload.viewer_input,
        "channel": payload.channel or "rgba",
        "width": width,
        "height": height,
        "source_width": width,
        "source_height": height,
        "pixel_aspect": probe.pixel_aspect,
        "colorspace": probe.colorspace,
        "apply_ocio": apply_ocio,
        "format_bbox": probe.format_bbox,
        "data_window": probe.data_window,
        "dtype": precision,
        "layout": "rgba",
        "byte_length": encoded_rgba_byte_length(width, height, precision),
        "cache_hit": False,
        "float_cache_lookup_ms": 0.0,
        "evaluate_ms": round(probe_ms, 2),
        "node_eval_ms": round(probe_ms, 2),
        "resize_ms": 0.0,
        "tile_stream": True,
        "tile_native": True,
        "tile_height": tile_height_value,
        "tile_width": int(payload.tile_width or width),
        "tile_count": len(tiles),
        "tile_count_total": len(tiles_total),
        "tile_lanes": lane_count,
        "tile_lane": payload.tile_lane,
        "transfer_mode": payload.transfer_mode,
        "zoom": payload.zoom,
        "render_scale": payload.render_scale,
        "mipmap_level": payload.mipmap_level,
        "channels": payload.channels or [payload.channel or "rgba"],
        "layers": payload.layers,
        "priority": payload.priority,
        "cache_policy": payload.cache_policy,
        "storage": payload.storage,
        "requested_tile_height": tile_height_value,
    }
    if payload.viewport is not None:
        header["viewport"] = payload.viewport.model_dump()
    if payload.roi is not None:
        header["roi"] = payload.roi.model_dump()
    return header, None, tiles, preview_request.eval_node_id


def record_viewer_request_timing(evaluator: GraphEvaluator, payload: FrameRequest, timing: dict[str, Any]) -> None:
    """Record one normalized viewer transport timing entry on the evaluator."""

    evaluator.record_request_timing(
        {
            "type": "viewer_frame",
            "request_id": ensure_frame_request_id(payload),
            "node_id": payload.node_id,
            "frame": payload.frame,
            "viewer_input": payload.viewer_input,
            "compare_input": payload.compare_input,
            "compare_mode": payload.compare_mode,
            "channel": payload.channel or "rgba",
            "display": payload.display,
            "view": payload.view,
            "gain": payload.gain,
            "saturation": payload.saturation,
            "fstop": payload.fstop,
            "render_scale": payload.render_scale,
            "mipmap_level": payload.mipmap_level,
            "channels": payload.channels or [payload.channel or "rgba"],
            "layers": payload.layers,
            "priority": payload.priority,
            "cache_policy": payload.cache_policy,
            "storage": payload.storage,
            **{key: round(value, 2) if isinstance(value, float) else value for key, value in timing.items()},
        }
    )
