from __future__ import annotations

import re
import time
from pathlib import Path

import numpy as np

from opencomp.core.channel_demand import BASE_READ_CHANNELS, ChannelDemand
from opencomp.core.models import ImageFrame, Node
from opencomp.io.image_reader import read_image
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError


class ReadNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        path = str(node.params.get("path") or node.params.get("file") or "builtin://gradient")
        colorspace = str(node.params.get("colorspace") or context.settings.working_colorspace)
        read_channels = _read_channels(node, context)
        read_frame = _mapped_frame(node, context.frame)
        read_frame = _range_frame(node, read_frame)
        if read_frame is None:
            return _black_frame(node, context)
        try:
            started = time.perf_counter()
            image = read_image(path, frame=read_frame, colorspace=colorspace, read_channels=read_channels)
            context.record_metric(
                node.id,
                "read.image",
                (time.perf_counter() - started) * 1000.0,
                {
                    "path": path,
                    "frame": read_frame,
                    "colorspace": colorspace,
                    "width": image.width,
                    "height": image.height,
                    "read_channels": "all" if read_channels is None else read_channels,
                    "channel_demand": _demand_label(context.requested_channels),
                    "loaded_channel_groups": len(image.channel_data),
                },
            )
            return image
        except Exception as exc:
            missing_policy = str(node.params.get("missing_frames") or node.params.get("on_error") or "error").lower()
            if missing_policy == "black":
                return _black_frame(node, context)
            if missing_policy in {"nearest", "nearest frame", "nearest_frame"}:
                nearest = _nearest_existing_frame(path, read_frame, node)
                if nearest is not None:
                    try:
                        started = time.perf_counter()
                        image = read_image(path, frame=nearest, colorspace=colorspace, read_channels=read_channels)
                        context.record_metric(
                            node.id,
                            "read.image",
                            (time.perf_counter() - started) * 1000.0,
                            {
                                "path": path,
                                "frame": nearest,
                                "requested_frame": read_frame,
                                "colorspace": colorspace,
                                "width": image.width,
                                "height": image.height,
                                "read_channels": "all" if read_channels is None else read_channels,
                                "channel_demand": _demand_label(context.requested_channels),
                                "loaded_channel_groups": len(image.channel_data),
                            },
                        )
                        return image
                    except Exception:
                        pass
            raise NodeEvaluationError(node.id, str(exc)) from exc


def _mapped_frame(node: Node, frame: int) -> int:
    mode = str(node.params.get("frame_mode") or "expression").lower()
    if mode == "offset":
        return frame + int(node.params.get("frame_offset") or node.params.get("frame") or 0)
    if mode in {"start", "start at", "start_at"}:
        first = int(node.params.get("frame_start") or node.params.get("first") or frame)
        start_at = int(node.params.get("frame_start_at") or node.params.get("frame") or first)
        return first + (frame - start_at)
    if mode in {"frame", "constant"}:
        return int(node.params.get("frame") or frame)

    expression = str(node.params.get("frame_expression") or node.params.get("frame") or "frame")
    if expression.strip().lower() in {"", "frame"}:
        return frame
    match = re.fullmatch(r"frame\s*([+-])\s*(\d+)", expression.strip())
    if match:
        amount = int(match.group(2))
        return frame + amount if match.group(1) == "+" else frame - amount
    if re.fullmatch(r"\d+", expression.strip()):
        return int(expression)
    return frame


def _range_frame(node: Node, frame: int) -> int | None:
    first = _optional_int(node.params.get("frame_start", node.params.get("first")))
    last = _optional_int(node.params.get("frame_end", node.params.get("last")))
    if first is None or last is None or first > last:
        return frame
    if first <= frame <= last:
        return frame
    policy = str(node.params.get("before" if frame < first else "after") or "hold").lower()
    if policy == "hold":
        return first if frame < first else last
    if policy == "black":
        return None
    span = last - first + 1
    if span <= 0:
        return frame
    offset = (frame - first) % span
    if policy == "loop":
        return first + offset
    if policy == "bounce":
        cycle = max(1, span * 2 - 2)
        position = (frame - first) % cycle
        return first + position if position < span else last - (position - span + 2)
    return frame


def _black_frame(node: Node, context: EvaluationContext) -> ImageFrame:
    width = int(node.params.get("width") or context.settings.width)
    height = int(node.params.get("height") or context.settings.height)
    data = np.zeros((height, width, 4), dtype=np.float32)
    return ImageFrame(
        width=width,
        height=height,
        data=data,
        colorspace=str(node.params.get("colorspace") or context.settings.working_colorspace),
        frame=context.frame,
        metadata={"read/missing": "black", "input/frame": context.frame},
    )


def _read_channels(node: Node, context: EvaluationContext | None = None) -> list[str] | None:
    read_all = node.params.get("read_all_channels", node.params.get("load_all_channels"))
    if _truthy(read_all):
        return None
    demand = context.requested_channels if context is not None else None
    if demand is not None and demand.load_all:
        return None

    channels = list(BASE_READ_CHANNELS)
    manual = _channel_list(node.params.get("read_channels", node.params.get("channels_to_load")))
    if manual is None:
        return None
    channels.extend(manual)
    if demand is not None:
        channels.extend(demand.channels)
    return _dedupe_channels(channels)


def _channel_list(value: object) -> list[str] | None:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,;\s]+", value) if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        return []
    if any(part.lower() in {"all", "*"} for part in parts):
        return None
    return parts


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "all"}
    return bool(value)


def _dedupe_channels(channels: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for channel in channels:
        cleaned = str(channel).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _demand_label(demand: ChannelDemand | None) -> str:
    if demand is None:
        return "auto-rgba"
    return demand.cache_key()


def _nearest_existing_frame(path: str, frame: int, node: Node) -> int | None:
    first = _optional_int(node.params.get("frame_start", node.params.get("first"))) or frame
    last = _optional_int(node.params.get("frame_end", node.params.get("last"))) or frame
    for distance in range(0, max(abs(frame - first), abs(frame - last)) + 1):
        for candidate in (frame - distance, frame + distance):
            if first <= candidate <= last and _resolved_exists(path, candidate):
                return candidate
    return None


def _resolved_exists(path: str, frame: int) -> bool:
    if path.startswith("builtin://"):
        return True
    resolved = path.replace("####", f"{frame:04d}")
    if "%04d" in resolved:
        resolved = resolved % frame
    elif "%d" in resolved:
        resolved = resolved % frame
    try:
        return Path(resolved).exists()
    except OSError:
        return False


def _optional_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
