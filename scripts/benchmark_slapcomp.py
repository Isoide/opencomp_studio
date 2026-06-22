from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
TESTS = BACKEND / "tests"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(TESTS))

from opencomp.core.evaluator import GraphEvaluator  # noqa: E402
from opencomp.core.models import ProjectSettings, TileWindow  # noqa: E402
from test_slapcomp import (  # noqa: E402
    LOCAL_CLOTHES,
    LOCAL_MAIN_3D,
    LOCAL_PLATE,
    local_lal_105_slapcomp_graph,
    synthetic_slapcomp_graph,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the OpenComp slapcomp graph shape.")
    parser.add_argument("--mode", choices=["synthetic", "local"], default="synthetic")
    parser.add_argument("--frames", default="1001", help="Comma-separated frames to evaluate.")
    parser.add_argument("--iterations", type=int, default=3, help="Warm repeated viewer evaluations per frame.")
    parser.add_argument("--tile-width", type=int, default=256)
    parser.add_argument("--tile-height", type=int, default=128)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    frames = [int(item.strip()) for item in args.frames.split(",") if item.strip()]
    if not frames:
        raise ValueError("At least one frame is required.")

    if args.mode == "local":
        missing = [
            str(path).replace("####", str(frames[0]))
            for path in (LOCAL_PLATE, LOCAL_MAIN_3D, LOCAL_CLOTHES)
            if not Path(str(path).replace("####", str(frames[0]))).exists()
        ]
        if missing:
            raise FileNotFoundError(f"Local EXR benchmark files are unavailable: {missing}")
        graph = local_lal_105_slapcomp_graph(ROOT / "renders" / "benchmark_local.####.png")
        settings = _settings(width=4096, height=3024, tile_height=64)
    else:
        graph = synthetic_slapcomp_graph(ROOT / "renders" / "benchmark_synthetic.####.png", width=1024, height=576)
        settings = _settings(width=1024, height=576, tile_height=64)

    evaluator = GraphEvaluator(settings=settings, max_cache_bytes=10 * 1024 * 1024 * 1024)
    results: list[dict[str, Any]] = []
    for frame in frames:
        result = _benchmark_frame(evaluator, graph, frame, args.iterations, args.tile_width, args.tile_height)
        results.append(result)

    payload = {
        "mode": args.mode,
        "frames": frames,
        "iterations": args.iterations,
        "results": results,
        "cache": _cache_summary(evaluator.cache_snapshot()),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_report(payload)
    return 0


def _benchmark_frame(
    evaluator: GraphEvaluator,
    graph,
    frame: int,
    iterations: int,
    tile_width: int,
    tile_height: int,
) -> dict[str, Any]:
    cold_started = time.perf_counter()
    image = evaluator.evaluate_node(graph, "Viewer1", frame)
    cold_ms = _elapsed_ms(cold_started)

    warm_ms: list[float] = []
    for _index in range(max(1, iterations)):
        started = time.perf_counter()
        evaluator.evaluate_node(graph, "Viewer1", frame)
        warm_ms.append(_elapsed_ms(started))

    tile_window = TileWindow(0, 0, min(tile_width, image.width), min(tile_height, image.height))
    tile_started = time.perf_counter()
    evaluator.evaluate_node_tile(graph, "Viewer1", frame, tile_window)
    tile_cold_ms = _elapsed_ms(tile_started)
    tile_started = time.perf_counter()
    evaluator.evaluate_node_tile(graph, "Viewer1", frame, tile_window)
    tile_warm_ms = _elapsed_ms(tile_started)

    return {
        "frame": frame,
        "width": image.width,
        "height": image.height,
        "cold_viewer_ms": round(cold_ms, 2),
        "warm_viewer_ms": [round(value, 2) for value in warm_ms],
        "warm_viewer_avg_ms": round(statistics.mean(warm_ms), 2),
        "tile_window": {"x": tile_window.x, "y": tile_window.y, "width": tile_window.width, "height": tile_window.height},
        "cold_tile_ms": round(tile_cold_ms, 2),
        "warm_tile_ms": round(tile_warm_ms, 2),
    }


def _settings(*, width: int, height: int, tile_height: int) -> ProjectSettings:
    return ProjectSettings(
        frame_start=1001,
        frame_end=1010,
        width=width,
        height=height,
        working_colorspace="ACES2065-1",
        proxy_enabled=False,
        cache_enabled=True,
        tile_rendering_enabled=True,
        tile_height=tile_height,
        tile_workers=4,
        render_workers=4,
        read_workers=4,
    )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _cache_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "entries",
        "hits",
        "misses",
        "tile_cache_entries",
        "tile_cache_hits",
        "tile_cache_misses",
        "execution_plan_entries",
        "execution_plan_hits",
        "execution_plan_misses",
        "memory_bytes",
        "tile_cache_memory_bytes",
    )
    return {key: snapshot.get(key) for key in keys}


def _print_report(payload: dict[str, Any]) -> None:
    print(f"OpenComp slapcomp benchmark: {payload['mode']}")
    for result in payload["results"]:
        print(
            f"F{result['frame']} {result['width']}x{result['height']} "
            f"cold viewer {result['cold_viewer_ms']} ms | "
            f"warm avg {result['warm_viewer_avg_ms']} ms | "
            f"tile cold/warm {result['cold_tile_ms']}/{result['warm_tile_ms']} ms"
        )
    print("Cache:")
    for key, value in payload["cache"].items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
