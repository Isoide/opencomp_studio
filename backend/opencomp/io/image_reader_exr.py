"""EXR-specific image loading helpers for OpenComp.

This module owns OpenEXR and OpenImageIO read paths, channel selection, and
display/data-window expansion for EXR sources. It keeps the main image reader
entrypoint focused on high-level format dispatch instead of EXR details.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable, Literal

import numpy as np

from opencomp.core.models import ImageFrame
from opencomp.io.backend_support import resolve_exr_backend_modules
from opencomp.io.image_reader_support import base_file_metadata, metadata_value


def read_exr_image(
    path: Path,
    frame: int,
    colorspace: str,
    read_channels: Iterable[str] | None = None,
    backend: Literal["auto", "openexr", "oiio"] = "auto",
) -> ImageFrame:
    """Read one EXR image using the preferred optional backend and fallbacks."""

    normalized_backend, oiio, openexr = resolve_exr_backend_modules(backend)

    if openexr is not None:
        single_channel = _legacy_single_channel_candidate(openexr, path, read_channels)
        if single_channel is not None:
            try:
                return _read_exr_legacy_single_channel(openexr, path, frame, colorspace, single_channel)
            except Exception:
                pass

    if normalized_backend in {"auto", "oiio"} and oiio is not None:
        try:
            return _read_exr_oiio(oiio, path, frame, colorspace, read_channels=read_channels)
        except Exception:
            if normalized_backend == "oiio" and openexr is None:
                raise

    if openexr is None:
        raise NotImplementedError(
            "EXR reading requires OpenImageIO or the OpenEXR Python package. Install backend[oiio] or backend[exr]."
        )
    return _read_exr_openexr(openexr, path, frame, colorspace, read_channels=read_channels)


def _read_exr_openexr(
    OpenEXR: Any,
    path: Path,
    frame: int,
    colorspace: str,
    read_channels: Iterable[str] | None = None,
) -> ImageFrame:
    if hasattr(OpenEXR, "File"):
        try:
            return _read_exr_v3(OpenEXR, path, frame, colorspace, read_channels=read_channels)
        except Exception:
            pass
    return _read_exr_legacy(OpenEXR, path, frame, colorspace, read_channels=read_channels)


def _read_exr_oiio(
    oiio: Any,
    path: Path,
    frame: int,
    colorspace: str,
    read_channels: Iterable[str] | None = None,
) -> ImageFrame:
    inp = oiio.ImageInput.open(str(path))
    if not inp:
        error = oiio.geterror() if hasattr(oiio, "geterror") else "OpenImageIO could not open the EXR."
        raise RuntimeError(error)
    try:
        spec = inp.spec()
        pixels = inp.read_image(oiio.FLOAT)
        if pixels is None:
            raise RuntimeError(inp.geterror() or f"OpenImageIO could not read image data from {path}.")
        pixels = np.ascontiguousarray(np.asarray(pixels, dtype=np.float32))
        if pixels.ndim != 3:
            raise RuntimeError(f"Unexpected OpenImageIO image layout {pixels.shape!r} for {path}.")
        selected_names = _oiio_select_channel_names(tuple(map(str, spec.channelnames)), read_channels)
        channel_data = _selected_oiio_channel_data(pixels, tuple(map(str, spec.channelnames)), selected_names)
        rgba = _rgba_from_channel_data(channel_data, spec.width, spec.height)
        exr_metadata = _oiio_header_metadata(spec)
        exr_metadata["exr/read_method"] = "oiio"
        exr_metadata["exr/backend"] = "oiio"
        channel_names = _header_channel_names({"channels": list(map(str, spec.channelnames))}, channel_data)
        return _exr_frame(
            path,
            frame,
            colorspace,
            rgba,
            channel_data,
            channel_names,
            exr_metadata,
            _oiio_data_window_bbox(spec),
        )
    finally:
        inp.close()


def _read_exr_v3(
    OpenEXR: Any,
    path: Path,
    frame: int,
    colorspace: str,
    read_channels: Iterable[str] | None = None,
) -> ImageFrame:
    exr = OpenEXR.File(str(path))
    header = exr.header()
    channels = exr.channels()
    channel_data = _exr_v3_channel_data(channels, read_channels)
    channel_data = _expand_channel_data_to_display(channel_data, header)
    channel_names = _header_channel_names(header, channel_data)
    data_window = _data_window_bbox(header)

    rgba_group = channel_data.get("RGBA") or channel_data.get("rgba")
    if rgba_group is not None and rgba_group.ndim == 3 and rgba_group.shape[2] >= 4:
        data = np.ascontiguousarray(rgba_group[:, :, :4], dtype=np.float32)
        return _exr_frame(path, frame, colorspace, data, channel_data, channel_names, _exr_header_metadata(exr), data_window)

    data = _rgba_from_channel_data(channel_data, data_window["width"], data_window["height"], path=path)
    return _exr_frame(path, frame, colorspace, data, channel_data, channel_names, _exr_header_metadata(exr), data_window)


def _read_exr_legacy(
    OpenEXR: Any,
    path: Path,
    frame: int,
    colorspace: str,
    read_channels: Iterable[str] | None = None,
) -> ImageFrame:
    try:
        import Imath  # type: ignore
    except ImportError as exc:
        raise NotImplementedError(
            "This OpenEXR install needs Imath for legacy EXR reading, but Imath is missing."
        ) from exc

    exr = OpenEXR.InputFile(str(path))
    header = exr.header()
    data_window = header["dataWindow"]
    data_width = data_window.max.x - data_window.min.x + 1
    data_height = data_window.max.y - data_window.min.y + 1
    display_width, display_height = _display_size(header)
    pixel_type = Imath.PixelType(Imath.PixelType.FLOAT)
    available = list(header.get("channels", {}).keys())
    selected = _select_legacy_channel_names(available, read_channels)
    channel_data: dict[str, np.ndarray] = {}
    for channel in selected:
        try:
            buffer = exr.channel(channel, pixel_type)
        except Exception:
            continue
        channel_data[channel] = np.frombuffer(buffer, dtype=np.float32).reshape(data_height, data_width)
    channel_data = _expand_channel_data_to_display(channel_data, header)

    data = _rgba_from_channel_data(channel_data, display_width, display_height)
    pixel_aspect = _pixel_aspect_from_metadata(header)
    metadata = {
        **base_file_metadata(path, frame, colorspace, display_width, display_height),
        "input/pixel_aspect": pixel_aspect,
        "exr/channels": _header_channel_names(header, channel_data),
    }
    for key, value in header.items():
        if key == "channels":
            continue
        metadata[f"exr/{key}"] = metadata_value(value)
    return ImageFrame(
        width=display_width,
        height=display_height,
        data=data,
        channels=_header_channel_names(header, channel_data),
        channel_data=channel_data,
        pixel_aspect=pixel_aspect,
        colorspace=colorspace,
        frame=frame,
        metadata=metadata,
        data_window=_data_window_bbox(header),
    )


def _read_exr_legacy_single_channel(
    OpenEXR: Any,
    path: Path,
    frame: int,
    colorspace: str,
    channel_name: str,
) -> ImageFrame:
    try:
        import Imath  # type: ignore
    except ImportError as exc:
        raise NotImplementedError(
            "This OpenEXR install needs Imath for legacy EXR reading, but Imath is missing."
        ) from exc

    exr = OpenEXR.InputFile(str(path))
    try:
        header = exr.header()
        data_window = header["dataWindow"]
        data_width = data_window.max.x - data_window.min.x + 1
        data_height = data_window.max.y - data_window.min.y + 1
        display_width, display_height = _display_size(header)
        pixel_type = Imath.PixelType(Imath.PixelType.FLOAT)
        buffer = exr.channel(channel_name, pixel_type)
        plane = np.frombuffer(buffer, dtype=np.float32).reshape(data_height, data_width)
        channel_data = _expand_channel_data_to_display({channel_name: plane}, header)
        display_plane = channel_data[channel_name]

        data = np.zeros((display_height, display_width, 4), dtype=np.float32)
        main_index = {"r": 0, "g": 1, "b": 2, "a": 3}.get(channel_name.lower())
        if main_index is not None:
            data[:, :, main_index] = display_plane
        if main_index != 3:
            data[:, :, 3] = 1.0

        pixel_aspect = _pixel_aspect_from_metadata(header)
        metadata = {
            **base_file_metadata(path, frame, colorspace, display_width, display_height),
            "input/pixel_aspect": pixel_aspect,
            "exr/channels": _header_channel_names(header, channel_data),
            "exr/read_method": "legacy_single_channel",
            "exr/read_channel": channel_name,
        }
        for key, value in header.items():
            if key == "channels":
                continue
            metadata[f"exr/{key}"] = metadata_value(value)
        return ImageFrame(
            width=display_width,
            height=display_height,
            data=data,
            channels=_header_channel_names(header, channel_data),
            channel_data=channel_data,
            pixel_aspect=pixel_aspect,
            colorspace=colorspace,
            frame=frame,
            metadata=metadata,
            data_window=_data_window_bbox(header),
        )
    finally:
        exr.close()


def _legacy_single_channel_candidate(OpenEXR: Any, path: Path, read_channels: Iterable[str] | None) -> str | None:
    if read_channels is None or not hasattr(OpenEXR, "InputFile"):
        return None
    try:
        exr = OpenEXR.InputFile(str(path))
    except Exception:
        return None
    try:
        header = exr.header()
        available = list(header.get("channels", {}).keys())
    except Exception:
        return None
    finally:
        exr.close()
    selected = _select_legacy_channel_names(available, read_channels)
    if len(selected) != 1:
        return None
    requested = _requested_channel_set(read_channels)
    if requested is None:
        return None
    selected_lower = selected[0].lower()
    if selected_lower in {"rgba", "rgb"}:
        return None
    if any(_channel_pattern_matches(selected_lower, channel) for channel in requested):
        return selected[0]
    if selected_lower in requested:
        return selected[0]
    if selected_lower in {"r", "g", "b", "a"} and requested.intersection({"r", "g", "b", "a"}):
        return selected[0]
    return None


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
        metadata[f"exr/{key}"] = metadata_value(value)
    return metadata


def _exr_frame(
    path: Path,
    frame: int,
    colorspace: str,
    data: np.ndarray,
    channel_data: dict[str, np.ndarray],
    channel_names: list[str],
    exr_metadata: dict[str, object],
    data_window: dict[str, int] | None = None,
) -> ImageFrame:
    pixel_aspect = _pixel_aspect_from_metadata(exr_metadata)
    metadata = {
        **base_file_metadata(path, frame, colorspace, data.shape[1], data.shape[0]),
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
        data_window=data_window,
    )


def _selected_oiio_channel_data(
    pixels: np.ndarray,
    channel_names: tuple[str, ...],
    selected_names: set[str] | None,
) -> dict[str, np.ndarray]:
    channel_data: dict[str, np.ndarray] = {}
    for index, name in enumerate(channel_names):
        if selected_names is not None and name not in selected_names:
            continue
        if index >= pixels.shape[2]:
            continue
        channel_data[name] = np.ascontiguousarray(pixels[:, :, index], dtype=np.float32)
    if channel_data:
        return channel_data
    for index, name in enumerate(channel_names):
        if index >= pixels.shape[2]:
            continue
        channel_data[name] = np.ascontiguousarray(pixels[:, :, index], dtype=np.float32)
    return channel_data


def _exr_v3_channel_data(channels: Any, read_channels: Iterable[str] | None = None) -> dict[str, np.ndarray]:
    channel_data: dict[str, np.ndarray] = {}
    selected = _select_v3_channel_names(channels.keys(), read_channels)
    for name, channel in channels.items():
        if selected is not None and str(name) not in selected:
            continue
        if not hasattr(channel, "pixels"):
            continue
        pixels = np.asarray(channel.pixels, dtype=np.float32)
        if pixels.ndim == 3 and pixels.shape[2] == 1:
            pixels = pixels[:, :, 0]
        if pixels.ndim in {2, 3}:
            channel_data[str(name)] = np.ascontiguousarray(pixels)
    return channel_data


def _select_v3_channel_names(channel_names: Iterable[str], read_channels: Iterable[str] | None) -> set[str] | None:
    requested = _requested_channel_set(read_channels)
    if requested is None:
        return None
    selected: set[str] = set()
    for name in map(str, channel_names):
        lower = name.lower()
        if lower in requested:
            selected.add(name)
            continue
        if lower == "rgba" and requested.intersection({"r", "g", "b", "a", "rgb", "rgba", "red", "green", "blue", "alpha"}):
            selected.add(name)
            continue
        if any(_channel_pattern_matches(lower, channel) for channel in requested):
            selected.add(name)
            continue
        if any(channel == lower or channel.startswith(f"{lower}.") for channel in requested):
            selected.add(name)
    return selected


def _oiio_select_channel_names(channel_names: Iterable[str], read_channels: Iterable[str] | None) -> set[str] | None:
    requested = _requested_channel_set(read_channels)
    if requested is None:
        return None
    selected: set[str] = set()
    for name in map(str, channel_names):
        lower = name.lower()
        layer = lower.rsplit(".", 1)[0] if "." in lower else lower
        if lower in requested or layer in requested:
            selected.add(name)
            continue
        if any(_channel_pattern_matches(lower, channel) or _channel_pattern_matches(layer, channel) for channel in requested):
            selected.add(name)
            continue
        if lower in {"r", "g", "b", "a"} and requested.intersection({"rgba", "rgb", "red", "green", "blue", "alpha"}):
            selected.add(name)
    return selected


def _select_legacy_channel_names(channel_names: Iterable[str], read_channels: Iterable[str] | None) -> list[str]:
    requested = _requested_channel_set(read_channels)
    names = list(map(str, channel_names))
    if requested is None:
        return names
    selected: list[str] = []
    for name in names:
        lower = name.lower()
        layer = lower.rsplit(".", 1)[0] if "." in lower else lower
        if lower in requested or layer in requested:
            selected.append(name)
            continue
        if any(_channel_pattern_matches(lower, channel) or _channel_pattern_matches(layer, channel) for channel in requested):
            selected.append(name)
            continue
        if lower in {"r", "g", "b", "a"} and requested.intersection({"rgba", "rgb", "red", "green", "blue", "alpha"}):
            selected.append(name)
    return selected


def _requested_channel_set(read_channels: Iterable[str] | None) -> set[str] | None:
    if read_channels is None:
        return None
    requested = {str(channel).strip().lower() for channel in read_channels if str(channel).strip()}
    if not requested or "all" in requested or "*" in requested:
        return None
    aliases = {
        "red": "r",
        "green": "g",
        "blue": "b",
        "alpha": "a",
    }
    expanded = set(requested)
    for name in list(requested):
        expanded.add(aliases.get(name, name))
    return expanded


def _channel_pattern_matches(name: str, requested: str) -> bool:
    if "*" not in requested and "?" not in requested:
        return False
    return fnmatch(name, requested)


def _expand_channel_data_to_display(channel_data: dict[str, np.ndarray], header: dict[str, object]) -> dict[str, np.ndarray]:
    if not channel_data:
        return channel_data
    display_width, display_height = _display_size(header)
    data_box = _data_window_bbox(header)
    expanded: dict[str, np.ndarray] = {}
    for name, pixels in channel_data.items():
        expanded[name] = _expand_pixels_to_display(np.asarray(pixels, dtype=np.float32), display_width, display_height, data_box)
    return expanded


def _expand_pixels_to_display(
    pixels: np.ndarray,
    display_width: int,
    display_height: int,
    data_box: dict[str, int],
) -> np.ndarray:
    if pixels.shape[:2] == (display_height, display_width) and data_box == {
        "x": 0,
        "y": 0,
        "width": display_width,
        "height": display_height,
    }:
        return np.ascontiguousarray(pixels, dtype=np.float32)

    output_shape = (display_height, display_width, *pixels.shape[2:]) if pixels.ndim > 2 else (display_height, display_width)
    output = np.zeros(output_shape, dtype=np.float32)
    src_height, src_width = pixels.shape[:2]
    dst_x0 = max(0, data_box["x"])
    dst_y0 = max(0, data_box["y"])
    src_x0 = max(0, -data_box["x"])
    src_y0 = max(0, -data_box["y"])
    width = min(src_width - src_x0, display_width - dst_x0)
    height = min(src_height - src_y0, display_height - dst_y0)
    if width > 0 and height > 0:
        output[dst_y0 : dst_y0 + height, dst_x0 : dst_x0 + width] = pixels[
            src_y0 : src_y0 + height,
            src_x0 : src_x0 + width,
        ]
    return np.ascontiguousarray(output)


def _display_size(header: dict[str, object]) -> tuple[int, int]:
    display = header.get("displayWindow") or header.get("dataWindow")
    min_x, min_y, max_x, max_y = _window_bounds(display)
    return max(1, max_x - min_x + 1), max(1, max_y - min_y + 1)


def _data_window_bbox(header: dict[str, object]) -> dict[str, int]:
    data = header.get("dataWindow")
    display = header.get("displayWindow") or data
    data_min_x, data_min_y, data_max_x, data_max_y = _window_bounds(data)
    display_min_x, display_min_y, _display_max_x, _display_max_y = _window_bounds(display)
    return {
        "x": int(data_min_x - display_min_x),
        "y": int(data_min_y - display_min_y),
        "width": max(0, int(data_max_x - data_min_x + 1)),
        "height": max(0, int(data_max_y - data_min_y + 1)),
    }


def _window_bounds(window: object) -> tuple[int, int, int, int]:
    if hasattr(window, "min") and hasattr(window, "max"):
        return (
            int(getattr(window.min, "x")),
            int(getattr(window.min, "y")),
            int(getattr(window.max, "x")),
            int(getattr(window.max, "y")),
        )
    if isinstance(window, (list, tuple)) and len(window) == 2:
        minimum, maximum = window
        return int(minimum[0]), int(minimum[1]), int(maximum[0]), int(maximum[1])
    raise ValueError(f"Unsupported EXR window metadata: {window!r}")


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


def _header_channel_names(header: dict[str, object], channel_data: dict[str, np.ndarray]) -> list[str]:
    names = ["rgba", "rgb", "r", "g", "b", "a", "luma"]
    raw_channels = header.get("channels")
    if isinstance(raw_channels, dict):
        for name in raw_channels.keys():
            _append_channel_name(names, str(name))
    elif isinstance(raw_channels, list):
        for channel in raw_channels:
            name = getattr(channel, "name", None)
            if name is not None:
                _append_channel_name(names, str(name))
    for name in _expanded_channel_names(channel_data):
        _append_channel_name(names, name)
    return names


def _append_channel_name(names: list[str], name: str) -> None:
    if name not in names:
        names.append(name)
    if "." in name:
        layer = name.rsplit(".", 1)[0]
        if layer not in names:
            names.append(layer)


def _channel_lookup(channel_data: dict[str, np.ndarray], name: str) -> np.ndarray | None:
    if name in channel_data:
        return channel_data[name]
    target = name.lower()
    for key, value in channel_data.items():
        if key.lower() == target:
            return value
    return None


def _rgba_from_channel_data(
    channel_data: dict[str, np.ndarray],
    width: int,
    height: int,
    *,
    path: Path | None = None,
) -> np.ndarray:
    planes: list[np.ndarray] = []
    for index, channel_name in enumerate(("R", "G", "B", "A")):
        plane = _channel_lookup(channel_data, channel_name)
        if plane is None:
            if index == 0 and path is not None:
                raise NotImplementedError(f"EXR file has no readable {channel_name} channel: {path}")
            fill_value = 1.0 if channel_name == "A" else 0.0
            plane_array = np.full((height, width), fill_value, dtype=np.float32)
        else:
            plane_array = np.asarray(plane, dtype=np.float32)
            if plane_array.ndim == 3:
                plane_array = plane_array[:, :, 0]
        planes.append(np.ascontiguousarray(plane_array, dtype=np.float32))
    return np.stack(planes, axis=-1).astype(np.float32)


def _pixel_aspect_from_metadata(metadata: dict[str, object]) -> float:
    value = metadata.get("exr/pixelAspectRatio", metadata.get("pixelAspectRatio", 1.0))
    try:
        pixel_aspect = float(value)
    except (TypeError, ValueError):
        pixel_aspect = 1.0
    return pixel_aspect if pixel_aspect > 0 else 1.0


def _oiio_data_window_bbox(spec: Any) -> dict[str, int] | None:
    full_width = int(getattr(spec, "full_width", 0) or 0)
    full_height = int(getattr(spec, "full_height", 0) or 0)
    width = int(getattr(spec, "width", 0) or 0)
    height = int(getattr(spec, "height", 0) or 0)
    full_x = int(getattr(spec, "full_x", 0) or 0)
    full_y = int(getattr(spec, "full_y", 0) or 0)
    x = int(getattr(spec, "x", 0) or 0)
    y = int(getattr(spec, "y", 0) or 0)
    if full_width <= 0 or full_height <= 0 or (width == full_width and height == full_height and x == full_x and y == full_y):
        return None
    return {
        "x": x - full_x,
        "y": y - full_y,
        "width": width,
        "height": height,
    }


def _oiio_header_metadata(spec: Any) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in ("full_x", "full_y", "full_width", "full_height", "x", "y", "z", "width", "height", "depth"):
        if hasattr(spec, key):
            metadata[f"exr/{key}"] = metadata_value(getattr(spec, key))
    for attribute_name in ("PixelAspectRatio", "pixelAspectRatio", "oiio:ColorSpace"):
        value = spec.getattribute(attribute_name) if hasattr(spec, "getattribute") else None
        if value is not None:
            metadata[f"exr/{attribute_name}"] = metadata_value(value)
            if attribute_name.lower() == "pixelaspectratio":
                metadata["pixelAspectRatio"] = metadata_value(value)
    extra_attribs = getattr(spec, "extra_attribs", None)
    if extra_attribs is None:
        return metadata
    try:
        count = len(extra_attribs)
    except Exception:
        return metadata
    for index in range(count):
        try:
            param = extra_attribs[index]
        except Exception:
            continue
        name = getattr(param, "name", None)
        if not name:
            continue
        try:
            value = spec.getattribute(name)
        except Exception:
            value = None
        metadata[f"exr/{name}"] = metadata_value(value)
    return metadata
