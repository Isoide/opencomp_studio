"""Integration tests for sequence-path handling through image read/write APIs.

These checks verify that shared token expansion works the same way for file IO
entry points used by nodes, CLI renders, and project fixtures. The tests use
simple PNG payloads so they stay independent from optional EXR backends.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from opencomp.core.models import ImageFrame
from opencomp.io.image_reader import read_image
from opencomp.io.image_writer import write_image


def test_write_image_expands_supported_sequence_tokens(tmp_path: Path) -> None:
    frame = ImageFrame(
        width=2,
        height=2,
        data=np.ones((2, 2, 4), dtype=np.float32),
        colorspace="ACES2065-1",
        frame=1005,
    )

    outputs = {
        "hashes": tmp_path / "hashes.####.png",
        "printf": tmp_path / "printf.%04d.png",
        "nuke": tmp_path / "nuke.$4F.png",
    }

    written = {name: write_image(frame, str(path), overwrite=True) for name, path in outputs.items()}

    assert written["hashes"].name == "hashes.1005.png"
    assert written["printf"].name == "printf.1005.png"
    assert written["nuke"].name == "nuke.1005.png"
    for path in written.values():
        assert path.exists()


def test_read_image_resolves_supported_sequence_tokens(tmp_path: Path) -> None:
    source = ImageFrame(
        width=2,
        height=2,
        data=np.full((2, 2, 4), 0.5, dtype=np.float32),
        colorspace="ACES2065-1",
        frame=1006,
    )
    concrete_path = write_image(source, str(tmp_path / "plate.1006.png"), overwrite=True)

    hash_image = read_image(str(tmp_path / "plate.####.png"), frame=1006, colorspace="ACES2065-1")
    printf_image = read_image(str(tmp_path / "plate.%04d.png"), frame=1006, colorspace="ACES2065-1")
    nuke_image = read_image(str(tmp_path / "plate.$4F.png"), frame=1006, colorspace="ACES2065-1")

    assert concrete_path.exists()
    for image in (hash_image, printf_image, nuke_image):
        assert image.width == 2
        assert image.height == 2
        np.testing.assert_allclose(image.data, source.data, atol=1 / 255.0, rtol=0.0)

