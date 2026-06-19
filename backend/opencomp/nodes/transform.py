from __future__ import annotations

import numpy as np

from opencomp.core.bbox import scale_bbox, transform_bbox
from opencomp.core.models import ImageFrame, Node
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError, require_input
from opencomp.nodes.reformat import _resize_channel_data, _resize_float_rgba


class ScaleNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        scale = float(node.params.get("scale") or 1.0)
        if scale <= 0:
            raise NodeEvaluationError(node.id, "Scale must be greater than zero.")
        preserve_channels = bool(node.params.get("preserve_channels", node.params.get("resize_channels", False)))
        width = max(1, int(round(source.width * scale)))
        height = max(1, int(round(source.height * scale)))
        data = _resize_float_rgba(source.data, width, height)
        return ImageFrame(
            width=width,
            height=height,
            data=data,
            channels=source.channels,
            channel_data=_resize_channel_data(source.channel_data, width, height) if preserve_channels else {},
            pixel_aspect=source.pixel_aspect,
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={
                **source.metadata,
                "scale/factor": scale,
                "scale/width": width,
                "scale/height": height,
                "scale/preserve_channels": preserve_channels,
            },
            format_bbox=scale_bbox(source.format_bbox, scale, scale, source.width, source.height),
            data_window=scale_bbox(source.data_window, scale, scale, source.width, source.height),
        )


class TransformNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        translate_x = float(node.params.get("translate_x", 0.0))
        translate_y = float(node.params.get("translate_y", 0.0))
        scale = float(node.params.get("scale", 1.0))
        if scale <= 0:
            raise NodeEvaluationError(node.id, "Transform scale must be greater than zero.")

        data = source.data
        if scale != 1.0:
            scaled = _resize_float_rgba(data, max(1, int(source.width * scale)), max(1, int(source.height * scale)))
        else:
            scaled = data.copy()

        output = np.zeros_like(data)
        src_h, src_w = scaled.shape[:2]
        x0 = int(round((source.width - src_w) / 2 + translate_x))
        y0 = int(round((source.height - src_h) / 2 + translate_y))
        dst_x0 = max(0, x0)
        dst_y0 = max(0, y0)
        dst_x1 = min(source.width, x0 + src_w)
        dst_y1 = min(source.height, y0 + src_h)
        if dst_x1 > dst_x0 and dst_y1 > dst_y0:
            src_x0 = dst_x0 - x0
            src_y0 = dst_y0 - y0
            output[dst_y0:dst_y1, dst_x0:dst_x1] = scaled[
                src_y0 : src_y0 + (dst_y1 - dst_y0),
                src_x0 : src_x0 + (dst_x1 - dst_x0),
            ]
        return ImageFrame(
            width=source.width,
            height=source.height,
            data=output,
            channels=source.channels,
            channel_data=source.copy_channel_data(),
            pixel_aspect=source.pixel_aspect,
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={
                **source.metadata,
                "transform/translate_x": translate_x,
                "transform/translate_y": translate_y,
                "transform/scale": scale,
            },
            format_bbox=source.format_bbox,
            data_window=transform_bbox(source.data_window, scale, translate_x, translate_y, source.width, source.height),
        )
