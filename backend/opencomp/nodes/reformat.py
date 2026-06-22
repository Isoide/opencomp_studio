from __future__ import annotations

import numpy as np
from PIL import Image

from opencomp.core.bbox import default_bbox, intersect_bbox, scale_bbox
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
        width = int(node.params.get("width") or node.params.get("box_width") or source.width)
        height = int(node.params.get("height") or node.params.get("box_height") or source.height)
        if width <= 0 or height <= 0:
            raise NodeEvaluationError(node.id, "Reformat width and height must be positive.")
        preserve_channels = bool(node.params.get("preserve_channels", node.params.get("resize_channels", False)))
        resize_mode = str(node.params.get("resize") or node.params.get("resize_type") or "distort").lower()
        output_width, output_height, scale_x, scale_y = _reformat_geometry(source, width, height, resize_mode)
        centered = _truthy(node.params.get("centered", node.params.get("reformatCentered", True)))
        offset_x = int(round((width - output_width) * 0.5)) if centered else 0
        offset_y = int(round((height - output_height) * 0.5)) if centered else 0
        format_bbox = scale_bbox(source.format_bbox, scale_x, scale_y, source.width, source.height)
        data_window = scale_bbox(source.data_window, scale_x, scale_y, source.width, source.height)
        format_bbox["x"] += offset_x
        format_bbox["y"] += offset_y
        data_window["x"] += offset_x
        data_window["y"] += offset_y
        preserve_bbox = _truthy(node.params.get("preserve_bbox", node.params.get("pbb", node.params.get("preserveBB", False))))
        if not preserve_bbox:
            data_window = intersect_bbox(data_window, default_bbox(width, height))
        resized_source = _resize_float_rgba(
            source.data,
            output_width,
            output_height,
            source.data_window,
            data_window if offset_x == 0 and offset_y == 0 else None,
            filter_name=str(node.params.get("filter") or "bilinear"),
        )
        resized = _place_resized_image(resized_source, width, height, offset_x, offset_y)
        return ImageFrame(
            width=width,
            height=height,
            data=resized,
            channels=source.channels,
            channel_data=_place_resized_channels(
                _resize_channel_data(source.channel_data, output_width, output_height, str(node.params.get("filter") or "bilinear")),
                width,
                height,
                offset_x,
                offset_y,
            )
            if preserve_channels
            else {},
            pixel_aspect=float(node.params.get("pixel_aspect", node.params.get("boxPar", source.pixel_aspect))),
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={
                **source.metadata,
                "reformat": [width, height],
                "reformat/resize": resize_mode,
                "reformat/preserve_channels": preserve_channels,
                "reformat/preserve_bbox": preserve_bbox,
            },
            format_bbox=format_bbox if preserve_bbox else default_bbox(width, height),
            data_window=data_window,
        )


def _resize_float_rgba(
    data: np.ndarray,
    width: int,
    height: int,
    source_data_window: dict[str, int] | None = None,
    target_data_window: dict[str, int] | None = None,
    filter_name: str = "bilinear",
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
                filter_name=filter_name,
            )
        return np.ascontiguousarray(output)
    return _resize_float_rgba_full(data, width, height, filter_name=filter_name)


def _resize_float_rgba_full(data: np.ndarray, width: int, height: int, filter_name: str = "bilinear") -> np.ndarray:
    resample = _resample_filter(filter_name)
    channels = []
    for index in range(4):
        plane = Image.fromarray(data[:, :, index].astype(np.float32), mode="F")
        plane = plane.resize((width, height), resample)
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


def _resize_channel_data(channel_data: dict[str, np.ndarray], width: int, height: int, filter_name: str = "bilinear") -> dict[str, np.ndarray]:
    return {name: _resize_plane_or_group(data, width, height, filter_name=filter_name) for name, data in channel_data.items()}


def _resize_plane_or_group(data: np.ndarray, width: int, height: int, filter_name: str = "bilinear") -> np.ndarray:
    resample = _resample_filter(filter_name)
    if data.ndim == 2:
        plane = Image.fromarray(data.astype(np.float32), mode="F")
        return np.asarray(plane.resize((width, height), resample), dtype=np.float32)
    if data.ndim == 3:
        planes = []
        for index in range(data.shape[2]):
            plane = Image.fromarray(data[:, :, index].astype(np.float32), mode="F")
            planes.append(np.asarray(plane.resize((width, height), resample), dtype=np.float32))
        return np.stack(planes, axis=-1).astype(np.float32)
    return data.copy()


def _reformat_geometry(source: ImageFrame, width: int, height: int, resize_mode: str) -> tuple[int, int, float, float]:
    source_width = max(source.width, 1)
    source_height = max(source.height, 1)
    mode = resize_mode.replace(" ", "_").replace("-", "_")
    if mode in {"none", "no_resize"}:
        return source.width, source.height, 1.0, 1.0
    if mode == "width":
        scale = width / source_width
        output_height = max(1, int(round(source.height * scale)))
        return width, output_height, scale, scale
    if mode == "height":
        scale = height / source_height
        output_width = max(1, int(round(source.width * scale)))
        return output_width, height, scale, scale
    if mode == "fit":
        scale = min(width / source_width, height / source_height)
        return max(1, int(round(source.width * scale))), max(1, int(round(source.height * scale))), scale, scale
    if mode == "fill":
        scale = max(width / source_width, height / source_height)
        return max(1, int(round(source.width * scale))), max(1, int(round(source.height * scale))), scale, scale
    return width, height, width / source_width, height / source_height


def _place_resized_image(data: np.ndarray, width: int, height: int, offset_x: int, offset_y: int) -> np.ndarray:
    output = np.zeros((height, width, 4), dtype=np.float32)
    _copy_region(data, output, offset_x, offset_y)
    return np.ascontiguousarray(output)


def _place_resized_channels(channel_data: dict[str, np.ndarray], width: int, height: int, offset_x: int, offset_y: int) -> dict[str, np.ndarray]:
    placed: dict[str, np.ndarray] = {}
    for name, value in channel_data.items():
        shape_tail = value.shape[2:] if value.ndim == 3 else ()
        output = np.zeros((height, width, *shape_tail), dtype=np.float32)
        _copy_region(value, output, offset_x, offset_y)
        placed[name] = np.ascontiguousarray(output)
    return placed


def _copy_region(source: np.ndarray, output: np.ndarray, offset_x: int, offset_y: int) -> None:
    src_h, src_w = source.shape[:2]
    out_h, out_w = output.shape[:2]
    dst_x0 = max(0, offset_x)
    dst_y0 = max(0, offset_y)
    dst_x1 = min(out_w, offset_x + src_w)
    dst_y1 = min(out_h, offset_y + src_h)
    if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
        return
    src_x0 = dst_x0 - offset_x
    src_y0 = dst_y0 - offset_y
    output[dst_y0:dst_y1, dst_x0:dst_x1] = source[src_y0 : src_y0 + (dst_y1 - dst_y0), src_x0 : src_x0 + (dst_x1 - dst_x0)]


def _resample_filter(filter_name: str) -> Image.Resampling:
    normalized = str(filter_name or "bilinear").strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in {"impulse", "nearest", "none"}:
        return Image.Resampling.NEAREST
    if normalized in {"cubic", "keys", "simon", "rifman", "mitchell", "parzen"}:
        return Image.Resampling.BICUBIC
    return Image.Resampling.BILINEAR


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
