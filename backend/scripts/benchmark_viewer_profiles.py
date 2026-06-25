from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a matrix of backend viewer benchmarks for speed/quality profile comparison.")
    parser.add_argument("--path", required=True, help="Sequence path pattern, for example \\\\server\\share\\shot_####.exr")
    parser.add_argument("--frame", type=int, default=1001, help="Frame to benchmark.")
    parser.add_argument("--colorspace", default="ACES2065-1", help="Read node colorspace assumption.")
    parser.add_argument("--working-colorspace", default="ACES2065-1", help="Project working colorspace.")
    parser.add_argument("--runs", type=int, default=1, help="Warm runs per profile.")
    parser.add_argument("--python", help="Python executable to use. Defaults to current interpreter.")
    parser.add_argument("--output", help="Optional path to write the combined JSON report.")
    args = parser.parse_args()

    script_path = Path(__file__).with_name("benchmark_viewer_pipeline.py")
    python_exe = args.python or sys.executable
    env = dict(os.environ)
    backend_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = backend_root if not env.get("PYTHONPATH") else backend_root + os.pathsep + env["PYTHONPATH"]

    profiles = [
        {"label": "quality_full_current", "extra": []},
        {"label": "speed_1280_current", "extra": ["--proxy-enabled", "--proxy-width", "1280", "--proxy-height", "720"]},
        {"label": "speed_1024_current", "extra": ["--proxy-enabled", "--proxy-width", "1024", "--proxy-height", "1024"]},
        {"label": "diag_full_raw", "extra": ["--display", "sRGB - Display", "--view", "Raw"]},
        {"label": "diag_full_video_colorimetric", "extra": ["--display", "sRGB - Display", "--view", "Video (colorimetric)"]},
    ]

    results: list[dict[str, Any]] = []
    for profile in profiles:
        command = [
            python_exe,
            str(script_path),
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
            "--quiet",
            *profile["extra"],
        ]
        completed = subprocess.run(command, capture_output=True, text=True, env=env, check=True)
        payload = json.loads(completed.stdout)
        payload["profile_label"] = profile["label"]
        results.append(payload)

    summary = []
    for result in results:
        benchmark = result["benchmark"]
        summary.append(
            {
                "profile_label": result["profile_label"],
                "proxy_enabled": benchmark["proxy_enabled"],
                "viewer_max_width": benchmark["viewer_max_width"],
                "viewer_max_height": benchmark["viewer_max_height"],
                "viewer_display": benchmark["viewer_display"],
                "viewer_view": benchmark["viewer_view"],
                "png_cold_wall_ms": result["png"]["cold"]["wall_ms"],
                "png_cold_ocio_ms": result["png"]["cold"]["preview_timing"].get("ocio_ms", 0.0),
                "png_cold_encode_ms": result["png"]["cold"]["preview_timing"].get("encode_ms", 0.0),
                "float_cold_wall_ms": result["float"]["cold"]["wall_ms"],
                "float_cold_encode_ms": _phase_value(result["float"]["cold"]["phase_summary"], "Viewer1:viewer.float_encode"),
                "png_warm_wall_ms": result["png"]["warm_average"].get("wall_ms_avg", 0.0),
                "float_warm_wall_ms": result["float"]["warm_average"].get("wall_ms_avg", 0.0),
            }
        )

    report = {"profiles": results, "summary": summary}
    report_json = json.dumps(report, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_json + "\n", encoding="utf-8")
    print(report_json)
    return 0


def _phase_value(summary: dict[str, Any], key: str) -> float:
    value = summary.get(key)
    if value is None:
        return 0.0
    return float(value)


if __name__ == "__main__":
    raise SystemExit(main())
