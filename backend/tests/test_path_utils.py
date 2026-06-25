"""Unit tests for shared backend path normalization and frame-token expansion.

These tests pin the token rules used by project defaults, image IO, and read
node probing so future cleanup work can remove duplication without changing
behavior silently.
"""

from __future__ import annotations

from pathlib import Path

from opencomp.io.path_utils import normalize_path_string, path_exists, resolve_sequence_path


def test_resolve_sequence_path_supports_hash_printf_and_nuke_tokens() -> None:
    assert resolve_sequence_path(r"C:\shots\plate.####.exr", 1007) == r"C:\shots\plate.1007.exr"
    assert resolve_sequence_path(r"C:\shots\plate.%04d.exr", 1007) == r"C:\shots\plate.1007.exr"
    assert resolve_sequence_path(r"C:\shots\plate.%d.exr", 1007) == r"C:\shots\plate.1007.exr"
    assert resolve_sequence_path(r"C:\shots\plate.$4F.exr", 1007) == r"C:\shots\plate.1007.exr"
    assert resolve_sequence_path(r"C:\shots\plate.$F.exr", 1007) == r"C:\shots\plate.1007.exr"


def test_normalize_path_string_preserves_virtual_uris() -> None:
    assert normalize_path_string("builtin://gradient") == "builtin://gradient"
    assert normalize_path_string("ocio://display") == "ocio://display"


def test_path_exists_uses_sequence_resolution(tmp_path: Path) -> None:
    sequence_path = tmp_path / "plate.1008.png"
    sequence_path.write_bytes(b"test")

    assert path_exists(tmp_path / "plate.####.png", 1008)
    assert path_exists(tmp_path / "plate.%04d.png", 1008)
    assert path_exists(tmp_path / "plate.$4F.png", 1008)
    assert not path_exists(tmp_path / "plate.####.png", 1009)
