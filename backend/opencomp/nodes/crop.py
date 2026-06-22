from __future__ import annotations

import numpy as np

from opencomp.core.bbox import default_bbox, intersect_bbox, translate_bbox
from opencomp.core.models import ImageFrame, Node
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError, require_input


class CropNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        crop_box = _crop_box(node, source, context)
        reformat = _truthy(node.params.get("reformat", False))
        black_outside = _truthy(node.params.get("black_outside", node.params.get("blackOutside", True)))

        if reformat:
            return _crop_to_new_format(source, crop_box, node, context)
        return _crop_inside_current_format(source, crop_box, node, context, black_outside)


def _crop_inside_current_format(
    source: ImageFrame,
    crop_box: dict[str, int],
    node: Node,
    context: EvaluationContext,
    black_outside: bool,
) -> ImageFrame:
    data = source.data.copy()
    channel_data = source.copy_channel_data()
    source_format = default_bbox(source.width, source.height)
    data_window = intersect_bbox(source.data_window, crop_box)
    visible_crop = intersect_bbox(crop_box, source_format)
    if black_outside:
        mask = np.zeros((source.height, source.width), dtype=bool)
        x0 = max(0, visible_crop["x"])
        y0 = max(0, visible_crop["y"])
        x1 = min(source.width, visible_crop["x"] + visible_crop["width"])
        y1 = min(source.height, visible_crop["y"] + visible_crop["height"])
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
        data[~mask] = 0.0
        channel_data = {name: _black_outside_plane(value, mask) for name, value in channel_data.items()}
    return ImageFrame(
        width=source.width,
        height=source.height,
        data=np.ascontiguousarray(data),
        channels=source.channels,
        channel_data=channel_data,
        pixel_aspect=source.pixel_aspect,
        colorspace=source.colorspace,
        frame=context.frame,
        metadata={**source.metadata, "crop": crop_box, "crop/reformat": False, "crop/black_outside": black_outside},
        format_bbox=source.format_bbox,
        data_window=data_window,
    )


def _crop_to_new_format(source: ImageFrame, crop_box: dict[str, int], node: Node, context: EvaluationContext) -> ImageFrame:
    width = max(1, int(crop_box["width"]))
    height = max(1, int(crop_box["height"]))
    output = np.zeros((height, width, 4), dtype=np.float32)
    src_x0 = max(0, crop_box["x"])
    src_y0 = max(0, crop_box["y"])
    src_x1 = min(source.width, crop_box["x"] + crop_box["width"])
    src_y1 = min(source.height, crop_box["y"] + crop_box["height"])
    dst_x0 = max(0, -crop_box["x"])
    dst_y0 = max(0, -crop_box["y"])
    if src_x1 > src_x0 and src_y1 > src_y0:
        dst_x1 = dst_x0 + (src_x1 - src_x0)
        dst_y1 = dst_y0 + (src_y1 - src_y0)
        output[dst_y0:dst_y1, dst_x0:dst_x1] = source.data[src_y0:src_y1, src_x0:src_x1]
    data_window = translate_bbox(intersect_bbox(source.data_window, crop_box), -crop_box["x"], -crop_box["y"], width, height)
    channel_data = {
        name: _crop_plane_to_new_format(value, crop_box, width, height, source.width, source.height)
        for name, value in source.channel_data.items()
    }
    return ImageFrame(
        width=width,
        height=height,
        data=np.ascontiguousarray(output),
        channels=source.channels,
        channel_data=channel_data,
        pixel_aspect=float(node.params.get("pixel_aspect", source.pixel_aspect)),
        colorspace=source.colorspace,
        frame=context.frame,
        metadata={**source.metadata, "crop": crop_box, "crop/reformat": True},
        format_bbox=default_bbox(width, height),
        data_window=data_window,
    )


def _crop_box(node: Node, source: ImageFrame, context: EvaluationContext) -> dict[str, int]:
    extent = str(node.params.get("extent") or "size").strip().lower()
    if extent in {"default", "source", "rod", "data_window"}:
        return dict(source.data_window or default_bbox(source.width, source.height))
    if extent in {"project", "root"}:
        return default_bbox(context.settings.width, context.settings.height)
    width = int(node.params.get("width") or node.params.get("w") or source.width)
    height = int(node.params.get("height") or node.params.get("h") or source.height)
    if width <= 0 or height <= 0:
        raise NodeEvaluationError(node.id, "Crop width and height must be positive.")
    return {
        "x": int(round(float(node.params.get("x", node.params.get("left", 0))))),
        "y": int(round(float(node.params.get("y", node.params.get("top", 0))))),
        "width": width,
        "height": height,
    }


def _black_outside_plane(value: np.ndarray, mask: np.ndarray) -> np.ndarray:
    output = np.asarray(value, dtype=np.float32).copy()
    if output.ndim == 2:
        output[~mask] = 0.0
    elif output.ndim == 3:
        output[~mask, :] = 0.0
    return np.ascontiguousarray(output)


def _crop_plane_to_new_format(
    value: np.ndarray,
    crop_box: dict[str, int],
    width: int,
    height: int,
    source_width: int,
    source_height: int,
) -> np.ndarray:
    shape_tail = value.shape[2:] if value.ndim == 3 else ()
    output = np.zeros((height, width, *shape_tail), dtype=np.float32)
    src_x0 = max(0, crop_box["x"])
    src_y0 = max(0, crop_box["y"])
    src_x1 = min(source_width, crop_box["x"] + crop_box["width"])
    src_y1 = min(source_height, crop_box["y"] + crop_box["height"])
    dst_x0 = max(0, -crop_box["x"])
    dst_y0 = max(0, -crop_box["y"])
    if src_x1 > src_x0 and src_y1 > src_y0:
        dst_x1 = dst_x0 + (src_x1 - src_x0)
        dst_y1 = dst_y0 + (src_y1 - src_y0)
        output[dst_y0:dst_y1, dst_x0:dst_x1] = value[src_y0:src_y1, src_x0:src_x1]
    return np.ascontiguousarray(output)


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
