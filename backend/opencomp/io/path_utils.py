"""Sequence-path helpers shared by backend file IO.

This module centralizes the path normalization and frame-token expansion rules
used by Read, Write, defaults, and project IO. It keeps sequence references
consistent across platforms and avoids repeating token-specific string logic in
multiple nodes or IO helpers.
"""

from __future__ import annotations

import re
from pathlib import Path


HASH_SEQUENCE_PATTERN = re.compile(r"(#+)")
PRINTF_SEQUENCE_PATTERN = re.compile(r"%([0]?)(\d*)d")
NUKE_SEQUENCE_PATTERN = re.compile(r"\$(\d*)F")


def normalize_path_string(path: str | Path) -> str:
    """Return a user-expanded path string while preserving virtual URI schemes."""

    raw = str(path).strip()
    if not raw or "://" in raw:
        return raw
    return str(Path(raw).expanduser())


def resolve_sequence_path(path: str | Path, frame: int | None = None) -> str:
    """Expand known sequence token styles for a specific frame when requested."""

    normalized = normalize_path_string(path)
    if frame is None:
        return normalized
    resolved = HASH_SEQUENCE_PATTERN.sub(lambda match: f"{frame:0{len(match.group(1))}d}", normalized)
    resolved = PRINTF_SEQUENCE_PATTERN.sub(lambda match: _printf_frame_token(match, frame), resolved)
    resolved = NUKE_SEQUENCE_PATTERN.sub(lambda match: _nuke_frame_token(match, frame), resolved)
    return resolved


def path_exists(path: str | Path, frame: int | None = None) -> bool:
    """Return True when a resolved local path exists, treating virtual sources as valid."""

    resolved = resolve_sequence_path(path, frame)
    if resolved.startswith("builtin://") or "://" in resolved:
        return True
    try:
        return Path(resolved).exists()
    except OSError:
        return False


def local_path(path: str | Path, frame: int | None = None) -> Path:
    """Return a pathlib Path for callers that operate only on local files."""

    return Path(resolve_sequence_path(path, frame))


def _printf_frame_token(match: re.Match[str], frame: int) -> str:
    zero_fill, digits = match.groups()
    width = int(digits) if digits else 0
    if width <= 0:
        return str(frame)
    if zero_fill == "0":
        return f"{frame:0{width}d}"
    return f"{frame:>{width}d}"


def _nuke_frame_token(match: re.Match[str], frame: int) -> str:
    digits = match.group(1)
    width = int(digits) if digits else 1
    return f"{frame:0{width}d}"
