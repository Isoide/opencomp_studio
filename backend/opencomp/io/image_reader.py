"""High-level image loading entrypoints for OpenComp backend nodes.

This module resolves sequence paths, dispatches by file format, and normalizes
simple raster sources into ImageFrame objects. EXR-specific parsing lives in a
dedicated helper module so the public reader flow stays short and readable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Literal

import numpy as np
from PIL import Image

from opencomp.core.models import ImageFrame
from opencomp.io.image_reader_exr import read_exr_image
from opencomp.io.image_reader_support import base_file_metadata, metadata_value, pillow_metadata
from opencomp.io.path_utils import local_path, resolve_sequence_path


def read_image(
    path: str,
    frame: int | None = None,
    colorspace: str = "Utility - sRGB - Texture",
    read_channels: Iterable[str] | None = None,
    backend: Literal["auto", "openexr", "oiio"] = "auto",
) -> ImageFrame:
    """Read one image or sequence frame and return it as an ImageFrame."""

    resolved = resolve_sequence_path(path, frame)
    if resolved.startswith("builtin://"):
        return _read_builtin(resolved, frame or 1001, colorspace)

    file_path = local_path(resolved)
    if not file_path.exists():
        raise FileNotFoundError(f"Input image does not exist: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        return _read_pillow(file_path, frame or 1001, colorspace)
    if suffix == ".exr":
        return read_exr_image(file_path, frame or 1001, colorspace, read_channels=read_channels, backend=backend)
    raise NotImplementedError(f"Unsupported image extension '{suffix}' for {file_path}")


def image_source_fingerprint(path: str, frame: int | None = None) -> dict[str, object]:
    """Build a cache-friendly source fingerprint for one path/frame reference."""

    resolved = resolve_sequence_path(path, frame)
    if resolved.startswith("builtin://"):
        return {"kind": "builtin", "path": resolved}
    if "://" in resolved:
        return {"kind": "virtual", "path": resolved}

    file_path = local_path(resolved)
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
        metadata = base_file_metadata(path, frame, colorspace, image.width, image.height)
        metadata.update(pillow_metadata(image))
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
