from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.core.models import ImageFrame
from opencomp.io.image_reader import read_image
from opencomp.io.image_writer import write_image


def _oiio_available() -> bool:
    try:
        import OpenImageIO  # noqa: F401
    except ImportError:
        return False
    return True


def test_oiio_exr_write_roundtrip_preserves_aux_channels() -> None:
    if not _oiio_available():
        pytest.skip("OpenImageIO is not available.")

    frame = ImageFrame(
        width=4,
        height=3,
        data=np.full((3, 4, 4), 0.25, dtype=np.float32),
        pixel_aspect=2.0,
        colorspace="ACEScg",
        frame=1001,
        metadata={"test": "ok"},
        channel_data={"Z": np.full((3, 4), 2.0, dtype=np.float32)},
    )

    with tempfile.TemporaryDirectory() as td:
        output_path = Path(td) / "roundtrip.1001.exr"
        write_image(frame, str(output_path), overwrite=True, channels="all", backend="oiio")
        reread = read_image(str(output_path), colorspace="ACEScg", backend="oiio")

    assert reread.metadata["exr/read_method"] == "oiio"
    assert reread.pixel_aspect == 2.0
    assert "Z" in reread.channel_data
    np.testing.assert_allclose(reread.channel_data["Z"], np.full((3, 4), 2.0, dtype=np.float32), atol=1e-6, rtol=1e-6)


def test_oiio_colorconvert_matches_pyocio_baseline() -> None:
    if not _oiio_available():
        pytest.skip("OpenImageIO is not available.")
    engine_oiio = OCIOColorEngine(None)
    if not engine_oiio.available:
        pytest.skip("OpenColorIO is not available.")
    engine_pyocio = OCIOColorEngine(None)
    engine_pyocio._oiio = None

    image = np.ones((16, 16, 4), dtype=np.float32)
    image[..., 0] = 0.5
    image[..., 1] = 0.25
    image[..., 2] = 0.1

    oiio_result = engine_oiio.convert_colorspace(image, "ACES2065-1", "ACEScg")
    pyocio_result = engine_pyocio.convert_colorspace(image, "ACES2065-1", "ACEScg")

    np.testing.assert_allclose(oiio_result, pyocio_result, atol=1e-5, rtol=1e-5)
    engine_oiio.close()
    engine_pyocio.close()


def test_oiio_display_matches_pyocio_baseline() -> None:
    if not _oiio_available():
        pytest.skip("OpenImageIO is not available.")
    engine_oiio = OCIOColorEngine(None)
    if not engine_oiio.available:
        pytest.skip("OpenColorIO is not available.")
    engine_pyocio = OCIOColorEngine(None)
    engine_pyocio._oiio = None

    image = np.ones((16, 16, 4), dtype=np.float32)
    image[..., 0] = 0.5
    image[..., 1] = 0.25
    image[..., 2] = 0.1
    display = engine_oiio.default_display()
    assert display is not None
    view = engine_oiio.default_view(display)
    assert view is not None

    oiio_result = engine_oiio.apply_display_view(image, "ACES2065-1", display, view)
    pyocio_result = engine_pyocio.apply_display_view(image, "ACES2065-1", display, view)

    np.testing.assert_allclose(oiio_result, pyocio_result, atol=1e-5, rtol=1e-5)
    engine_oiio.close()
    engine_pyocio.close()
