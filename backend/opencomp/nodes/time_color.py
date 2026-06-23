from __future__ import annotations

import colorsys
import json
import math
from typing import Any

import numpy as np

from opencomp.core.models import ImageFrame, Node
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError, require_input


class FrameHoldNode:
    def evaluate(self, node: Node, inputs: dict[str, ImageFrame], context: EvaluationContext) -> ImageFrame:
        first_frame = int(node.params.get("first_frame", node.params.get("first", context.frame)))
        increment = int(node.params.get("increment", 0))
        if increment == 0:
            source_frame = first_frame
        else:
            source_frame = first_frame + increment * math.floor((context.frame - first_frame) / increment)
        result = context.fetch_input("in", int(source_frame))
        return _clone_with_metadata(
            result,
            context.frame,
            {"time/source_frame": int(source_frame), "time/node": node.id},
        )


class FrameRangeNode:
    def evaluate(self, node: Node, inputs: dict[str, ImageFrame], context: EvaluationContext) -> ImageFrame:
        start = int(node.params.get("frame_start", node.params.get("first", context.settings.frame_start)))
        end = int(node.params.get("frame_end", node.params.get("last", context.settings.frame_end)))
        mode = str(node.params.get("mode", node.params.get("outside_mode", "hold"))).lower()
        source_frame = _frame_range_source_frame(context.frame, start, end, mode)
        if source_frame is None:
            return _black_frame(context, context.settings.width, context.settings.height, node.id, context.frame)
        result = context.fetch_input("in", int(source_frame))
        return _clone_with_metadata(
            result,
            context.frame,
            {"time/source_frame": int(source_frame), "time/mode": mode, "time/node": node.id},
        )


class RetimeNode:
    def evaluate(self, node: Node, inputs: dict[str, ImageFrame], context: EvaluationContext) -> ImageFrame:
        src_start = int(node.params.get("src_start", node.params.get("frame_start", context.settings.frame_start)))
        src_end = int(node.params.get("src_end", node.params.get("frame_end", context.settings.frame_end)))
        speed = float(node.params.get("speed", 1.0))
        reverse = _truthy(node.params.get("reverse", False))
        source_time = src_start + (context.frame - context.settings.frame_start) * speed
        if reverse:
            source_time = src_end - (source_time - src_start)
        source_time = _apply_warp_curve(node.params.get("warp_points"), source_time, src_start, src_end)
        filter_name = str(node.params.get("filter", "linear")).lower()
        if filter_name in {"none", "nearest"}:
            source_frame = int(round(source_time if filter_name == "nearest" else source_time))
            result = context.fetch_input("in", source_frame)
            return _clone_with_metadata(
                result,
                context.frame,
                {
                    "time/source_time": float(source_time),
                    "time/source_frame": int(source_frame),
                    "time/filter": filter_name,
                },
            )

        frame_a = math.floor(source_time)
        frame_b = math.ceil(source_time)
        mix = float(source_time - frame_a)
        image_a = context.fetch_input("in", int(frame_a))
        image_b = context.fetch_input("in", int(frame_b))
        data = (image_a.data * (1.0 - mix)) + (image_b.data * mix)
        metadata = {
            **image_a.metadata,
            "time/source_time": float(source_time),
            "time/source_frame_a": int(frame_a),
            "time/source_frame_b": int(frame_b),
            "time/filter": filter_name,
        }
        return ImageFrame(
            width=image_a.width,
            height=image_a.height,
            data=np.ascontiguousarray(data.astype(np.float32)),
            channels=image_a.channels,
            channel_data=image_a.copy_channel_data(),
            pixel_aspect=image_a.pixel_aspect,
            colorspace=image_a.colorspace,
            frame=context.frame,
            metadata=metadata,
            format_bbox=image_a.format_bbox,
            data_window=image_a.data_window,
        )


class ColorCorrectNode:
    def evaluate(self, node: Node, inputs: dict[str, ImageFrame], context: EvaluationContext) -> ImageFrame:
        source = require_input(node, inputs)
        saturation = float(node.params.get("saturation", 1.0))
        contrast = float(node.params.get("contrast", 1.0))
        gamma = max(float(node.params.get("gamma", 1.0)), 1e-6)
        gain = float(node.params.get("gain", 1.0))
        offset = float(node.params.get("offset", 0.0))
        mix = float(node.params.get("mix", 1.0))
        clamp = _truthy(node.params.get("clamp", False))

        data = source.data.copy()
        rgb = data[:, :, :3]
        luma = _luma(rgb)[..., None]
        rgb = luma + (rgb - luma) * saturation
        rgb = np.power(np.maximum(rgb / 0.18, 0.0), contrast) * 0.18
        rgb = np.power(np.maximum(rgb, 0.0), 1.0 / gamma)
        rgb = rgb * gain + offset
        if clamp:
            rgb = np.clip(rgb, 0.0, 1.0)
        data[:, :, :3] = source.data[:, :, :3] * (1.0 - mix) + rgb * mix
        return ImageFrame(
            width=source.width,
            height=source.height,
            data=np.ascontiguousarray(data.astype(np.float32)),
            channels=source.channels,
            channel_data=source.copy_channel_data(),
            pixel_aspect=source.pixel_aspect,
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={**source.metadata, "colorcorrect/mix": mix},
            format_bbox=source.format_bbox,
            data_window=source.data_window,
        )


class HueCorrectNode:
    def evaluate(self, node: Node, inputs: dict[str, ImageFrame], context: EvaluationContext) -> ImageFrame:
        source = require_input(node, inputs)
        curves = {
            "hue_shift": _curve_points(node.params.get("hue_shift_points")),
            "sat": _curve_points(node.params.get("sat_points")),
            "lum": _curve_points(node.params.get("lum_points")),
            "red": _curve_points(node.params.get("red_gain_points")),
            "green": _curve_points(node.params.get("green_gain_points")),
            "blue": _curve_points(node.params.get("blue_gain_points")),
            "red_sup": _curve_points(node.params.get("red_suppress_points")),
            "green_sup": _curve_points(node.params.get("green_suppress_points")),
            "blue_sup": _curve_points(node.params.get("blue_suppress_points")),
        }
        sat_threshold = float(node.params.get("sat_threshold", 0.0))
        mix = float(node.params.get("mix", 1.0))

        pixels = source.data.copy()
        rgb_in = np.clip(source.data[:, :, :3], 0.0, None)
        out = rgb_in.copy()
        for y in range(source.height):
            for x in range(source.width):
                r, g, b = [float(channel) for channel in rgb_in[y, x]]
                h, s, v = colorsys.rgb_to_hsv(r, g, b)
                hue_shift = _eval_curve(curves["hue_shift"], h, 0.0) % 1.0
                sat_gain = _eval_curve(curves["sat"], h, 1.0)
                lum_gain = _eval_curve(curves["lum"], h, 1.0)
                red_gain = _eval_curve(curves["red"], h, 1.0)
                green_gain = _eval_curve(curves["green"], h, 1.0)
                blue_gain = _eval_curve(curves["blue"], h, 1.0)
                r_sup = _eval_curve(curves["red_sup"], h, 1.0)
                g_sup = _eval_curve(curves["green_sup"], h, 1.0)
                b_sup = _eval_curve(curves["blue_sup"], h, 1.0)
                h = (h + hue_shift) % 1.0
                r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
                if s >= sat_threshold:
                    r2 *= red_gain
                    g2 *= green_gain
                    b2 *= blue_gain
                    luma = _luma(np.asarray([[[r2, g2, b2]]], dtype=np.float32))[0, 0]
                    if luma > 0:
                        target_luma = luma * lum_gain
                        scale = target_luma / max(luma, 1e-6)
                        r2 *= scale
                        g2 *= scale
                        b2 *= scale
                gray = (r2 + g2 + b2) / 3.0
                r2 = gray + (r2 - gray) * sat_gain
                g2 = gray + (g2 - gray) * sat_gain
                b2 = gray + (b2 - gray) * sat_gain
                r2 = _suppress_primary(r2, g2, b2, r_sup, "r")
                g2 = _suppress_primary(r2, g2, b2, g_sup, "g")
                b2 = _suppress_primary(r2, g2, b2, b_sup, "b")
                out[y, x] = [r2, g2, b2]
        pixels[:, :, :3] = source.data[:, :, :3] * (1.0 - mix) + out * mix
        return ImageFrame(
            width=source.width,
            height=source.height,
            data=np.ascontiguousarray(pixels.astype(np.float32)),
            channels=source.channels,
            channel_data=source.copy_channel_data(),
            pixel_aspect=source.pixel_aspect,
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={**source.metadata, "huecorrect/mix": mix},
            format_bbox=source.format_bbox,
            data_window=source.data_window,
        )


def _frame_range_source_frame(frame: int, start: int, end: int, mode: str) -> int | None:
    if start > end:
        start, end = end, start
    if start <= frame <= end or mode == "original":
        return frame
    if mode == "hold":
        return start if frame < start else end
    if mode == "black":
        return None
    span = end - start + 1
    if span <= 0:
        return frame
    if mode == "loop":
        return start + ((frame - start) % span)
    if mode == "bounce":
        cycle = max(1, span * 2 - 2)
        pos = (frame - start) % cycle
        return start + pos if pos < span else end - (pos - span + 2)
    return frame


def _apply_warp_curve(value: object, source_time: float, src_start: int, src_end: int) -> float:
    points = _curve_points(value)
    if not points:
        return source_time
    span = max(float(src_end - src_start), 1.0)
    normalized = (source_time - src_start) / span
    warped = _eval_curve(points, normalized, normalized)
    return src_start + warped * span


def _curve_points(value: object) -> list[tuple[float, float]]:
    if value is None or value == "":
        return []
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    points: list[tuple[float, float]] = []
    for item in raw:
        if isinstance(item, dict):
            x = float(item.get("x", 0.0))
            y = float(item.get("y", 0.0))
            points.append((x, y))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            points.append((float(item[0]), float(item[1])))
    return sorted(points, key=lambda item: item[0])


def _eval_curve(points: list[tuple[float, float]], x: float, default: float) -> float:
    if not points:
        return default
    wrapped = x % 1.0
    if wrapped <= points[0][0]:
        return points[0][1]
    if wrapped >= points[-1][0]:
        return points[-1][1]
    for index in range(1, len(points)):
        left_x, left_y = points[index - 1]
        right_x, right_y = points[index]
        if left_x <= wrapped <= right_x:
            t = 0.0 if right_x == left_x else (wrapped - left_x) / (right_x - left_x)
            return left_y + (right_y - left_y) * t
    return default


def _black_frame(context: EvaluationContext, width: int, height: int, node_id: str, frame: int) -> ImageFrame:
    data = np.zeros((height, width, 4), dtype=np.float32)
    return ImageFrame(
        width=width,
        height=height,
        data=data,
        colorspace=context.settings.working_colorspace,
        frame=frame,
        metadata={"time/black": True, "node": node_id},
    )


def _clone_with_metadata(source: ImageFrame, frame: int, extra_metadata: dict[str, Any]) -> ImageFrame:
    return ImageFrame(
        width=source.width,
        height=source.height,
        data=source.data,
        channels=list(source.channels),
        channel_data=source.copy_channel_data(),
        pixel_aspect=source.pixel_aspect,
        colorspace=source.colorspace,
        frame=frame,
        metadata={**source.metadata, **extra_metadata},
        format_bbox=dict(source.format_bbox or {}),
        data_window=dict(source.data_window or {}),
    )


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _suppress_primary(r: float, g: float, b: float, amount: float, channel: str) -> float:
    if channel == "r":
        minimum = min(g, b)
        return minimum + amount * max(r - minimum, 0.0)
    if channel == "g":
        minimum = min(r, b)
        return minimum + amount * max(g - minimum, 0.0)
    minimum = min(r, g)
    return minimum + amount * max(b - minimum, 0.0)
