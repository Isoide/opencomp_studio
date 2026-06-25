"""Build OpenComp Vulkan GLSL shaders into distributable SPIR-V artifacts.

This script is the developer-side compile entrypoint used to validate or
regenerate the shader manifest shipped with the backend. It shares compiler
discovery policy with the runtime so build and runtime diagnostics stay aligned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opencomp.gpu.toolchain import compiler_warning, require_compiler


SRC_DIR = ROOT / "opencomp" / "gpu" / "shaders" / "src"
OUT_DIR = ROOT / "opencomp" / "gpu" / "shaders" / "compiled"
MANIFEST_PATH = OUT_DIR / "manifest.json"
VALID_SUFFIXES = (".comp.glsl", ".vert.glsl", ".frag.glsl")


def _discover_compiler(explicit: str | None) -> tuple[str, str]:
    compiler = require_compiler(explicit)
    return compiler.path, compiler.kind


def _shader_stage(path: Path) -> str:
    if path.name.endswith(".comp.glsl"):
        return "comp"
    if path.name.endswith(".vert.glsl"):
        return "vert"
    if path.name.endswith(".frag.glsl"):
        return "frag"
    raise ValueError(f"Unsupported shader stage for {path.name}")


def _compile_shader(compiler_path: str, compiler_kind: str, source_path: Path, output_path: Path) -> dict[str, object]:
    stage = _shader_stage(source_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if compiler_kind == "glslc":
        cmd = [compiler_path, "-fshader-stage=" + stage, "-o", str(output_path), str(source_path)]
    else:
        cmd = [compiler_path, "-V", "-S", stage, "-o", str(output_path), str(source_path)]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Shader compile failed for {source_path.name} with exit code {completed.returncode}.\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return {
        "name": source_path.name,
        "stage": stage,
        "source": str(source_path.relative_to(SRC_DIR)).replace("\\", "/"),
        "output": str(output_path.relative_to(OUT_DIR)).replace("\\", "/"),
        "entry": "main",
        "source_sha256": _source_sha256(source_path),
        "size_bytes": output_path.stat().st_size,
    }


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile OpenComp Vulkan shaders into SPIR-V artifacts.")
    parser.add_argument("--compiler", help="Explicit path to glslang, glslangValidator, or glslc.")
    parser.add_argument("--check", action="store_true", help="Validate toolchain and shader discovery without compiling.")
    args = parser.parse_args()

    compiler_path, compiler_kind = _discover_compiler(args.compiler)
    warning = compiler_warning(compiler_path)
    shader_sources = sorted(
        path for path in SRC_DIR.rglob("*") if path.is_file() and path.name.endswith(VALID_SUFFIXES)
    )
    if not shader_sources:
        raise SystemExit(f"No shader sources found in {SRC_DIR}")
    if args.check:
        print(
            json.dumps(
                {
                    "compiler_path": compiler_path,
                    "compiler_kind": compiler_kind,
                    "warning": warning,
                    "source_dir": str(SRC_DIR),
                    "shader_count": len(shader_sources),
                    "shaders": [str(path) for path in shader_sources],
                },
                indent=2,
            )
        )
        return 0

    compiled = []
    for source_path in shader_sources:
        rel = source_path.relative_to(SRC_DIR)
        output_path = OUT_DIR / rel.with_suffix(".spv")
        compiled.append(_compile_shader(compiler_path, compiler_kind, source_path, output_path))

    manifest = {
        "compiler_path": Path(compiler_path).name,
        "compiler_kind": compiler_kind,
        "warning": warning,
        "compiled_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_dir": ".",
        "compiled_dir": ".",
        "shaders": compiled,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
