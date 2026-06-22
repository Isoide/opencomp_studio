from __future__ import annotations

import math
from collections.abc import Mapping

BBox = dict[str, int]


def default_bbox(width: int, height: int) -> BBox:
    return {"x": 0, "y": 0, "width": max(0, int(width)), "height": max(0, int(height))}


def normalize_bbox(value: Mapping[str, object] | None, width: int, height: int) -> BBox:
    if value is None:
        return default_bbox(width, height)
    try:
        return {
            "x": int(round(float(value.get("x", 0)))),
            "y": int(round(float(value.get("y", 0)))),
            "width": max(0, int(round(float(value.get("width", width))))),
            "height": max(0, int(round(float(value.get("height", height))))),
        }
    except (TypeError, ValueError):
        return default_bbox(width, height)


def scale_bbox(box: Mapping[str, object] | None, scale_x: float, scale_y: float, width: int, height: int) -> BBox:
    source = normalize_bbox(box, width, height)
    return {
        "x": int(round(source["x"] * scale_x)),
        "y": int(round(source["y"] * scale_y)),
        "width": max(0, int(round(source["width"] * scale_x))),
        "height": max(0, int(round(source["height"] * scale_y))),
    }


def transform_bbox(
    box: Mapping[str, object] | None,
    scale: float,
    translate_x: float,
    translate_y: float,
    source_width: int,
    source_height: int,
) -> BBox:
    source = normalize_bbox(box, source_width, source_height)
    scaled_width = max(1, int(round(source_width * scale)))
    scaled_height = max(1, int(round(source_height * scale)))
    origin_x = (source_width - scaled_width) / 2.0 + translate_x
    origin_y = (source_height - scaled_height) / 2.0 + translate_y
    return {
        "x": int(round(origin_x + source["x"] * scale)),
        "y": int(round(origin_y + source["y"] * scale)),
        "width": max(0, int(round(source["width"] * scale))),
        "height": max(0, int(round(source["height"] * scale))),
    }


def affine_bbox(
    box: Mapping[str, object] | None,
    matrix: tuple[float, float, float, float, float, float],
    source_width: int,
    source_height: int,
) -> BBox:
    source = normalize_bbox(box, source_width, source_height)
    x0 = float(source["x"])
    y0 = float(source["y"])
    x1 = x0 + float(source["width"])
    y1 = y0 + float(source["height"])
    points = [
        _apply_affine(matrix, x0, y0),
        _apply_affine(matrix, x1, y0),
        _apply_affine(matrix, x1, y1),
        _apply_affine(matrix, x0, y1),
    ]
    min_x = math.floor(min(point[0] for point in points))
    min_y = math.floor(min(point[1] for point in points))
    max_x = math.ceil(max(point[0] for point in points))
    max_y = math.ceil(max(point[1] for point in points))
    return {"x": min_x, "y": min_y, "width": max(0, max_x - min_x), "height": max(0, max_y - min_y)}


def translate_bbox(box: Mapping[str, object] | None, offset_x: int, offset_y: int, width: int, height: int) -> BBox:
    source = normalize_bbox(box, width, height)
    return {
        "x": source["x"] + int(offset_x),
        "y": source["y"] + int(offset_y),
        "width": source["width"],
        "height": source["height"],
    }


def union_bbox(*boxes: Mapping[str, object] | None) -> BBox:
    normalized = [normalize_bbox(box, 0, 0) for box in boxes if box is not None]
    if not normalized:
        return default_bbox(0, 0)
    x0 = min(box["x"] for box in normalized)
    y0 = min(box["y"] for box in normalized)
    x1 = max(box["x"] + box["width"] for box in normalized)
    y1 = max(box["y"] + box["height"] for box in normalized)
    return {"x": x0, "y": y0, "width": max(0, x1 - x0), "height": max(0, y1 - y0)}


def intersect_bbox(*boxes: Mapping[str, object] | None) -> BBox:
    normalized = [normalize_bbox(box, 0, 0) for box in boxes if box is not None]
    if not normalized:
        return default_bbox(0, 0)
    x0 = max(box["x"] for box in normalized)
    y0 = max(box["y"] for box in normalized)
    x1 = min(box["x"] + box["width"] for box in normalized)
    y1 = min(box["y"] + box["height"] for box in normalized)
    return {"x": x0, "y": y0, "width": max(0, x1 - x0), "height": max(0, y1 - y0)}


def bbox_equal(a: Mapping[str, object] | None, b: Mapping[str, object] | None) -> bool:
    return normalize_bbox(a, 0, 0) == normalize_bbox(b, 0, 0)


def _apply_affine(matrix: tuple[float, float, float, float, float, float], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + b * y + c, d * x + e * y + f)
