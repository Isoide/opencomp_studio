from pathlib import Path

import numpy as np
import pytest

from opencomp.io.cryptomatte import (
    build_cryptomatte_matte,
    cryptomatte_id_preview_rgba,
    cryptomatte_layers,
    pick_cryptomatte_id,
)
from opencomp.io.image_reader import read_image
from opencomp.nodes.cryptomatte import CryptomatteNode
from opencomp.nodes.base import EvaluationContext
from opencomp.core.models import Node, ProjectSettings


REFERENCE_3D_EXR = Path(
    r"E:\Windows-Shortcuts\Downloads\opencomp_studio_codex_docs\LAL_101_148_0030_3D_v013.1071.exr"
)


class IdentityOcio:
    pass


def _context() -> EvaluationContext:
    return EvaluationContext(frame=1071, settings=ProjectSettings(), ocio=IdentityOcio())  # type: ignore[arg-type]


def _crypto_frame():
    if not REFERENCE_3D_EXR.exists():
        pytest.skip("Local 3D EXR reference file is not available.")
    return read_image(str(REFERENCE_3D_EXR), frame=1071, colorspace="ACES2065-1")


def _find_pickable_pixel(frame):
    layer = cryptomatte_layers(frame)[0]
    reverse = {value.lower(): name for name, value in layer.manifest.items()}
    for group_name in layer.channels:
        group = frame.channel_data[group_name]
        if group.ndim != 3 or group.shape[2] < 2:
            continue
        ids = np.ascontiguousarray(group[:, :, 0].astype(np.float32)).view(np.uint32)
        coverage = group[:, :, 1]
        ys, xs = np.where(coverage > 0.2)
        for y, x in zip(ys[:1000], xs[:1000]):
            id_hex = f"{int(ids[y, x]):08x}"
            if id_hex in reverse:
                return layer, int(x), int(y), id_hex, reverse[id_hex]
    raise AssertionError("No pickable Cryptomatte sample was found.")


def test_cryptomatte_layers_parse_manifest_and_channels() -> None:
    frame = _crypto_frame()

    layers = cryptomatte_layers(frame)

    assert len(layers) == 1
    assert layers[0].name == "VRayCryptomatte"
    assert layers[0].hash == "MurmurHash3_32"
    assert layers[0].conversion == "uint32_to_float32"
    assert len(layers[0].manifest) == 733
    assert "VRayCryptomatte00" in layers[0].channels


def test_cryptomatte_pick_and_matte_generation() -> None:
    frame = _crypto_frame()
    layer, x, y, id_hex, name = _find_pickable_pixel(frame)

    pick = pick_cryptomatte_id(frame, layer.name, x, y)
    matte = build_cryptomatte_matte(frame, layer.name, [id_hex])

    assert pick is not None
    assert pick.id_hex == id_hex
    assert pick.name == name
    assert matte[y, x] > 0.0
    assert matte.max() <= 1.0


def test_cryptomatte_id_preview_uses_multiple_object_colors() -> None:
    frame = _crypto_frame()
    layer = cryptomatte_layers(frame)[0]

    preview = cryptomatte_id_preview_rgba(frame, layer.name, ProjectSettings(tile_height=32, tile_workers=2))
    visible = preview[:, :, 3] > 0.0
    colors = np.unique((preview[visible][:, :3] * 255).astype(np.uint8), axis=0)

    assert preview.shape == (frame.height, frame.width, 4)
    assert visible.any()
    assert len(colors) > 3


def test_cryptomatte_node_outputs_selected_matte_alpha() -> None:
    frame = _crypto_frame()
    layer, _x, _y, id_hex, _name = _find_pickable_pixel(frame)
    node = Node(id="Crypto1", type="Cryptomatte", params={"layer": layer.name, "matte_list": id_hex})

    result = CryptomatteNode().evaluate(node, {"in": frame}, _context())

    assert result.data[:, :, 3].max() > 0.0
    assert result.metadata["cryptomatte/layer"] == layer.name
