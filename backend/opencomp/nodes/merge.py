from __future__ import annotations

import numpy as np

from opencomp.core.bbox import intersect_bbox, union_bbox
from opencomp.core.models import ImageFrame, Node
from opencomp.core.tile_engine import map_row_tiles, tile_rendering_enabled
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError
from opencomp.nodes.channel import _get_plane


class MergeNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        operation = str(node.params.get("operation") or "over").lower()
        mix = float(node.params.get("mix", 1.0))
        a_inputs = _ordered_a_inputs(inputs)
        first_a = a_inputs[0] if a_inputs else None
        b_input = inputs.get("b") or inputs.get("bg")
        b = b_input
        mask = inputs.get("mask")
        if first_a is None:
            raise NodeEvaluationError(node.id, "Merge requires at least an A/foreground input.")
        if b is None:
            b = _transparent_like(first_a)
        for a in a_inputs:
            if a.data.shape != b.data.shape:
                raise NodeEvaluationError(node.id, "Merge inputs must have matching resolution for MVP.")
        if mask is not None and mask.data.shape != b.data.shape:
            raise NodeEvaluationError(node.id, "Merge mask must match the A/B resolution for MVP.")

        data = b.data
        try:
            for a in a_inputs:
                data = merge_rgba(
                    a.data,
                    data,
                    operation,
                    mix=mix,
                    mask=mask,
                    mask_channel=str(node.params.get("mask") or node.params.get("mask_channel") or "rgba.alpha"),
                    invert_mask=bool(node.params.get("invert_mask", False)),
                    settings=context.settings,
                )
        except ValueError as exc:
            raise NodeEvaluationError(node.id, str(exc)) from exc

        metadata_from = str(node.params.get("metadata_from", node.params.get("metainput", "b"))).lower()
        metadata_source = _metadata_source(a_inputs, b, b_input is not None, metadata_from)
        metadata = _metadata_from_many(a_inputs, b, metadata_from)
        metadata.update(
            {
                "merge/operation": operation,
                "merge/metadata_from": metadata_from,
                "merge/a": first_a.metadata.get("input/filename"),
                "merge/a_inputs": [frame.metadata.get("input/filename") for frame in a_inputs],
                "merge/b": b_input.metadata.get("input/filename") if b_input is not None else None,
                "merge/a_channels": node.params.get("a_channels", node.params.get("Achannels", "rgba")),
                "merge/b_channels": node.params.get("b_channels", node.params.get("Bchannels", "rgba")),
                "merge/output": node.params.get("output", "rgba"),
                "merge/bbox": node.params.get("bbox", node.params.get("set_bbox", "union")),
            }
        )
        bbox_mode = str(node.params.get("bbox", node.params.get("set_bbox", "union"))).lower()
        return ImageFrame(
            width=b.width,
            height=b.height,
            data=data,
            channels=metadata_source.channels,
            channel_data=metadata_source.copy_channel_data(),
            pixel_aspect=metadata_source.pixel_aspect,
            colorspace=metadata_source.colorspace,
            frame=context.frame,
            metadata=metadata,
            format_bbox=b.format_bbox,
            data_window=_merged_data_window(a_inputs, b, bbox_mode),
        )


def _ordered_a_inputs(inputs: dict[str, ImageFrame]) -> list[ImageFrame]:
    ordered: list[ImageFrame] = []
    for socket in ("a", "fg", "in"):
        if socket in inputs:
            ordered.append(inputs[socket])
            break
    numbered = []
    for socket, frame in inputs.items():
        key = socket.strip().lower()
        if key.startswith("a") and key[1:].isdigit():
            numbered.append((int(key[1:]), frame))
    ordered.extend(frame for _index, frame in sorted(numbered, key=lambda item: item[0]))
    return ordered


def _metadata_source(a_inputs: list[ImageFrame], b: ImageFrame, has_real_b: bool, metadata_from: str) -> ImageFrame:
    if metadata_from == "a" or not has_real_b:
        return a_inputs[0]
    return b


def _metadata_from_many(a_inputs: list[ImageFrame], b: ImageFrame, metadata_from: str) -> dict:
    if metadata_from == "a":
        return dict(a_inputs[0].metadata)
    if metadata_from == "all":
        metadata = dict(b.metadata)
        for frame in a_inputs:
            metadata.update(frame.metadata)
        return metadata
    return dict(b.metadata)


def merge_rgba(
    a: np.ndarray,
    b: np.ndarray,
    operation: str,
    mix: float = 1.0,
    mask: ImageFrame | np.ndarray | None = None,
    mask_channel: str = "rgba.alpha",
    invert_mask: bool = False,
    settings=None,
) -> np.ndarray:
    fg = a.astype(np.float32, copy=False)
    bg = b.astype(np.float32, copy=False)
    if tile_rendering_enabled(settings, fg.shape[0]):
        output = np.empty_like(fg, dtype=np.float32)

        def process(start: int, end: int) -> None:
            if isinstance(mask, ImageFrame):
                sliced_mask = ImageFrame(
                    width=mask.width,
                    height=end - start,
                    data=mask.data[start:end],
                    channels=mask.channels,
                    channel_data={name: value[start:end] for name, value in mask.channel_data.items()},
                    pixel_aspect=mask.pixel_aspect,
                    colorspace=mask.colorspace,
                    frame=mask.frame,
                    metadata=mask.metadata,
                    format_bbox=mask.format_bbox,
                    data_window=mask.data_window,
                )
            elif mask is not None:
                sliced_mask = mask[start:end]
            else:
                sliced_mask = None
            output[start:end] = _merge_rgba_full(
                fg[start:end],
                bg[start:end],
                operation,
                mix=mix,
                mask=sliced_mask,
                mask_channel=mask_channel,
                invert_mask=invert_mask,
            )

        map_row_tiles(fg.shape[0], settings, process)
        return np.ascontiguousarray(output)

    return _merge_rgba_full(
        fg,
        bg,
        operation,
        mix=mix,
        mask=mask,
        mask_channel=mask_channel,
        invert_mask=invert_mask,
    )


def _merge_rgba_full(
    fg: np.ndarray,
    bg: np.ndarray,
    operation: str,
    mix: float = 1.0,
    mask: ImageFrame | np.ndarray | None = None,
    mask_channel: str = "rgba.alpha",
    invert_mask: bool = False,
) -> np.ndarray:
    a_alpha = np.clip(fg[:, :, 3:4], 0.0, 1.0)
    b_alpha = np.clip(bg[:, :, 3:4], 0.0, 1.0)

    operation = operation.lower().replace(" ", "_").replace("-", "_")
    if operation == "over":
        data = fg + bg * (1.0 - a_alpha)
    elif operation == "under":
        data = fg * (1.0 - b_alpha) + bg
    elif operation == "atop":
        data = fg * b_alpha + bg * (1.0 - a_alpha)
    elif operation == "in":
        data = fg * b_alpha
    elif operation == "out":
        data = fg * (1.0 - b_alpha)
    elif operation == "mask":
        data = bg * a_alpha
    elif operation == "stencil":
        data = bg * (1.0 - a_alpha)
    elif operation == "xor":
        data = fg * (1.0 - b_alpha) + bg * (1.0 - a_alpha)
    elif operation in {"plus", "add"}:
        data = fg + bg
    elif operation == "minus":
        data = fg - bg
    elif operation == "from":
        data = bg - fg
    elif operation in {"difference", "absminus"}:
        data = np.abs(fg - bg)
    elif operation == "exclusion":
        data = fg + bg - (2.0 * fg * bg)
    elif operation == "grain_extract":
        data = bg - fg + 0.5
    elif operation == "grain_merge":
        data = bg + fg - 0.5
    elif operation == "hypot":
        data = np.sqrt((fg * fg) + (bg * bg))
    elif operation == "multiply":
        data = np.where((fg < 0) & (bg < 0), fg, fg * bg)
    elif operation == "screen":
        data = fg + bg - (fg * bg)
    elif operation == "overlay":
        data = np.where(bg <= 0.5, 2.0 * fg * bg, 1.0 - (2.0 * (1.0 - fg) * (1.0 - bg)))
    elif operation == "hard_light":
        data = np.where(fg <= 0.5, 2.0 * fg * bg, 1.0 - (2.0 * (1.0 - fg) * (1.0 - bg)))
    elif operation == "soft_light":
        data = np.where(fg <= 0.5, bg - (1.0 - 2.0 * fg) * bg * (1.0 - bg), bg + (2.0 * fg - 1.0) * (_soft_light_d(bg) - bg))
    elif operation == "max":
        data = np.maximum(fg, bg)
    elif operation == "min":
        data = np.minimum(fg, bg)
    elif operation == "average":
        data = (fg + bg) * 0.5
    elif operation == "divide":
        data = np.divide(fg, bg, out=np.zeros_like(fg, dtype=np.float32), where=np.abs(bg) > 1e-8)
    elif operation == "copy":
        data = fg
    elif operation == "matte":
        data = fg * a_alpha + bg * (1.0 - a_alpha)
    else:
        raise ValueError(
            "Unsupported Merge operation. Use over, under, atop, in, out, plus, minus, from, "
            "difference, exclusion, grain_extract, grain_merge, hypot, multiply, overlay, hard_light, "
            "soft_light, screen, max, min, average, divide, mask, stencil, xor, matte, or copy."
        )

    if mask is not None:
        mask_alpha = _mask_alpha(mask, mask_channel, invert_mask)
        data = bg * (1.0 - mask_alpha) + data * mask_alpha
    mix = np.clip(mix, 0.0, 1.0)
    if mix < 1.0:
        data = bg * (1.0 - mix) + data * mix
    return np.ascontiguousarray(data.astype(np.float32))


def _soft_light_d(value: np.ndarray) -> np.ndarray:
    return np.where(value <= 0.25, ((16.0 * value - 12.0) * value + 4.0) * value, np.sqrt(np.maximum(value, 0.0)))


def _mask_alpha(mask: ImageFrame | np.ndarray, mask_channel: str, invert_mask: bool) -> np.ndarray:
    if isinstance(mask, ImageFrame):
        if mask_channel.strip().lower() in {"", "none", "disabled"}:
            return np.ones((*mask.data.shape[:2], 1), dtype=np.float32)
        plane = _get_plane(mask, mask_channel)
    else:
        plane = mask[:, :, 3] if mask.ndim == 3 and mask.shape[2] >= 4 else mask
    alpha = np.clip(np.asarray(plane, dtype=np.float32), 0.0, 1.0)[:, :, None]
    return 1.0 - alpha if invert_mask else alpha


def _transparent_like(frame: ImageFrame) -> ImageFrame:
    data = np.zeros_like(frame.data, dtype=np.float32)
    return ImageFrame(
        width=frame.width,
        height=frame.height,
        data=data,
        pixel_aspect=frame.pixel_aspect,
        colorspace=frame.colorspace,
        frame=frame.frame,
        metadata={"generated": "transparent"},
        format_bbox=frame.format_bbox,
        data_window=frame.data_window,
    )


def _merged_data_window(a_inputs: list[ImageFrame], b: ImageFrame, bbox_mode: str) -> dict[str, int]:
    if bbox_mode in {"a", "a input", "input a"}:
        return union_bbox(*(frame.data_window for frame in a_inputs))
    if bbox_mode in {"b", "b input", "input b"}:
        return dict(b.data_window or {})
    if bbox_mode in {"intersect", "intersection"}:
        return intersect_bbox(b.data_window, *(frame.data_window for frame in a_inputs))
    return union_bbox(b.data_window, *(frame.data_window for frame in a_inputs))
