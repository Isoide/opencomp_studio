from __future__ import annotations

import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.core.defaults import create_default_project
from opencomp.core.evaluator import (
    TILE_FULL_HEIGHT,
    TILE_FULL_WIDTH,
    TILE_LOCAL_NODE_TYPES,
    GraphCycleError,
    GraphEvaluator,
    UnknownNodeTypeError,
    _viewer_input_edges,
)
from opencomp.core.models import (
    CreateScriptTabRequest,
    CryptomatteMatteRequest,
    CryptomattePickRequest,
    ExportNukeRequest,
    FrameRequest,
    GraphUpdate,
    HealthResponse,
    ImportProjectRequest,
    LoadProjectRequest,
    NodeCatalogItem,
    Project,
    ProjectPreferencesUpdate,
    ProjectSettingsUpdate,
    PythonScriptRequest,
    PythonScriptResponse,
    ReadWarmRequest,
    RenameScriptTabRequest,
    SaveProjectRequest,
    ScriptTab,
    SetActiveScriptTabRequest,
    TileWindow,
    ViewerWarmRequest,
)
from opencomp.core.render_contract import RenderROI, RenderRequest
from opencomp.core.project_io import (
    ensure_project_scripts,
    export_nuke_project,
    get_active_script as project_io_get_active_script,
    load_project_file,
    normalize_project_for_serialization,
    save_project_file,
)
from opencomp.core.render_scheduler import RenderScheduler
from opencomp.core.preview_renderer import (
    PreviewRequest,
    ViewerProcess,
    get_float_preview,
    render_cryptomatte_preview,
    render_difference_preview,
    render_standard_preview,
    warm_viewer_input_previews,
)
from opencomp.io.cryptomatte import (
    cryptomatte_layer_payload,
    cryptomatte_layers,
    pick_cryptomatte_id,
)
from opencomp.io.nuke_exporter import build_nuke_script
from opencomp.io.preview import MAIN_CHANNEL_ALIASES, preview_rgba_for_channel
from opencomp.nodes import NODE_DEFINITIONS
from opencomp.nodes.base import NodeEvaluationError
from opencomp.core.scripting import run_session_script

router = APIRouter()


def get_project_from_state(state: Any) -> Project:
    project = getattr(state, "project", None)
    if project is None:
        project = create_default_project()
        state.project = project
    return project


def get_project(request: Request) -> Project:
    return get_project_from_state(request.app.state)


def ensure_script_tabs(project: Project) -> None:
    ensure_project_scripts(project)


def get_active_script(project: Project) -> ScriptTab:
    return project_io_get_active_script(project)


def get_evaluator_from_state(state: Any, project: Project) -> GraphEvaluator:
    settings_key = project.settings.model_dump_json()
    max_cache_bytes = max(0, int(project.preferences.cache_memory_limit_mb)) * 1024 * 1024
    max_preview_cache_bytes = _viewer_preview_cache_bytes(max_cache_bytes)
    evaluator = getattr(state, "evaluator", None)
    evaluator_settings_key = getattr(state, "evaluator_settings_key", None)
    if evaluator is None or evaluator_settings_key != settings_key:
        evaluator = GraphEvaluator(
            settings=project.settings,
            max_cache_bytes=max_cache_bytes,
            max_preview_cache_bytes=max_preview_cache_bytes,
            max_float_preview_cache_bytes=max_preview_cache_bytes,
        )
        state.evaluator = evaluator
        state.evaluator_settings_key = settings_key
    else:
        evaluator.set_cache_limits(max_cache_bytes, max_preview_cache_bytes)
    return evaluator


def get_evaluator(request: Request, project: Project) -> GraphEvaluator:
    return get_evaluator_from_state(request.app.state, project)


def get_render_scheduler_from_state(state: Any) -> RenderScheduler:
    scheduler = getattr(state, "render_scheduler", None)
    if scheduler is None:
        scheduler = RenderScheduler()
        state.render_scheduler = scheduler
    return scheduler


def _viewer_preview_cache_bytes(max_cache_bytes: int) -> int:
    if max_cache_bytes <= 0:
        return 0
    return max(64 * 1024 * 1024, max_cache_bytes // 2)


def install_project_in_state(state: Any, project: Project) -> Project:
    ensure_script_tabs(project)
    state.project = project
    max_cache_bytes = max(0, int(project.preferences.cache_memory_limit_mb)) * 1024 * 1024
    state.evaluator = GraphEvaluator(
        settings=project.settings,
        max_cache_bytes=max_cache_bytes,
        max_preview_cache_bytes=_viewer_preview_cache_bytes(max_cache_bytes),
        max_float_preview_cache_bytes=_viewer_preview_cache_bytes(max_cache_bytes),
    )
    state.evaluator_settings_key = project.settings.model_dump_json()
    state.graph_revision = getattr(state, "graph_revision", 0) + 1
    return project


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


@router.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.post("/api/projects/new", response_model=Project)
async def new_project(request: Request) -> Project:
    project = create_default_project()
    return install_project_in_state(request.app.state, project)


@router.post("/api/projects/save", response_model=Project)
async def save_project(request: Request, payload: SaveProjectRequest) -> Project:
    project = payload.project or get_project(request)
    normalize_project_for_serialization(project)
    request.app.state.project = project
    if payload.path:
        save_project_file(project, payload.path)
    return project


@router.post("/api/projects/load", response_model=Project)
async def load_project(request: Request, payload: LoadProjectRequest) -> Project:
    try:
        project = await asyncio.to_thread(load_project_file, payload.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return install_project_in_state(request.app.state, project)


@router.post("/api/projects/import", response_model=Project)
async def import_project(request: Request, payload: ImportProjectRequest) -> Project:
    return install_project_in_state(request.app.state, payload.project)


@router.post("/api/projects/export-nuke")
async def export_nuke(request: Request, payload: ExportNukeRequest):
    project = payload.project or get_project(request)
    ensure_script_tabs(project)
    try:
        output_path = await asyncio.to_thread(export_nuke_project, project, payload.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "exported",
        "path": str(output_path),
        "message": "Nuke .nk export written with v1 OpenComp node mappings.",
    }


@router.post("/api/projects/export-nuke/content")
async def export_nuke_content(request: Request, payload: ExportNukeRequest):
    project = payload.project or get_project(request)
    normalize_project_for_serialization(project)
    try:
        nuke_text = await asyncio.to_thread(build_nuke_script, project, payload.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=nuke_text, media_type="text/plain; charset=utf-8")


@router.get("/api/projects/settings")
async def get_project_settings(request: Request):
    return get_project(request).settings


@router.put("/api/projects/settings")
async def put_project_settings(request: Request, payload: ProjectSettingsUpdate):
    project = get_project(request)
    ensure_script_tabs(project)
    project.settings = payload.settings
    request.app.state.project = project
    return project.settings


@router.get("/api/projects/preferences")
async def get_project_preferences(request: Request):
    return get_project(request).preferences


@router.put("/api/projects/preferences")
async def put_project_preferences(request: Request, payload: ProjectPreferencesUpdate):
    project = get_project(request)
    project.preferences = payload.preferences
    request.app.state.project = project
    return project.preferences


@router.get("/api/graph")
async def get_graph(request: Request):
    project = get_project(request)
    ensure_script_tabs(project)
    return get_active_script(project).graph


@router.put("/api/graph")
async def put_graph(request: Request, payload: GraphUpdate):
    project = get_project(request)
    ensure_script_tabs(project)
    active_script = get_active_script(project)
    active_script.graph = payload.graph
    project.graph = payload.graph
    request.app.state.project = project
    request.app.state.graph_revision = getattr(request.app.state, "graph_revision", 0) + 1
    return project.graph


@router.get("/api/scripts", response_model=list[ScriptTab])
async def get_scripts(request: Request) -> list[ScriptTab]:
    project = get_project(request)
    ensure_script_tabs(project)
    return project.script_tabs


@router.post("/api/scripts", response_model=Project)
async def create_script(request: Request, payload: CreateScriptTabRequest) -> Project:
    project = get_project(request)
    ensure_script_tabs(project)
    index = len(project.script_tabs) + 1
    script_name = payload.name or f"Comp {index}"
    base_id = "".join(character if character.isalnum() else "-" for character in script_name.lower()).strip("-") or "comp"
    script_id = base_id
    existing_ids = {tab.id for tab in project.script_tabs}
    suffix = 2
    while script_id in existing_ids:
        script_id = f"{base_id}-{suffix}"
        suffix += 1
    script_graph = create_default_project().graph
    script = ScriptTab(id=script_id, name=script_name, graph=script_graph, kind=payload.kind)
    project.script_tabs.append(script)
    project.active_script_id = script.id
    project.graph = script.graph
    request.app.state.project = project
    request.app.state.graph_revision = getattr(request.app.state, "graph_revision", 0) + 1
    return project


@router.put("/api/scripts/active", response_model=Project)
async def set_active_script(request: Request, payload: SetActiveScriptTabRequest) -> Project:
    project = get_project(request)
    ensure_script_tabs(project)
    if not any(tab.id == payload.script_id for tab in project.script_tabs):
        raise HTTPException(status_code=404, detail=f"Unknown script tab: {payload.script_id}")
    project.active_script_id = payload.script_id
    project.graph = get_active_script(project).graph
    request.app.state.project = project
    request.app.state.graph_revision = getattr(request.app.state, "graph_revision", 0) + 1
    return project


@router.patch("/api/scripts/{script_id}", response_model=Project)
async def rename_script(request: Request, script_id: str, payload: RenameScriptTabRequest) -> Project:
    project = get_project(request)
    ensure_script_tabs(project)
    for tab in project.script_tabs:
        if tab.id == script_id:
            tab.name = payload.name
            request.app.state.project = project
            return project
    raise HTTPException(status_code=404, detail=f"Unknown script tab: {script_id}")


@router.post("/api/python/run", response_model=PythonScriptResponse)
async def run_python(request: Request, payload: PythonScriptRequest) -> PythonScriptResponse:
    project = get_project(request)
    ensure_script_tabs(project)
    result = await asyncio.to_thread(run_session_script, project, payload.code)
    ensure_script_tabs(project)
    request.app.state.project = project
    if result.changed:
        request.app.state.graph_revision = getattr(request.app.state, "graph_revision", 0) + 1
    return PythonScriptResponse(
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
        error=result.error,
        traceback=result.traceback,
        changed=result.changed,
        project=project,
    )


@router.get("/api/nodes/catalog", response_model=list[NodeCatalogItem])
async def node_catalog() -> list[NodeCatalogItem]:
    return [
        NodeCatalogItem(
            type=definition.type,
            label=definition.label,
            category=definition.category,
            inputs=list(definition.inputs),
            outputs=list(definition.outputs),
        )
        for definition in NODE_DEFINITIONS
    ]


@router.get("/api/color/config")
async def color_config(request: Request):
    project = get_project(request)
    engine = OCIOColorEngine(project.settings.ocio_config)
    display = project.settings.viewer_display
    return {
        "available": engine.available,
        "current_config": project.settings.ocio_config,
        "builtin_configs": engine.builtin_configs(),
        "colorspaces": engine.colorspaces(),
        "displays": engine.displays(),
        "views": engine.views(display),
        "default_display": engine.default_display(),
        "default_view": engine.default_view(display),
        "viewer_display": project.settings.viewer_display,
        "viewer_view": project.settings.viewer_view,
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
    return engine.gpu_display_shader(
        source,
        display or project.settings.viewer_display,
        view or project.settings.viewer_view,
    )


@router.get("/api/nodes/{node_id}/metadata")
async def node_metadata(request: Request, node_id: str, frame: int | None = None):
    project = get_project(request)
    ensure_script_tabs(project)
    active_graph = get_active_script(project).graph
    if node_id not in active_graph.nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node: {node_id}")
    evaluator = get_evaluator(request, project)
    frame_number = frame if frame is not None else project.settings.frame_start
    try:
        image = await asyncio.to_thread(evaluator.evaluate_node, active_graph, node_id, frame_number)
    except (GraphCycleError, UnknownNodeTypeError, NodeEvaluationError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "node_id": node_id,
        "frame": frame_number,
        "width": image.width,
        "height": image.height,
        "pixel_aspect": image.pixel_aspect,
        "display_width": image.width * image.pixel_aspect,
        "display_height": image.height,
        "colorspace": image.colorspace,
        "channels": image.channels,
        "format_bbox": image.format_bbox,
        "data_window": image.data_window,
        "cryptomatte_layers": [cryptomatte_layer_payload(layer) for layer in cryptomatte_layers(image)],
        "metadata": json_safe(image.metadata),
        "resolved_params": json_safe(evaluator.resolved_node(active_graph, node_id, frame_number).params),
        "expression_errors": json_safe(evaluator.expression_errors(active_graph, node_id, frame_number)),
        "bindable_outputs": json_safe(evaluator.bindable_outputs(active_graph, node_id, frame_number)),
    }


@router.get("/api/nodes/{node_id}/bindings")
async def node_bindings(request: Request, node_id: str, frame: int | None = None):
    project = get_project(request)
    ensure_script_tabs(project)
    active_graph = get_active_script(project).graph
    if node_id not in active_graph.nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node: {node_id}")
    evaluator = get_evaluator(request, project)
    frame_number = frame if frame is not None else project.settings.frame_start
    return {
        "node_id": node_id,
        "frame": frame_number,
        "bindable_outputs": json_safe(evaluator.bindable_outputs(active_graph, node_id, frame_number)),
        "expression_errors": json_safe(evaluator.expression_errors(active_graph, node_id, frame_number)),
    }


@router.get("/api/nodes/{node_id}/cryptomatte")
async def node_cryptomatte(request: Request, node_id: str, frame: int | None = None):
    project = get_project(request)
    ensure_script_tabs(project)
    active_graph = get_active_script(project).graph
    if node_id not in active_graph.nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node: {node_id}")
    evaluator = get_evaluator(request, project)
    frame_number = frame if frame is not None else project.settings.frame_start
    try:
        image = await asyncio.to_thread(evaluator.evaluate_node, active_graph, node_id, frame_number)
    except (GraphCycleError, UnknownNodeTypeError, NodeEvaluationError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "node_id": node_id,
        "frame": frame_number,
        "layers": [cryptomatte_layer_payload(layer) for layer in cryptomatte_layers(image)],
    }


@router.post("/api/cryptomatte/pick")
async def cryptomatte_pick(request: Request, payload: CryptomattePickRequest):
    project = get_project(request)
    ensure_script_tabs(project)
    active_graph = get_active_script(project).graph
    if payload.node_id not in active_graph.nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node: {payload.node_id}")
    evaluator = get_evaluator(request, project)
    try:
        image = await asyncio.to_thread(evaluator.evaluate_node, active_graph, payload.node_id, payload.frame)
        pick = await asyncio.to_thread(pick_cryptomatte_id, image, payload.layer, payload.x, payload.y)
    except (GraphCycleError, UnknownNodeTypeError, NodeEvaluationError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if pick is None:
        raise HTTPException(status_code=404, detail="No Cryptomatte id found at this pixel.")
    return {
        "node_id": payload.node_id,
        "frame": payload.frame,
        "layer": pick.layer,
        "id": pick.id_hex,
        "id_float": pick.id_float,
        "name": pick.name,
        "coverage": pick.coverage,
        "x": pick.x,
        "y": pick.y,
    }


@router.post("/api/cryptomatte/matte")
async def cryptomatte_matte(request: Request, payload: CryptomatteMatteRequest) -> Response:
    project = get_project(request)
    ensure_script_tabs(project)
    active_graph = get_active_script(project).graph
    if payload.node_id not in active_graph.nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node: {payload.node_id}")
    evaluator = get_evaluator(request, project)
    max_width = (payload.max_width or project.settings.viewer_max_width) if project.settings.proxy_enabled else None
    max_height = (payload.max_height or project.settings.viewer_max_height) if project.settings.proxy_enabled else None
    try:
        png_bytes = await asyncio.to_thread(
            render_cryptomatte_preview,
            evaluator,
            active_graph,
            payload.node_id,
            payload.frame,
            payload.layer,
            payload.matte_ids,
            max_width,
            max_height,
            project.settings,
        )
    except (GraphCycleError, UnknownNodeTypeError, NodeEvaluationError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=png_bytes, media_type="image/png")


@router.post("/api/viewer/frame")
async def viewer_frame(request: Request, payload: FrameRequest) -> Response:
    project = get_project(request)
    ensure_script_tabs(project)
    evaluator = get_evaluator(request, project)
    active_graph = get_active_script(project).graph
    started = time.perf_counter()
    try:
        png_bytes = await asyncio.to_thread(_render_viewer_png, project, active_graph, evaluator, payload)
    except (GraphCycleError, UnknownNodeTypeError, NodeEvaluationError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    total_ms = (time.perf_counter() - started) * 1000.0
    _record_viewer_request_timing(
        evaluator,
        payload,
        {
            "transport": "http",
            "total_ms": total_ms,
            "backend_render_ms": total_ms,
            "send_ms": 0.0,
            "bytes": len(png_bytes),
        },
    )
    _schedule_viewer_warm(project, active_graph, evaluator, payload)
    return Response(content=png_bytes, media_type="image/png")


@router.post("/api/viewer/warm")
async def viewer_warm(request: Request, payload: ViewerWarmRequest):
    project = get_project(request)
    ensure_script_tabs(project)
    evaluator = get_evaluator(request, project)
    active_graph = get_active_script(project).graph
    if payload.node_id not in active_graph.nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node: {payload.node_id}")
    frames = [int(frame) for frame in payload.frames if project.settings.frame_start <= int(frame) <= project.settings.frame_end]
    if not frames:
        return {"status": "skipped", "frames": []}
    warm_graph = active_graph.model_copy(deep=True)
    asyncio.create_task(
        asyncio.to_thread(
            _warm_viewer_float_frames,
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
    active_graph = get_active_script(project).graph
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
    read_nodes = _read_preload_node_ids(active_graph, payload.node_id, payload.viewer_input)
    if not read_nodes:
        return {"status": "skipped", "frames": frames, "read_nodes": []}
    warm_graph = active_graph.model_copy(deep=True)
    asyncio.create_task(
        asyncio.to_thread(
            _warm_read_frames,
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
    active_graph = get_active_script(project).graph
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
    active_graph = get_active_script(project).graph
    node = active_graph.nodes.get(payload.node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Unknown node: {payload.node_id}")
    if node.type.lower() != "write":
        raise HTTPException(status_code=400, detail="Render jobs currently expect a Write node.")
    jobs = getattr(request.app.state, "render_jobs", None)
    if jobs is None:
        jobs = {}
        request.app.state.render_jobs = jobs
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
    jobs = getattr(request.app.state, "render_jobs", {})
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown render job: {job_id}")
    return job


@router.post("/api/render/jobs/{job_id}/cancel")
async def cancel_render_job(request: Request, job_id: str):
    jobs = getattr(request.app.state, "render_jobs", {})
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
    jobs = getattr(request.app.state, "render_jobs", {})
    return jobs.get(job_id, {"job_id": job_id, "status": "unknown", "message": "Render job was not found in this backend session."})


@router.get("/api/cache/status")
async def cache_status(request: Request):
    project = get_project(request)
    ensure_script_tabs(project)
    active_graph = get_active_script(project).graph
    evaluator = get_evaluator(request, project)
    viewer_node_ids = {node.id for node in active_graph.nodes.values() if node.type.lower() == "viewer"}
    return {
        **evaluator.cache_snapshot(viewer_node_ids or None),
        "graph_revision": getattr(request.app.state, "graph_revision", 0),
        "scheduler": get_render_scheduler_from_state(request.app.state).snapshot(),
    }


@router.post("/api/cache/clear")
async def clear_cache(request: Request):
    project = get_project(request)
    evaluator = get_evaluator(request, project)
    evaluator.clear_cache()
    return {"status": "cleared"}


def _preview_dimensions(project: Project) -> tuple[int | None, int | None]:
    if not project.settings.proxy_enabled:
        return None, None
    return project.settings.viewer_max_width, project.settings.viewer_max_height


def _viewer_process(payload: FrameRequest) -> ViewerProcess:
    return ViewerProcess(gain=payload.gain, saturation=payload.saturation, fstop=payload.fstop)


def _ensure_frame_request_id(payload: FrameRequest) -> str:
    if not payload.request_id:
        payload.request_id = uuid.uuid4().hex
    return payload.request_id


def _viewer_request_scope(payload: FrameRequest) -> str:
    return ":".join(
        [
            payload.node_id,
            str(payload.viewer_input or ""),
            payload.channel or "rgba",
            payload.compare_mode,
        ]
    )


def _render_request_from_frame(
    payload: FrameRequest,
    *,
    node_id: str,
    frame: int | None = None,
    storage: str | None = None,
) -> RenderRequest:
    request_id = _ensure_frame_request_id(payload)
    roi = None
    if payload.roi is not None:
        roi = RenderROI(
            x=int(payload.roi.x),
            y=int(payload.roi.y),
            width=max(0, int(payload.roi.width)),
            height=max(0, int(payload.roi.height)),
        )
    channels = payload.channels or [payload.channel or "rgba"]
    return RenderRequest(
        node_id=node_id,
        frame=int(frame if frame is not None else payload.frame),
        view=payload.view,
        roi=roi,
        render_scale=payload.render_scale,
        mipmap_level=payload.mipmap_level,
        channels=channels,
        layers=payload.layers,
        precision=payload.precision,
        storage=storage or payload.storage,
        request_id=request_id,
        priority=payload.priority,
        cache_policy=payload.cache_policy,
    )


def _build_preview_request(
    project: Project,
    graph,
    evaluator: GraphEvaluator,
    payload: FrameRequest,
    viewer_input: str | None,
) -> PreviewRequest:
    max_width, max_height = _preview_dimensions(project)
    eval_node_id, output_signature = _viewer_eval_source(
        graph,
        evaluator,
        payload.node_id,
        payload.frame,
        viewer_input,
        payload.channel or "rgba",
    )
    evaluator.execution_plan_for(
        graph,
        _render_request_from_frame(payload, node_id=payload.node_id, storage="frontend"),
        eval_node_id=eval_node_id,
        output_signature=output_signature,
    )
    return PreviewRequest(
        cache_node_id=payload.node_id,
        eval_node_id=eval_node_id,
        frame=payload.frame,
        display=payload.display or project.settings.viewer_display,
        view=payload.view or project.settings.viewer_view,
        channel=payload.channel or "rgba",
        max_width=max_width,
        max_height=max_height,
        ocio_config=project.settings.ocio_config,
        output_signature=output_signature,
        viewer_process=_viewer_process(payload),
    )


def _render_viewer_png(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest) -> bytes:
    preview_request = _build_preview_request(project, graph, evaluator, payload, payload.viewer_input)
    if payload.compare_mode == "difference":
        if payload.compare_input is None:
            raise ValueError("Difference mode requires compare_input.")
        return render_difference_preview(
            evaluator,
            graph,
            preview_request,
            _build_preview_request(project, graph, evaluator, payload, payload.compare_input),
        )
    return render_standard_preview(evaluator, graph, preview_request)


def _schedule_viewer_warm(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest) -> None:
    node = graph.nodes.get(payload.node_id)
    if node is None or node.type.lower() != "viewer":
        return
    max_width, max_height = _preview_dimensions(project)
    input_sockets = _viewer_warm_inputs(graph, payload)
    if not input_sockets:
        return
    warm_graph = graph.model_copy(deep=True)
    asyncio.create_task(
        asyncio.to_thread(
            warm_viewer_input_previews,
            evaluator,
            warm_graph,
            payload.node_id,
            payload.frame,
            payload.display or project.settings.viewer_display,
            payload.view or project.settings.viewer_view,
            payload.channel or "rgba",
            max_width,
            max_height,
            project.settings.ocio_config,
            _viewer_process(payload),
            input_sockets,
        )
    )


def _warm_viewer_float_frames(
    project: Project,
    graph,
    evaluator: GraphEvaluator,
    payload: ViewerWarmRequest,
    frames: list[int],
) -> None:
    for frame in frames:
        request = FrameRequest(
            node_id=payload.node_id,
            frame=frame,
            display=payload.display or project.settings.viewer_display,
            view=payload.view or project.settings.viewer_view,
            channel=payload.channel or "rgba",
            viewer_input=payload.viewer_input,
            precision="float16",
            stream_tiles=False,
        )
        try:
            _float_preview_entry(project, graph, evaluator, request)
        except Exception as exc:
            evaluator.record_phase_timing(
                payload.node_id,
                "viewer.warm_failed",
                0.0,
                {"frame": frame, "error": str(exc)},
            )


def _warm_read_frames(
    project: Project,
    graph,
    evaluator: GraphEvaluator,
    payload: ReadWarmRequest,
    frames: list[int],
) -> None:
    target_node_id = _read_preload_target_node(graph, payload.node_id, payload.viewer_input)
    if target_node_id is None:
        return
    read_node_ids = _upstream_read_nodes(graph, target_node_id)
    if not read_node_ids:
        return
    demand = evaluator.channel_demand_for(graph, target_node_id, payload.channel or "rgba")
    tasks = [(read_id, int(frame)) for frame in frames for read_id in read_node_ids]
    if not tasks:
        return

    started = time.perf_counter()
    completed = 0
    failed = 0

    def warm_one(read_id: str, frame: int) -> None:
        evaluator.evaluate_node(graph, read_id, frame, channel_demand=demand)

    worker_count = max(1, min(int(project.settings.read_workers or 1), len(tasks)))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="opencomp-read-preload") as executor:
        future_map = {executor.submit(warm_one, read_id, frame): (read_id, frame) for read_id, frame in tasks}
        for future in as_completed(future_map):
            read_id, frame = future_map[future]
            try:
                future.result()
                completed += 1
            except Exception as exc:
                failed += 1
                evaluator.record_phase_timing(
                    read_id,
                    "read.preload_failed",
                    0.0,
                    {"frame": frame, "error": str(exc)},
                )
    evaluator.record_phase_timing(
        target_node_id,
        "read.preload",
        (time.perf_counter() - started) * 1000.0,
        {
            "frames": frames,
            "read_nodes": read_node_ids,
            "completed": completed,
            "failed": failed,
            "workers": worker_count,
            "channel_demand": demand.cache_key(),
        },
    )


def _read_preload_node_ids(graph, node_id: str, viewer_input: str | None = None) -> list[str]:
    target = _read_preload_target_node(graph, node_id, viewer_input)
    if target is None:
        return []
    return _upstream_read_nodes(graph, target)


def _read_preload_target_node(graph, node_id: str, viewer_input: str | None = None) -> str | None:
    node = graph.nodes.get(node_id)
    if node is None:
        return None
    if node.type.lower() != "viewer":
        return node_id
    edges = graph.incoming_edges(node_id, str(viewer_input)) if viewer_input is not None else _viewer_input_edges(graph, node_id)
    if not edges:
        return None
    source_node = edges[0].source_node
    return source_node if source_node in graph.nodes else None


def _upstream_read_nodes(graph, node_id: str) -> list[str]:
    seen: set[str] = set()
    read_ids: list[str] = []

    def walk(current_id: str) -> None:
        if current_id in seen:
            return
        seen.add(current_id)
        node = graph.nodes.get(current_id)
        if node is None:
            return
        if node.type.lower() == "read":
            read_ids.append(current_id)
            return
        for edge in graph.incoming_edges(current_id):
            walk(edge.source_node)

    walk(node_id)
    return sorted(read_ids)


def _viewer_warm_inputs(graph, payload: FrameRequest) -> set[str]:
    if payload.compare_mode == "difference":
        return {str(value) for value in (payload.viewer_input, payload.compare_input) if value is not None}
    if payload.viewer_input is not None:
        return {str(payload.viewer_input)}
    node = graph.nodes.get(payload.node_id)
    if node is not None and node.type.lower() == "viewer":
        return {str(node.params.get("active_input", "0"))}
    return set()


def _float_preview_entry(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest):
    if payload.compare_mode != "none":
        raise ValueError("Float streaming supports one viewer input per request. Request A and B separately for GPU wipe.")
    preview_request = _build_preview_request(project, graph, evaluator, payload, payload.viewer_input)
    return get_float_preview(evaluator, graph, preview_request)


def _float_preview_header(
    payload: FrameRequest,
    result,
    *,
    stream_tiles: bool,
    tile_height: int | None = None,
    tile_count: int | None = None,
    tile_count_total: int | None = None,
) -> dict[str, Any]:
    entry = result.entry
    rgba = np.asarray(entry.rgba)
    precision = _float_precision(payload)
    height = int(rgba.shape[0])
    width = int(rgba.shape[1])
    resolved_tile_height = _resolved_tile_height(payload, height) if stream_tiles else None
    lane_count = _resolved_tile_lanes(payload)
    header = {
        "type": "viewer_float_frame",
        "request_id": _ensure_frame_request_id(payload),
        "node_id": payload.node_id,
        "frame": payload.frame,
        "viewer_input": payload.viewer_input,
        "channel": payload.channel or "rgba",
        "width": width,
        "height": height,
        "source_width": entry.source_width,
        "source_height": entry.source_height,
        "pixel_aspect": entry.pixel_aspect,
        "colorspace": entry.colorspace,
        "apply_ocio": entry.apply_ocio,
        "format_bbox": entry.format_bbox,
        "data_window": entry.data_window,
        "dtype": precision,
        "layout": "rgba",
        "byte_length": _encoded_rgba_byte_length(width, height, precision),
        "cache_hit": result.cache_hit,
        "float_cache_lookup_ms": round(result.lookup_ms, 2),
        "evaluate_ms": round(result.evaluate_ms, 2),
        "node_eval_ms": round(result.evaluate_ms, 2),
        "resize_ms": round(result.resize_ms, 2),
        "tile_stream": stream_tiles,
        "tile_height": resolved_tile_height,
        "tile_width": int(payload.tile_width or width),
        "tile_count": tile_count if tile_count is not None else (_tile_count(height, resolved_tile_height) if resolved_tile_height else 0),
        "tile_count_total": tile_count_total if tile_count_total is not None else (_tile_count(height, resolved_tile_height) if resolved_tile_height else 0),
        "tile_lanes": lane_count,
        "tile_lane": payload.tile_lane,
        "transfer_mode": payload.transfer_mode,
        "zoom": payload.zoom,
        "render_scale": payload.render_scale,
        "mipmap_level": payload.mipmap_level,
        "channels": payload.channels or [payload.channel or "rgba"],
        "layers": payload.layers,
        "priority": payload.priority,
        "cache_policy": payload.cache_policy,
        "storage": payload.storage,
    }
    if payload.roi is not None:
        header["roi"] = payload.roi.model_dump()
    if payload.viewport is not None:
        header["viewport"] = payload.viewport.model_dump()
    if tile_height is not None:
        header["requested_tile_height"] = tile_height
    return header


def _float_preview_payload(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest) -> tuple[dict[str, Any], bytes]:
    result = _float_preview_entry(project, graph, evaluator, payload)
    encode_started = time.perf_counter()
    data = _encode_float_rgba(result.entry.rgba, _float_precision(payload))
    encode_ms = (time.perf_counter() - encode_started) * 1000.0
    evaluator.record_phase_timing(
        payload.node_id,
        "viewer.float_encode",
        encode_ms,
        {
            "width": int(result.entry.rgba.shape[1]),
            "height": int(result.entry.rgba.shape[0]),
            "dtype": _float_precision(payload),
            "bytes": len(data),
        },
    )
    header = _float_preview_header(payload, result, stream_tiles=False)
    header["tile_encode_ms"] = round(encode_ms, 2)
    return header, data


def _float_preview_tile_source(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest):
    native = _float_preview_native_tile_source(project, graph, evaluator, payload)
    if native is not None:
        return native

    result = _float_preview_entry(project, graph, evaluator, payload)
    tile_height = _resolved_tile_height(payload, int(result.entry.rgba.shape[0]))
    tiles_total = _ordered_tile_ranges(payload, int(result.entry.rgba.shape[1]), int(result.entry.rgba.shape[0]), tile_height)
    tiles = _lane_tile_ranges(payload, tiles_total)
    header = _float_preview_header(
        payload,
        result,
        stream_tiles=True,
        tile_height=tile_height,
        tile_count=len(tiles),
        tile_count_total=len(tiles_total),
    )
    header["tile_native"] = False
    return header, result.entry.rgba, tiles, None


def _float_preview_native_tile_source(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest):
    preview_request = _build_preview_request(project, graph, evaluator, payload, payload.viewer_input)
    channel_name = (preview_request.channel or "rgba").strip().lower()
    if MAIN_CHANNEL_ALIASES.get(channel_name) is None:
        return None
    if preview_request.max_width is not None or preview_request.max_height is not None:
        return None
    if not _tile_native_supported(graph, preview_request.eval_node_id):
        return None

    probe_started = time.perf_counter()
    probe = evaluator.evaluate_node_tile(
        graph,
        preview_request.eval_node_id,
        preview_request.frame,
        TileWindow(0, 0, 1, 1),
        preview_request.channel,
    )
    probe_ms = (time.perf_counter() - probe_started) * 1000.0
    width = int(probe.metadata.get(TILE_FULL_WIDTH) or probe.width)
    height = int(probe.metadata.get(TILE_FULL_HEIGHT) or probe.height)
    if width <= 0 or height <= 0:
        return None
    tile_height = _resolved_tile_height(payload, height)
    tiles_total = _ordered_tile_ranges(payload, width, height, tile_height)
    tiles = _lane_tile_ranges(payload, tiles_total)
    _probe_rgba, apply_ocio = preview_rgba_for_channel(probe, preview_request.channel)
    precision = _float_precision(payload)
    lane_count = _resolved_tile_lanes(payload)
    header = {
        "type": "viewer_float_frame",
        "request_id": _ensure_frame_request_id(payload),
        "node_id": payload.node_id,
        "frame": payload.frame,
        "viewer_input": payload.viewer_input,
        "channel": payload.channel or "rgba",
        "width": width,
        "height": height,
        "source_width": width,
        "source_height": height,
        "pixel_aspect": probe.pixel_aspect,
        "colorspace": probe.colorspace,
        "apply_ocio": apply_ocio,
        "format_bbox": probe.format_bbox,
        "data_window": probe.data_window,
        "dtype": precision,
        "layout": "rgba",
        "byte_length": _encoded_rgba_byte_length(width, height, precision),
        "cache_hit": False,
        "float_cache_lookup_ms": 0.0,
        "evaluate_ms": round(probe_ms, 2),
        "node_eval_ms": round(probe_ms, 2),
        "resize_ms": 0.0,
        "tile_stream": True,
        "tile_native": True,
        "tile_height": tile_height,
        "tile_width": int(payload.tile_width or width),
        "tile_count": len(tiles),
        "tile_count_total": len(tiles_total),
        "tile_lanes": lane_count,
        "tile_lane": payload.tile_lane,
        "transfer_mode": payload.transfer_mode,
        "zoom": payload.zoom,
        "render_scale": payload.render_scale,
        "mipmap_level": payload.mipmap_level,
        "channels": payload.channels or [payload.channel or "rgba"],
        "layers": payload.layers,
        "priority": payload.priority,
        "cache_policy": payload.cache_policy,
        "storage": payload.storage,
        "requested_tile_height": tile_height,
    }
    if payload.viewport is not None:
        header["viewport"] = payload.viewport.model_dump()
    if payload.roi is not None:
        header["roi"] = payload.roi.model_dump()
    return header, None, tiles, preview_request.eval_node_id


def _tile_native_supported(graph, node_id: str, visiting: set[str] | None = None) -> bool:
    if visiting is None:
        visiting = set()
    if node_id in visiting:
        return False
    node = graph.nodes.get(node_id)
    if node is None:
        return False
    node_type = node.type.lower()
    if node_type not in TILE_LOCAL_NODE_TYPES:
        return False
    visiting.add(node_id)
    input_edges = _viewer_input_edges(graph, node_id) if node_type == "viewer" else graph.incoming_edges(node_id)
    for edge in input_edges:
        if not _tile_native_supported(graph, edge.source_node, visiting):
            visiting.remove(node_id)
            return False
    visiting.remove(node_id)
    return True


def _encode_float_rgba(rgba: np.ndarray, precision: str) -> bytes:
    if precision == "uint8":
        data = (np.clip(np.asarray(rgba, dtype=np.float32), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
        return np.ascontiguousarray(data).tobytes(order="C")
    if precision == "rgb10a2":
        image = np.clip(np.asarray(rgba, dtype=np.float32), 0.0, 1.0)
        rgb = (image[:, :, :3] * 1023.0 + 0.5).astype(np.uint32)
        alpha = (image[:, :, 3] * 3.0 + 0.5).astype(np.uint32)
        packed = rgb[:, :, 0] | (rgb[:, :, 1] << np.uint32(10)) | (rgb[:, :, 2] << np.uint32(20)) | (alpha << np.uint32(30))
        return np.ascontiguousarray(packed.astype(np.uint32, copy=False)).tobytes(order="C")
    if precision == "float16":
        return np.ascontiguousarray(np.asarray(rgba, dtype=np.float16)).tobytes(order="C")
    return np.ascontiguousarray(np.asarray(rgba, dtype=np.float32)).tobytes(order="C")


def _encode_float_tile(rgba: np.ndarray, x: int, y: int, width: int, height: int, precision: str) -> bytes:
    return _encode_float_rgba(rgba[y : y + height, x : x + width], precision)


async def _parallel_native_float_tiles(
    graph,
    evaluator: GraphEvaluator,
    payload: FrameRequest,
    eval_node_id: str,
    tiles: list[tuple[int, int, int, int]],
    worker_count: int,
):
    workers = max(1, min(int(worker_count or 1), 16))
    semaphore = asyncio.Semaphore(workers)

    async def run_tile(tile: tuple[int, int, int, int]):
        async with semaphore:
            return await asyncio.to_thread(_render_native_float_tile, graph, evaluator, payload, eval_node_id, tile)

    tasks = [asyncio.create_task(run_tile(tile)) for tile in tiles]
    try:
        for task in asyncio.as_completed(tasks):
            yield await task
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


def _render_native_float_tile(
    graph,
    evaluator: GraphEvaluator,
    payload: FrameRequest,
    eval_node_id: str,
    tile: tuple[int, int, int, int],
) -> tuple[dict[str, Any], bytes, float, float]:
    x, y, width, height = tile
    render_started = time.perf_counter()
    image = evaluator.evaluate_node_tile(graph, eval_node_id, payload.frame, TileWindow(x, y, width, height), payload.channel)
    rgba, _apply_ocio = preview_rgba_for_channel(image, payload.channel)
    render_ms = (time.perf_counter() - render_started) * 1000.0
    encode_started = time.perf_counter()
    data = _encode_float_rgba(rgba, _float_precision(payload))
    encode_ms = (time.perf_counter() - encode_started) * 1000.0
    return (
        {
            "type": "viewer_float_tile",
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "byte_length": len(data),
        },
        data,
        render_ms,
        encode_ms,
    )


def _float_precision(payload: FrameRequest) -> str:
    if payload.precision in {"float16", "uint8", "rgb10a2"}:
        return payload.precision
    return "float32"


def _precision_bytes(precision: str) -> int:
    if precision == "float16":
        return 2
    if precision in {"uint8", "rgb10a2"}:
        return 1
    return 4


def _encoded_rgba_byte_length(width: int, height: int, precision: str) -> int:
    if precision == "rgb10a2":
        return width * height * 4
    return width * height * 4 * _precision_bytes(precision)


def _resolved_tile_height(payload: FrameRequest, image_height: int) -> int:
    if payload.tile_height is not None:
        return max(1, min(int(payload.tile_height), max(1, image_height)))
    return max(1, min(128, max(1, image_height)))


def _resolved_tile_lanes(payload: FrameRequest) -> int:
    return max(1, min(int(payload.tile_lanes or 1), 8))


def _ordered_tile_ranges(payload: FrameRequest, image_width: int, image_height: int, tile_height: int) -> list[tuple[int, int, int, int]]:
    tiles = [(0, y, image_width, min(tile_height, image_height - y)) for y in range(0, image_height, tile_height)]
    viewport = payload.viewport
    if viewport is None or viewport.height <= 0:
        return tiles
    visible_top = max(0, min(image_height, int(viewport.y)))
    visible_bottom = max(visible_top, min(image_height, int(viewport.y + viewport.height)))
    visible_center = (visible_top + visible_bottom) * 0.5

    def score(tile: tuple[int, int, int, int]) -> tuple[int, float, int]:
        _x, y, _width, height = tile
        tile_center = y + height * 0.5
        intersects = y < visible_bottom and (y + height) > visible_top
        return (0 if intersects else 1, abs(tile_center - visible_center), y)

    return sorted(tiles, key=score)


def _lane_tile_ranges(payload: FrameRequest, tiles: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    lane_count = _resolved_tile_lanes(payload)
    lane_index = payload.tile_lane
    if lane_index is None or lane_count <= 1:
        return tiles
    resolved_lane = max(0, min(int(lane_index), lane_count - 1))
    return [tile for index, tile in enumerate(tiles) if index % lane_count == resolved_lane]


def _tile_count(image_height: int, tile_height: int | None) -> int:
    if not tile_height:
        return 0
    return max(0, (max(0, image_height) + tile_height - 1) // tile_height)


def _record_viewer_request_timing(evaluator: GraphEvaluator, payload: FrameRequest, timing: dict[str, Any]) -> None:
    evaluator.record_request_timing(
        {
            "type": "viewer_frame",
            "request_id": _ensure_frame_request_id(payload),
            "node_id": payload.node_id,
            "frame": payload.frame,
            "viewer_input": payload.viewer_input,
            "compare_input": payload.compare_input,
            "compare_mode": payload.compare_mode,
            "channel": payload.channel or "rgba",
            "display": payload.display,
            "view": payload.view,
            "gain": payload.gain,
            "saturation": payload.saturation,
            "fstop": payload.fstop,
            "render_scale": payload.render_scale,
            "mipmap_level": payload.mipmap_level,
            "channels": payload.channels or [payload.channel or "rgba"],
            "layers": payload.layers,
            "priority": payload.priority,
            "cache_policy": payload.cache_policy,
            "storage": payload.storage,
            **{key: round(value, 2) if isinstance(value, float) else value for key, value in timing.items()},
        }
    )


async def _close_websocket_quietly(websocket: WebSocket, code: int = 1000) -> None:
    try:
        await websocket.close(code=code)
    except Exception:
        return


async def _send_websocket_error_quietly(websocket: WebSocket, detail: str) -> None:
    try:
        await websocket.send_text(json.dumps({"type": "error", "detail": detail}))
    except Exception:
        pass
    await _close_websocket_quietly(websocket, code=1011)


async def _send_websocket_cancelled_quietly(websocket: WebSocket, request_id: str) -> None:
    try:
        await websocket.send_text(json.dumps({"type": "viewer_request_cancelled", "request_id": request_id}))
    except Exception:
        pass
    await _close_websocket_quietly(websocket)


def _viewer_eval_source(
    graph,
    evaluator: GraphEvaluator,
    node_id: str,
    frame: int,
    viewer_input: str | None,
    channel: str | None = None,
) -> tuple[str, str | None]:
    node = graph.nodes.get(node_id)
    if node is None:
        raise KeyError(f"Graph does not contain node '{node_id}'.")
    if node.type.lower() != "viewer" or viewer_input is None:
        demand = evaluator.channel_demand_for(graph, node_id, channel)
        with evaluator.channel_demand_scope(demand):
            return node_id, evaluator.output_signature(graph, node_id, frame)
    edges = graph.incoming_edges(node_id, str(viewer_input))
    if not edges:
        raise ValueError(f"Viewer input {viewer_input} is not connected.")
    source_node = edges[0].source_node
    if source_node not in graph.nodes:
        raise KeyError(f"Viewer input {viewer_input} references missing node '{source_node}'.")
    demand = evaluator.channel_demand_for(graph, source_node, channel)
    with evaluator.channel_demand_scope(demand):
        return source_node, evaluator.node_signature(graph, source_node, frame)


@router.websocket("/ws/viewer/frame")
async def websocket_viewer_frame(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            payload = FrameRequest.model_validate_json(await websocket.receive_text())
            request_id = _ensure_frame_request_id(payload)
            scheduler = get_render_scheduler_from_state(websocket.app.state)
            request_scope = _viewer_request_scope(payload)
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
            active_graph = get_active_script(project).graph
            render_started = time.perf_counter()
            png_bytes = await asyncio.to_thread(_render_viewer_png, project, active_graph, evaluator, payload)
            render_ms = (time.perf_counter() - render_started) * 1000.0
            if not scheduler.is_current(request_scope, request_id):
                await _send_websocket_cancelled_quietly(websocket, request_id)
                return
            send_started = time.perf_counter()
            await websocket.send_bytes(png_bytes)
            send_ms = (time.perf_counter() - send_started) * 1000.0
            _record_viewer_request_timing(
                evaluator,
                payload,
                {
                    "transport": "websocket",
                    "total_ms": render_ms + send_ms,
                    "backend_render_ms": render_ms,
                    "send_ms": send_ms,
                    "bytes": len(png_bytes),
                },
            )
            _schedule_viewer_warm(project, active_graph, evaluator, payload)
            scheduler.complete(request_id)
            await _close_websocket_quietly(websocket)
            return
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await _send_websocket_error_quietly(websocket, str(exc))


@router.websocket("/ws/viewer/float")
async def websocket_viewer_float(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            payload = FrameRequest.model_validate_json(await websocket.receive_text())
            request_id = _ensure_frame_request_id(payload)
            scheduler = get_render_scheduler_from_state(websocket.app.state)
            request_scope = _viewer_request_scope(payload)
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
            active_graph = get_active_script(project).graph
            render_started = time.perf_counter()
            if payload.stream_tiles:
                header, rgba, tiles, native_eval_node_id = await asyncio.to_thread(
                    _float_preview_tile_source,
                    project,
                    active_graph,
                    evaluator,
                    payload,
                )
            else:
                header, data = await asyncio.to_thread(_float_preview_payload, project, active_graph, evaluator, payload)
                native_eval_node_id = None
            render_ms = (time.perf_counter() - render_started) * 1000.0
            send_started = time.perf_counter()
            ws_write_ms = 0.0
            tile_encode_ms = float(header.get("tile_encode_ms") or 0.0)
            tile_render_ms = 0.0
            if not scheduler.is_current(request_scope, request_id):
                await _send_websocket_cancelled_quietly(websocket, request_id)
                return
            header_write_started = time.perf_counter()
            await websocket.send_text(json.dumps(header))
            ws_write_ms += (time.perf_counter() - header_write_started) * 1000.0
            sent_bytes = 0
            if payload.stream_tiles:
                tile_index = 0
                if bool(header.get("tile_native")) and native_eval_node_id is not None:
                    async for tile_header, tile_data, render_tile_ms, encode_tile_ms in _parallel_native_float_tiles(
                        active_graph,
                        evaluator,
                        payload,
                        native_eval_node_id,
                        tiles,
                        project.settings.tile_workers,
                    ):
                        if not scheduler.is_current(request_scope, request_id):
                            await _send_websocket_cancelled_quietly(websocket, request_id)
                            return
                        tile_render_ms += render_tile_ms
                        tile_encode_ms += encode_tile_ms
                        tile_write_started = time.perf_counter()
                        await websocket.send_text(json.dumps({**tile_header, "index": tile_index}))
                        await websocket.send_bytes(tile_data)
                        ws_write_ms += (time.perf_counter() - tile_write_started) * 1000.0
                        sent_bytes += len(tile_data)
                        tile_index += 1
                else:
                    if rgba is None:
                        raise ValueError("Tile stream fallback has no source pixels.")
                    precision = _float_precision(payload)
                    for x, y, width, current_height in tiles:
                        if not scheduler.is_current(request_scope, request_id):
                            await _send_websocket_cancelled_quietly(websocket, request_id)
                            return
                        encode_started = time.perf_counter()
                        tile_data = await asyncio.to_thread(_encode_float_tile, rgba, x, y, width, current_height, precision)
                        tile_encode_ms += (time.perf_counter() - encode_started) * 1000.0
                        tile_header = {
                            "type": "viewer_float_tile",
                            "index": tile_index,
                            "x": x,
                            "y": y,
                            "width": width,
                            "height": current_height,
                            "byte_length": len(tile_data),
                        }
                        tile_write_started = time.perf_counter()
                        await websocket.send_text(json.dumps(tile_header))
                        await websocket.send_bytes(tile_data)
                        ws_write_ms += (time.perf_counter() - tile_write_started) * 1000.0
                        sent_bytes += len(tile_data)
                        tile_index += 1
                done_write_started = time.perf_counter()
                await websocket.send_text(json.dumps({"type": "viewer_float_tiles_done", "tiles": tile_index}))
                ws_write_ms += (time.perf_counter() - done_write_started) * 1000.0
            else:
                if not scheduler.is_current(request_scope, request_id):
                    await _send_websocket_cancelled_quietly(websocket, request_id)
                    return
                data_write_started = time.perf_counter()
                await websocket.send_bytes(data)
                ws_write_ms += (time.perf_counter() - data_write_started) * 1000.0
                sent_bytes = len(data)
            send_ms = (time.perf_counter() - send_started) * 1000.0
            _record_viewer_request_timing(
                evaluator,
                payload,
                {
                    "transport": f"websocket-{header['dtype']}{'-tiles' if payload.stream_tiles else ''}",
                    "total_ms": render_ms + tile_render_ms + tile_encode_ms + ws_write_ms,
                    "backend_render_ms": render_ms + tile_render_ms,
                    "send_ms": tile_encode_ms + ws_write_ms,
                    "float_cache_lookup_ms": header.get("float_cache_lookup_ms", 0.0),
                    "node_eval_ms": float(header.get("node_eval_ms", 0.0) or 0.0) + tile_render_ms,
                    "resize_ms": header.get("resize_ms", 0.0),
                    "tile_encode_ms": tile_encode_ms,
                    "ws_write_ms": ws_write_ms,
                    "tile_native": bool(header.get("tile_native")),
                    "tile_render_ms": tile_render_ms,
                    "bytes": sent_bytes,
                    "tile_count": header.get("tile_count", 0),
                    "tile_count_total": header.get("tile_count_total", header.get("tile_count", 0)),
                    "tile_height": header.get("tile_height"),
                    "lane_count": header.get("tile_lanes", 1),
                    "tile_lane": header.get("tile_lane"),
                    "transfer_mode": header.get("transfer_mode"),
                    "float_cache_hit": header.get("cache_hit", False),
                },
            )
            scheduler.complete(request_id)
            await _close_websocket_quietly(websocket)
            return
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await _send_websocket_error_quietly(websocket, str(exc))


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
