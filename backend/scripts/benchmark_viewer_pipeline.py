from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from opencomp.api.viewer_float import float_preview_payload
from opencomp.api.viewer_requests import render_viewer_png
from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, FrameRequest, Node, Project, ProjectGraph, ProjectSettings


@dataclass(slots=True)
class RunMetrics:
    label: str
    wall_ms: float
    bytes_out: int
    preview_timing: dict[str, Any]
    node_timings: dict[str, Any]
    phase_timings: list[dict[str, Any]]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the backend viewer pipeline on a plate-like slapcomp.")
    parser.add_argument("--path", required=True, help="Sequence path pattern, for example \\\\server\\share\\shot_####.exr")
    parser.add_argument("--frame", type=int, default=1001, help="Frame to benchmark.")
    parser.add_argument("--colorspace", default="ACES2065-1", help="Read node colorspace assumption.")
    parser.add_argument("--working-colorspace", default="ACES2065-1", help="Project working colorspace.")
    parser.add_argument("--runs", type=int, default=3, help="Warm runs to average after the cold run.")
    parser.add_argument("--precision", default="float16", choices=["float16", "float32", "rgb10a2", "uint8"], help="Float transport precision.")
    parser.add_argument("--include-huecorrect", action="store_true", help="Include HueCorrect in the benchmark graph.")
    parser.add_argument("--proxy-enabled", action="store_true", help="Enable proxy resizing in the benchmarked viewer path.")
    parser.add_argument("--proxy-width", type=int, default=1280, help="Proxy width when proxy is enabled.")
    parser.add_argument("--proxy-height", type=int, default=720, help="Proxy height when proxy is enabled.")
    parser.add_argument("--execution-backend", default="auto", choices=["auto", "cpu", "vulkan"], help="Backend execution preference.")
    parser.add_argument("--image-io-backend", default="auto", choices=["auto", "openexr", "oiio"], help="EXR reader backend preference.")
    parser.add_argument("--display", help="Viewer display override.")
    parser.add_argument("--view", help="Viewer view override.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress logging.")
    parser.add_argument("--output", help="Optional path to write the benchmark JSON report.")
    parser.add_argument("--log-file", help="Optional path to append benchmark progress logs.")
    args = parser.parse_args()

    path = args.path
    if "####" not in path:
        raise SystemExit("Expected a sequence path with #### frame padding.")
    concrete_path = path.replace("####", f"{args.frame:04d}")
    if not Path(concrete_path).exists():
        raise SystemExit(f"Frame does not exist: {concrete_path}")

    log_enabled = not args.quiet
    log = _logger(log_enabled, args.log_file)
    log(f"benchmark input: {concrete_path}")

    project = _build_project(
        path,
        args.frame,
        args.colorspace,
        args.working_colorspace,
        args.include_huecorrect,
        args.proxy_enabled,
        args.proxy_width,
        args.proxy_height,
        args.execution_backend,
        args.image_io_backend,
        args.display,
        args.view,
    )
    evaluator = GraphEvaluator(settings=project.settings, max_cache_bytes=1024 * 1024 * 1024)
    payload = FrameRequest(
        node_id="Viewer1",
        frame=args.frame,
        display=project.settings.viewer_display,
        view=project.settings.viewer_view,
        channel="rgba",
        precision=args.precision,
        stream_tiles=False,
        transfer_mode="float16-rgba" if args.precision == "float16" else "float32-rgba",
    )

    log(f"graph: {' -> '.join(node.type for node in project.graph.nodes.values())}")
    cold_png = _run_png_benchmark(project, evaluator, payload, "png-cold", clear_cache=True, log=log)
    warm_png = [_run_png_benchmark(project, evaluator, payload, f"png-warm-{index + 1}", clear_cache=False, log=log) for index in range(args.runs)]

    cold_float = _run_float_benchmark(project, evaluator, payload, "float-cold", clear_cache=True, log=log)
    warm_float = [_run_float_benchmark(project, evaluator, payload, f"float-warm-{index + 1}", clear_cache=False, log=log) for index in range(args.runs)]

    report = {
        "benchmark": {
            "path_pattern": path,
            "frame": args.frame,
            "frame_path": concrete_path,
            "colorspace": args.colorspace,
            "working_colorspace": args.working_colorspace,
            "ocio_available": OCIOColorEngine(project.settings.ocio_config).available,
            "viewer_display": project.settings.viewer_display,
            "viewer_view": project.settings.viewer_view,
            "proxy_enabled": project.settings.proxy_enabled,
            "viewer_max_width": project.settings.viewer_max_width,
            "viewer_max_height": project.settings.viewer_max_height,
            "execution_backend": project.settings.execution_backend,
            "image_io_backend": project.settings.image_io_backend,
            "graph": [node.type for node in project.graph.nodes.values()],
        },
        "png": _summarize_mode(cold_png, warm_png),
        "float": _summarize_mode(cold_float, warm_float),
        "cache_status": evaluator.cache_snapshot(),
    }
    report_json = json.dumps(report, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_json + "\n", encoding="utf-8")
        log(f"wrote benchmark report: {output_path}")
    print(report_json)
    return 0


def _build_project(
    path: str,
    frame: int,
    colorspace: str,
    working_colorspace: str,
    include_huecorrect: bool,
    proxy_enabled: bool,
    proxy_width: int,
    proxy_height: int,
    execution_backend: str,
    image_io_backend: str,
    display_override: str | None,
    view_override: str | None,
) -> Project:
    engine = OCIOColorEngine(None)
    display = display_override or engine.default_display()
    view = view_override or (engine.default_view(display) if display else None)
    settings = ProjectSettings(
        frame_start=frame,
        frame_end=frame,
        working_colorspace=working_colorspace,
        viewer_display=display,
        viewer_view=view,
        proxy_enabled=proxy_enabled,
        viewer_max_width=proxy_width,
        viewer_max_height=proxy_height,
        cache_enabled=True,
        tile_rendering_enabled=True,
        tile_workers=4,
        read_workers=4,
        render_workers=4,
        execution_backend=execution_backend,
        image_io_backend=image_io_backend,
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
        ],
    )
    if include_huecorrect:
        graph.nodes["HueCorrect1"] = Node(
            id="HueCorrect1",
            type="HueCorrect",
            name="HueCorrect",
            position=(120, 500),
            params={
                "sat_points": [[0.0, 1.0], [0.5, 1.1], [1.0, 1.0]],
                "lum_points": [[0.0, 1.0], [1.0, 1.0]],
                "hue_shift_points": [[0.0, 0.0], [1.0, 0.0]],
                "red_gain_points": [[0.0, 1.0], [1.0, 1.0]],
                "green_gain_points": [[0.0, 1.0], [1.0, 1.0]],
                "blue_gain_points": [[0.0, 1.0], [1.0, 1.0]],
                "red_suppress_points": [[0.0, 1.0], [1.0, 1.0]],
                "green_suppress_points": [[0.0, 1.0], [1.0, 1.0]],
                "blue_suppress_points": [[0.0, 1.0], [1.0, 1.0]],
                "sat_threshold": 0.0,
                "mix": 1.0,
            },
        )
        graph.edges.append(Edge(id="e-colorcorrect-huecorrect", source_node="ColorCorrect1", target_node="HueCorrect1"))
        graph.edges.append(Edge(id="e-huecorrect-viewer", source_node="HueCorrect1", target_node="Viewer1", target_socket="0"))
    else:
        graph.edges.append(Edge(id="e-colorcorrect-viewer", source_node="ColorCorrect1", target_node="Viewer1", target_socket="0"))
    return Project(project_name="benchmark", settings=settings, graph=graph, script_tabs=[], active_script_id="main")


def _run_png_benchmark(
    project: Project,
    evaluator: GraphEvaluator,
    payload: FrameRequest,
    label: str,
    clear_cache: bool,
    log,
) -> RunMetrics:
    if clear_cache:
        evaluator.clear_cache()
    log(f"[{label}] start png preview")
    phase_start = len(evaluator.phase_timings)
    wall_started = time.perf_counter()
    png_bytes = render_viewer_png(project, project.graph, evaluator, payload)
    wall_ms = (time.perf_counter() - wall_started) * 1000.0
    metrics = RunMetrics(
        label=label,
        wall_ms=wall_ms,
        bytes_out=len(png_bytes),
        preview_timing=dict(evaluator.preview_timings.get("Viewer1", {})),
        node_timings=dict(evaluator.node_timings),
        phase_timings=list(evaluator.phase_timings[phase_start:]),
    )
    log(f"[{label}] done png preview: {wall_ms:.2f} ms, {len(png_bytes)} bytes")
    return metrics


def _run_float_benchmark(
    project: Project,
    evaluator: GraphEvaluator,
    payload: FrameRequest,
    label: str,
    clear_cache: bool,
    log,
) -> RunMetrics:
    if clear_cache:
        evaluator.clear_cache()
    log(f"[{label}] start float preview payload")
    phase_start = len(evaluator.phase_timings)
    wall_started = time.perf_counter()
    header, data = float_preview_payload(project, project.graph, evaluator, payload)
    wall_ms = (time.perf_counter() - wall_started) * 1000.0
    metrics = RunMetrics(
        label=label,
        wall_ms=wall_ms,
        bytes_out=len(data),
        preview_timing={
            "cache_hit": bool(header.get("cache_hit")),
            "evaluate_ms": float(header.get("node_eval_ms", 0.0) or 0.0),
            "resize_ms": float(header.get("resize_ms", 0.0) or 0.0),
            "float_cache_lookup_ms": float(header.get("float_cache_lookup_ms", 0.0) or 0.0),
            "execution_backend": header.get("execution_backend", "cpu"),
            "gpu_kernel_mode": header.get("gpu_kernel_mode", "cpu_fallback"),
            "gpu_upload_ms": float(header.get("gpu_upload_ms", 0.0) or 0.0),
            "gpu_dispatch_ms": float(header.get("gpu_dispatch_ms", 0.0) or 0.0),
            "gpu_download_ms": float(header.get("gpu_download_ms", 0.0) or 0.0),
            "gpu_resize_ms": float(header.get("gpu_resize_ms", 0.0) or 0.0),
            "gpu_cache_hit": bool(header.get("gpu_cache_hit", False)),
            "dtype": header.get("dtype"),
            "width": header.get("width"),
            "height": header.get("height"),
        },
        node_timings=dict(evaluator.node_timings),
        phase_timings=list(evaluator.phase_timings[phase_start:]),
    )
    log(f"[{label}] done float preview payload: {wall_ms:.2f} ms, {len(data)} bytes")
    return metrics


def _summarize_mode(cold: RunMetrics, warm: list[RunMetrics]) -> dict[str, Any]:
    return {
        "cold": _run_summary(cold),
        "warm_average": _average_runs(warm),
        "warm_runs": [_run_summary(run) for run in warm],
    }


def _run_summary(run: RunMetrics) -> dict[str, Any]:
    return {
        "label": run.label,
        "wall_ms": round(run.wall_ms, 2),
        "bytes_out": run.bytes_out,
        "preview_timing": _round_nested(run.preview_timing),
        "node_timings": _round_nested(run.node_timings),
        "phase_summary": _phase_summary(run.phase_timings),
        "phase_timings": [_round_nested(item) for item in run.phase_timings],
    }


def _average_runs(runs: list[RunMetrics]) -> dict[str, Any]:
    if not runs:
        return {}
    phase_bucket: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        summary = _phase_summary(run.phase_timings)
        for phase, duration in summary.items():
            phase_bucket[phase].append(duration)
    return {
        "count": len(runs),
        "wall_ms_avg": round(statistics.mean(run.wall_ms for run in runs), 2),
        "wall_ms_min": round(min(run.wall_ms for run in runs), 2),
        "wall_ms_max": round(max(run.wall_ms for run in runs), 2),
        "bytes_out": runs[0].bytes_out,
        "phase_summary_avg": {phase: round(statistics.mean(values), 2) for phase, values in sorted(phase_bucket.items())},
    }


def _phase_summary(phases: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for phase in phases:
        totals[str(phase.get("node_id")) + ":" + str(phase.get("phase"))] += float(phase.get("duration_ms", 0.0) or 0.0)
    return {key: round(value, 2) for key, value in sorted(totals.items())}


def _round_nested(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_nested(item) for item in value]
    return value


def _logger(enabled: bool, file_path: str | None):
    def log(message: str) -> None:
        if file_path:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            timestamped = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(timestamped)
        if not enabled:
            return
        print(message, file=sys.stderr, flush=True)

    return log


if __name__ == "__main__":
    raise SystemExit(main())
