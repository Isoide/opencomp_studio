"""Shared Vulkan shader-toolchain discovery helpers for OpenComp.

This module centralizes how OpenComp discovers `glslang` / `glslangValidator`
or `glslc` across runtime diagnostics and shader build scripts. It keeps host-
specific fallback roots and warning policy in one place so GPU integration
stays configurable and easier to reason about across platforms.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

VULKAN_SHADER_COMPILER_ENV = "OPENCOMP_VULKAN_SHADER_COMPILER"
VULKAN_SHADER_SEARCH_ROOTS_ENV = "OPENCOMP_VULKAN_SHADER_SEARCH_ROOTS"
COMPILER_EXECUTABLE_NAMES = ("glslangValidator", "glslang", "glslc")
COMPILER_FILE_PATTERNS = (
    "glslang.exe",
    "glslangValidator.exe",
    "glslc.exe",
    "glslang",
    "glslangValidator",
    "glslc",
)
WINDOWS_FALLBACK_ROOTS = (
    Path("C:/VulkanSDK"),
    Path("C:/Program Files/VulkanSDK"),
    Path("C:/Program Files/Side Effects Software"),
)


@dataclass(frozen=True, slots=True)
class ShaderCompiler:
    """Resolved shader compiler executable and normalized compiler kind."""

    path: str
    kind: str


def compiler_warning(path: str | None) -> str | None:
    """Return a warning string when a non-primary fallback compiler is used."""

    if not path:
        return None
    normalized = path.replace("\\", "/").lower()
    if "/side effects software/" in normalized or "/houdini " in normalized:
        return "Using Houdini-bundled glslang as a fallback compiler. Prefer Vulkan SDK glslang/glslangValidator or glslc when available."
    return None


def compiler_kind(path: Path) -> str:
    """Normalize a compiler executable path to the tool kind used by callers."""

    compiler_name = path.name.lower()
    if compiler_name.startswith("glslc"):
        return "glslc"
    if compiler_name == "glslangvalidator" or compiler_name == "glslangvalidator.exe":
        return "glslangValidator"
    return "glslang"


def configured_search_roots() -> list[Path]:
    """Return user-configured and platform fallback search roots for compilers."""

    roots: list[Path] = []
    raw = os.environ.get(VULKAN_SHADER_SEARCH_ROOTS_ENV, "").strip()
    if raw:
        roots.extend(Path(part) for part in raw.split(os.pathsep) if part.strip())
    if os.name == "nt":
        roots.extend(WINDOWS_FALLBACK_ROOTS)
    return roots


def compiler_candidates(explicit: str | None = None) -> list[Path]:
    """Collect candidate compiler paths in priority order."""

    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_compiler = os.environ.get(VULKAN_SHADER_COMPILER_ENV, "").strip()
    if env_compiler:
        candidates.append(Path(env_compiler))
    vulkan_sdk = os.environ.get("VULKAN_SDK", "").strip()
    if vulkan_sdk:
        sdk_bin = Path(vulkan_sdk) / "Bin"
        candidates.extend(sdk_bin / name for name in COMPILER_FILE_PATTERNS)
    for name in COMPILER_EXECUTABLE_NAMES:
        resolved = shutil.which(name)
        if resolved:
            candidates.append(Path(resolved))
    for root in configured_search_roots():
        if not root.exists():
            continue
        for pattern in COMPILER_FILE_PATTERNS:
            candidates.extend(sorted(root.glob(f"**/{pattern}"), reverse=True))
    return _dedupe_paths(candidates)


def discover_compiler(explicit: str | None = None) -> ShaderCompiler | None:
    """Resolve the first available shader compiler from the configured candidates."""

    for candidate in compiler_candidates(explicit):
        if candidate.exists() and candidate.is_file():
            return ShaderCompiler(path=str(candidate), kind=compiler_kind(candidate))
    return None


def require_compiler(explicit: str | None = None) -> ShaderCompiler:
    """Resolve a compiler or raise a clear setup error for build-time workflows."""

    compiler = discover_compiler(explicit)
    if compiler is None:
        raise FileNotFoundError(
            "No Vulkan shader compiler found. Install 'glslang'/'glslangValidator' or 'glslc', "
            f"set {VULKAN_SHADER_COMPILER_ENV} to an explicit executable path, "
            f"or configure {VULKAN_SHADER_SEARCH_ROOTS_ENV} with fallback search roots."
        )
    return compiler


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique
