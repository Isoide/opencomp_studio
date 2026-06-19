from __future__ import annotations

import numpy as np
from PIL import Image

from opencomp.core.bbox import scale_bbox
from opencomp.core.models import ImageFrame, Node
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError, require_input


class ReformatNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        width = int(node.params.get("width") or source.width)
        height = int(node.params.get("height") or source.height)
        if width <= 0 or height <= 0:
            raise NodeEvaluationError(node.id, "Reformat width and height must be positive.")
        preserve_channels = bool(node.params.get("preserve_channels", node.params.get("resize_channels", False)))
        scale_x = width / max(source.width, 1)
        scale_y = height / max(source.height, 1)
        format_bbox = scale_bbox(source.format_bbox, scale_x, scale_y, source.width, source.height)
        data_window = scale_bbox(source.data_window, scale_x, scale_y, source.width, source.height)
        resized = _resize_float_rgba(source.data, width, height, source.data_window, data_window)
        return ImageFrame(
            width=width,
            height=height,
            data=resized,
            channels=source.channels,
            channel_data=_resize_channel_data(source.channel_data, width, height) if preserve_channels else {},
            pixel_aspect=source.pixel_aspect,
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={**source.metadata, "reformat": [width, height], "reformat/preserve_channels": preserve_channels},
            format_bbox=format_bbox,
            data_window=data_window,
        )


def _resize_float_rgba(
    data: np.ndarray,
    width: int,
    height: int,
    source_data_window: dict[str, int] | None = None,
    target_data_window: dict[str, int] | None = None,
) -> np.ndarray:
    if _should_resize_data_window(data, source_data_window, target_data_window):
        source_box = source_data_window or {}
        target_box = target_data_window or {}
        src_x = max(0, int(source_box.get("x", 0)))
        src_y = max(0, int(source_box.get("y", 0)))
        src_w = max(0, min(int(source_box.get("width", data.shape[1])), data.shape[1] - src_x))
        src_h = max(0, min(int(source_box.get("height", data.shape[0])), data.shape[0] - src_y))
        dst_x = max(0, int(target_box.get("x", 0)))
        dst_y = max(0, int(target_box.get("y", 0)))
        dst_w = max(0, min(int(target_box.get("width", width)), width - dst_x))
        dst_h = max(0, min(int(target_box.get("height", height)), height - dst_y))
        output = np.zeros((height, width, 4), dtype=np.float32)
        if src_w > 0 and src_h > 0 and dst_w > 0 and dst_h > 0:
            output[dst_y : dst_y + dst_h, dst_x : dst_x + dst_w] = _resize_float_rgba_full(
                data[src_y : src_y + src_h, src_x : src_x + src_w],
                dst_w,
                dst_h,
            )
        return np.ascontiguousarray(output)
    return _resize_float_rgba_full(data, width, height)


def _resize_float_rgba_full(data: np.ndarray, width: int, height: int) -> np.ndarray:
    channels = []
    for index in range(4):
        plane = Image.fromarray(data[:, :, index].astype(np.float32), mode="F")
        plane = plane.resize((width, height), Image.Resampling.BILINEAR)
        channels.append(np.asarray(plane, dtype=np.float32))
    return np.stack(channels, axis=-1).astype(np.float32)


def _should_resize_data_window(
    data: np.ndarray,
    source_data_window: dict[str, int] | None,
    target_data_window: dict[str, int] | None,
) -> bool:
    if not source_data_window or not target_data_window:
        return False
    source_area = max(0, int(source_data_window.get("width", 0))) * max(0, int(source_data_window.get("height", 0)))
    full_area = max(1, int(data.shape[0]) * int(data.shape[1]))
    if source_area <= 0:
        return True
    if source_area >= full_area * 0.65:
        return False
    return True


def _resize_channel_data(channel_data: dict[str, np.ndarray], width: int, height: int) -> dict[str, np.ndarray]:
    return {name: _resize_plane_or_group(data, width, height) for name, data in channel_data.items()}


def _resize_plane_or_group(data: np.ndarray, width: int, height: int) -> np.ndarray:
    if data.ndim == 2:
        plane = Image.fromarray(data.astype(np.float32), mode="F")
        return np.asarray(plane.resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32)
    if data.ndim == 3:
        planes = []
        for index in range(data.shape[2]):
            plane = Image.fromarray(data[:, :, index].astype(np.float32), mode="F")
            planes.append(np.asarray(plane.resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32))
        return np.stack(planes, axis=-1).astype(np.float32)
    return data.copy()
