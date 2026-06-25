"""Viewer transport and payload helpers for the OpenComp backend API.

This module contains the calculation-heavy utilities used by the viewer HTTP
and websocket routes: float payload encoding, tile ordering/splitting, native
tile rendering helpers, and small websocket response helpers. Keeping these
pieces outside the route module makes the transport layer easier to scan.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import numpy as np
from fastapi import WebSocket

from opencomp.api.context import node_error_payload
from opencomp.core.evaluator import TILE_LOCAL_NODE_TYPES, GraphEvaluator, _viewer_input_edges
from opencomp.core.models import FrameROI, FrameRequest, TileWindow
from opencomp.io.preview import preview_rgba_for_channel


def normalized_frame_roi(roi: FrameROI | None, width: int, height: int) -> FrameROI | None:
    """Clip a requested ROI to valid image bounds and discard empty regions."""

    if roi is None:
        return None
    x = max(0, min(int(roi.x), max(0, width - 1)))
    y = max(0, min(int(roi.y), max(0, height - 1)))
    clipped_width = min(max(0, int(roi.width)), max(0, width - x))
    clipped_height = min(max(0, int(roi.height)), max(0, height - y))
    if clipped_width <= 0 or clipped_height <= 0:
        return None
    return FrameROI(x=x, y=y, width=clipped_width, height=clipped_height)


def float_precision(payload: FrameRequest) -> str:
    """Return the effective transport precision for a float viewer request."""

    if payload.precision in {"float16", "uint8", "rgb10a2"}:
        return payload.precision
    return "float32"


def precision_bytes(precision: str) -> int:
    """Return bytes per channel element for a transport precision."""

    if precision == "float16":
        return 2
    if precision in {"uint8", "rgb10a2"}:
        return 1
    return 4


def encoded_rgba_byte_length(width: int, height: int, precision: str) -> int:
    """Return the encoded byte size for a contiguous RGBA buffer."""

    if precision == "rgb10a2":
        return width * height * 4
    return width * height * 4 * precision_bytes(precision)


def resolved_tile_height(payload: FrameRequest, image_height: int) -> int:
    """Resolve the requested tile height to a bounded image-safe value."""

    if payload.tile_height is not None:
        return max(1, min(int(payload.tile_height), max(1, image_height)))
    return max(1, min(128, max(1, image_height)))


def resolved_tile_lanes(payload: FrameRequest) -> int:
    """Resolve the number of parallel websocket tile lanes for a request."""

    return max(1, min(int(payload.tile_lanes or 1), 8))


def ordered_tile_ranges(payload: FrameRequest, image_width: int, image_height: int, tile_height: int) -> list[tuple[int, int, int, int]]:
    """Return tiles ordered to prioritize the visible viewport when present."""

    tiles = [(0, y, image_width, min(tile_height, image_height - y)) for y in range(0, image_height, tile_height)]
    viewport = payload.viewport
    if viewport is None or viewport.height <= 0:
        return tiles
    visible_top = max(0, min(image_height, int(viewport.y)))
    visible_bottom = max(visible_top, min(image_height, int(viewport.y + viewport.height)))
    visible_center = (visible_top + visible_bottom) * 0.5

    def score(tile: tuple[int, int, int, int]) -> tuple[int, float, int]:
        _x, y, _width, height = tile
        tile_center = y + height * 0.5
        intersects = y < visible_bottom and (y + height) > visible_top
        return (0 if intersects else 1, abs(tile_center - visible_center), y)

    return sorted(tiles, key=score)


def lane_tile_ranges(payload: FrameRequest, tiles: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Filter a tile list down to the subset assigned to one websocket lane."""

    lane_count = resolved_tile_lanes(payload)
    lane_index = payload.tile_lane
    if lane_index is None or lane_count <= 1:
        return tiles
    resolved_lane = max(0, min(int(lane_index), lane_count - 1))
    return [tile for index, tile in enumerate(tiles) if index % lane_count == resolved_lane]


def tile_count(image_height: int, tile_height: int | None) -> int:
    """Return the number of vertical tiles implied by an image/tile height."""

    if not tile_height:
        return 0
    return max(0, (max(0, image_height) + tile_height - 1) // tile_height)


def tile_native_supported(graph, node_id: str, visiting: set[str] | None = None) -> bool:
    """Return whether a graph branch can be evaluated directly in tile mode."""

    if visiting is None:
        visiting = set()
    if node_id in visiting:
        return False
    node = graph.nodes.get(node_id)
    if node is None:
        return False
    node_type = node.type.lower()
    if node_type not in TILE_LOCAL_NODE_TYPES:
        return False
    visiting.add(node_id)
    input_edges = _viewer_input_edges(graph, node_id) if node_type == "viewer" else graph.incoming_edges(node_id)
    for edge in input_edges:
        if not tile_native_supported(graph, edge.source_node, visiting):
            visiting.remove(node_id)
            return False
    visiting.remove(node_id)
    return True


def encode_float_rgba(rgba: np.ndarray, precision: str) -> bytes:
    """Encode RGBA float data into the requested transport precision."""

    if precision == "uint8":
        data = (np.clip(np.asarray(rgba, dtype=np.float32), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
        return np.ascontiguousarray(data).tobytes(order="C")
    if precision == "rgb10a2":
        image = np.clip(np.asarray(rgba, dtype=np.float32), 0.0, 1.0)
        rgb = (image[:, :, :3] * 1023.0 + 0.5).astype(np.uint32)
        alpha = (image[:, :, 3] * 3.0 + 0.5).astype(np.uint32)
        packed = rgb[:, :, 0] | (rgb[:, :, 1] << np.uint32(10)) | (rgb[:, :, 2] << np.uint32(20)) | (alpha << np.uint32(30))
        return np.ascontiguousarray(packed.astype(np.uint32, copy=False)).tobytes(order="C")
    if precision == "float16":
        return np.ascontiguousarray(np.asarray(rgba, dtype=np.float16)).tobytes(order="C")
    return np.ascontiguousarray(np.asarray(rgba, dtype=np.float32)).tobytes(order="C")


def encode_float_tile(rgba: np.ndarray, x: int, y: int, width: int, height: int, precision: str) -> bytes:
    """Encode one rectangular tile from a larger RGBA image buffer."""

    return encode_float_rgba(rgba[y : y + height, x : x + width], precision)


async def parallel_native_float_tiles(
    graph,
    evaluator: GraphEvaluator,
    payload: FrameRequest,
    eval_node_id: str,
    tiles: list[tuple[int, int, int, int]],
    worker_count: int,
):
    """Yield native tile renders in completion order across bounded workers."""

    workers = max(1, min(int(worker_count or 1), 16))
    semaphore = asyncio.Semaphore(workers)

    async def run_tile(tile: tuple[int, int, int, int]):
        async with semaphore:
            return await asyncio.to_thread(render_native_float_tile, graph, evaluator, payload, eval_node_id, tile)

    tasks = [asyncio.create_task(run_tile(tile)) for tile in tiles]
    try:
        for task in asyncio.as_completed(tasks):
            yield await task
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


def render_native_float_tile(
    graph,
    evaluator: GraphEvaluator,
    payload: FrameRequest,
    eval_node_id: str,
    tile: tuple[int, int, int, int],
) -> tuple[dict[str, Any], bytes, float, float]:
    """Render and encode one tile from the native tile-evaluation path."""

    x, y, width, height = tile
    render_started = time.perf_counter()
    image = evaluator.evaluate_node_tile(graph, eval_node_id, payload.frame, TileWindow(x, y, width, height), payload.channel)
    rgba, _apply_ocio = preview_rgba_for_channel(image, payload.channel)
    render_ms = (time.perf_counter() - render_started) * 1000.0
    encode_started = time.perf_counter()
    data = encode_float_rgba(rgba, float_precision(payload))
    encode_ms = (time.perf_counter() - encode_started) * 1000.0
    return (
        {
            "type": "viewer_float_tile",
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "byte_length": len(data),
        },
        data,
        render_ms,
        encode_ms,
    )


async def close_websocket_quietly(websocket: WebSocket, code: int = 1000) -> None:
    """Close a websocket and ignore transport-level close failures."""

    try:
        await websocket.close(code=code)
    except Exception:
        return


async def send_websocket_error_quietly(websocket: WebSocket, error: Exception | str) -> None:
    """Send a structured websocket error payload and then close the socket."""

    payload = node_error_payload(error) if isinstance(error, Exception) else {"detail": str(error), "kind": "Error"}
    try:
        await websocket.send_text(json.dumps({"type": "error", **payload}))
    except Exception:
        pass
    await close_websocket_quietly(websocket, code=1011)


async def send_websocket_cancelled_quietly(websocket: WebSocket, request_id: str) -> None:
    """Send a structured cancellation notification and then close the socket."""

    try:
        await websocket.send_text(json.dumps({"type": "viewer_request_cancelled", "request_id": request_id}))
    except Exception:
        pass
    await close_websocket_quietly(websocket)
