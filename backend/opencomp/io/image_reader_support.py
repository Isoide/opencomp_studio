"""Shared metadata helpers for backend image reader modules.

This module keeps small path, Pillow, and metadata-normalization helpers out of
the main reader entrypoints. It exists to make raster and EXR reader modules
shorter without changing the public image-loading behavior.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def base_file_metadata(path: Path, frame: int, colorspace: str, width: int, height: int) -> dict[str, object]:
    """Build metadata common to all file-backed image reads."""

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


def pillow_metadata(image: Image.Image) -> dict[str, object]:
    """Extract lightweight Pillow and EXIF metadata for raster images."""

    metadata: dict[str, object] = {}
    for key, value in image.info.items():
        if key in {"exif", "icc_profile"}:
            metadata[f"image/{key}_bytes"] = len(value) if isinstance(value, bytes) else str(value)
            continue
        metadata[f"image/{key}"] = metadata_value(value)
    try:
        exif = image.getexif()
    except Exception:
        return metadata
    for key, value in exif.items():
        metadata[f"exif/{key}"] = metadata_value(value)
    return metadata


def metadata_value(value: object) -> object:
    """Normalize backend-specific metadata values into JSON-friendly objects."""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return f"{len(value)} bytes"
    if isinstance(value, (list, tuple)):
        return [metadata_value(item) for item in value]
    if hasattr(value, "x") and hasattr(value, "y"):
        return [getattr(value, "x"), getattr(value, "y")]
    return str(value)
