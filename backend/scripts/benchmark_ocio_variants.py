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
from opencomp.io.preview import resize_float_rgba


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark OCIO backend variants on the current float-preview workflow.")
    parser.add_argument("--path", required=True, help="Sequence path pattern, for example \\\\server\\share\\shot_####.exr")
    parser.add_argument("--frame", type=int, default=1001, help="Frame to benchmark.")
    parser.add_argument("--colorspace", default="ACES2065-1", help="Read node colorspace assumption.")
    parser.add_argument("--working-colorspace", default="ACES2065-1", help="Project working colorspace.")
    parser.add_argument("--display", help="Viewer display override.")
    parser.add_argument("--view", help="Viewer view override.")
    parser.add_argument("--runs", type=int, default=3, help="Timed runs per variant.")
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
    source_rgba = result.entry.rgba

    engine = evaluator.ocio or OCIOColorEngine(project.settings.ocio_config)
    if not engine.available:
        raise SystemExit("OCIO is not available in this environment.")

    display_name = payload.display or engine.default_display()
    current_view = payload.view or engine.default_view(display_name)
    available_views = engine.views(display_name)
    comparison_views = _comparison_views(current_view, available_views)

    variants = [
        _run_variant(engine, source_rgba, result.entry.colorspace, display_name, current_view, "full_rgba_current", runs=args.runs),
        _run_variant(engine, source_rgba, result.entry.colorspace, display_name, current_view, "full_rgb_current", mode="rgb", runs=args.runs),
        _run_variant(engine, source_rgba, result.entry.colorspace, display_name, current_view, "w2048_rgba_current", max_width=2048, max_height=2048, runs=args.runs),
        _run_variant(engine, source_rgba, result.entry.colorspace, display_name, current_view, "w1920_rgba_current", max_width=1920, max_height=1920, runs=args.runs),
        _run_variant(engine, source_rgba, result.entry.colorspace, display_name, current_view, "w1024_rgba_current", max_width=1024, max_height=1024, runs=args.runs),
        _run_variant(engine, source_rgba, result.entry.colorspace, display_name, current_view, "w1024_rgb_current", max_width=1024, max_height=1024, mode="rgb", runs=args.runs),
    ]

    for view_name in comparison_views:
        variants.append(
            _run_variant(
                engine,
                source_rgba,
                result.entry.colorspace,
                display_name,
                view_name,
                f"full_rgba_view_{_slug(view_name)}",
                runs=args.runs,
            )
        )

    report = {
        "input": {
            "path_pattern": path,
            "frame_path": concrete_path,
            "frame": args.frame,
            "colorspace": result.entry.colorspace,
            "display": display_name,
            "current_view": current_view,
            "available_views": available_views,
            "source_prepare_ms": round(source_ms, 2),
            "float_cache_hit": bool(result.cache_hit),
            "evaluate_ms": round(result.evaluate_ms, 2),
            "resize_ms": round(result.resize_ms, 2),
            "source_array": _array_info(source_rgba),
        },
        "variants": variants,
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
    return Project(project_name="ocio-variant-benchmark", settings=settings, graph=graph, script_tabs=[], active_script_id="main")


def _run_variant(
    engine: OCIOColorEngine,
    source_rgba: np.ndarray,
    src: str,
    display: str | None,
    view: str | None,
    label: str,
    *,
    mode: str = "rgba",
    max_width: int | None = None,
    max_height: int | None = None,
    runs: int = 3,
) -> dict[str, Any]:
    prepared, resize_ms = _prepare_input(source_rgba, max_width=max_width, max_height=max_height)
    timings = [_profile_variant_once(engine, prepared, src, display, view, mode=mode) for _ in range(max(1, runs))]
    summary = _summarize_variant_runs(timings)
    pixels = int(prepared.shape[0] * prepared.shape[1])
    mp = pixels / 1_000_000.0
    summary["megapixels"] = round(mp, 3)
    summary["resize_ms"] = round(resize_ms, 2)
    summary["end_to_end_with_resize_ms_avg"] = round(summary["total_ms_avg"] + resize_ms, 2)
    if summary["apply_ms_avg"] > 0:
        summary["megapixels_per_second"] = round(mp / (summary["apply_ms_avg"] / 1000.0), 3)
    else:
        summary["megapixels_per_second"] = None
    return {
        "label": label,
        "mode": mode,
        "display": display,
        "view": view,
        "resize_target": {"max_width": max_width, "max_height": max_height},
        "prepared_array": _array_info(prepared),
        "runs": timings,
        "summary": summary,
    }


def _prepare_input(source_rgba: np.ndarray, *, max_width: int | None, max_height: int | None) -> tuple[np.ndarray, float]:
    if max_width is None or max_height is None:
        return np.ascontiguousarray(np.asarray(source_rgba, dtype=np.float32)), 0.0
    started = time.perf_counter()
    resized = resize_float_rgba(source_rgba, max_width=max_width, max_height=max_height)
    resize_ms = _ms(started)
    return np.ascontiguousarray(np.asarray(resized, dtype=np.float32)), resize_ms


def _profile_variant_once(
    engine: OCIOColorEngine,
    prepared_rgba: np.ndarray,
    src: str,
    display: str | None,
    view: str | None,
    *,
    mode: str,
) -> dict[str, Any]:
    run: dict[str, Any] = {"mode": mode}
    display_name = display or engine.default_display()
    view_name = view or engine.default_view(display_name)

    started = time.perf_counter()
    rgba = np.asarray(prepared_rgba, dtype=np.float32)
    run["np_asarray_ms"] = _ms(started)

    started = time.perf_counter()
    contiguous = np.ascontiguousarray(rgba)
    run["np_ascontiguousarray_ms"] = _ms(started)

    started = time.perf_counter()
    processor_object = engine._get_display_processor_object(src, display_name, view_name)
    run["get_processor_object_ms"] = _ms(started)
    run["processor_object_type"] = type(processor_object).__name__

    started = time.perf_counter()
    cpu = engine._get_display_processor(src, display_name, view_name)
    run["get_cpu_processor_ms"] = _ms(started)
    run["cpu_processor_type"] = type(cpu).__name__

    started = time.perf_counter()
    if mode == "rgb":
        rgb = np.ascontiguousarray(contiguous[:, :, :3].copy())
        alpha = contiguous[:, :, 3:4].copy()
        run["copy_input_ms"] = _ms(started)
        run["working_array"] = _array_info(rgb)

        started = time.perf_counter()
        flat_rgb = np.ascontiguousarray(rgb.reshape((-1, 3)))
        run["reshape_flatten_ms"] = _ms(started)

        started = time.perf_counter()
        cpu.applyRGB(flat_rgb)
        run["apply_ms"] = _ms(started)

        started = time.perf_counter()
        rgb_display = flat_rgb.reshape(rgb.shape)
        combined = np.ascontiguousarray(np.concatenate([rgb_display, alpha], axis=-1))
        run["recombine_ms"] = _ms(started)
        output = combined
    else:
        working = contiguous.copy()
        run["copy_input_ms"] = _ms(started)
        run["working_array"] = _array_info(working)

        started = time.perf_counter()
        flat_rgba = np.ascontiguousarray(working.reshape((-1, 4)))
        run["reshape_flatten_ms"] = _ms(started)

        started = time.perf_counter()
        cpu.applyRGBA(flat_rgba)
        run["apply_ms"] = _ms(started)

        started = time.perf_counter()
        output = flat_rgba.reshape(working.shape)
        run["recombine_ms"] = _ms(started)

    started = time.perf_counter()
    uint8_preview = (np.clip(output, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    run["post_convert_uint8_ms"] = _ms(started)
    run["output_array"] = _array_info(output)
    run["uint8_array"] = _array_info(uint8_preview)
    run["total_ms"] = round(
        run["np_asarray_ms"]
        + run["np_ascontiguousarray_ms"]
        + run["get_processor_object_ms"]
        + run["get_cpu_processor_ms"]
        + run["copy_input_ms"]
        + run["reshape_flatten_ms"]
        + run["apply_ms"]
        + run["recombine_ms"]
        + run["post_convert_uint8_ms"],
        2,
    )
    return run


def _summarize_variant_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [
        "np_asarray_ms",
        "np_ascontiguousarray_ms",
        "get_processor_object_ms",
        "get_cpu_processor_ms",
        "copy_input_ms",
        "reshape_flatten_ms",
        "apply_ms",
        "recombine_ms",
        "post_convert_uint8_ms",
        "total_ms",
    ]
    summary: dict[str, Any] = {"count": len(runs)}
    for key in keys:
        values = [float(run[key]) for run in runs]
        summary[f"{key}_avg"] = round(sum(values) / len(values), 2)
        summary[f"{key}_min"] = round(min(values), 2)
        summary[f"{key}_max"] = round(max(values), 2)
    return summary


def _comparison_views(current_view: str | None, available_views: list[str]) -> list[str]:
    if not current_view:
        return []
    priorities = ["Raw", "Un-tone-mapped", "Video (colorimetric)"]
    selected: list[str] = []
    for candidate in priorities:
        if candidate in available_views and candidate != current_view:
            selected.append(candidate)
    if not selected:
        for candidate in available_views:
            if candidate != current_view:
                selected.append(candidate)
                break
    return selected


def _array_info(array: np.ndarray) -> dict[str, Any]:
    return {
        "dtype": str(array.dtype),
        "shape": [int(value) for value in array.shape],
        "strides": [int(value) for value in array.strides],
        "contiguous": bool(array.flags["C_CONTIGUOUS"]),
        "owns_data": bool(array.flags["OWNDATA"]),
    }


def _slug(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "_" for character in value).strip("_")


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 2)


if __name__ == "__main__":
    raise SystemExit(main())
