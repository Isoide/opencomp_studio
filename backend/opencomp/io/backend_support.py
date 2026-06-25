"""Shared optional-backend helpers for OpenComp image IO modules.

This module centralizes backend-name normalization and optional dependency
loading for EXR readers and writers. It keeps reader/writer modules focused on
image logic instead of repeating the same import and backend-selection rules.
"""

from __future__ import annotations

from typing import Any, Literal

from opencomp.core.optional_dependencies import import_optional

ExrBackend = Literal["auto", "openexr", "oiio"]


def normalize_exr_backend(backend: str | None) -> ExrBackend:
    """Normalize caller-supplied EXR backend names to the supported set."""

    normalized = str(backend or "auto").strip().lower()
    if normalized in {"auto", "openexr", "oiio"}:
        return normalized
    return "auto"


def import_oiio() -> Any | None:
    """Import OpenImageIO when available without raising on missing hosts."""

    return import_optional("OpenImageIO")


def import_openexr() -> Any | None:
    """Import OpenEXR when available without raising on missing hosts."""

    return import_optional("OpenEXR")


def resolve_exr_backend_modules(backend: str | None, *, include_openexr_for_oiio_fallback: bool = True) -> tuple[ExrBackend, Any | None, Any | None]:
    """Return the normalized backend name and optional EXR backend modules."""

    normalized = normalize_exr_backend(backend)
    oiio = import_oiio() if normalized in {"auto", "oiio"} else None
    openexr = None
    if normalized in {"auto", "openexr"} or (include_openexr_for_oiio_fallback and normalized == "oiio"):
        openexr = import_openexr()
    return normalized, oiio, openexr
