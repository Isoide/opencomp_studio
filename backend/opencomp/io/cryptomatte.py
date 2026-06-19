from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

import numpy as np

from opencomp.core.tile_engine import map_row_tiles, tile_rendering_enabled
from opencomp.core.models import ImageFrame


@dataclass(frozen=True, slots=True)
class CryptomatteLayer:
    key: str
    name: str
    hash: str
    conversion: str
    manifest: dict[str, str]
    channels: list[str]


@dataclass(frozen=True, slots=True)
class CryptomattePick:
    layer: str
    id_hex: str
    id_float: float
    name: str | None
    coverage: float
    x: int
    y: int


def cryptomatte_layers(image: ImageFrame) -> list[CryptomatteLayer]:
    namespaces: dict[str, dict[str, Any]] = {}
    for key, value in image.metadata.items():
        match = re.match(r"^(?:exr/)?cryptomatte/([^/]+)/([^/]+)$", str(key))
        if not match:
            continue
        namespace, field = match.groups()
        namespaces.setdefault(namespace, {})[field] = value

    layers: list[CryptomatteLayer] = []
    for namespace, fields in namespaces.items():
        name = str(fields.get("name") or namespace)
        manifest = _parse_manifest(fields.get("manifest"))
        layers.append(
            CryptomatteLayer(
                key=namespace,
                name=name,
                hash=str(fields.get("hash") or ""),
                conversion=str(fields.get("conversion") or ""),
                manifest=manifest,
                channels=_cryptomatte_channel_groups(image, name),
            )
        )
    return sorted(layers, key=lambda layer: layer.name.lower())


def cryptomatte_layer_payload(layer: CryptomatteLayer, limit: int = 2000) -> dict[str, Any]:
    entries = [
        {"name": name, "id": matte_id}
        for name, matte_id in sorted(layer.manifest.items(), key=lambda item: item[0].lower())[:limit]
    ]
    return {
        "key": layer.key,
        "name": layer.name,
        "hash": layer.hash,
        "conversion": layer.conversion,
        "manifest_count": len(layer.manifest),
        "manifest_entries": entries,
        "channels": layer.channels,
    }


def pick_cryptomatte_id(image: ImageFrame, layer_name: str | None, x: int, y: int) -> CryptomattePick | None:
    layer = _find_layer(image, layer_name)
    if layer is None:
        return None
    x = max(0, min(int(x), image.width - 1))
    y = max(0, min(int(y), image.height - 1))

    best: CryptomattePick | None = None
    id_to_name = {matte_id.lower(): name for name, matte_id in layer.manifest.items()}
    for group_name in layer.channels:
        group = image.channel_data.get(group_name)
        if group is None or group.ndim != 3:
            continue
        for id_index, coverage_index in _cryptomatte_pairs(group):
            coverage = float(group[y, x, coverage_index])
            if coverage <= 0.0:
                continue
            id_value = np.float32(group[y, x, id_index])
            id_hex = float32_to_hex(id_value)
            if id_hex == "00000000":
                continue
            candidate = CryptomattePick(
                layer=layer.name,
                id_hex=id_hex,
                id_float=float(id_value),
                name=id_to_name.get(id_hex.lower()),
                coverage=coverage,
                x=x,
                y=y,
            )
            if best is None or candidate.coverage > best.coverage:
                best = candidate
    return best


def build_cryptomatte_matte(image: ImageFrame, layer_name: str | None, matte_ids: list[str], settings=None) -> np.ndarray:
    layer = _find_layer(image, layer_name)
    matte = np.zeros((image.height, image.width), dtype=np.float32)
    if layer is None or not matte_ids:
        return matte

    target_uints = {hex_to_uint32(item) for item in _resolve_matte_ids(layer, matte_ids)}
    if not target_uints:
        return matte
    target_list = list(target_uints)

    if tile_rendering_enabled(settings, image.height):
        def process(start: int, end: int) -> np.ndarray:
            tile_matte = np.zeros((end - start, image.width), dtype=np.float32)
            for group_name in layer.channels:
                group = image.channel_data.get(group_name)
                if group is None or group.ndim != 3:
                    continue
                group_tile = group[start:end]
                for id_index, coverage_index in _cryptomatte_pairs(group_tile):
                    id_uints = np.ascontiguousarray(group_tile[:, :, id_index].astype(np.float32)).view(np.uint32)
                    coverage = np.clip(group_tile[:, :, coverage_index], 0.0, 1.0)
                    selected = np.isin(id_uints, target_list)
                    tile_matte = np.where(selected, tile_matte + coverage, tile_matte)
            return np.clip(tile_matte, 0.0, 1.0).astype(np.float32)

        return np.vstack(map_row_tiles(image.height, settings, process))

    for group_name in layer.channels:
        group = image.channel_data.get(group_name)
        if group is None or group.ndim != 3:
            continue
        for id_index, coverage_index in _cryptomatte_pairs(group):
            id_uints = np.ascontiguousarray(group[:, :, id_index].astype(np.float32)).view(np.uint32)
            coverage = np.clip(group[:, :, coverage_index], 0.0, 1.0)
            selected = np.isin(id_uints, target_list)
            matte = np.where(selected, matte + coverage, matte)
    return np.clip(matte, 0.0, 1.0).astype(np.float32)


def cryptomatte_preview_rgba(matte: np.ndarray) -> np.ndarray:
    matte = np.clip(np.asarray(matte, dtype=np.float32), 0.0, 1.0)
    rgba = np.zeros((*matte.shape, 4), dtype=np.float32)
    rgba[:, :, 0] = 1.0
    rgba[:, :, 1] = 0.82
    rgba[:, :, 2] = 0.08
    rgba[:, :, 3] = matte
    return rgba


def cryptomatte_id_preview_rgba(image: ImageFrame, layer_name: str | None, settings=None) -> np.ndarray:
    layer = _find_layer(image, layer_name)
    if layer is None:
        return np.zeros((image.height, image.width, 4), dtype=np.float32)

    def process(start: int, end: int) -> np.ndarray:
        best_coverage = np.zeros((end - start, image.width), dtype=np.float32)
        best_id = np.zeros((end - start, image.width), dtype=np.uint32)
        for group_name in layer.channels:
            group = image.channel_data.get(group_name)
            if group is None or group.ndim != 3:
                continue
            group_tile = group[start:end]
            for id_index, coverage_index in _cryptomatte_pairs(group_tile):
                coverage = np.clip(group_tile[:, :, coverage_index], 0.0, 1.0)
                ids = np.ascontiguousarray(group_tile[:, :, id_index].astype(np.float32)).view(np.uint32)
                selected = (coverage > best_coverage) & (ids != 0)
                best_coverage = np.where(selected, coverage, best_coverage)
                best_id = np.where(selected, ids, best_id)
        return _id_colors(best_id, best_coverage)

    if tile_rendering_enabled(settings, image.height):
        return np.ascontiguousarray(np.vstack(map_row_tiles(image.height, settings, process)))
    return process(0, image.height)


def hex_to_float32(hex_id: str) -> np.float32:
    return np.float32(struct.unpack(">f", bytes.fromhex(_clean_hex_id(hex_id)))[0])


def hex_to_uint32(hex_id: str) -> int:
    return int(_clean_hex_id(hex_id), 16)


def float32_to_hex(value: np.float32 | float) -> str:
    return struct.pack(">f", float(np.float32(value))).hex()


def _id_colors(ids: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    values = np.asarray(ids, dtype=np.uint32)
    mixed = values ^ (values >> np.uint32(16))
    mixed = mixed * np.uint32(0x7FEB352D)
    mixed = mixed ^ (mixed >> np.uint32(15))
    mixed = mixed * np.uint32(0x846CA68B)
    mixed = mixed ^ (mixed >> np.uint32(16))
    rgba = np.zeros((*values.shape, 4), dtype=np.float32)
    rgba[:, :, 0] = 0.25 + 0.75 * (((mixed >> np.uint32(0)) & np.uint32(255)).astype(np.float32) / 255.0)
    rgba[:, :, 1] = 0.25 + 0.75 * (((mixed >> np.uint32(8)) & np.uint32(255)).astype(np.float32) / 255.0)
    rgba[:, :, 2] = 0.25 + 0.75 * (((mixed >> np.uint32(16)) & np.uint32(255)).astype(np.float32) / 255.0)
    rgba[:, :, 3] = np.where(values == 0, 0.0, np.clip(alpha, 0.0, 1.0))
    return np.ascontiguousarray(rgba)


def _find_layer(image: ImageFrame, layer_name: str | None) -> CryptomatteLayer | None:
    layers = cryptomatte_layers(image)
    if not layers:
        return None
    if not layer_name:
        return layers[0]
    target = layer_name.lower()
    return next((layer for layer in layers if layer.name.lower() == target or layer.key.lower() == target), layers[0])


def _cryptomatte_channel_groups(image: ImageFrame, layer_name: str) -> list[str]:
    exact = []
    for name, data in image.channel_data.items():
        if not name.lower().startswith(layer_name.lower()):
            continue
        if data.ndim == 3 and data.shape[2] >= 2:
            exact.append(name)
    return sorted(exact, key=_cryptomatte_group_sort_key)


def _cryptomatte_group_sort_key(name: str) -> tuple[int, str]:
    match = re.search(r"(\d+)$", name)
    if not match:
        return (0, name.lower())
    return (int(match.group(1)) + 1, name.lower())


def _cryptomatte_pairs(group: np.ndarray) -> list[tuple[int, int]]:
    max_channels = min(group.shape[2], 4)
    return [(index, index + 1) for index in range(0, max_channels - 1, 2)]


def _resolve_matte_ids(layer: CryptomatteLayer, items: list[str]) -> set[str]:
    resolved: set[str] = set()
    for item in items:
        resolved.update(_normalize_matte_id(layer, item))
    return resolved


def _normalize_matte_id(layer: CryptomatteLayer, item: str) -> set[str]:
    token = item.strip()
    if not token:
        return set()
    if re.fullmatch(r"[0-9a-fA-F]{8}", token):
        return {token.lower()}
    if token.startswith("<") and token.endswith(">"):
        try:
            return {float32_to_hex(np.float32(float(token[1:-1]))).lower()}
        except ValueError:
            return set()
    if "*" in token:
        return {matte_id.lower() for name, matte_id in layer.manifest.items() if fnmatchcase(name, token)}
    matte_id = str(layer.manifest.get(token, "")).lower()
    return {matte_id} if matte_id else set()


def _parse_manifest(value: object) -> dict[str, str]:
    if not value:
        return {}
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    try:
        manifest = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    if not isinstance(manifest, dict):
        return {}
    return {str(key): str(item) for key, item in manifest.items()}


def _clean_hex_id(hex_id: str) -> str:
    cleaned = hex_id.strip().lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{8}", cleaned):
        raise ValueError(f"Invalid Cryptomatte id: {hex_id}")
    return cleaned
