from __future__ import annotations

import re
from fnmatch import fnmatch

import numpy as np
from PIL import Image, ImageFilter

from opencomp.core.models import ImageFrame, Node
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError, require_input

CHANNEL_INDEX = {
    "r": 0,
    "red": 0,
    "x": 0,
    "g": 1,
    "green": 1,
    "y": 1,
    "b": 2,
    "blue": 2,
    "z": 2,
    "a": 3,
    "alpha": 3,
    "w": 3,
}
COMPONENT_SUFFIX = ("red", "green", "blue", "alpha")
MAIN_COMPONENTS = {
    "rgba.red",
    "rgba.green",
    "rgba.blue",
    "rgba.alpha",
    "r",
    "g",
    "b",
    "a",
    "red",
    "green",
    "blue",
    "alpha",
}


class ShuffleNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        b_input = inputs.get("b") or inputs.get("in") or inputs.get("a")
        if b_input is None:
            raise NodeEvaluationError(node.id, "Shuffle requires a B/input image.")
        a_input = inputs.get("a")
        output_layer = str(node.params.get("output_layer") or node.params.get("out_layer") or "rgba")
        mappings = [
            str(node.params.get("out_r", "r")),
            str(node.params.get("out_g", "g")),
            str(node.params.get("out_b", "b")),
            str(node.params.get("out_a", "a")),
        ]

        data = b_input.data.copy()
        channel_data = b_input.copy_channel_data()
        try:
            planes = [_plane_from_mapping(mapping, a_input, b_input) for mapping in mappings]
            if output_layer.lower() in {"rgba", "rgb"}:
                for index, plane in enumerate(planes):
                    data[:, :, index] = plane
            else:
                channel_data[output_layer] = np.ascontiguousarray(np.stack(planes, axis=-1).astype(np.float32))
        except ValueError as exc:
            raise NodeEvaluationError(node.id, str(exc)) from exc

        return _copy_frame(
            b_input,
            data,
            context.frame,
            {"shuffle/mappings": mappings, "shuffle/output_layer": output_layer},
            channel_data=channel_data,
        )


class CopyNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        a_input = inputs.get("a")
        b_input = inputs.get("b") or inputs.get("in")
        if b_input is None and a_input is not None:
            b_input = a_input
            a_input = None
        if b_input is None:
            raise NodeEvaluationError(node.id, "Copy requires a B/input image.")
        if a_input is None:
            return _copy_frame(b_input, b_input.data.copy(), context.frame, {"copy/skipped": "missing A input"})
        _require_matching_resolution(node, a_input, b_input)

        data = b_input.data.copy()
        channel_data = b_input.copy_channel_data()
        pairs = _copy_pairs(node)
        try:
            for from_channel, to_channel in pairs:
                _set_plane(data, channel_data, to_channel, _get_plane(a_input, from_channel))
            for channel in _expanded_selectors(a_input, str(node.params.get("channels") or node.params.get("layer_copy") or "none")):
                if _has_plane(a_input, channel):
                    _set_plane(data, channel_data, channel, _get_plane(a_input, channel))
        except ValueError as exc:
            raise NodeEvaluationError(node.id, str(exc)) from exc

        data = _apply_mask_and_mix(node, data, b_input.data, inputs.get("mask"))
        metadata_from = str(node.params.get("metadata_from") or "b").lower()
        metadata = _metadata_from_inputs(a_input, b_input, metadata_from)
        metadata.update({"copy/pairs": pairs, "copy/metadata_from": metadata_from})
        return _copy_frame(b_input, data, context.frame, metadata, channel_data=channel_data)


class ChannelMergeNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        b_input = inputs.get("b") or inputs.get("in") or inputs.get("a")
        if b_input is None:
            raise NodeEvaluationError(node.id, "ChannelMerge requires a B/input image.")
        a_input = inputs.get("a") or b_input
        _require_matching_resolution(node, a_input, b_input)

        a_channel = str(node.params.get("a_channel") or node.params.get("A") or "rgba.alpha")
        b_channel = str(node.params.get("b_channel") or node.params.get("B") or "rgba.alpha")
        output_channel = str(node.params.get("output") or "rgba.alpha")
        operation = str(node.params.get("operation") or "union").lower()
        data = b_input.data.copy()
        channel_data = b_input.copy_channel_data()

        try:
            a_plane = _get_plane(a_input, a_channel)
            b_plane = _get_plane(b_input, b_channel)
            merged = _merge_scalar(a_plane, b_plane, operation)
            current = _get_plane(b_input, output_channel) if _has_plane(b_input, output_channel) else b_plane
            mix = float(node.params.get("mix", 1.0))
            merged = current * (1.0 - np.clip(mix, 0.0, 1.0)) + merged * np.clip(mix, 0.0, 1.0)
            mask = _mask_plane(node, inputs.get("mask"))
            if mask is not None:
                merged = current * (1.0 - mask) + merged * mask
            _set_plane(data, channel_data, output_channel, merged)
        except ValueError as exc:
            raise NodeEvaluationError(node.id, str(exc)) from exc

        return _copy_frame(
            b_input,
            data,
            context.frame,
            {
                "channelmerge/operation": operation,
                "channelmerge/a": a_channel,
                "channelmerge/b": b_channel,
                "channelmerge/output": output_channel,
            },
            channel_data=channel_data,
        )


class AddChannelsNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        data = source.data.copy()
        channel_data = source.copy_channel_data()
        color = _color_values(node.params.get("color", 0.0))
        selectors = _node_channel_selectors(node)
        for selector in selectors:
            for channel in _expanded_selectors(source, selector, default_all=["rgba.red", "rgba.green", "rgba.blue", "rgba.alpha"]):
                if _has_plane(ImageFrame(source.width, source.height, data, source.channels, channel_data, source.pixel_aspect), channel):
                    continue
                _set_plane(data, channel_data, channel, _color_plane(source, channel, color))
        return _copy_frame(
            source,
            data,
            context.frame,
            {"addchannels/channels": selectors, "addchannels/color": color},
            channel_data=channel_data,
        )


class RemoveNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        operation = str(node.params.get("operation") or "remove").lower()
        selectors = _node_channel_selectors(node)
        selected = set(_expand_many(source, selectors, default_all=_all_channel_refs(source)))
        if operation not in {"remove", "keep"}:
            raise NodeEvaluationError(node.id, "Remove operation must be remove or keep.")

        if operation == "keep":
            data, channel_data = _keep_selected_channels(source, selected)
        else:
            data, channel_data = source.data.copy(), source.copy_channel_data()
            for channel in selected:
                _remove_plane(data, channel_data, channel)

        return _copy_frame(
            source,
            data,
            context.frame,
            {"remove/operation": operation, "remove/channels": sorted(selected)},
            channel_data=channel_data,
        )


class PremultNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        data = source.data.copy()
        data[:, :, :3] *= np.clip(data[:, :, 3:4], 0.0, 1.0)
        return _copy_frame(source, data, context.frame, {"premult": True})


class UnpremultNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        threshold = float(node.params.get("threshold", 1e-6))
        data = source.data.copy()
        alpha = data[:, :, 3:4]
        safe_alpha = np.where(np.abs(alpha) > threshold, alpha, 1.0)
        data[:, :, :3] = np.where(np.abs(alpha) > threshold, data[:, :, :3] / safe_alpha, data[:, :, :3])
        return _copy_frame(source, data, context.frame, {"unpremult": True})


class InvertNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        data = source.data.copy()
        for index in _primary_indices(str(node.params.get("channels", "rgb"))):
            data[:, :, index] = 1.0 - data[:, :, index]
        return _copy_frame(source, data, context.frame, {"invert": node.params.get("channels", "rgb")})


class ClampNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        channels = str(node.params.get("channels", "rgba"))
        minimum = float(node.params.get("min", 0.0))
        maximum = float(node.params.get("max", 1.0))
        data = source.data.copy()
        for index in _primary_indices(channels):
            data[:, :, index] = np.clip(data[:, :, index], minimum, maximum)
        return _copy_frame(source, data, context.frame, {"clamp": [minimum, maximum, channels]})


class ExposureNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        stops = float(node.params.get("stops", 0.0))
        data = source.data.copy()
        data[:, :, :3] *= float(2.0**stops)
        return _copy_frame(source, data, context.frame, {"exposure_stops": stops})


class SaturationNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        saturation = float(node.params.get("saturation", 1.0))
        data = source.data.copy()
        luma = data[:, :, 0:1] * 0.2126 + data[:, :, 1:2] * 0.7152 + data[:, :, 2:3] * 0.0722
        data[:, :, :3] = luma + (data[:, :, :3] - luma) * saturation
        return _copy_frame(source, data, context.frame, {"saturation": saturation})


class BlurNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        radius = max(float(node.params.get("size", node.params.get("radius", 2.0))), 0.0)
        if radius == 0:
            return source
        data = source.data.copy()
        for index in _primary_indices(str(node.params.get("channels", "rgba"))):
            plane = Image.fromarray(data[:, :, index].astype(np.float32), mode="F")
            blurred = plane.filter(ImageFilter.GaussianBlur(radius=radius))
            data[:, :, index] = np.asarray(blurred, dtype=np.float32)
        return _copy_frame(source, data, context.frame, {"blur_radius": radius})


class ConstantNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        width = int(node.params.get("width") or context.settings.width)
        height = int(node.params.get("height") or context.settings.height)
        color = [
            float(node.params.get("r", 0.0)),
            float(node.params.get("g", 0.0)),
            float(node.params.get("b", 0.0)),
            float(node.params.get("a", 1.0)),
        ]
        data = np.zeros((height, width, 4), dtype=np.float32)
        data[:, :] = color
        return ImageFrame(
            width=width,
            height=height,
            data=data,
            colorspace=str(node.params.get("colorspace") or context.settings.working_colorspace),
            frame=context.frame,
            metadata={"generated": "constant", "color": color},
        )


class GroupNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = inputs.get("in") or inputs.get("a") or next(iter(inputs.values()), None)
        if source is None:
            raise NodeEvaluationError(node.id, "Group nodes need an input until nested graph evaluation is implemented.")
        return _copy_frame(source, source.data.copy(), context.frame, {"group": node.params.get("label", node.name)})


class ViewMetadataNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        return _copy_frame(source, source.data.copy(), context.frame, {"metadata/viewed_by": node.id})


class CompareMetadataNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        b_input = inputs.get("b") or inputs.get("in") or inputs.get("a")
        if b_input is None:
            raise NodeEvaluationError(node.id, "CompareMetaData requires a B/input image.")
        a_input = inputs.get("a")
        metadata = dict(b_input.metadata)
        if a_input is not None:
            a_keys = set(a_input.metadata)
            b_keys = set(b_input.metadata)
            metadata["metadata/compare/only_a"] = sorted(a_keys - b_keys)
            metadata["metadata/compare/only_b"] = sorted(b_keys - a_keys)
            metadata["metadata/compare/different"] = sorted(
                key for key in a_keys & b_keys if a_input.metadata[key] != b_input.metadata[key]
            )
        return _copy_frame(b_input, b_input.data.copy(), context.frame, metadata)


class CopyMetadataNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        a_input = inputs.get("a")
        b_input = inputs.get("b") or inputs.get("in")
        if b_input is None and a_input is not None:
            b_input = a_input
            a_input = None
        if b_input is None:
            raise NodeEvaluationError(node.id, "CopyMetaData requires a B/input image.")
        metadata = dict(b_input.metadata)
        if a_input is not None:
            mode = str(node.params.get("mode") or "all").lower()
            prefix = str(node.params.get("prefix") or "")
            copied = {
                f"{prefix}{key}": value
                for key, value in a_input.metadata.items()
                if mode == "all" or fnmatch(key, str(node.params.get("pattern") or "*"))
            }
            metadata.update(copied)
            metadata["metadata/copied_from"] = a_input.metadata.get("input/filename", a_input.frame)
        return _copy_frame(b_input, b_input.data.copy(), context.frame, metadata)


class AddTimeCodeNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        start_frame = int(node.params.get("start_frame") or context.settings.frame_start)
        fps = float(node.params.get("fps") or context.settings.fps)
        metadata = dict(source.metadata)
        metadata[str(node.params.get("metadata_key") or "input/timecode")] = _timecode(context.frame, start_frame, fps)
        return _copy_frame(source, source.data.copy(), context.frame, metadata)


class ModifyMetadataNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        key = str(node.params.get("key") or "").strip()
        action = str(node.params.get("action") or "set").lower()
        metadata = dict(source.metadata)
        if key:
            if action == "remove":
                metadata.pop(key, None)
            else:
                metadata[key] = node.params.get("value", "")
        metadata["metadata/node"] = node.id
        return ImageFrame(
            width=source.width,
            height=source.height,
            data=source.data.copy(),
            channels=source.channels,
            channel_data=source.copy_channel_data(),
            pixel_aspect=source.pixel_aspect,
        colorspace=source.colorspace,
        frame=context.frame,
        metadata=metadata,
        format_bbox=source.format_bbox,
        data_window=source.data_window,
    )


def _copy_pairs(node: Node) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if "from" in node.params or "to" in node.params:
        pairs.append((str(node.params.get("from", "rgba.alpha")), str(node.params.get("to", "rgba.alpha"))))
    for index in range(4):
        from_channel = str(node.params.get(f"from{index}") or ("rgba.alpha" if index == 0 else "none"))
        to_channel = str(node.params.get(f"to{index}") or ("rgba.alpha" if index == 0 else "none"))
        if _is_none(from_channel) or _is_none(to_channel):
            continue
        pairs.append((from_channel, to_channel))
    return pairs


def _get_plane(frame: ImageFrame, channel: str) -> np.ndarray:
    channel = channel.strip()
    normalized = channel.lower()
    if normalized in {"black", "zero", "0", "none"}:
        return np.zeros((frame.height, frame.width), dtype=np.float32)
    if normalized in {"white", "one", "1"}:
        return np.ones((frame.height, frame.width), dtype=np.float32)
    if normalized in {"luma", "luminance"}:
        return (frame.data[:, :, 0] * 0.2126 + frame.data[:, :, 1] * 0.7152 + frame.data[:, :, 2] * 0.0722).astype(
            np.float32
        )

    layer, component = _split_channel_ref(channel)
    if layer == "rgba":
        if component is None:
            component = 0
        return frame.data[:, :, component].astype(np.float32, copy=False)
    if channel in frame.channel_data:
        value = frame.channel_data[channel]
        return value[:, :, 0] if value.ndim == 3 else value
    layer_key = _case_insensitive_key(frame.channel_data, layer)
    if layer_key:
        value = frame.channel_data[layer_key]
        if component is None:
            return value[:, :, 0] if value.ndim == 3 else value
        if value.ndim == 2:
            if component == 0:
                return value
            raise ValueError(f"Channel '{channel}' does not exist.")
        if component >= value.shape[2]:
            raise ValueError(f"Channel '{channel}' does not exist.")
        return value[:, :, component]
    raise ValueError(f"Channel '{channel}' does not exist.")


def _set_plane(data: np.ndarray, channel_data: dict[str, np.ndarray], channel: str, plane: np.ndarray) -> None:
    if _is_none(channel):
        return
    layer, component = _split_channel_ref(channel)
    value = np.asarray(plane, dtype=np.float32)
    if value.shape != data.shape[:2]:
        raise ValueError(f"Channel '{channel}' has mismatched resolution.")
    if layer == "rgba":
        if component is None:
            component = 0
        data[:, :, component] = value
        return

    if component is None:
        channel_data[layer] = np.ascontiguousarray(value)
        return

    existing_key = _case_insensitive_key(channel_data, layer) or layer
    existing = channel_data.get(existing_key)
    if existing is not None and existing.ndim == 3:
        channels = max(existing.shape[2], component + 1, 4)
        group = np.zeros((*data.shape[:2], channels), dtype=np.float32)
        group[:, :, : existing.shape[2]] = existing
    else:
        channels = max(component + 1, 4)
        group = np.zeros((*data.shape[:2], channels), dtype=np.float32)
        if existing is not None and existing.ndim == 2:
            group[:, :, 0] = existing
    group[:, :, component] = value
    channel_data[existing_key] = np.ascontiguousarray(group)


def _remove_plane(data: np.ndarray, channel_data: dict[str, np.ndarray], channel: str) -> None:
    layer, component = _split_channel_ref(channel)
    if layer == "rgba":
        if component is None:
            data[:, :, :] = 0.0
        else:
            data[:, :, component] = 0.0
        return
    key = _case_insensitive_key(channel_data, layer) or layer
    if key not in channel_data:
        return
    if component is None:
        channel_data.pop(key, None)
        return
    value = channel_data[key].copy()
    if value.ndim == 2:
        if component == 0:
            channel_data.pop(key, None)
        return
    if component < value.shape[2]:
        value[:, :, component] = 0.0
    channel_data[key] = np.ascontiguousarray(value)


def _has_plane(frame: ImageFrame, channel: str) -> bool:
    try:
        _get_plane(frame, channel)
    except ValueError:
        return False
    return True


def _plane_from_mapping(mapping: str, a_input: ImageFrame | None, b_input: ImageFrame) -> np.ndarray:
    raw = mapping.strip()
    normalized = raw.lower()
    if normalized.startswith(("a.", "a:")):
        if a_input is None:
            return np.zeros((b_input.height, b_input.width), dtype=np.float32)
        return _get_plane(a_input, raw[2:])
    if normalized.startswith(("b.", "b:")):
        return _get_plane(b_input, raw[2:])
    return _get_plane(b_input, raw)


def _split_channel_ref(channel: str) -> tuple[str, int | None]:
    normalized = channel.strip().lower()
    if normalized in CHANNEL_INDEX:
        return "rgba", CHANNEL_INDEX[normalized]
    if normalized.startswith("rgba."):
        component_name = normalized.rsplit(".", 1)[1]
        if component_name in CHANNEL_INDEX:
            return "rgba", CHANNEL_INDEX[component_name]
    if "." in normalized:
        layer, component_name = normalized.rsplit(".", 1)
        if component_name in CHANNEL_INDEX:
            return layer, CHANNEL_INDEX[component_name]
    return normalized, None


def _primary_indices(channels: str) -> list[int]:
    selected: set[int] = set()
    for channel in _expanded_selectors(None, channels, default_all=["rgba.red", "rgba.green", "rgba.blue", "rgba.alpha"]):
        layer, component = _split_channel_ref(channel)
        if layer == "rgba" and component is not None:
            selected.add(component)
    return sorted(selected)


def _node_channel_selectors(node: Node) -> list[str]:
    return [
        str(node.params.get("channels") or "none"),
        str(node.params.get("channels2") or "none"),
        str(node.params.get("channels3") or "none"),
        str(node.params.get("channels4") or "none"),
    ]


def _expanded_selectors(
    frame: ImageFrame | None,
    selector: str,
    default_all: list[str] | None = None,
) -> list[str]:
    selector = selector.strip()
    if not selector or _is_none(selector):
        return []
    result: list[str] = []
    for token in re.split(r"[\s,]+", selector):
        token = token.strip()
        if not token or _is_none(token):
            continue
        lower = token.lower()
        if lower == "all":
            result.extend(default_all or (_all_channel_refs(frame) if frame is not None else ["rgba.red", "rgba.green", "rgba.blue", "rgba.alpha"]))
        elif lower == "rgba":
            result.extend(["rgba.red", "rgba.green", "rgba.blue", "rgba.alpha"])
        elif lower == "rgb":
            result.extend(["rgba.red", "rgba.green", "rgba.blue"])
        elif lower in {"alpha", "a"}:
            result.append("rgba.alpha")
        elif lower in {"r", "red"}:
            result.append("rgba.red")
        elif lower in {"g", "green"}:
            result.append("rgba.green")
        elif lower in {"b", "blue"}:
            result.append("rgba.blue")
        elif "*" in lower and frame is not None:
            result.extend(channel for channel in _all_channel_refs(frame) if fnmatch(channel.lower(), lower))
        else:
            result.append(token)
    return list(dict.fromkeys(result))


def _expand_many(frame: ImageFrame, selectors: list[str], default_all: list[str]) -> list[str]:
    result: list[str] = []
    for selector in selectors:
        result.extend(_expanded_selectors(frame, selector, default_all=default_all))
    return list(dict.fromkeys(result))


def _all_channel_refs(frame: ImageFrame | None) -> list[str]:
    refs = ["rgba.red", "rgba.green", "rgba.blue", "rgba.alpha"]
    if frame is None:
        return refs
    for name, value in frame.channel_data.items():
        if value.ndim == 2:
            refs.append(name)
        else:
            for index in range(value.shape[2]):
                suffix = COMPONENT_SUFFIX[index] if index < len(COMPONENT_SUFFIX) else f"c{index}"
                refs.append(f"{name}.{suffix}")
    return refs


def _keep_selected_channels(source: ImageFrame, selected: set[str]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    data = np.zeros_like(source.data, dtype=np.float32)
    channel_data: dict[str, np.ndarray] = {}
    for channel in selected:
        if _has_plane(source, channel):
            _set_plane(data, channel_data, channel, _get_plane(source, channel))
    return data, channel_data


def _merge_scalar(a: np.ndarray, b: np.ndarray, operation: str) -> np.ndarray:
    operation = operation.lower().replace(" ", "_")
    if operation in {"absminus", "difference"}:
        return np.abs(a - b)
    if operation == "divide":
        return np.divide(a, b, out=np.zeros_like(a, dtype=np.float32), where=np.abs(b) > 1e-8)
    if operation in {"from", "b-a"}:
        return b - a
    if operation == "in":
        return a * b
    if operation == "max":
        return np.maximum(a, b)
    if operation == "min":
        return np.minimum(a, b)
    if operation in {"minus", "a-b"}:
        return a - b
    if operation == "multiply":
        return np.where((a < 0) & (b < 0), a, a * b)
    if operation == "out":
        return a * (1.0 - b)
    if operation in {"plus", "add"}:
        return a + b
    if operation == "screen":
        return a + b - (a * b)
    if operation == "stencil":
        return b * (1.0 - a)
    if operation == "union":
        return a + b - (a * b)
    if operation == "xor":
        return a + b - (2.0 * a * b)
    if operation in {"b_if_not_a", "b_if_not_a?"}:
        return np.where(a != 0, a, b)
    raise ValueError(
        "Unsupported ChannelMerge operation. Use union, plus, minus, from, multiply, divide, "
        "max, min, absminus, in, out, stencil, screen, or xor."
    )


def _apply_mask_and_mix(node: Node, data: np.ndarray, base: np.ndarray, mask: ImageFrame | None) -> np.ndarray:
    result = data
    mask_plane = _mask_plane(node, mask)
    if mask_plane is not None:
        result = base * (1.0 - mask_plane[:, :, None]) + result * mask_plane[:, :, None]
    mix = np.clip(float(node.params.get("mix", 1.0)), 0.0, 1.0)
    if mix < 1.0:
        result = base * (1.0 - mix) + result * mix
    return np.ascontiguousarray(result.astype(np.float32))


def _mask_plane(node: Node, mask: ImageFrame | None) -> np.ndarray | None:
    if mask is None:
        return None
    channel = str(node.params.get("mask") or node.params.get("mask_channel") or "rgba.alpha")
    if _is_none(channel):
        return None
    plane = np.clip(_get_plane(mask, channel), 0.0, 1.0)
    if bool(node.params.get("invert_mask", False)) or bool(node.params.get("invert", False)):
        plane = 1.0 - plane
    return plane


def _color_values(value: object) -> list[float]:
    if isinstance(value, (list, tuple)):
        values = [float(item) for item in value]
    else:
        values = [float(part) for part in re.split(r"[\s,]+", str(value).strip()) if part]
    if not values:
        values = [0.0]
    while len(values) < 4:
        values.append(values[-1])
    return values[:4]


def _color_plane(source: ImageFrame, channel: str, color: list[float]) -> np.ndarray:
    _layer, component = _split_channel_ref(channel)
    value = color[component or 0]
    return np.full((source.height, source.width), value, dtype=np.float32)


def _metadata_from_inputs(a_input: ImageFrame, b_input: ImageFrame, metadata_from: str) -> dict:
    if metadata_from == "a":
        return dict(a_input.metadata)
    if metadata_from == "all":
        return {**a_input.metadata, **b_input.metadata}
    return dict(b_input.metadata)


def _require_matching_resolution(node: Node, *frames: ImageFrame) -> None:
    if not frames:
        return
    shape = frames[0].data.shape
    for frame in frames[1:]:
        if frame.data.shape != shape:
            raise NodeEvaluationError(node.id, "Inputs must have matching resolution for this operation.")


def _case_insensitive_key(values: dict[str, np.ndarray], key: str) -> str | None:
    target = key.lower()
    for candidate in values:
        if candidate.lower() == target:
            return candidate
    return None


def _is_none(value: str) -> bool:
    return value.strip().lower() in {"", "none", "disabled"}


def _copy_frame(
    source: ImageFrame,
    data: np.ndarray,
    frame: int,
    metadata: dict,
    channel_data: dict[str, np.ndarray] | None = None,
) -> ImageFrame:
    return ImageFrame(
        width=source.width,
        height=source.height,
        data=data,
        channels=_expanded_channel_names(data, channel_data if channel_data is not None else source.channel_data),
        channel_data=channel_data if channel_data is not None else source.copy_channel_data(),
        pixel_aspect=source.pixel_aspect,
        colorspace=source.colorspace,
        frame=frame,
        metadata={**source.metadata, **metadata},
        format_bbox=source.format_bbox,
        data_window=source.data_window,
    )


def _expanded_channel_names(data: np.ndarray, channel_data: dict[str, np.ndarray]) -> list[str]:
    names: list[str] = ["rgba", "rgb", "r", "g", "b", "a", "luma"]
    if data.shape[2] >= 4:
        for name in ("rgba.red", "rgba.green", "rgba.blue", "rgba.alpha"):
            if name not in names:
                names.append(name)
    for name, value in channel_data.items():
        if name not in names:
            names.append(name)
        if value.ndim != 3:
            continue
        for index in range(value.shape[2]):
            suffix = COMPONENT_SUFFIX[index] if index < len(COMPONENT_SUFFIX) else f"C{index}"
            component_name = f"{name}.{suffix}"
            if component_name not in names:
                names.append(component_name)
    return names


def _plane(data: np.ndarray, mapping: str) -> np.ndarray:
    mapping = mapping.lower()
    if mapping in CHANNEL_INDEX:
        return data[:, :, CHANNEL_INDEX[mapping]]
    if mapping in {"black", "zero", "0"}:
        return np.zeros(data.shape[:2], dtype=np.float32)
    if mapping in {"white", "one", "1"}:
        return np.ones(data.shape[:2], dtype=np.float32)
    if mapping in {"luma", "luminance"}:
        return (data[:, :, 0] * 0.2126 + data[:, :, 1] * 0.7152 + data[:, :, 2] * 0.0722).astype(np.float32)
    raise ValueError(f"Unsupported channel mapping: {mapping}")


def _timecode(frame: int, start_frame: int, fps: float) -> str:
    fps_int = max(1, int(round(fps)))
    offset = max(0, frame - start_frame)
    frames = offset % fps_int
    total_seconds = offset // fps_int
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"
