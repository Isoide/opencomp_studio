from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.io.image_reader import read_image


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark PyOpenColorIO CPU conversion against OpenImageIO's C++ colorconvert path.")
    parser.add_argument("--path", required=True, help="Sequence path pattern or concrete file path.")
    parser.add_argument("--frame", type=int, default=1001, help="Frame to benchmark when the path uses #### padding.")
    parser.add_argument("--src-space", default="ACES2065-1", help="Source colorspace.")
    parser.add_argument("--dst-space", default="ACEScg", help="Destination colorspace.")
    parser.add_argument("--ocio-config", help="Optional OCIO config path or builtin config name.")
    parser.add_argument("--output", help="Optional path to write the JSON report.")
    args = parser.parse_args()

    image_path = _resolve_frame_path(args.path, args.frame)
    if not Path(image_path).exists():
        raise SystemExit(f"Input image does not exist: {image_path}")

    engine = OCIOColorEngine(args.ocio_config)
    if not engine.available:
        raise SystemExit("OpenColorIO is not available in this environment.")

    oiio = _import_oiio()
    if oiio is None:
        raise SystemExit("OpenImageIO is not installed in this environment.")

    report = {
        "input": {
            "path": image_path,
            "frame": args.frame,
            "src_space": args.src_space,
            "dst_space": args.dst_space,
            "ocio_config": args.ocio_config,
        },
        "openexr_pyocio": _benchmark_openexr_pyocio(engine, image_path, args.src_space, args.dst_space),
        "oiio_pyocio": _benchmark_oiio_pyocio(engine, image_path, args.src_space, args.dst_space),
        "oiio_cpp_colorconvert": _benchmark_oiio_cpp_colorconvert(engine, oiio, image_path, args.src_space, args.dst_space),
    }
    report_json = json.dumps(report, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_json + "\n", encoding="utf-8")
    print(report_json)
    return 0


def _benchmark_openexr_pyocio(engine: OCIOColorEngine, image_path: str, src_space: str, dst_space: str) -> dict[str, Any]:
    started = time.perf_counter()
    frame = read_image(image_path, colorspace=src_space, backend="openexr")
    read_ms = (time.perf_counter() - started) * 1000.0

    convert_started = time.perf_counter()
    converted = engine.convert_colorspace(frame.data, src_space, dst_space)
    convert_ms = (time.perf_counter() - convert_started) * 1000.0
    return {
        "read_ms": round(read_ms, 2),
        "convert_ms": round(convert_ms, 2),
        "total_ms": round(read_ms + convert_ms, 2),
        "shape": list(converted.shape),
        "dtype": str(converted.dtype),
    }


def _benchmark_oiio_pyocio(engine: OCIOColorEngine, image_path: str, src_space: str, dst_space: str) -> dict[str, Any]:
    started = time.perf_counter()
    frame = read_image(image_path, colorspace=src_space, backend="oiio")
    read_ms = (time.perf_counter() - started) * 1000.0

    convert_started = time.perf_counter()
    converted = engine.convert_colorspace(frame.data, src_space, dst_space)
    convert_ms = (time.perf_counter() - convert_started) * 1000.0
    return {
        "read_ms": round(read_ms, 2),
        "convert_ms": round(convert_ms, 2),
        "total_ms": round(read_ms + convert_ms, 2),
        "shape": list(converted.shape),
        "dtype": str(converted.dtype),
    }


def _benchmark_oiio_cpp_colorconvert(
    engine: OCIOColorEngine,
    oiio: Any,
    image_path: str,
    src_space: str,
    dst_space: str,
) -> dict[str, Any]:
    with _temporary_ocio_environment(engine):
        started = time.perf_counter()
        src = oiio.ImageBuf(image_path)
        dst = oiio.ImageBufAlgo.colorconvert(src, src_space, dst_space, True)
        if hasattr(dst, "has_error") and dst.has_error:
            raise RuntimeError(dst.geterror())
        pixels = dst.get_pixels(oiio.FLOAT)
        total_ms = (time.perf_counter() - started) * 1000.0
    array = np.asarray(pixels, dtype=np.float32)
    return {
        "total_ms": round(total_ms, 2),
        "shape": list(array.shape),
        "dtype": str(array.dtype),
    }


@contextmanager
def _temporary_ocio_environment(engine: OCIOColorEngine):
    config = getattr(engine, "_config", None)
    if config is None or not hasattr(config, "serialize"):
        yield
        return
    original = os.environ.get("OCIO")
    with tempfile.NamedTemporaryFile("w", suffix=".ocio", delete=False, encoding="utf-8") as handle:
        handle.write(config.serialize())
        temp_path = handle.name
    os.environ["OCIO"] = temp_path
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("OCIO", None)
        else:
            os.environ["OCIO"] = original
        try:
            Path(temp_path).unlink()
        except OSError:
            pass


def _resolve_frame_path(path: str, frame: int) -> str:
    if "####" in path:
        return path.replace("####", f"{frame:04d}")
    if "%04d" in path:
        return path % frame
    if "%d" in path:
        return path % frame
    return path


def _import_oiio() -> Any | None:
    try:
        import OpenImageIO as oiio  # type: ignore
    except ImportError:
        return None
    return oiio


if __name__ == "__main__":
    raise SystemExit(main())
