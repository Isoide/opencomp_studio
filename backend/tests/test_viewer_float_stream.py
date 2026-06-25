"""Unit tests for float viewer websocket send-loop helpers."""

from __future__ import annotations

import asyncio
import json

import numpy as np

from opencomp.api.viewer_float_stream import FloatStreamSendStats, send_float_stream
from opencomp.core.models import FrameRequest


class _FakeWebSocket:
    def __init__(self) -> None:
        self.text_messages: list[str] = []
        self.binary_messages: list[bytes] = []
        self.closed_codes: list[int] = []

    async def send_text(self, message: str) -> None:
        self.text_messages.append(message)

    async def send_bytes(self, data: bytes) -> None:
        self.binary_messages.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed_codes.append(code)


class _FakeScheduler:
    def __init__(self, current: bool = True) -> None:
        self.current = current

    def is_current(self, scope: str, request_id: str) -> bool:
        return self.current


async def _send_non_tiled_frame() -> tuple[_FakeWebSocket, FloatStreamSendStats | None]:
    websocket = _FakeWebSocket()
    payload = FrameRequest(node_id="Viewer1", frame=1001, precision="float16", transfer_mode="float16-rgba")
    header = {"dtype": "float16", "tile_native": False}
    data = b"\x00\x01\x02\x03"
    stats = await send_float_stream(
        websocket,
        scheduler=_FakeScheduler(),
        request_scope="Viewer1:0:rgba:none",
        request_id="req-1",
        payload=payload,
        header=header,
        data=data,
    )
    return websocket, stats


async def _send_tiled_fallback_frame() -> tuple[_FakeWebSocket, FloatStreamSendStats | None]:
    websocket = _FakeWebSocket()
    payload = FrameRequest(
        node_id="Viewer1",
        frame=1001,
        precision="float16",
        transfer_mode="float16-rgba",
        stream_tiles=True,
    )
    header = {"dtype": "float16", "tile_native": False}
    rgba = np.zeros((2, 2, 4), dtype=np.float32)
    tiles = [(0, 0, 2, 1), (0, 1, 2, 1)]
    stats = await send_float_stream(
        websocket,
        scheduler=_FakeScheduler(),
        request_scope="Viewer1:0:rgba:none",
        request_id="req-2",
        payload=payload,
        header=header,
        rgba=rgba,
        tiles=tiles,
    )
    return websocket, stats


def test_send_float_stream_sends_header_and_contiguous_payload() -> None:
    websocket, stats = asyncio.run(_send_non_tiled_frame())

    assert isinstance(stats, FloatStreamSendStats)
    assert len(websocket.text_messages) == 1
    assert json.loads(websocket.text_messages[0])["dtype"] == "float16"
    assert websocket.binary_messages == [b"\x00\x01\x02\x03"]
    assert stats.sent_bytes == 4


def test_send_float_stream_sends_tile_headers_and_done_message() -> None:
    websocket, stats = asyncio.run(_send_tiled_fallback_frame())

    assert isinstance(stats, FloatStreamSendStats)
    assert len(websocket.binary_messages) == 2
    messages = [json.loads(message) for message in websocket.text_messages]
    assert messages[0]["dtype"] == "float16"
    assert messages[1]["type"] == "viewer_float_tile"
    assert messages[2]["type"] == "viewer_float_tile"
    assert messages[3] == {"type": "viewer_float_tiles_done", "tiles": 2}
    assert stats.sent_bytes == sum(len(message) for message in websocket.binary_messages)


async def _send_cancelled_frame() -> tuple[_FakeWebSocket, FloatStreamSendStats | None]:
    websocket = _FakeWebSocket()
    payload = FrameRequest(node_id="Viewer1", frame=1001, precision="float16", transfer_mode="float16-rgba")
    header = {"dtype": "float16", "tile_native": False}
    stats = await send_float_stream(
        websocket,
        scheduler=_FakeScheduler(current=False),
        request_scope="Viewer1:0:rgba:none",
        request_id="req-3",
        payload=payload,
        header=header,
        data=b"\x00\x01",
    )
    return websocket, stats


def test_send_float_stream_reports_cancellation_before_sending_payload() -> None:
    websocket, stats = asyncio.run(_send_cancelled_frame())

    assert stats is None
    assert websocket.binary_messages == []
    assert websocket.closed_codes == [1000]
    payload = json.loads(websocket.text_messages[0])
    assert payload["type"] == "viewer_request_cancelled"
    assert payload["request_id"] == "req-3"
