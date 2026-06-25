from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an end-to-end backend matrix benchmark across execution and EXR reader backends.")
    parser.add_argument("--path", required=True, help="Sequence path pattern, for example \\\\server\\share\\shot_####.exr")
    parser.add_argument("--frame", type=int, default=1001, help="Frame to benchmark.")
    parser.add_argument("--colorspace", default="ACES2065-1", help="Read node colorspace assumption.")
    parser.add_argument("--working-colorspace", default="ACES2065-1", help="Project working colorspace.")
    parser.add_argument("--runs", type=int, default=2, help="Warm runs to average after the cold run.")
    parser.add_argument("--precision", default="float16", choices=["float16", "float32", "rgb10a2", "uint8"], help="Float transport precision.")
    parser.add_argument("--proxy-enabled", action="store_true", help="Enable proxy resizing in the benchmarked viewer path.")
    parser.add_argument("--proxy-width", type=int, default=1280, help="Proxy width when proxy is enabled.")
    parser.add_argument("--proxy-height", type=int, default=720, help="Proxy height when proxy is enabled.")
    parser.add_argument("--display", help="Viewer display override.")
    parser.add_argument("--view", help="Viewer view override.")
    parser.add_argument("--output", help="Optional path to write the combined JSON report.")
    args = parser.parse_args()

    benchmark_script = Path(__file__).with_name("benchmark_viewer_pipeline.py")
    combinations = [
        ("cpu", "openexr"),
        ("cpu", "oiio"),
        ("vulkan", "openexr"),
        ("vulkan", "oiio"),
    ]
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="opencomp_backend_matrix_") as temp_dir:
        temp_root = Path(temp_dir)
        for execution_backend, image_io_backend in combinations:
            output_path = temp_root / f"{execution_backend}_{image_io_backend}.json"
            command = [
                sys.executable,
                str(benchmark_script),
                "--path",
                args.path,
                "--frame",
                str(args.frame),
                "--colorspace",
                args.colorspace,
                "--working-colorspace",
                args.working_colorspace,
                "--runs",
                str(args.runs),
                "--precision",
                args.precision,
                "--execution-backend",
                execution_backend,
                "--image-io-backend",
                image_io_backend,
                "--output",
                str(output_path),
                "--quiet",
            ]
            if args.proxy_enabled:
                command.extend(
                    [
                        "--proxy-enabled",
                        "--proxy-width",
                        str(args.proxy_width),
                        "--proxy-height",
                        str(args.proxy_height),
                    ]
                )
            if args.display:
                command.extend(["--display", args.display])
            if args.view:
                command.extend(["--view", args.view])
            subprocess.run(command, check=True)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            results.append(_summarize_report(report))

    combined = {
        "benchmark": {
            "path_pattern": args.path,
            "frame": args.frame,
            "colorspace": args.colorspace,
            "working_colorspace": args.working_colorspace,
            "proxy_enabled": args.proxy_enabled,
            "proxy_width": args.proxy_width if args.proxy_enabled else None,
            "proxy_height": args.proxy_height if args.proxy_enabled else None,
            "precision": args.precision,
        },
        "results": results,
    }
    report_json = json.dumps(combined, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_json + "\n", encoding="utf-8")
    print(report_json)
    return 0


def _summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    benchmark = report["benchmark"]
    read_ms = 0.0
    phase_timings = report["float"]["cold"].get("phase_timings", [])
    for phase in phase_timings:
        if phase.get("node_id") == "Read1" and phase.get("phase") == "read.image":
            read_ms = float(phase.get("duration_ms", 0.0))
            break
    float_preview = report["float"]["cold"]["preview_timing"]
    png_preview = report["png"]["cold"]["preview_timing"]
    return {
        "execution_backend": benchmark["execution_backend"],
        "image_io_backend": benchmark.get("image_io_backend", "auto"),
        "graph": benchmark["graph"],
        "float_cold_wall_ms": report["float"]["cold"]["wall_ms"],
        "float_cold_evaluate_ms": float_preview.get("evaluate_ms", 0.0),
        "float_cold_read_ms": read_ms,
        "float_cold_gpu_upload_ms": float_preview.get("gpu_upload_ms", 0.0),
        "float_cold_gpu_dispatch_ms": float_preview.get("gpu_dispatch_ms", 0.0),
        "float_cold_gpu_download_ms": float_preview.get("gpu_download_ms", 0.0),
        "png_cold_wall_ms": report["png"]["cold"]["wall_ms"],
        "png_cold_evaluate_ms": png_preview.get("evaluate_ms", 0.0),
        "png_cold_ocio_ms": png_preview.get("ocio_ms", 0.0),
        "png_cold_encode_ms": png_preview.get("encode_ms", 0.0),
    }


if __name__ == "__main__":
    raise SystemExit(main())
