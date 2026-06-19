from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from opencomp.core.models import ImageFrame


def write_image(
    frame: ImageFrame,
    path: str,
    overwrite: bool = False,
    metadata_policy: str = "all",
    channels: str = "rgba",
    create_directories: bool = True,
) -> Path:
    output_path = Path(_resolve_frame_path(path, frame.frame))
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists and overwrite is disabled: {output_path}")
    suffix = output_path.suffix.lower()
    if create_directories:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    elif not output_path.parent.exists():
        raise FileNotFoundError(f"Output directory does not exist: {output_path.parent}")
    if suffix == ".exr":
        _write_exr(frame, output_path, metadata_policy, channels)
        return output_path
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise NotImplementedError("MVP writer supports EXR/PNG/JPG.")
    rgb_or_rgba = np.clip(_selected_rgba(frame, channels), 0.0, 1.0)
    if suffix in {".jpg", ".jpeg"}:
        array = (rgb_or_rgba[:, :, :3] * 255.0 + 0.5).astype(np.uint8)
        Image.fromarray(array, mode="RGB").save(output_path)
    else:
        array = (rgb_or_rgba * 255.0 + 0.5).astype(np.uint8)
        Image.fromarray(array, mode="RGBA").save(
            output_path,
            pnginfo=_png_metadata(frame.metadata) if metadata_policy != "none" else None,
        )
    return output_path


def _write_exr(frame: ImageFrame, output_path: Path, metadata_policy: str, channels: str) -> None:
    try:
        import OpenEXR  # type: ignore
    except ImportError as exc:
        raise NotImplementedError("EXR writing requires the OpenEXR Python package.") from exc

    data = np.ascontiguousarray(_selected_rgba(frame, channels)[:, :, :4], dtype=np.float32)
    if data.shape[2] < 4:
        data = _ensure_rgba(data)

    header: dict[str, Any] = {
        "compression": OpenEXR.ZIP_COMPRESSION,
        "type": OpenEXR.scanlineimage,
        "pixelAspectRatio": float(frame.pixel_aspect),
        "opencomp.colorspace": frame.colorspace,
        "opencomp.frame": int(frame.frame),
    }
    if metadata_policy != "none":
        header.update(_exr_metadata(frame.metadata))

    if hasattr(OpenEXR, "File"):
        exr_channels = _exr_v3_channels(frame, data, channels)
        with OpenEXR.File(header, exr_channels) as outfile:
            outfile.write(str(output_path))
        return

    _write_exr_legacy(OpenEXR, data, header, output_path, frame, channels)


def _write_exr_legacy(
    OpenEXR: Any,
    data: np.ndarray,
    header_metadata: dict[str, Any],
    output_path: Path,
    frame: ImageFrame,
    channels: str,
) -> None:
    try:
        import Imath  # type: ignore
    except ImportError as exc:
        raise NotImplementedError("Legacy EXR writing requires Imath.") from exc

    height, width = data.shape[:2]
    pixel_type = Imath.PixelType(Imath.PixelType.FLOAT)
    header = OpenEXR.Header(width, height)
    planes = _legacy_exr_planes(frame, data, channels)
    header["channels"] = {name: Imath.Channel(pixel_type) for name in planes}
    for key, value in header_metadata.items():
        if isinstance(value, (str, int, float)):
            header[key] = value
    outfile = OpenEXR.OutputFile(str(output_path), header)
    try:
        outfile.writePixels(
            {name: np.ascontiguousarray(plane, dtype=np.float32).tobytes() for name, plane in planes.items()}
        )
    finally:
        outfile.close()


def _ensure_rgba(data: np.ndarray) -> np.ndarray:
    height, width, channels = data.shape
    rgba = np.zeros((height, width, 4), dtype=np.float32)
    rgba[:, :, :channels] = data
    rgba[:, :, 3] = 1.0
    return rgba


def _resolve_frame_path(path: str, frame: int) -> str:
    if "####" in path:
        return path.replace("####", f"{frame:04d}")
    if "%04d" in path:
        return path % frame
    if "%d" in path:
        return path % frame
    return path


def _selected_rgba(frame: ImageFrame, channels: str) -> np.ndarray:
    selected = str(channels or "rgba").lower()
    data = frame.data.copy()
    if selected in {"all", "rgba"}:
        return data
    if selected == "rgb":
        data[:, :, 3] = 1.0
        return data
    if selected in {"alpha", "a"}:
        data[:, :, 0:3] = data[:, :, 3:4]
        data[:, :, 3] = 1.0
        return data
    keep = set()
    if "r" in selected:
        keep.add(0)
    if "g" in selected:
        keep.add(1)
    if "b" in selected:
        keep.add(2)
    if "a" in selected:
        keep.add(3)
    for index in range(4):
        if index not in keep:
            data[:, :, index] = 1.0 if index == 3 else 0.0
    return data


def _exr_v3_channels(frame: ImageFrame, rgba: np.ndarray, channels: str) -> dict[str, np.ndarray]:
    selected = str(channels or "rgba").lower()
    if selected == "all":
        result = {"RGBA": np.ascontiguousarray(rgba, dtype=np.float32)}
        for name, value in frame.channel_data.items():
            result[name] = np.ascontiguousarray(value, dtype=np.float32)
        return result
    if selected == "rgb":
        return {"RGB": np.ascontiguousarray(rgba[:, :, :3], dtype=np.float32)}
    return {"RGBA": np.ascontiguousarray(rgba, dtype=np.float32)}


def _legacy_exr_planes(frame: ImageFrame, rgba: np.ndarray, channels: str) -> dict[str, np.ndarray]:
    selected = str(channels or "rgba").lower()
    planes: dict[str, np.ndarray] = {
        "R": rgba[:, :, 0],
        "G": rgba[:, :, 1],
        "B": rgba[:, :, 2],
    }
    if selected != "rgb":
        planes["A"] = rgba[:, :, 3]
    if selected == "all":
        for layer, value in frame.channel_data.items():
            if value.ndim == 2:
                planes[layer] = value
                continue
            suffixes = ("R", "G", "B", "A") if value.shape[2] <= 4 else tuple(f"C{index}" for index in range(value.shape[2]))
            for index, suffix in enumerate(suffixes[: value.shape[2]]):
                planes[f"{layer}.{suffix}"] = value[:, :, index]
    return planes


def _png_metadata(metadata: dict[str, object]) -> PngInfo:
    png_info = PngInfo()
    for key, value in metadata.items():
        if value is None:
            continue
        png_info.add_text(str(key), _metadata_text(value))
    return png_info


def _exr_metadata(metadata: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        safe_key = _safe_exr_key(str(key))
        result[safe_key] = _metadata_text(value)
    return result


def _safe_exr_key(key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", key).strip("_.")
    if not safe:
        safe = "metadata"
    return f"opencomp.{safe}"[:240]


def _metadata_text(value: object) -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return repr(value)
