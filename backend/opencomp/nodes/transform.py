from __future__ import annotations

import math
import numpy as np
from PIL import Image

from opencomp.core.bbox import affine_bbox, scale_bbox
from opencomp.core.models import ImageFrame, Node
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError, require_input
from opencomp.nodes.reformat import _resize_channel_data, _resize_float_rgba, _resample_filter


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
        scale_value = float(node.params.get("scale", 1.0))
        scale_x = float(node.params.get("scale_x", node.params.get("x_scale", scale_value)))
        scale_y = float(node.params.get("scale_y", node.params.get("y_scale", scale_value)))
        amount = float(node.params.get("transform_amount", node.params.get("amount", 1.0)))
        rotate = float(node.params.get("rotate", node.params.get("rotation", 0.0)))
        if scale_x <= 0 or scale_y <= 0:
            raise NodeEvaluationError(node.id, "Transform scale must be greater than zero.")
        scale_x = 1.0 + (scale_x - 1.0) * amount
        scale_y = 1.0 + (scale_y - 1.0) * amount
        translate_x *= amount
        translate_y *= amount
        rotate *= amount
        center_x = float(node.params.get("center_x", source.width / 2.0))
        center_y = float(node.params.get("center_y", source.height / 2.0))
        forward = _transform_matrix(translate_x, translate_y, scale_x, scale_y, rotate, center_x, center_y)
        if _truthy(node.params.get("invert", False)):
            forward = _invert_affine(forward)
        data = _affine_rgba(
            source.data,
            source.width,
            source.height,
            forward,
            str(node.params.get("filter") or "bilinear"),
        )
        if _truthy(node.params.get("clamp", False)):
            data = np.clip(data, np.nanmin(source.data), np.nanmax(source.data))
        preserve_channels = _truthy(node.params.get("preserve_channels", node.params.get("resize_channels", False)))
        channel_data = (
            {
                name: _affine_plane_or_group(value, source.width, source.height, forward, str(node.params.get("filter") or "bilinear"))
                for name, value in source.channel_data.items()
            }
            if preserve_channels
            else {}
        )
        return ImageFrame(
            width=source.width,
            height=source.height,
            data=data,
            channels=source.channels,
            channel_data=channel_data,
            pixel_aspect=source.pixel_aspect,
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={
                **source.metadata,
                "transform/translate_x": translate_x,
                "transform/translate_y": translate_y,
                "transform/scale_x": scale_x,
                "transform/scale_y": scale_y,
                "transform/rotate": rotate,
                "transform/center_x": center_x,
                "transform/center_y": center_y,
                "transform/preserve_channels": preserve_channels,
            },
            format_bbox=source.format_bbox,
            data_window=affine_bbox(source.data_window, forward, source.width, source.height),
        )


def _transform_matrix(
    translate_x: float,
    translate_y: float,
    scale_x: float,
    scale_y: float,
    rotate_degrees: float,
    center_x: float,
    center_y: float,
) -> tuple[float, float, float, float, float, float]:
    radians = math.radians(rotate_degrees)
    cos_value = math.cos(radians)
    sin_value = math.sin(radians)
    a = cos_value * scale_x
    b = -sin_value * scale_y
    d = sin_value * scale_x
    e = cos_value * scale_y
    c = center_x + translate_x - (a * center_x) - (b * center_y)
    f = center_y + translate_y - (d * center_x) - (e * center_y)
    return (a, b, c, d, e, f)


def _affine_rgba(
    data: np.ndarray,
    width: int,
    height: int,
    forward: tuple[float, float, float, float, float, float],
    filter_name: str,
) -> np.ndarray:
    channels = [_affine_plane(data[:, :, index], width, height, forward, filter_name) for index in range(4)]
    return np.ascontiguousarray(np.stack(channels, axis=-1).astype(np.float32))


def _affine_plane_or_group(
    value: np.ndarray,
    width: int,
    height: int,
    forward: tuple[float, float, float, float, float, float],
    filter_name: str,
) -> np.ndarray:
    if value.ndim == 2:
        return _affine_plane(value, width, height, forward, filter_name)
    if value.ndim == 3:
        return np.stack([_affine_plane(value[:, :, index], width, height, forward, filter_name) for index in range(value.shape[2])], axis=-1)
    return value.copy()


def _affine_plane(
    plane: np.ndarray,
    width: int,
    height: int,
    forward: tuple[float, float, float, float, float, float],
    filter_name: str,
) -> np.ndarray:
    inverse = _invert_affine(forward)
    image = Image.fromarray(plane.astype(np.float32), mode="F")
    transformed = image.transform((width, height), Image.Transform.AFFINE, inverse, resample=_resample_filter(filter_name), fillcolor=0.0)
    return np.asarray(transformed, dtype=np.float32)


def _invert_affine(matrix: tuple[float, float, float, float, float, float]) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = matrix
    determinant = a * e - b * d
    if abs(determinant) <= 1e-12:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    inv_a = e / determinant
    inv_b = -b / determinant
    inv_d = -d / determinant
    inv_e = a / determinant
    inv_c = -(inv_a * c + inv_b * f)
    inv_f = -(inv_d * c + inv_e * f)
    return (inv_a, inv_b, inv_c, inv_d, inv_e, inv_f)


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
