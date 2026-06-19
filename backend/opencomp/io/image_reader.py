from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from opencomp.core.models import ImageFrame


def read_image(path: str, frame: int | None = None, colorspace: str = "Utility - sRGB - Texture") -> ImageFrame:
    resolved = _resolve_frame_path(path, frame)
    if resolved.startswith("builtin://"):
        return _read_builtin(resolved, frame or 1001, colorspace)

    file_path = Path(resolved)
    if not file_path.exists():
        raise FileNotFoundError(f"Input image does not exist: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        return _read_pillow(file_path, frame or 1001, colorspace)
    if suffix == ".exr":
        return _read_exr(file_path, frame or 1001, colorspace)
    raise NotImplementedError(f"Unsupported image extension '{suffix}' for {file_path}")


def image_source_fingerprint(path: str, frame: int | None = None) -> dict[str, object]:
    resolved = _resolve_frame_path(path, frame)
    if resolved.startswith("builtin://"):
        return {"kind": "builtin", "path": resolved}
    if "://" in resolved:
        return {"kind": "virtual", "path": resolved}

    file_path = Path(resolved)
    try:
        stat = file_path.stat()
    except FileNotFoundError:
        return {"kind": "file", "path": str(file_path), "exists": False}

    return {
        "kind": "file",
        "path": str(file_path),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _resolve_frame_path(path: str, frame: int | None) -> str:
    if frame is None:
        return path
    if "####" in path:
        return path.replace("####", f"{frame:04d}")
    if "%04d" in path:
        return path % frame
    if "%d" in path:
        return path % frame
    return path


def _read_builtin(path: str, frame: int, colorspace: str) -> ImageFrame:
    width, height = 640, 360
    y, x = np.mgrid[0:height, 0:width].astype(np.float32)
    r = x / max(width - 1, 1)
    g = y / max(height - 1, 1)
    b = 0.25 + 0.25 * np.sin((x + frame) / 36.0)
    a = np.ones_like(r)
    data = np.stack([r, g, b, a], axis=-1).astype(np.float32)
    return ImageFrame(
        width=width,
        height=height,
        data=data,
        colorspace=colorspace,
        frame=frame,
        pixel_aspect=1.0,
        metadata={
            "input/filename": path,
            "input/frame": frame,
            "input/width": width,
            "input/height": height,
            "input/colorspace": colorspace,
            "input/pixel_aspect": 1.0,
            "source/type": "builtin",
        },
    )


def _read_pillow(path: Path, frame: int, colorspace: str) -> ImageFrame:
    with Image.open(path) as image:
        metadata = _base_file_metadata(path, frame, colorspace, image.width, image.height)
        metadata.update(_pillow_metadata(image))
        rgba_u8 = np.asarray(image.convert("RGBA"), dtype=np.float32)
    rgba = rgba_u8 / 255.0
    return ImageFrame(
        width=rgba.shape[1],
        height=rgba.shape[0],
        data=rgba,
        colorspace=colorspace,
        frame=frame,
        pixel_aspect=1.0,
        metadata=metadata,
    )


def _read_exr(path: Path, frame: int, colorspace: str) -> ImageFrame:
    try:
        import OpenEXR  # type: ignore
    except ImportError as exc:
        raise NotImplementedError(
            "EXR reading requires the OpenEXR Python package. Install backend[exr] to enable it."
        ) from exc

    if hasattr(OpenEXR, "File"):
        try:
            return _read_exr_v3(OpenEXR, path, frame, colorspace)
        except Exception:
            pass

    return _read_exr_legacy(OpenEXR, path, frame, colorspace)


def _read_exr_v3(OpenEXR: Any, path: Path, frame: int, colorspace: str) -> ImageFrame:
    exr = OpenEXR.File(str(path))
    channels = exr.channels()
    channel_data = _exr_v3_channel_data(channels)
    channel_names = _expanded_channel_names(channel_data)

    rgba_group = channel_data.get("RGBA")
    if rgba_group is None:
        rgba_group = channel_data.get("rgba")
    if rgba_group is not None and rgba_group.ndim == 3 and rgba_group.shape[2] >= 4:
        data = np.ascontiguousarray(rgba_group[:, :, :4], dtype=np.float32)
        return _exr_frame(path, frame, colorspace, data, channel_data, channel_names, _exr_header_metadata(exr))

    planes = []
    for name in ("R", "G", "B", "A"):
        value = _channel_lookup(channel_data, name)
        if value is None:
            if name == "A" and planes:
                planes.append(np.ones_like(planes[0], dtype=np.float32))
                continue
            if planes:
                planes.append(np.zeros_like(planes[0], dtype=np.float32))
                continue
            raise NotImplementedError(f"EXR file has no readable {name} channel: {path}")
        pixels = np.asarray(value, dtype=np.float32)
        if pixels.ndim == 3:
            pixels = pixels[:, :, 0]
        planes.append(pixels)
    data = np.stack(planes, axis=-1).astype(np.float32)
    return _exr_frame(path, frame, colorspace, data, channel_data, channel_names, _exr_header_metadata(exr))


def _read_exr_legacy(OpenEXR: Any, path: Path, frame: int, colorspace: str) -> ImageFrame:
    try:
        import Imath  # type: ignore
    except ImportError as exc:
        raise NotImplementedError(
            "This OpenEXR install needs Imath for legacy EXR reading, but Imath is missing."
        ) from exc

    exr = OpenEXR.InputFile(str(path))
    header = exr.header()
    data_window = header["dataWindow"]
    width = data_window.max.x - data_window.min.x + 1
    height = data_window.max.y - data_window.min.y + 1
    pixel_type = Imath.PixelType(Imath.PixelType.FLOAT)
    available = header.get("channels", {}).keys()
    channel_data: dict[str, np.ndarray] = {}
    for channel in available:
        try:
            buffer = exr.channel(channel, pixel_type)
        except Exception:
            continue
        channel_data[channel] = np.frombuffer(buffer, dtype=np.float32).reshape(height, width)

    planes: list[np.ndarray] = []
    for channel in ("R", "G", "B", "A"):
        if channel in channel_data:
            plane = channel_data[channel]
        elif channel == "A":
            plane = np.ones((height, width), dtype=np.float32)
        else:
            plane = np.zeros((height, width), dtype=np.float32)
        planes.append(plane)
    data = np.stack(planes, axis=-1).astype(np.float32)
    pixel_aspect = _pixel_aspect_from_metadata(header)
    metadata = {
        **_base_file_metadata(path, frame, colorspace, width, height),
        "input/pixel_aspect": pixel_aspect,
        "exr/channels": _expanded_channel_names(channel_data),
    }
    for key, value in header.items():
        if key == "channels":
            continue
        metadata[f"exr/{key}"] = _metadata_value(value)
    return ImageFrame(
        width=width,
        height=height,
        data=data,
        channels=_expanded_channel_names(channel_data),
        channel_data=channel_data,
        pixel_aspect=pixel_aspect,
        colorspace=colorspace,
        frame=frame,
        metadata=metadata,
    )


def _base_file_metadata(path: Path, frame: int, colorspace: str, width: int, height: int) -> dict[str, object]:
    metadata: dict[str, object] = {
        "input/filename": str(path),
        "input/frame": frame,
        "input/width": int(width),
        "input/height": int(height),
        "input/colorspace": colorspace,
        "input/pixel_aspect": 1.0,
        "source/type": path.suffix.lower().lstrip("."),
    }
    try:
        stat = path.stat()
    except OSError:
        return metadata
    metadata["file/size"] = stat.st_size
    metadata["file/mtime_ns"] = stat.st_mtime_ns
    return metadata


def _pillow_metadata(image: Image.Image) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key, value in image.info.items():
        if key in {"exif", "icc_profile"}:
            metadata[f"image/{key}_bytes"] = len(value) if isinstance(value, bytes) else str(value)
            continue
        metadata[f"image/{key}"] = _metadata_value(value)
    try:
        exif = image.getexif()
    except Exception:
        return metadata
    for key, value in exif.items():
        metadata[f"exif/{key}"] = _metadata_value(value)
    return metadata


def _exr_header_metadata(exr: Any) -> dict[str, object]:
    try:
        header = exr.header()
    except Exception:
        return {}
    metadata: dict[str, object] = {}
    if not isinstance(header, dict):
        return metadata
    for key, value in header.items():
        if key == "channels":
            continue
        metadata[f"exr/{key}"] = _metadata_value(value)
    return metadata


def _exr_frame(
    path: Path,
    frame: int,
    colorspace: str,
    data: np.ndarray,
    channel_data: dict[str, np.ndarray],
    channel_names: list[str],
    exr_metadata: dict[str, object],
) -> ImageFrame:
    pixel_aspect = _pixel_aspect_from_metadata(exr_metadata)
    metadata = {
        **_base_file_metadata(path, frame, colorspace, data.shape[1], data.shape[0]),
        **exr_metadata,
        "input/pixel_aspect": pixel_aspect,
        "exr/channels": channel_names,
    }
    return ImageFrame(
        width=data.shape[1],
        height=data.shape[0],
        data=data,
        channels=channel_names,
        channel_data=channel_data,
        pixel_aspect=pixel_aspect,
        colorspace=colorspace,
        frame=frame,
        metadata=metadata,
    )


def _exr_v3_channel_data(channels: Any) -> dict[str, np.ndarray]:
    channel_data: dict[str, np.ndarray] = {}
    for name, channel in channels.items():
        if not hasattr(channel, "pixels"):
            continue
        pixels = np.asarray(channel.pixels, dtype=np.float32)
        if pixels.ndim == 3 and pixels.shape[2] == 1:
            pixels = pixels[:, :, 0]
        if pixels.ndim in {2, 3}:
            channel_data[str(name)] = np.ascontiguousarray(pixels)
    return channel_data


def _expanded_channel_names(channel_data: dict[str, np.ndarray]) -> list[str]:
    names: list[str] = ["rgba", "rgb", "r", "g", "b", "a", "luma"]
    for name, data in channel_data.items():
        if name not in names:
            names.append(name)
        if data.ndim != 3:
            continue
        components = ("R", "G", "B", "A") if data.shape[2] <= 4 else tuple(f"C{index}" for index in range(data.shape[2]))
        for index, component in enumerate(components[: data.shape[2]]):
            component_name = f"{name}.{component}"
            if component_name not in names:
                names.append(component_name)
    return names


def _channel_lookup(channel_data: dict[str, np.ndarray], name: str) -> np.ndarray | None:
    if name in channel_data:
        return channel_data[name]
    target = name.lower()
    for key, value in channel_data.items():
        if key.lower() == target:
            return value
    return None


def _pixel_aspect_from_metadata(metadata: dict[str, object]) -> float:
    value = metadata.get("exr/pixelAspectRatio", metadata.get("pixelAspectRatio", 1.0))
    try:
        pixel_aspect = float(value)
    except (TypeError, ValueError):
        pixel_aspect = 1.0
    return pixel_aspect if pixel_aspect > 0 else 1.0


def _metadata_value(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return f"{len(value)} bytes"
    if isinstance(value, (list, tuple)):
        return [_metadata_value(item) for item in value]
    if hasattr(value, "x") and hasattr(value, "y"):
        return [getattr(value, "x"), getattr(value, "y")]
    return str(value)
