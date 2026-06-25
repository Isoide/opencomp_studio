"""Float viewer websocket send-loop helpers for the backend API layer.

This module owns the transport-side send loop for float viewer websocket
responses: header delivery, tiled/native tile streaming, cancellation checks,
and byte/timing accumulation. Keeping this out of the main route file leaves
routes focused on request orchestration instead of socket bookkeeping.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from opencomp.api.viewer_transport import (
    encode_float_tile,
    float_precision,
    parallel_native_float_tiles,
    send_websocket_cancelled_quietly,
)
from opencomp.core.models import FrameRequest


@dataclass(slots=True)
class FloatStreamSendStats:
    sent_bytes: int
    ws_write_ms: float
    tile_encode_ms: float
    tile_render_ms: float


async def send_float_stream(
    websocket: WebSocket,
    *,
    scheduler,
    request_scope: str,
    request_id: str,
    payload: FrameRequest,
    header: dict[str, Any],
    data: bytes | None = None,
    rgba=None,
    tiles: list[tuple[int, int, int, int]] | None = None,
    native_eval_node_id: str | None = None,
    active_graph=None,
    evaluator=None,
    tile_workers: int = 1,
) -> FloatStreamSendStats | None:
    """Send one float viewer response and return accumulated send statistics.

    Returns ``None`` when the request is cancelled and a cancellation payload
    has already been sent to the client.
    """

    ws_write_ms = 0.0
    tile_encode_ms = float(header.get("tile_encode_ms") or 0.0)
    tile_render_ms = 0.0

    if not scheduler.is_current(request_scope, request_id):
        await send_websocket_cancelled_quietly(websocket, request_id)
        return None

    header_write_started = time.perf_counter()
    await websocket.send_text(json.dumps(header))
    ws_write_ms += (time.perf_counter() - header_write_started) * 1000.0

    sent_bytes = 0
    if payload.stream_tiles:
        tile_index = 0
        if bool(header.get("tile_native")) and native_eval_node_id is not None:
            if active_graph is None or evaluator is None or tiles is None:
                raise ValueError("Native tile streaming requires graph, evaluator, and tile ranges.")
            async for tile_header, tile_data, render_tile_ms, encode_tile_ms in parallel_native_float_tiles(
                active_graph,
                evaluator,
                payload,
                native_eval_node_id,
                tiles,
                tile_workers,
            ):
                if not scheduler.is_current(request_scope, request_id):
                    await send_websocket_cancelled_quietly(websocket, request_id)
                    return None
                tile_render_ms += render_tile_ms
                tile_encode_ms += encode_tile_ms
                tile_write_started = time.perf_counter()
                await websocket.send_text(json.dumps({**tile_header, "index": tile_index}))
                await websocket.send_bytes(tile_data)
                ws_write_ms += (time.perf_counter() - tile_write_started) * 1000.0
                sent_bytes += len(tile_data)
                tile_index += 1
        else:
            if rgba is None or tiles is None:
                raise ValueError("Tile stream fallback requires source pixels and tile ranges.")
            precision = float_precision(payload)
            for x, y, width, current_height in tiles:
                if not scheduler.is_current(request_scope, request_id):
                    await send_websocket_cancelled_quietly(websocket, request_id)
                    return None
                encode_started = time.perf_counter()
                tile_data = await asyncio.to_thread(
                    encode_float_tile,
                    rgba,
                    x,
                    y,
                    width,
                    current_height,
                    precision,
                )
                tile_encode_ms += (time.perf_counter() - encode_started) * 1000.0
                tile_header = {
                    "type": "viewer_float_tile",
                    "index": tile_index,
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": current_height,
                    "byte_length": len(tile_data),
                }
                tile_write_started = time.perf_counter()
                await websocket.send_text(json.dumps(tile_header))
                await websocket.send_bytes(tile_data)
                ws_write_ms += (time.perf_counter() - tile_write_started) * 1000.0
                sent_bytes += len(tile_data)
                tile_index += 1
        done_write_started = time.perf_counter()
        await websocket.send_text(json.dumps({"type": "viewer_float_tiles_done", "tiles": tile_index}))
        ws_write_ms += (time.perf_counter() - done_write_started) * 1000.0
    else:
        if data is None:
            raise ValueError("Non-tiled float streaming requires a contiguous payload.")
        if not scheduler.is_current(request_scope, request_id):
            await send_websocket_cancelled_quietly(websocket, request_id)
            return None
        data_write_started = time.perf_counter()
        await websocket.send_bytes(data)
        ws_write_ms += (time.perf_counter() - data_write_started) * 1000.0
        sent_bytes = len(data)

    return FloatStreamSendStats(
        sent_bytes=sent_bytes,
        ws_write_ms=ws_write_ms,
        tile_encode_ms=tile_encode_ms,
        tile_render_ms=tile_render_ms,
    )
