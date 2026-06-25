"""Unit tests for shared Vulkan shader-toolchain discovery helpers."""

from __future__ import annotations

from pathlib import Path

from opencomp.gpu import toolchain


def test_discover_compiler_uses_configured_search_roots(monkeypatch, tmp_path: Path) -> None:
    compiler_root = tmp_path / "houdini" / "bin"
    compiler_root.mkdir(parents=True)
    compiler_path = compiler_root / "glslangValidator"
    compiler_path.write_text("", encoding="utf-8")

    monkeypatch.setenv(toolchain.VULKAN_SHADER_SEARCH_ROOTS_ENV, str(tmp_path))
    monkeypatch.delenv(toolchain.VULKAN_SHADER_COMPILER_ENV, raising=False)
    monkeypatch.delenv("VULKAN_SDK", raising=False)
    monkeypatch.setattr(toolchain.shutil, "which", lambda _name: None)

    compiler = toolchain.discover_compiler()

    assert compiler is not None
    assert compiler.path == str(compiler_path)
    assert compiler.kind == "glslangValidator"


def test_require_compiler_error_mentions_configurable_envs(monkeypatch) -> None:
    monkeypatch.delenv(toolchain.VULKAN_SHADER_SEARCH_ROOTS_ENV, raising=False)
    monkeypatch.delenv(toolchain.VULKAN_SHADER_COMPILER_ENV, raising=False)
    monkeypatch.delenv("VULKAN_SDK", raising=False)
    monkeypatch.setattr(toolchain.shutil, "which", lambda _name: None)
    monkeypatch.setattr(toolchain, "configured_search_roots", lambda: [])

    try:
        toolchain.require_compiler()
    except FileNotFoundError as exc:
        message = str(exc)
    else:
        raise AssertionError("require_compiler() was expected to raise FileNotFoundError")

    assert toolchain.VULKAN_SHADER_COMPILER_ENV in message
    assert toolchain.VULKAN_SHADER_SEARCH_ROOTS_ENV in message
