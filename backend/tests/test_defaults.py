"""Unit tests for startup defaults and reference-sequence fallback behavior.

These tests pin the small amount of environment-sensitive logic used when the
app boots a fresh project. They make the startup defaults easier to refactor
without silently reintroducing host-specific hard-coded behavior.
"""

from __future__ import annotations

from opencomp.core import defaults


def test_configured_reference_sequence_path_prefers_environment(monkeypatch) -> None:
    monkeypatch.setenv(defaults.REFERENCE_SEQUENCE_ENV, "/tmp/custom_plate.%04d.exr")
    assert defaults.configured_reference_sequence_path() == "/tmp/custom_plate.%04d.exr"


def test_default_read_source_path_falls_back_to_builtin(monkeypatch) -> None:
    monkeypatch.delenv(defaults.REFERENCE_SEQUENCE_ENV, raising=False)
    monkeypatch.setattr(defaults, "path_exists", lambda path, frame=None: False)
    assert defaults.default_read_source_path() == "builtin://gradient"


def test_default_read_source_path_uses_configured_reference_when_available(monkeypatch) -> None:
    monkeypatch.setenv(defaults.REFERENCE_SEQUENCE_ENV, "/tmp/custom_plate.####.exr")
    monkeypatch.setattr(defaults, "path_exists", lambda path, frame=None: path == "/tmp/custom_plate.####.exr" and frame == 1001)
    assert defaults.default_read_source_path() == "/tmp/custom_plate.####.exr"
