from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from opencomp.api.routes import _float_preview_entry
from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, FrameRequest, Node, Project, ProjectGraph, ProjectSettings


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile the OCIO display-view CPU path using the current backend workflow.")
    parser.add_argument("--path", required=True, help="Sequence path pattern, for example \\\\server\\share\\shot_####.exr")
    parser.add_argument("--frame", type=int, default=1001, help="Frame to profile.")
    parser.add_argument("--colorspace", default="ACES2065-1", help="Read node colorspace assumption.")
    parser.add_argument("--working-colorspace", default="ACES2065-1", help="Project working colorspace.")
    parser.add_argument("--display", help="Viewer display override.")
    parser.add_argument("--view", help="Viewer view override.")
    parser.add_argument("--runs", type=int, default=3, help="OCIO runs to measure after the source float buffer is prepared.")
    parser.add_argument("--output", help="Optional path to write the JSON report.")
    args = parser.parse_args()

    path = args.path
    if "####" not in path:
        raise SystemExit("Expected a sequence path with #### frame padding.")
    concrete_path = path.replace("####", f"{args.frame:04d}")
    if not Path(concrete_path).exists():
        raise SystemExit(f"Frame does not exist: {concrete_path}")

    project = _build_project(path, args.frame, args.colorspace, args.working_colorspace, args.display, args.view)
    evaluator = GraphEvaluator(settings=project.settings, max_cache_bytes=1024 * 1024 * 1024)
    payload = FrameRequest(
        node_id="Viewer1",
        frame=args.frame,
        display=project.settings.viewer_display,
        view=project.settings.viewer_view,
        channel="rgba",
        precision="float16",
        stream_tiles=False,
        transfer_mode="float16-rgba",
    )

    source_started = time.perf_counter()
    result = _float_preview_entry(project, project.graph, evaluator, payload)
    source_ms = (time.perf_counter() - source_started) * 1000.0
    rgba = result.entry.rgba

    engine = evaluator.ocio or OCIOColorEngine(project.settings.ocio_config)
    if not engine.available:
        raise SystemExit("OCIO is not available in this environment.")

    runs = [_profile_once(engine, rgba, result.entry.colorspace, payload.display, payload.view) for _ in range(max(1, args.runs))]
    report = {
        "input": {
            "path_pattern": path,
            "frame_path": concrete_path,
            "frame": args.frame,
            "colorspace": result.entry.colorspace,
            "display": payload.display,
            "view": payload.view,
            "source_prepare_ms": round(source_ms, 2),
            "float_cache_hit": bool(result.cache_hit),
            "evaluate_ms": round(result.evaluate_ms, 2),
            "resize_ms": round(result.resize_ms, 2),
        },
        "array": _array_info(rgba),
        "runs": runs,
        "summary": _summarize_runs(runs),
    }

    report_json = json.dumps(report, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_json + "\n", encoding="utf-8")
    print(report_json)
    return 0


def _build_project(
    path: str,
    frame: int,
    colorspace: str,
    working_colorspace: str,
    display: str | None,
    view: str | None,
) -> Project:
    engine = OCIOColorEngine(None)
    display_name = display or engine.default_display()
    view_name = view or (engine.default_view(display_name) if display_name else None)
    settings = ProjectSettings(
        frame_start=frame,
        frame_end=frame,
        working_colorspace=working_colorspace,
        viewer_display=display_name,
        viewer_view=view_name,
        proxy_enabled=False,
        cache_enabled=True,
        tile_rendering_enabled=True,
        tile_workers=4,
        read_workers=4,
        render_workers=4,
    )
    graph = ProjectGraph(
        nodes={
            "Read1": Node(
                id="Read1",
                type="Read",
                name="Read",
                position=(120, 80),
                params={
                    "path": path,
                    "colorspace": colorspace,
                    "frame_start": frame,
                    "frame_end": frame,
                    "before": "hold",
                    "after": "hold",
                    "frame_mode": "expression",
                    "frame": "frame",
                    "missing_frames": "error",
                    "input_transform": "default (linear)",
                },
            ),
            "Grade1": Node(
                id="Grade1",
                type="Grade",
                name="Grade",
                position=(120, 220),
                params={"gain": 1.1, "offset": 0.01, "gamma": 0.95},
            ),
            "ColorCorrect1": Node(
                id="ColorCorrect1",
                type="ColorCorrect",
                name="ColorCorrect",
                position=(120, 360),
                params={"saturation": 1.05, "contrast": 1.08, "gamma": 1.02, "gain": 1.03, "offset": 0.005, "mix": 1.0},
            ),
            "Viewer1": Node(
                id="Viewer1",
                type="Viewer",
                name="Viewer",
                position=(120, 640),
                params={"active_input": "0"},
            ),
        },
        edges=[
            Edge(id="e-read-grade", source_node="Read1", target_node="Grade1"),
            Edge(id="e-grade-colorcorrect", source_node="Grade1", target_node="ColorCorrect1"),
            Edge(id="e-colorcorrect-viewer", source_node="ColorCorrect1", target_node="Viewer1", target_socket="0"),
        ],
    )
    return Project(project_name="ocio-profile", settings=settings, graph=graph, script_tabs=[], active_script_id="main")


def _profile_once(
    engine: OCIOColorEngine,
    rgba: np.ndarray,
    src: str,
    display: str | None,
    view: str | None,
) -> dict[str, Any]:
    run: dict[str, Any] = {}

    started = time.perf_counter()
    asarray_view = np.asarray(rgba, dtype=np.float32)
    run["np_asarray_ms"] = _ms(started)
    run["after_asarray"] = _array_info(asarray_view)
    run["asarray_same_object"] = asarray_view is rgba

    started = time.perf_counter()
    contiguous = np.ascontiguousarray(asarray_view)
    run["np_ascontiguousarray_ms"] = _ms(started)
    run["after_ascontiguous"] = _array_info(contiguous)
    run["contiguous_same_object"] = contiguous is asarray_view

    started = time.perf_counter()
    processor_object = engine._get_display_processor_object(src, display or engine.default_display(), view or engine.default_view(display))
    run["get_processor_object_ms"] = _ms(started)
    run["processor_object_type"] = type(processor_object).__name__

    started = time.perf_counter()
    cpu = engine._get_display_processor(src, display or engine.default_display(), view or engine.default_view(display))
    run["get_cpu_processor_ms"] = _ms(started)
    run["cpu_processor_type"] = type(cpu).__name__

    started = time.perf_counter()
    result = contiguous.copy()
    run["copy_input_ms"] = _ms(started)
    run["copied_array"] = _array_info(result)

    started = time.perf_counter()
    flat = np.ascontiguousarray(result.reshape((-1, 4)))
    run["reshape_flatten_ms"] = _ms(started)
    run["flat_array"] = _array_info(flat)
    run["flat_same_data_pointer"] = bool(flat.__array_interface__["data"][0] == result.__array_interface__["data"][0])

    started = time.perf_counter()
    cpu.applyRGBA(flat)
    run["apply_rgba_ms"] = _ms(started)

    started = time.perf_counter()
    reshaped = flat.reshape(result.shape)
    run["reshape_back_ms"] = _ms(started)
    run["output_array"] = _array_info(reshaped)

    started = time.perf_counter()
    uint8_preview = (np.clip(reshaped, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    run["post_convert_uint8_ms"] = _ms(started)
    run["uint8_array"] = _array_info(uint8_preview)

    run["total_profiled_ms"] = round(
        run["np_asarray_ms"]
        + run["np_ascontiguousarray_ms"]
        + run["get_processor_object_ms"]
        + run["get_cpu_processor_ms"]
        + run["copy_input_ms"]
        + run["reshape_flatten_ms"]
        + run["apply_rgba_ms"]
        + run["reshape_back_ms"]
        + run["post_convert_uint8_ms"],
        2,
    )
    return run


def _array_info(array: np.ndarray) -> dict[str, Any]:
    return {
        "dtype": str(array.dtype),
        "shape": [int(value) for value in array.shape],
        "strides": [int(value) for value in array.strides],
        "contiguous": bool(array.flags["C_CONTIGUOUS"]),
        "owns_data": bool(array.flags["OWNDATA"]),
    }


def _summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    numeric_keys = [
        "np_asarray_ms",
        "np_ascontiguousarray_ms",
        "get_processor_object_ms",
        "get_cpu_processor_ms",
        "copy_input_ms",
        "reshape_flatten_ms",
        "apply_rgba_ms",
        "reshape_back_ms",
        "post_convert_uint8_ms",
        "total_profiled_ms",
    ]
    summary: dict[str, Any] = {"count": len(runs)}
    for key in numeric_keys:
        values = [float(run[key]) for run in runs]
        summary[key] = {
            "avg": round(sum(values) / len(values), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
        }
    return summary


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 2)


if __name__ == "__main__":
    raise SystemExit(main())
