from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

from opencomp.core.models import ImageFrame


MAIN_CHANNEL_ALIASES = {
    "rgba": "rgba",
    "rgb": "rgb",
    "r": "r",
    "red": "r",
    "g": "g",
    "green": "g",
    "b": "b",
    "blue": "b",
    "a": "a",
    "alpha": "a",
    "luma": "luma",
    "luminance": "luma",
}


def resize_float_rgba(
    rgba: np.ndarray,
    max_width: int | None = None,
    max_height: int | None = None,
) -> np.ndarray:
    image = np.asarray(rgba, dtype=np.float32)
    if image.ndim != 3 or image.shape[2] != 4:
        raise ValueError("Preview resizing requires an H x W x 4 RGBA array.")
    if not max_width or not max_height:
        return np.ascontiguousarray(image)

    size = preview_resize_dimensions(int(image.shape[1]), int(image.shape[0]), max_width, max_height)
    if size is None:
        return np.ascontiguousarray(image)
    planes = [
        np.asarray(
            Image.fromarray(np.ascontiguousarray(image[:, :, channel]), mode="F").resize(
                size,
                Image.Resampling.BILINEAR,
            ),
            dtype=np.float32,
        )
        for channel in range(4)
    ]
    return np.ascontiguousarray(np.stack(planes, axis=-1))


def encode_preview_png(
    rgba: np.ndarray,
    max_width: int | None = None,
    max_height: int | None = None,
) -> bytes:
    image = np.asarray(rgba, dtype=np.float32)
    if image.ndim != 3 or image.shape[2] != 4:
        raise ValueError("Preview encoding requires an H x W x 4 RGBA array.")
    image_u8 = (np.clip(image, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    if max_width and max_height:
        size = preview_resize_dimensions(int(image_u8.shape[1]), int(image_u8.shape[0]), max_width, max_height)
        if size is not None:
            image_u8 = np.asarray(Image.fromarray(image_u8, mode="RGBA").resize(size, Image.Resampling.BILINEAR))
    output = BytesIO()
    Image.fromarray(image_u8, mode="RGBA").save(output, format="PNG")
    return output.getvalue()


def preview_rgba_for_channel(image: ImageFrame, channel: str | None) -> tuple[np.ndarray, bool]:
    channel_name = (channel or "rgba").strip()
    normalized = channel_name.lower()
    main = MAIN_CHANNEL_ALIASES.get(normalized)
    if main:
        return _main_channel_preview(image.data, main), True

    plane = image.channel_data.get(channel_name)
    if plane is None:
        plane = image.channel_data.get(_case_insensitive_key(image.channel_data, channel_name) or "")
    if plane is None and "." in channel_name:
        plane = _component_plane(image.channel_data, channel_name)
    if plane is None:
        return np.ascontiguousarray(image.data), True
    return _aux_channel_preview(plane), plane.ndim == 3 and plane.shape[2] >= 3


def _main_channel_preview(data: np.ndarray, channel: str) -> np.ndarray:
    if channel == "rgba":
        return np.ascontiguousarray(data)
    if channel == "rgb":
        rgba = data.copy()
        rgba[:, :, 3] = 1.0
        return rgba
    if channel == "luma":
        plane = data[:, :, 0] * 0.2126 + data[:, :, 1] * 0.7152 + data[:, :, 2] * 0.0722
    else:
        index = {"r": 0, "g": 1, "b": 2, "a": 3}[channel]
        plane = data[:, :, index]
    return _scalar_to_rgba(plane, normalize=False)


def _aux_channel_preview(plane: np.ndarray) -> np.ndarray:
    if plane.ndim == 2:
        return _scalar_to_rgba(plane, normalize=True)
    if plane.ndim != 3:
        raise ValueError("Channel preview requires a 2D plane or an H x W x N array.")
    if plane.shape[2] >= 4:
        return np.ascontiguousarray(plane[:, :, :4])
    if plane.shape[2] >= 3:
        alpha = np.ones((*plane.shape[:2], 1), dtype=np.float32)
        return np.ascontiguousarray(np.concatenate([plane[:, :, :3], alpha], axis=2))
    if plane.shape[2] == 2:
        rgb = np.zeros((*plane.shape[:2], 3), dtype=np.float32)
        rgb[:, :, :2] = plane[:, :, :2]
        alpha = np.ones((*plane.shape[:2], 1), dtype=np.float32)
        return np.ascontiguousarray(np.concatenate([rgb, alpha], axis=2))
    return _scalar_to_rgba(plane[:, :, 0], normalize=True)


def preview_resize_dimensions(
    width: int,
    height: int,
    max_width: int | None = None,
    max_height: int | None = None,
) -> tuple[int, int] | None:
    width = max(1, int(width))
    height = max(1, int(height))
    if not max_width or not max_height:
        return None
    scale = min(max_width / width, max_height / height, 1.0)
    if scale >= 1.0:
        return None
    return (max(1, int(width * scale)), max(1, int(height * scale)))


def _scalar_to_rgba(plane: np.ndarray, normalize: bool) -> np.ndarray:
    value = np.asarray(plane, dtype=np.float32)
    if normalize:
        finite = value[np.isfinite(value)]
        if finite.size:
            minimum = float(np.min(finite))
            maximum = float(np.max(finite))
            if maximum > minimum:
                value = (value - minimum) / (maximum - minimum)
            else:
                value = np.zeros_like(value, dtype=np.float32)
        else:
            value = np.zeros_like(value, dtype=np.float32)
    value = np.nan_to_num(value, nan=0.0, posinf=1.0, neginf=0.0)
    alpha = np.ones_like(value, dtype=np.float32)
    return np.ascontiguousarray(np.stack([value, value, value, alpha], axis=-1).astype(np.float32))


def _component_plane(channel_data: dict[str, np.ndarray], channel_name: str) -> np.ndarray | None:
    base, component = channel_name.rsplit(".", 1)
    base_key = base if base in channel_data else _case_insensitive_key(channel_data, base)
    if not base_key:
        return None
    data = channel_data[base_key]
    if data.ndim != 3:
        return None
    component_index = {"r": 0, "x": 0, "g": 1, "y": 1, "b": 2, "z": 2, "a": 3, "w": 3}.get(component.lower())
    if component_index is None or component_index >= data.shape[2]:
        return None
    return data[:, :, component_index]


def _case_insensitive_key(channel_data: dict[str, np.ndarray], channel_name: str) -> str | None:
    target = channel_name.lower()
    for key in channel_data:
        if key.lower() == target:
            return key
    return None
