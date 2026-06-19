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
        resized = _resize_float_rgba(source.data, width, height)
        scale_x = width / max(source.width, 1)
        scale_y = height / max(source.height, 1)
        return ImageFrame(
            width=width,
            height=height,
            data=resized,
            channels=source.channels,
            channel_data=_resize_channel_data(source.channel_data, width, height),
            pixel_aspect=source.pixel_aspect,
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={**source.metadata, "reformat": [width, height]},
            format_bbox=scale_bbox(source.format_bbox, scale_x, scale_y, source.width, source.height),
            data_window=scale_bbox(source.data_window, scale_x, scale_y, source.width, source.height),
        )


def _resize_float_rgba(data: np.ndarray, width: int, height: int) -> np.ndarray:
    channels = []
    for index in range(4):
        plane = Image.fromarray(data[:, :, index].astype(np.float32), mode="F")
        plane = plane.resize((width, height), Image.Resampling.BILINEAR)
        channels.append(np.asarray(plane, dtype=np.float32))
    return np.stack(channels, axis=-1).astype(np.float32)


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
