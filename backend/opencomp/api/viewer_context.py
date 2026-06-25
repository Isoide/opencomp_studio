"""Compact viewer-context helpers for API responses and route logic.

This module exposes routine viewer settings as direct helpers so route handlers
do not need to keep re-implementing proxy-size, display/view, or resolution
fallback rules. It intentionally stays small and data-oriented.
"""

from __future__ import annotations

from typing import Any

from opencomp.core.models import ImageFrame, ProjectSettings


def viewer_proxy_size(settings: ProjectSettings) -> tuple[int | None, int | None]:
    """Return the active proxy size or ``(None, None)`` when full-res is active."""

    if not settings.proxy_enabled:
        return None, None
    return settings.viewer_max_width, settings.viewer_max_height


def resolved_viewer_display_view(
    settings: ProjectSettings,
    display: str | None = None,
    view: str | None = None,
) -> tuple[str | None, str | None]:
    """Return the effective display/view pair for a request."""

    return display or settings.viewer_display, view or settings.viewer_view


def viewer_proxy_limits(
    settings: ProjectSettings,
    max_width: int | None = None,
    max_height: int | None = None,
) -> tuple[int | None, int | None]:
    """Return proxy-constrained max dimensions for preview-style requests."""

    proxy_width, proxy_height = viewer_proxy_size(settings)
    if proxy_width is None or proxy_height is None:
        return None, None
    return max_width or proxy_width, max_height or proxy_height


def viewer_context_payload(settings: ProjectSettings, image: ImageFrame | None = None) -> dict[str, Any]:
    """Return a compact viewer settings snapshot for metadata and diagnostics."""

    proxy_width, proxy_height = viewer_proxy_size(settings)
    display, view = resolved_viewer_display_view(settings)
    render_width = image.width if image is not None else settings.width
    render_height = image.height if image is not None else settings.height
    requested_width = proxy_width if proxy_width is not None else render_width
    requested_height = proxy_height if proxy_height is not None else render_height
    return {
        "display": display,
        "view": view,
        "proxy_enabled": settings.proxy_enabled,
        "proxy_width": proxy_width,
        "proxy_height": proxy_height,
        "requested_width": requested_width,
        "requested_height": requested_height,
        "render_width": render_width,
        "render_height": render_height,
        "resolution_label": (
            f"{requested_width}x{requested_height} proxy"
            if settings.proxy_enabled and proxy_width is not None and proxy_height is not None
            else f"{render_width}x{render_height} full"
        ),
    }
