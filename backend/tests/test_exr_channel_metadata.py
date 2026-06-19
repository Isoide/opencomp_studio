from pathlib import Path

import pytest

from opencomp.io.image_reader import read_image
from opencomp.io.preview import preview_rgba_for_channel


REFERENCE_3D_EXR = Path(
    r"E:\Windows-Shortcuts\Downloads\opencomp_studio_codex_docs\LAL_101_148_0030_3D_v013.1071.exr"
)


def test_read_exr_reports_pixel_aspect_and_auxiliary_channels() -> None:
    if not REFERENCE_3D_EXR.exists():
        pytest.skip("Local 3D EXR reference file is not available.")

    frame = read_image(str(REFERENCE_3D_EXR), frame=1071, colorspace="ACES2065-1")

    assert frame.pixel_aspect == 2.0
    assert frame.metadata["input/pixel_aspect"] == 2.0
    assert "Environment" in frame.channels
    assert "Environment.R" in frame.channels
    assert "VRayDiffuseFilter" in frame.channels
    assert "VRayVelocity.X" in frame.channels
    assert "Z" in frame.channels
    assert frame.channel_data["Z"].shape == (1492, 2020)


def test_auxiliary_scalar_channel_can_be_previewed_as_rgba() -> None:
    if not REFERENCE_3D_EXR.exists():
        pytest.skip("Local 3D EXR reference file is not available.")

    frame = read_image(str(REFERENCE_3D_EXR), frame=1071, colorspace="ACES2065-1")

    preview, apply_ocio = preview_rgba_for_channel(frame, "Z")

    assert apply_ocio is False
    assert preview.shape == (frame.height, frame.width, 4)
    assert preview[:, :, 3].min() == 1.0
