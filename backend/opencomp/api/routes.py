"""Viewer, render, diagnostics, and WebSocket routes for the OpenComp backend.

This module now owns the transport-heavy side of the API: viewer rendering,
preview streaming, cache diagnostics, render-job control, and websocket event
delivery. Project/session CRUD lives in dedicated route modules so this file
can stay focused on frame-serving and runtime behavior.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from opencomp.api.app_state import ensure_runtime_state
from opencomp.api.context import (
    ensure_script_tabs,
    get_active_graph,
    get_evaluator,
    get_evaluator_from_state,
    get_project,
    get_project_from_state,
    get_render_scheduler_from_state,
    node_error_payload as _node_error_payload,
)
from opencomp.api.node_routes import router as node_router
from opencomp.api.project_routes import router as project_router
from opencomp.api.viewer_float import (
    float_preview_payload as _float_preview_payload_float,
    float_preview_tile_source as _float_preview_tile_source_float,
    record_viewer_request_timing as _record_viewer_request_timing_float,
)
from opencomp.api.viewer_float_stream import send_float_stream as _send_float_stream
from opencomp.api.viewer_requests import (
    build_preview_request as _build_preview_request_req,
    ensure_frame_request_id as _ensure_frame_request_id_req,
    float_preview_entry as _float_preview_entry_req,
    read_preload_node_ids as _read_preload_node_ids_req,
    render_viewer_png as _render_viewer_png_req,
    schedule_viewer_warm as _schedule_viewer_warm_req,
    viewer_eval_source as _viewer_eval_source_req,
    viewer_request_scope as _viewer_request_scope_req,
    warm_read_frames as _warm_read_frames_req,
    warm_viewer_float_frames as _warm_viewer_float_frames_req,
)
from opencomp.api.viewer_transport import (
    close_websocket_quietly as _close_websocket_quietly,
    send_websocket_cancelled_quietly as _send_websocket_cancelled_quietly,
    send_websocket_error_quietly as _send_websocket_error_quietly,
)
from opencomp.api.viewer_context import resolved_viewer_display_view
from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.core.evaluator import GraphCycleError, GraphEvaluator, UnknownNodeTypeError
from opencomp.core.models import FrameRequest, HealthResponse, ReadWarmRequest, ViewerWarmRequest
from opencomp.nodes.base import NodeEvaluationError

router = APIRouter()
router.include_router(node_router)
router.include_router(project_router)


@router.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/api/color/config")
async def color_config(request: Request):
    project = get_project(request)
    engine = OCIOColorEngine(project.settings.ocio_config)
    display, view = resolved_viewer_display_view(project.settings)
    return {
        "available": engine.available,
        "diagnostics": engine.diagnostics(),
        "current_config": project.settings.ocio_config,
        "builtin_configs": engine.builtin_configs(),
        "colorspaces": engine.colorspaces(),
        "displays": engine.displays(),
        "views": engine.views(display),
        "default_display": engine.default_display(),
        "default_view": engine.default_view(display),
        "viewer_display": display,
        "viewer_view": view,
    }


@router.get("/api/color/gpu-shader")
async def color_gpu_shader(
    request: Request,
    src: str | None = None,
    display: str | None = None,
    view: str | None = None,
):
    project = get_project(request)
    engine = OCIOColorEngine(project.settings.ocio_config)
    source = src or project.settings.working_colorspace
    resolved_display, resolved_view = resolved_viewer_display_view(project.settings, display, view)
    return engine.gpu_display_shader(
        source,
        resolved_display,
        resolved_view,
    )


@router.post("/api/viewer/frame")
async def viewer_frame(request: Request, payload: FrameRequest) -> Response:
    project = get_project(request)
    ensure_script_tabs(project)
    evaluator = get_evaluator(request, project)
    active_graph = get_active_graph(project)
    started = time.perf_counter()
    try:
        png_bytes = await asyncio.to_thread(_render_viewer_png_req, project, active_graph, evaluator, payload)
    except (GraphCycleError, UnknownNodeTypeError, NodeEvaluationError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=_node_error_payload(exc)) from exc
    total_ms = (time.perf_counter() - started) * 1000.0
    preview_timing = dict(evaluator.preview_timings.get(payload.node_id, {}))
    _record_viewer_request_timing_float(
        evaluator,
        payload,
        {
            "transport": "http",
            "total_ms": total_ms,
            "backend_render_ms": total_ms,
            "send_ms": 0.0,
            "bytes": len(png_bytes),
            "execution_backend": preview_timing.get("execution_backend", "cpu"),
            "gpu_kernel_mode": preview_timing.get("gpu_kernel_mode", "cpu_fallback"),
            "gpu_upload_ms": preview_timing.get("gpu_upload_ms", 0.0),
            "gpu_dispatch_ms": preview_timing.get("gpu_dispatch_ms", 0.0),
            "gpu_download_ms": preview_timing.get("gpu_download_ms", 0.0),
            "gpu_resize_ms": preview_timing.get("gpu_resize_ms", 0.0),
            "gpu_cache_hit": preview_timing.get("gpu_cache_hit", False),
        },
    )
    _schedule_viewer_warm_req(project, active_graph, evaluator, payload)
    return Response(content=png_bytes, media_type="image/png")


@router.post("/api/viewer/warm")
async def viewer_warm(request: Request, payload: ViewerWarmRequest):
    project = get_project(request)
    ensure_script_tabs(project)
    evaluator = get_evaluator(request, project)
    active_graph = get_active_graph(project)
    if payload.node_id not in active_graph.nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node: {payload.node_id}")
    frames = [int(frame) for frame in payload.frames if project.settings.frame_start <= int(frame) <= project.settings.frame_end]
    if not frames:
        return {"status": "skipped", "frames": []}
    warm_graph = active_graph.model_copy(deep=True)
    asyncio.create_task(
        asyncio.to_thread(
            _warm_viewer_float_frames_req,
            project,
            warm_graph,
            evaluator,
            payload,
            frames[: max(1, min(len(frames), 64))],
        )
    )
    return {"status": "scheduled", "frames": frames}


@router.post("/api/reads/warm")
async def reads_warm(request: Request, payload: ReadWarmRequest):
    project = get_project(request)
    ensure_script_tabs(project)
    evaluator = get_evaluator(request, project)
    active_graph = get_active_graph(project)
    if not project.preferences.read_preload_enabled:
        return {"status": "disabled", "frames": [], "read_nodes": []}
    if payload.node_id not in active_graph.nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node: {payload.node_id}")
    frame_limit = max(1, int(project.preferences.read_preload_max_frames or 1))
    frames = [
        int(frame)
        for frame in payload.frames
        if project.settings.frame_start <= int(frame) <= project.settings.frame_end
    ][:frame_limit]
    if not frames:
        return {"status": "skipped", "frames": [], "read_nodes": []}
    read_nodes = _read_preload_node_ids_req(active_graph, payload.node_id, payload.viewer_input)
    if not read_nodes:
        return {"status": "skipped", "frames": frames, "read_nodes": []}
    warm_graph = active_graph.model_copy(deep=True)
    asyncio.create_task(
        asyncio.to_thread(
            _warm_read_frames_req,
            project,
            warm_graph,
            evaluator,
            payload,
            frames,
        )
    )
    return {"status": "scheduled", "frames": frames, "read_nodes": read_nodes}


@router.post("/api/render")
async def render_single_frame(request: Request, payload: FrameRequest):
    project = get_project(request)
    ensure_script_tabs(project)
    active_graph = get_active_graph(project)
    node = active_graph.nodes.get(payload.node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Unknown node: {payload.node_id}")
    if node.type.lower() != "write":
        raise HTTPException(status_code=400, detail="MVP render endpoint expects a Write node.")
    try:
        evaluator = get_evaluator(request, project)
        await asyncio.to_thread(evaluator.evaluate_node, active_graph, payload.node_id, payload.frame)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "complete", "node_id": payload.node_id, "frame": payload.frame}


@router.post("/api/render/jobs")
async def create_render_job(request: Request, payload: FrameRequest):
    project = get_project(request)
    ensure_script_tabs(project)
    active_graph = get_active_graph(project)
    node = active_graph.nodes.get(payload.node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Unknown node: {payload.node_id}")
    if node.type.lower() != "write":
        raise HTTPException(status_code=400, detail="Render jobs currently expect a Write node.")
    jobs = ensure_runtime_state(request.app.state).render_jobs
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "queued",
        "node_id": payload.node_id,
        "frame": payload.frame,
        "created": time.time(),
        "started": None,
        "completed": None,
        "error": None,
        "result": None,
    }
    jobs[job_id] = job
    evaluator = get_evaluator(request, project)
    graph_snapshot = active_graph.model_copy(deep=True)
    asyncio.create_task(_run_render_job(jobs, job_id, evaluator, graph_snapshot, payload.model_copy(deep=True)))
    return job


@router.get("/api/render/jobs/{job_id}")
async def get_render_job(request: Request, job_id: str):
    jobs = ensure_runtime_state(request.app.state).render_jobs
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown render job: {job_id}")
    return job


@router.post("/api/render/jobs/{job_id}/cancel")
async def cancel_render_job(request: Request, job_id: str):
    jobs = ensure_runtime_state(request.app.state).render_jobs
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown render job: {job_id}")
    if job["status"] in {"complete", "failed", "cancelled"}:
        return job
    job["status"] = "cancel_requested"
    return job


async def _run_render_job(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    evaluator: GraphEvaluator,
    graph,
    payload: FrameRequest,
) -> None:
    job = jobs.get(job_id)
    if job is None:
        return
    if job.get("status") == "cancel_requested":
        job["status"] = "cancelled"
        job["completed"] = time.time()
        return
    job["status"] = "running"
    job["started"] = time.time()
    started = time.perf_counter()
    try:
        image = await asyncio.to_thread(evaluator.evaluate_node, graph, payload.node_id, payload.frame)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        job["completed"] = time.time()
        return
    if job.get("status") == "cancel_requested":
        job["status"] = "cancelled"
    else:
        job["status"] = "complete"
    job["completed"] = time.time()
    job["result"] = {
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
        "width": image.width,
        "height": image.height,
        "output": image.metadata.get("write/filename"),
        "skipped": image.metadata.get("write/skipped"),
    }


@router.get("/api/render/{job_id}")
async def get_render_legacy(request: Request, job_id: str):
    jobs = ensure_runtime_state(request.app.state).render_jobs
    return jobs.get(job_id, {"job_id": job_id, "status": "unknown", "message": "Render job was not found in this backend session."})


@router.get("/api/cache/status")
async def cache_status(request: Request):
    project = get_project(request)
    ensure_script_tabs(project)
    active_graph = get_active_graph(project)
    evaluator = get_evaluator(request, project)
    viewer_node_ids = {node.id for node in active_graph.nodes.values() if node.type.lower() == "viewer"}
    return {
        **evaluator.cache_snapshot(viewer_node_ids or None),
        "graph_revision": ensure_runtime_state(request.app.state).graph_revision,
        "scheduler": get_render_scheduler_from_state(request.app.state).snapshot(),
    }


@router.post("/api/cache/clear")
async def clear_cache(request: Request):
    project = get_project(request)
    evaluator = get_evaluator(request, project)
    evaluator.clear_cache()
    return {"status": "cleared"}


@router.websocket("/ws/viewer/frame")
async def websocket_viewer_frame(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            payload = FrameRequest.model_validate_json(await websocket.receive_text())
            request_id = _ensure_frame_request_id_req(payload)
            scheduler = get_render_scheduler_from_state(websocket.app.state)
            request_scope = _viewer_request_scope_req(payload)
            scheduler.begin(
                request_id,
                scope=request_scope,
                node_id=payload.node_id,
                frame=payload.frame,
                priority=payload.priority,
                cancel_before=payload.cancel_before,
                metadata={"transport": "websocket-png"},
            )
            project = get_project_from_state(websocket.app.state)
            ensure_script_tabs(project)
            evaluator = get_evaluator_from_state(websocket.app.state, project)
            active_graph = get_active_graph(project)
            render_started = time.perf_counter()
            png_bytes = await asyncio.to_thread(_render_viewer_png_req, project, active_graph, evaluator, payload)
            render_ms = (time.perf_counter() - render_started) * 1000.0
            if not scheduler.is_current(request_scope, request_id):
                await _send_websocket_cancelled_quietly(websocket, request_id)
                return
            send_started = time.perf_counter()
            await websocket.send_bytes(png_bytes)
            send_ms = (time.perf_counter() - send_started) * 1000.0
            _record_viewer_request_timing_float(
                evaluator,
                payload,
                {
                    "transport": "websocket",
                    "total_ms": render_ms + send_ms,
                    "backend_render_ms": render_ms,
                    "send_ms": send_ms,
                    "bytes": len(png_bytes),
                    "execution_backend": evaluator.preview_timings.get(payload.node_id, {}).get("execution_backend", "cpu"),
                    "gpu_kernel_mode": evaluator.preview_timings.get(payload.node_id, {}).get("gpu_kernel_mode", "cpu_fallback"),
                    "gpu_upload_ms": evaluator.preview_timings.get(payload.node_id, {}).get("gpu_upload_ms", 0.0),
                    "gpu_dispatch_ms": evaluator.preview_timings.get(payload.node_id, {}).get("gpu_dispatch_ms", 0.0),
                    "gpu_download_ms": evaluator.preview_timings.get(payload.node_id, {}).get("gpu_download_ms", 0.0),
                    "gpu_resize_ms": evaluator.preview_timings.get(payload.node_id, {}).get("gpu_resize_ms", 0.0),
                    "gpu_cache_hit": evaluator.preview_timings.get(payload.node_id, {}).get("gpu_cache_hit", False),
                },
            )
            _schedule_viewer_warm_req(project, active_graph, evaluator, payload)
            scheduler.complete(request_id)
            await _close_websocket_quietly(websocket)
            return
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await _send_websocket_error_quietly(websocket, exc)


@router.websocket("/ws/viewer/float")
async def websocket_viewer_float(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            payload = FrameRequest.model_validate_json(await websocket.receive_text())
            request_id = _ensure_frame_request_id_req(payload)
            scheduler = get_render_scheduler_from_state(websocket.app.state)
            request_scope = _viewer_request_scope_req(payload)
            scheduler.begin(
                request_id,
                scope=request_scope,
                node_id=payload.node_id,
                frame=payload.frame,
                priority=payload.priority,
                cancel_before=payload.cancel_before,
                metadata={"transport": "websocket-float", "tile_lane": payload.tile_lane},
            )
            project = get_project_from_state(websocket.app.state)
            ensure_script_tabs(project)
            evaluator = get_evaluator_from_state(websocket.app.state, project)
            active_graph = get_active_graph(project)
            render_started = time.perf_counter()
            if payload.stream_tiles:
                header, rgba, tiles, native_eval_node_id = await asyncio.to_thread(
                    _float_preview_tile_source_float,
                    project,
                    active_graph,
                    evaluator,
                    payload,
                )
            else:
                header, data = await asyncio.to_thread(_float_preview_payload_float, project, active_graph, evaluator, payload)
                native_eval_node_id = None
            render_ms = (time.perf_counter() - render_started) * 1000.0
            send_started = time.perf_counter()
            send_stats = await _send_float_stream(
                websocket,
                scheduler=scheduler,
                request_scope=request_scope,
                request_id=request_id,
                payload=payload,
                header=header,
                data=None if payload.stream_tiles else data,
                rgba=rgba if payload.stream_tiles else None,
                tiles=tiles if payload.stream_tiles else None,
                native_eval_node_id=native_eval_node_id,
                active_graph=active_graph if payload.stream_tiles else None,
                evaluator=evaluator if payload.stream_tiles else None,
                tile_workers=project.settings.tile_workers,
            )
            if send_stats is None:
                return
            send_ms = (time.perf_counter() - send_started) * 1000.0
            _record_viewer_request_timing_float(
                evaluator,
                payload,
                {
                    "transport": f"websocket-{header['dtype']}{'-tiles' if payload.stream_tiles else ''}",
                    "total_ms": render_ms + send_stats.tile_render_ms + send_stats.tile_encode_ms + send_stats.ws_write_ms,
                    "backend_render_ms": render_ms + send_stats.tile_render_ms,
                    "send_ms": send_stats.tile_encode_ms + send_stats.ws_write_ms,
                    "float_cache_lookup_ms": header.get("float_cache_lookup_ms", 0.0),
                    "node_eval_ms": float(header.get("node_eval_ms", 0.0) or 0.0) + send_stats.tile_render_ms,
                    "resize_ms": header.get("resize_ms", 0.0),
                    "tile_encode_ms": send_stats.tile_encode_ms,
                    "ws_write_ms": send_stats.ws_write_ms,
                    "tile_native": bool(header.get("tile_native")),
                    "tile_render_ms": send_stats.tile_render_ms,
                    "bytes": send_stats.sent_bytes,
                    "tile_count": header.get("tile_count", 0),
                    "tile_count_total": header.get("tile_count_total", header.get("tile_count", 0)),
                    "tile_height": header.get("tile_height"),
                    "lane_count": header.get("tile_lanes", 1),
                    "tile_lane": header.get("tile_lane"),
                    "transfer_mode": header.get("transfer_mode"),
                    "float_cache_hit": header.get("cache_hit", False),
                    "execution_backend": header.get("execution_backend", "cpu"),
                    "gpu_kernel_mode": header.get("gpu_kernel_mode", "cpu_fallback"),
                    "gpu_upload_ms": header.get("gpu_upload_ms", 0.0),
                    "gpu_dispatch_ms": header.get("gpu_dispatch_ms", 0.0),
                    "gpu_download_ms": header.get("gpu_download_ms", 0.0),
                    "gpu_resize_ms": header.get("gpu_resize_ms", 0.0),
                    "gpu_cache_hit": header.get("gpu_cache_hit", False),
                },
            )
            scheduler.complete(request_id)
            await _close_websocket_quietly(websocket)
            return
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await _send_websocket_error_quietly(websocket, exc)


@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await websocket.accept()
    try:
        await websocket.send_text(json.dumps({"type": "connected", "app": "OpenComp Studio"}))
        while True:
            message = await websocket.receive_text()
            await websocket.send_text(json.dumps({"type": "echo", "payload": message}))
    except WebSocketDisconnect:
        return
