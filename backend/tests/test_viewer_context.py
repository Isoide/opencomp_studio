"""Unit tests for compact viewer-context API helpers."""

from __future__ import annotations

import numpy as np

from opencomp.api.viewer_context import (
    resolved_viewer_display_view,
    viewer_context_payload,
    viewer_proxy_limits,
    viewer_proxy_size,
)
from opencomp.core.models import ImageFrame, ProjectSettings


def test_viewer_context_uses_proxy_resolution_when_enabled() -> None:
    settings = ProjectSettings(proxy_enabled=True, viewer_max_width=1280, viewer_max_height=720)

    payload = viewer_context_payload(settings)

    assert payload["requested_width"] == 1280
    assert payload["requested_height"] == 720
    assert payload["resolution_label"] == "1280x720 proxy"


def test_viewer_context_uses_render_resolution_when_proxy_disabled() -> None:
    settings = ProjectSettings(proxy_enabled=False, width=1920, height=1080)
    image = ImageFrame(width=640, height=360, data=np.zeros((360, 640, 4), dtype=np.float32))

    payload = viewer_context_payload(settings, image)

    assert payload["proxy_width"] is None
    assert payload["proxy_height"] is None
    assert payload["render_width"] == 640
    assert payload["render_height"] == 360
    assert payload["resolution_label"] == "640x360 full"


def test_viewer_proxy_helpers_share_proxy_rules() -> None:
    settings = ProjectSettings(
        proxy_enabled=True,
        viewer_max_width=1280,
        viewer_max_height=720,
        viewer_display="sRGB - Display",
        viewer_view="ACES 2.0 - SDR 100 nits (Rec.709)",
    )

    assert viewer_proxy_size(settings) == (1280, 720)
    assert viewer_proxy_limits(settings) == (1280, 720)
    assert viewer_proxy_limits(settings, 640, 480) == (640, 480)
    assert resolved_viewer_display_view(settings) == (
        "sRGB - Display",
        "ACES 2.0 - SDR 100 nits (Rec.709)",
    )
    assert resolved_viewer_display_view(settings, "P3 - Display", "Film") == ("P3 - Display", "Film")
