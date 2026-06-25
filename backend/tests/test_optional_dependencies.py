"""Unit tests for shared optional dependency loading helpers and diagnostics.

These tests pin the small utility layer used by OCIO, image IO, and Vulkan
runtime modules so cleanup refactors can remove duplicate import wrappers
without changing optional-host behavior.
"""

from __future__ import annotations

from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.core.optional_dependencies import import_first_available, import_optional
from opencomp.io.backend_support import normalize_exr_backend


def test_import_optional_returns_none_for_missing_module() -> None:
    assert import_optional("opencomp_nonexistent_dependency_for_test") is None


def test_import_first_available_prefers_existing_module() -> None:
    dependency = import_first_available("opencomp_nonexistent_dependency_for_test", "json")

    assert dependency.available is True
    assert dependency.module_name == "json"
    assert dependency.module is not None


def test_ocio_diagnostics_reports_optional_integration_state() -> None:
    diagnostics = OCIOColorEngine(None).diagnostics()

    assert "available" in diagnostics
    assert "ocio_available" in diagnostics
    assert "oiio_available" in diagnostics
    assert "warning" in diagnostics


def test_normalize_exr_backend_defaults_unknown_values_to_auto() -> None:
    assert normalize_exr_backend(None) == "auto"
    assert normalize_exr_backend("oiio") == "oiio"
    assert normalize_exr_backend("OPENEXR") == "openexr"
    assert normalize_exr_backend("unsupported") == "auto"
