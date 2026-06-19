from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, TypeVar

import numpy as np

from opencomp.core.models import ProjectSettings

T = TypeVar("T")


def tile_ranges(height: int, tile_height: int) -> Iterable[tuple[int, int]]:
    step = max(1, int(tile_height))
    for start in range(0, max(0, int(height)), step):
        yield start, min(start + step, height)


def tile_height(settings: ProjectSettings | None) -> int:
    if settings is None:
        return 64
    return max(1, int(settings.tile_height or 64))


def tile_workers(settings: ProjectSettings | None) -> int:
    if settings is None:
        return 1
    cpu_count = os.cpu_count() or 1
    requested = int(settings.tile_workers or 1)
    return max(1, min(requested, cpu_count))


def tile_rendering_enabled(settings: ProjectSettings | None, height: int) -> bool:
    if settings is None or not settings.tile_rendering_enabled:
        return False
    return height > tile_height(settings)


def map_row_tiles(
    height: int,
    settings: ProjectSettings | None,
    worker: Callable[[int, int], T],
) -> list[T]:
    ranges = list(tile_ranges(height, tile_height(settings)))
    workers = tile_workers(settings)
    if workers <= 1 or len(ranges) <= 1:
        return [worker(start, end) for start, end in ranges]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="opencomp-tile") as executor:
        return list(executor.map(lambda item: worker(item[0], item[1]), ranges))


def map_rgba_rows(
    source: np.ndarray,
    settings: ProjectSettings | None,
    worker: Callable[[np.ndarray], np.ndarray],
) -> np.ndarray:
    image = np.asarray(source, dtype=np.float32)
    if image.ndim != 3:
        raise ValueError("Tile processing requires an image with shape H x W x C.")
    if not tile_rendering_enabled(settings, image.shape[0]):
        return np.ascontiguousarray(worker(image).astype(np.float32))

    output = np.empty_like(image, dtype=np.float32)

    def process(start: int, end: int) -> None:
        output[start:end] = worker(image[start:end]).astype(np.float32)

    map_row_tiles(image.shape[0], settings, process)
    return np.ascontiguousarray(output)
