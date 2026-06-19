import numpy as np
from PIL import Image

from opencomp.io.preview import encode_preview_png


def test_preview_png_encoding() -> None:
    rgba = np.zeros((2, 2, 4), dtype=np.float32)
    rgba[:, :, 0] = 1.0
    rgba[:, :, 3] = 1.0
    png = encode_preview_png(rgba)
    assert png.startswith(b"\x89PNG")
    assert Image.open(__import__("io").BytesIO(png)).size == (2, 2)
