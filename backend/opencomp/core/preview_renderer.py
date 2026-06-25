from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass

import numpy as np

from opencomp.core.evaluator import FloatPreviewCacheEntry, GraphEvaluator, _viewer_input_edges
from opencomp.core.models import FrameROI, ProjectGraph, ProjectSettings
from opencomp.io.cryptomatte import (
    build_cryptomatte_matte,
    cryptomatte_id_preview_rgba,
    cryptomatte_preview_rgba,
)
from opencomp.io.preview import encode_preview_png, preview_rgba_for_channel, resize_float_rgba


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    cache_node_id: str
    eval_node_id: str
    frame: int
    display: str | None = None
    view: str | None = None
    channel: str | None = "rgba"
    max_width: int | None = None
    max_height: int | None = None
    ocio_config: str | None = None
    output_signature: str | None = None
    viewer_process: ViewerProcess | None = None
    roi: FrameROI | None = None


@dataclass(frozen=True, slots=True)
class ViewerProcess:
    gain: float = 1.0
    saturation: float = 1.0
    fstop: float = 0.0


@dataclass(frozen=True, slots=True)
class FloatPreviewResult:
    entry: FloatPreviewCacheEntry
    cache_hit: bool
    lookup_ms: float
    evaluate_ms: float
    resize_ms: float


def render_standard_preview(evaluator: GraphEvaluator, graph: ProjectGraph, request: PreviewRequest) -> bytes:
    demand = evaluator.channel_demand_for(graph, request.eval_node_id, request.channel)
    with evaluator.channel_demand_scope(demand):
        return _render_standard_preview_scoped(evaluator, graph, request)


def _render_standard_preview_scoped(evaluator: GraphEvaluator, graph: ProjectGraph, request: PreviewRequest) -> bytes:
    node = graph.nodes[request.cache_node_id]
    request_started = time.perf_counter()
    process = _normalized_viewer_process(request.viewer_process)
    cache_channel = _processed_channel_key(request.channel, process)
    preview_key = _preview_key(evaluator, graph, request, cache_channel)
    cached_preview = evaluator.get_cached_preview(preview_key)
    if cached_preview is not None:
        evaluator.mark_node_cache_hit(request.cache_node_id, node.type)
        evaluator.record_phase_timing(
            request.cache_node_id,
            "viewer.preview_cache",
            0.0,
            {"frame": request.frame, "channel": request.channel or "rgba", "hit": True, "bytes": len(cached_preview)},
        )
        evaluator.record_preview_timing(
            request.cache_node_id,
            {
                "cache_hit": True,
                "total_ms": round((time.perf_counter() - request_started) * 1000.0, 2),
                "evaluate_ms": 0.0,
                "resize_ms": 0.0,
                "ocio_ms": 0.0,
                "encode_ms": 0.0,
                "channel": request.channel or "rgba",
                "viewer_process": _viewer_process_payload(process),
                "float_cache_hit": True,
                "execution_backend": "cpu",
                "gpu_kernel_mode": "cpu_fallback",
                "gpu_upload_ms": 0.0,
                "gpu_dispatch_ms": 0.0,
                "gpu_download_ms": 0.0,
                "gpu_resize_ms": 0.0,
                "gpu_cache_hit": False,
                "bytes": len(cached_preview),
            },
        )
        return cached_preview

    float_result = get_float_preview(evaluator, graph, request)
    float_preview = float_result.entry
    float_hit = float_result.cache_hit
    evaluate_ms = float_result.evaluate_ms
    resize_ms = float_result.resize_ms
    evaluator.record_phase_timing(
        request.cache_node_id,
        "viewer.float_cache",
        float_result.lookup_ms + evaluate_ms + resize_ms,
        {
            "frame": request.frame,
            "channel": request.channel or "rgba",
            "hit": float_hit,
            "lookup_ms": round(float_result.lookup_ms, 2),
            "evaluate_ms": round(evaluate_ms, 2),
            "resize_ms": round(resize_ms, 2),
        },
    )
    with evaluator.node_runtime(request.cache_node_id, node.type):
        process_started = time.perf_counter()
        processed_rgba = apply_viewer_process(float_preview.rgba, process)
        process_ms = (time.perf_counter() - process_started) * 1000.0
        evaluator.record_phase_timing(
            request.cache_node_id,
            "viewer.process",
            process_ms,
            {"gain": process.gain, "saturation": process.saturation, "fstop": process.fstop},
        )
        ocio_started = time.perf_counter()
        display_rgba = (
            evaluator.ocio.apply_display_view(processed_rgba, float_preview.colorspace, request.display, request.view)
            if float_preview.apply_ocio
            else processed_rgba
        )
        ocio_ms = (time.perf_counter() - ocio_started) * 1000.0
        evaluator.record_phase_timing(
            request.cache_node_id,
            "viewer.ocio_display",
            ocio_ms,
            {
                "colorspace": float_preview.colorspace,
                "display": request.display,
                "view": request.view,
                "applied": float_preview.apply_ocio,
            },
        )
        encode_started = time.perf_counter()
        png_bytes = encode_preview_png(display_rgba)
        encode_ms = (time.perf_counter() - encode_started) * 1000.0
        evaluator.record_phase_timing(
            request.cache_node_id,
            "viewer.png_encode",
            encode_ms,
            {"width": int(display_rgba.shape[1]), "height": int(display_rgba.shape[0]), "bytes": len(png_bytes)},
        )

    evaluator.store_cached_preview(preview_key, png_bytes)
    evaluator.record_preview_timing(
        request.cache_node_id,
        {
            "cache_hit": False,
            "total_ms": round((time.perf_counter() - request_started) * 1000.0, 2),
            "evaluate_ms": round(evaluate_ms, 2),
            "resize_ms": round(resize_ms, 2),
            "viewer_process_ms": round(process_ms, 2),
            "ocio_ms": round(ocio_ms, 2),
            "encode_ms": round(encode_ms, 2),
            "source_width": float_preview.source_width,
            "source_height": float_preview.source_height,
            "pixel_aspect": float_preview.pixel_aspect,
            "channel": request.channel or "rgba",
            "viewer_process": _viewer_process_payload(process),
            "float_cache_hit": float_hit,
            "execution_backend": float_preview.execution_backend,
            "gpu_kernel_mode": float_preview.gpu_kernel_mode,
            "gpu_upload_ms": round(float_preview.gpu_upload_ms, 2),
            "gpu_dispatch_ms": round(float_preview.gpu_dispatch_ms, 2),
            "gpu_download_ms": round(float_preview.gpu_download_ms, 2),
            "gpu_resize_ms": round(float_preview.gpu_resize_ms, 2),
            "gpu_cache_hit": float_preview.gpu_cache_hit,
            "preview_width": int(display_rgba.shape[1]),
            "preview_height": int(display_rgba.shape[0]),
            "bytes": len(png_bytes),
        },
    )
    return png_bytes


def render_difference_preview(
    evaluator: GraphEvaluator,
    graph: ProjectGraph,
    request_a: PreviewRequest,
    request_b: PreviewRequest,
) -> bytes:
    demand_a = evaluator.channel_demand_for(graph, request_a.eval_node_id, request_a.channel)
    demand_b = evaluator.channel_demand_for(graph, request_b.eval_node_id, request_b.channel)
    with evaluator.channel_demand_scope(demand_a):
        output_a = request_a.output_signature or evaluator.output_signature(graph, request_a.cache_node_id, request_a.frame)
    with evaluator.channel_demand_scope(demand_b):
        output_b = request_b.output_signature or evaluator.output_signature(graph, request_b.cache_node_id, request_b.frame)
    return _render_difference_preview_scoped(evaluator, graph, request_a, request_b, _combined_signature(output_a, output_b))


def _render_difference_preview_scoped(
    evaluator: GraphEvaluator,
    graph: ProjectGraph,
    request_a: PreviewRequest,
    request_b: PreviewRequest,
    output_signature: str,
) -> bytes:
    node = graph.nodes[request_a.cache_node_id]
    request_started = time.perf_counter()
    process = _normalized_viewer_process(request_a.viewer_process)
    preview_key = evaluator.preview_cache_key_for_signature(
        request_a.cache_node_id,
        request_a.frame,
        output_signature,
        request_a.display,
        request_a.view,
        f"difference:{request_a.channel or 'rgba'}:{_viewer_process_key(process)}",
        request_a.max_width,
        request_a.max_height,
        request_a.ocio_config,
    )
    cached_preview = evaluator.get_cached_preview(preview_key)
    if cached_preview is not None:
        evaluator.mark_node_cache_hit(request_a.cache_node_id, node.type)
        evaluator.record_phase_timing(
            request_a.cache_node_id,
            "viewer.preview_cache",
            0.0,
            {"frame": request_a.frame, "channel": request_a.channel or "rgba", "hit": True, "mode": "difference"},
        )
        evaluator.record_preview_timing(request_a.cache_node_id, _cached_timing("difference", len(cached_preview)))
        return cached_preview

    result_a = get_float_preview(evaluator, graph, request_a)
    result_b = get_float_preview(evaluator, graph, request_b)
    float_a, hit_a, eval_a_ms, resize_a_ms = (
        result_a.entry,
        result_a.cache_hit,
        result_a.evaluate_ms,
        result_a.resize_ms,
    )
    float_b, hit_b, eval_b_ms, resize_b_ms = (
        result_b.entry,
        result_b.cache_hit,
        result_b.evaluate_ms,
        result_b.resize_ms,
    )
    evaluator.record_phase_timing(
        request_a.cache_node_id,
        "viewer.float_cache",
        result_a.lookup_ms + result_b.lookup_ms + eval_a_ms + eval_b_ms + resize_a_ms + resize_b_ms,
        {
            "frame": request_a.frame,
            "channel": request_a.channel or "rgba",
            "hit": hit_a and hit_b,
            "mode": "difference",
            "lookup_ms": round(result_a.lookup_ms + result_b.lookup_ms, 2),
            "evaluate_ms": round(eval_a_ms + eval_b_ms, 2),
            "resize_ms": round(resize_a_ms + resize_b_ms, 2),
        },
    )
    with evaluator.node_runtime(request_a.cache_node_id, node.type):
        process_started = time.perf_counter()
        processed_a = apply_viewer_process(float_a.rgba, process)
        processed_b = apply_viewer_process(float_b.rgba, process)
        height = min(processed_a.shape[0], processed_b.shape[0])
        width = min(processed_a.shape[1], processed_b.shape[1])
        difference = np.abs(processed_a[:height, :width] - processed_b[:height, :width]).astype(np.float32, copy=False)
        difference[:, :, 3] = 1.0
        process_ms = (time.perf_counter() - process_started) * 1000.0
        evaluator.record_phase_timing(
            request_a.cache_node_id,
            "viewer.process",
            process_ms,
            {"mode": "difference", "gain": process.gain, "saturation": process.saturation, "fstop": process.fstop},
        )
        ocio_started = time.perf_counter()
        display_rgba = (
            evaluator.ocio.apply_display_view(difference, float_a.colorspace, request_a.display, request_a.view)
            if float_a.apply_ocio
            else difference
        )
        ocio_ms = (time.perf_counter() - ocio_started) * 1000.0
        evaluator.record_phase_timing(
            request_a.cache_node_id,
            "viewer.ocio_display",
            ocio_ms,
            {"mode": "difference", "colorspace": float_a.colorspace, "display": request_a.display, "view": request_a.view},
        )
        encode_started = time.perf_counter()
        png_bytes = encode_preview_png(display_rgba)
        encode_ms = (time.perf_counter() - encode_started) * 1000.0
        evaluator.record_phase_timing(
            request_a.cache_node_id,
            "viewer.png_encode",
            encode_ms,
            {"mode": "difference", "width": int(display_rgba.shape[1]), "height": int(display_rgba.shape[0]), "bytes": len(png_bytes)},
        )

    evaluator.store_cached_preview(preview_key, png_bytes)
    evaluator.record_preview_timing(
        request_a.cache_node_id,
        {
            "cache_hit": False,
            "total_ms": round((time.perf_counter() - request_started) * 1000.0, 2),
            "evaluate_ms": round(eval_a_ms + eval_b_ms, 2),
            "resize_ms": round(resize_a_ms + resize_b_ms, 2),
            "viewer_process_ms": round(process_ms, 2),
            "ocio_ms": round(ocio_ms, 2),
            "encode_ms": round(encode_ms, 2),
            "channel": request_a.channel or "rgba",
            "compare_mode": "difference",
            "viewer_process": _viewer_process_payload(process),
            "float_cache_hit": hit_a and hit_b,
            "execution_backend": "vulkan" if float_a.execution_backend == "vulkan" or float_b.execution_backend == "vulkan" else "cpu",
            "gpu_kernel_mode": "native_compute" if float_a.gpu_kernel_mode == "native_compute" or float_b.gpu_kernel_mode == "native_compute" else "cpu_fallback",
            "gpu_upload_ms": round(float_a.gpu_upload_ms + float_b.gpu_upload_ms, 2),
            "gpu_dispatch_ms": round(float_a.gpu_dispatch_ms + float_b.gpu_dispatch_ms, 2),
            "gpu_download_ms": round(float_a.gpu_download_ms + float_b.gpu_download_ms, 2),
            "gpu_resize_ms": round(float_a.gpu_resize_ms + float_b.gpu_resize_ms, 2),
            "gpu_cache_hit": float_a.gpu_cache_hit and float_b.gpu_cache_hit,
            "preview_width": int(display_rgba.shape[1]),
            "preview_height": int(display_rgba.shape[0]),
            "bytes": len(png_bytes),
        },
    )
    return png_bytes


def get_float_preview(
    evaluator: GraphEvaluator,
    graph: ProjectGraph,
    request: PreviewRequest,
) -> FloatPreviewResult:
    demand = evaluator.channel_demand_for(graph, request.eval_node_id, request.channel)
    with evaluator.channel_demand_scope(demand):
        output_signature = request.output_signature or evaluator.output_signature(graph, request.cache_node_id, request.frame)
        cache_key = evaluator.float_preview_cache_key_for_signature(
            request.cache_node_id,
            request.frame,
            output_signature,
            request.channel,
            request.max_width,
            request.max_height,
            _preview_roi_key(request.roi),
        )
        lookup_started = time.perf_counter()
        cached = evaluator.get_cached_float_preview(cache_key)
        lookup_ms = (time.perf_counter() - lookup_started) * 1000.0
        if cached is not None:
            return FloatPreviewResult(cached, True, lookup_ms, 0.0, 0.0)

        preview_target_node_id = _preview_target_node_id(graph, request.eval_node_id)
        evaluate_started = time.perf_counter()
        with evaluator.preview_target_scope(preview_target_node_id, request.max_width, request.max_height):
            image = evaluator.evaluate_node(graph, request.eval_node_id, request.frame)
        evaluate_ms = (time.perf_counter() - evaluate_started) * 1000.0
        resize_started = time.perf_counter()
        source_rgba, apply_ocio = preview_rgba_for_channel(image, request.channel)
        preview_rgba = resize_float_rgba(source_rgba, max_width=request.max_width, max_height=request.max_height)
        display_height = int(preview_rgba.shape[0])
        display_width = int(preview_rgba.shape[1])
        if request.roi is not None:
            preview_rgba = crop_preview_rgba(preview_rgba, request.roi)
        resize_ms = (time.perf_counter() - resize_started) * 1000.0
        source_width = int(image.metadata.get("source_width") or image.metadata.get("gpu/source_width") or image.width)
        source_height = int(image.metadata.get("source_height") or image.metadata.get("gpu/source_height") or image.height)
        entry = FloatPreviewCacheEntry(
            rgba=np.ascontiguousarray(preview_rgba, dtype=np.float32),
            apply_ocio=apply_ocio,
            colorspace=image.colorspace,
            source_width=source_width,
            source_height=source_height,
            display_width=display_width,
            display_height=display_height,
            pixel_aspect=image.pixel_aspect,
            format_bbox=dict(image.format_bbox or {}),
            data_window=dict(image.data_window or {}),
            bytes=int(preview_rgba.nbytes),
            execution_backend=str(image.metadata.get("gpu/backend") or "cpu"),
            gpu_kernel_mode=str(image.metadata.get("gpu/kernel_mode") or "cpu_fallback"),
            gpu_upload_ms=float(image.metadata.get("gpu/upload_ms") or 0.0),
            gpu_dispatch_ms=float(image.metadata.get("gpu/dispatch_ms") or 0.0),
            gpu_download_ms=float(image.metadata.get("gpu/download_ms") or 0.0),
            gpu_resize_ms=float(image.metadata.get("gpu/resize_ms") or 0.0),
            gpu_cache_hit=bool(image.metadata.get("gpu/cache_hit", False)),
        )
        evaluator.store_cached_float_preview(cache_key, entry)
        return FloatPreviewResult(entry, False, lookup_ms, evaluate_ms, resize_ms)


def apply_viewer_process(rgba: np.ndarray, process: ViewerProcess) -> np.ndarray:
    image = np.asarray(rgba, dtype=np.float32)
    result = np.nan_to_num(image.copy(), nan=0.0, posinf=65504.0, neginf=-65504.0)
    exposure = float(2.0 ** process.fstop) * process.gain
    rgb = result[:, :, :3] * exposure
    if process.saturation != 1.0:
        luma = rgb[:, :, 0:1] * 0.2126 + rgb[:, :, 1:2] * 0.7152 + rgb[:, :, 2:3] * 0.0722
        rgb = luma + (rgb - luma) * process.saturation
    result[:, :, :3] = rgb
    return np.ascontiguousarray(result.astype(np.float32, copy=False))


def warm_viewer_input_previews(
    evaluator: GraphEvaluator,
    graph: ProjectGraph,
    viewer_id: str,
    frame: int,
    display: str | None,
    view: str | None,
    channel: str | None,
    max_width: int | None,
    max_height: int | None,
    ocio_config: str | None,
    viewer_process: ViewerProcess | None = None,
    input_sockets: set[str] | None = None,
) -> None:
    viewer = graph.nodes.get(viewer_id)
    if viewer is None or viewer.type.lower() != "viewer":
        return

    warmed_signatures: set[str] = set()
    for edge in graph.incoming_edges(viewer_id):
        if input_sockets is not None and edge.target_socket not in input_sockets:
            continue
        if edge.source_node not in graph.nodes:
            continue
        try:
            demand = evaluator.channel_demand_for(graph, edge.source_node, channel)
            with evaluator.channel_demand_scope(demand):
                output_signature = evaluator.node_signature(graph, edge.source_node, frame)
            if output_signature in warmed_signatures:
                continue
            warmed_signatures.add(output_signature)
            request = PreviewRequest(
                cache_node_id=viewer_id,
                eval_node_id=edge.source_node,
                frame=frame,
                display=display,
                view=view,
                channel=channel,
                max_width=max_width,
                max_height=max_height,
                ocio_config=ocio_config,
                output_signature=output_signature,
                viewer_process=viewer_process,
            )
            if evaluator.has_cached_preview(_preview_key(evaluator, graph, request, _processed_channel_key(channel, _normalized_viewer_process(viewer_process)))):
                continue
            render_standard_preview(evaluator, graph, request)
        except Exception:
            continue


def render_cryptomatte_preview(
    evaluator: GraphEvaluator,
    graph: ProjectGraph,
    node_id: str,
    frame: int,
    layer: str | None,
    matte_ids: list[str],
    max_width: int | None,
    max_height: int | None,
    settings: ProjectSettings,
) -> bytes:
    demand_channel = f"{layer}*" if layer else "*cryptomatte*"
    demand = evaluator.channel_demand_for(graph, node_id, demand_channel)
    with evaluator.channel_demand_scope(demand):
        return _render_cryptomatte_preview_scoped(
            evaluator,
            graph,
            node_id,
            frame,
            layer,
            matte_ids,
            max_width,
            max_height,
            settings,
        )


def _render_cryptomatte_preview_scoped(
    evaluator: GraphEvaluator,
    graph: ProjectGraph,
    node_id: str,
    frame: int,
    layer: str | None,
    matte_ids: list[str],
    max_width: int | None,
    max_height: int | None,
    settings: ProjectSettings,
) -> bytes:
    cache_channel = (
        f"cryptomatte:{layer or ''}:{','.join(sorted(matte_ids))}"
        if matte_ids
        else f"cryptomatte:{layer or ''}:id-preview"
    )
    request_started = time.perf_counter()
    preview_key = evaluator.preview_cache_key(
        graph,
        node_id,
        frame,
        None,
        None,
        cache_channel,
        max_width,
        max_height,
        settings.ocio_config,
    )
    cached_preview = evaluator.get_cached_preview(preview_key)
    if cached_preview is not None:
        evaluator.mark_node_cache_hit(node_id, graph.nodes[node_id].type)
        evaluator.record_preview_timing(node_id, _cached_timing(cache_channel, len(cached_preview)))
        return cached_preview

    evaluate_started = time.perf_counter()
    image = evaluator.evaluate_node(graph, node_id, frame)
    evaluate_ms = (time.perf_counter() - evaluate_started) * 1000.0
    preview_started = time.perf_counter()
    if matte_ids:
        preview_rgba = cryptomatte_preview_rgba(build_cryptomatte_matte(image, layer, matte_ids, settings))
    else:
        preview_rgba = cryptomatte_id_preview_rgba(image, layer, settings)
    preview_ms = (time.perf_counter() - preview_started) * 1000.0
    resize_started = time.perf_counter()
    proxy = resize_float_rgba(preview_rgba, max_width=max_width, max_height=max_height)
    resize_ms = (time.perf_counter() - resize_started) * 1000.0
    encode_started = time.perf_counter()
    png_bytes = encode_preview_png(proxy)
    encode_ms = (time.perf_counter() - encode_started) * 1000.0

    evaluator.store_cached_preview(preview_key, png_bytes)
    evaluator.record_preview_timing(
        node_id,
        {
            "cache_hit": False,
            "total_ms": round((time.perf_counter() - request_started) * 1000.0, 2),
            "evaluate_ms": round(evaluate_ms, 2),
            "preview_ms": round(preview_ms, 2),
            "resize_ms": round(resize_ms, 2),
            "ocio_ms": 0.0,
            "encode_ms": round(encode_ms, 2),
            "channel": cache_channel,
            "preview_width": int(proxy.shape[1]),
            "preview_height": int(proxy.shape[0]),
            "bytes": len(png_bytes),
        },
    )
    return png_bytes


def _preview_key(
    evaluator: GraphEvaluator,
    graph: ProjectGraph,
    request: PreviewRequest,
    cache_channel: str | None = None,
):
    roi_suffix = _preview_roi_key(request.roi)
    cache_channel = _preview_cache_channel(cache_channel or request.channel, roi_suffix)
    if request.output_signature is not None:
        return evaluator.preview_cache_key_for_signature(
            request.cache_node_id,
            request.frame,
            request.output_signature,
            request.display,
            request.view,
            cache_channel,
            request.max_width,
            request.max_height,
            request.ocio_config,
        )
    return evaluator.preview_cache_key(
        graph,
        request.cache_node_id,
        request.frame,
        request.display,
        request.view,
        cache_channel,
        request.max_width,
        request.max_height,
        request.ocio_config,
    )


def _cached_timing(channel: str, byte_count: int) -> dict:
    return {
        "cache_hit": True,
        "total_ms": 0.0,
        "evaluate_ms": 0.0,
        "resize_ms": 0.0,
        "ocio_ms": 0.0,
        "encode_ms": 0.0,
        "channel": channel,
        "execution_backend": "cpu",
        "gpu_kernel_mode": "cpu_fallback",
        "gpu_upload_ms": 0.0,
        "gpu_dispatch_ms": 0.0,
        "gpu_download_ms": 0.0,
        "gpu_resize_ms": 0.0,
        "gpu_cache_hit": False,
        "bytes": byte_count,
    }


def _normalized_viewer_process(process: ViewerProcess | None) -> ViewerProcess:
    process = process or ViewerProcess()
    gain = _finite_or_default(process.gain, 1.0)
    saturation = _finite_or_default(process.saturation, 1.0)
    fstop = _finite_or_default(process.fstop, 0.0)
    return ViewerProcess(gain=max(gain, 0.0), saturation=max(saturation, 0.0), fstop=fstop)


def _finite_or_default(value: float, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def _viewer_process_key(process: ViewerProcess) -> str:
    return f"g={process.gain:.6g};s={process.saturation:.6g};f={process.fstop:.6g}"


def _viewer_process_payload(process: ViewerProcess) -> dict[str, float]:
    return {"gain": process.gain, "saturation": process.saturation, "fstop": process.fstop}


def _processed_channel_key(channel: str | None, process: ViewerProcess) -> str:
    return f"{channel or 'rgba'}|vp:{_viewer_process_key(process)}"


def _combined_signature(a: str, b: str) -> str:
    return hashlib.sha1(f"{a}|{b}".encode("utf-8")).hexdigest()


def _preview_cache_channel(channel: str | None, roi_key: str | None) -> str:
    base = channel or "rgba"
    return base if not roi_key else f"{base}|roi:{roi_key}"


def _preview_roi_key(roi: FrameROI | None) -> str | None:
    if roi is None:
        return None
    width = max(0, int(roi.width))
    height = max(0, int(roi.height))
    if width <= 0 or height <= 0:
        return None
    return f"{int(roi.x)}:{int(roi.y)}:{width}:{height}"


def _preview_target_node_id(graph: ProjectGraph, node_id: str) -> str:
    node = graph.nodes.get(node_id)
    if node is None or node.type.lower() != "viewer":
        return node_id
    edges = _viewer_input_edges(graph, node_id)
    if not edges:
        return node_id
    return edges[0].source_node


def crop_preview_rgba(rgba: np.ndarray, roi: FrameROI | None) -> np.ndarray:
    if roi is None:
        return rgba
    height = int(rgba.shape[0])
    width = int(rgba.shape[1])
    x = clamp_int(int(roi.x), 0, width)
    y = clamp_int(int(roi.y), 0, height)
    max_width = max(0, width - x)
    max_height = max(0, height - y)
    cropped_width = min(max(0, int(roi.width)), max_width)
    cropped_height = min(max(0, int(roi.height)), max_height)
    if cropped_width <= 0 or cropped_height <= 0:
        return np.zeros((1, 1, 4), dtype=np.float32)
    return np.ascontiguousarray(rgba[y : y + cropped_height, x : x + cropped_width], dtype=np.float32)


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))
