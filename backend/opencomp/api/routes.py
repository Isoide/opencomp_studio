from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.core.defaults import create_default_project
from opencomp.core.evaluator import GraphCycleError, GraphEvaluator, UnknownNodeTypeError
from opencomp.core.models import (
    CreateScriptTabRequest,
    CryptomatteMatteRequest,
    CryptomattePickRequest,
    FrameRequest,
    GraphUpdate,
    HealthResponse,
    NodeCatalogItem,
    Project,
    ProjectPreferencesUpdate,
    ProjectSettingsUpdate,
    PythonScriptRequest,
    PythonScriptResponse,
    RenameScriptTabRequest,
    SaveProjectRequest,
    ScriptTab,
    SetActiveScriptTabRequest,
)
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
    if not project.script_tabs:
        project.script_tabs.append(ScriptTab(id="main", name="Comp 1", graph=project.graph, kind="comp"))
        project.active_script_id = "main"
    if not any(tab.id == project.active_script_id for tab in project.script_tabs):
        project.active_script_id = project.script_tabs[0].id
    project.graph = get_active_script(project).graph


def get_active_script(project: Project) -> ScriptTab:
    if not project.script_tabs:
        project.script_tabs.append(ScriptTab(id="main", name="Comp 1", graph=project.graph, kind="comp"))
        project.active_script_id = "main"
    for tab in project.script_tabs:
        if tab.id == project.active_script_id:
            return tab
    return project.script_tabs[0]


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


def _viewer_preview_cache_bytes(max_cache_bytes: int) -> int:
    if max_cache_bytes <= 0:
        return 0
    return max(64 * 1024 * 1024, min(512 * 1024 * 1024, max_cache_bytes // 2))


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


@router.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.post("/api/projects/new", response_model=Project)
async def new_project(request: Request) -> Project:
    project = create_default_project()
    ensure_script_tabs(project)
    request.app.state.project = project
    max_cache_bytes = max(0, int(project.preferences.cache_memory_limit_mb)) * 1024 * 1024
    request.app.state.evaluator = GraphEvaluator(
        settings=project.settings,
        max_cache_bytes=max_cache_bytes,
        max_preview_cache_bytes=_viewer_preview_cache_bytes(max_cache_bytes),
        max_float_preview_cache_bytes=_viewer_preview_cache_bytes(max_cache_bytes),
    )
    request.app.state.evaluator_settings_key = project.settings.model_dump_json()
    return project


@router.post("/api/projects/save", response_model=Project)
async def save_project(request: Request, payload: SaveProjectRequest) -> Project:
    project = payload.project or get_project(request)
    ensure_script_tabs(project)
    request.app.state.project = project
    if payload.path:
        path = Path(payload.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(project.model_dump_json(indent=2), encoding="utf-8")
    return project


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


@router.get("/api/render/{job_id}")
async def get_render(job_id: str):
    return {"job_id": job_id, "status": "unknown", "message": "In-memory render jobs are not implemented yet."}


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
    entry,
    cache_hit: bool,
    evaluate_ms: float,
    resize_ms: float,
    *,
    stream_tiles: bool,
    tile_height: int | None = None,
) -> dict[str, Any]:
    rgba = np.asarray(entry.rgba)
    precision = _float_precision(payload)
    height = int(rgba.shape[0])
    width = int(rgba.shape[1])
    resolved_tile_height = _resolved_tile_height(payload, height) if stream_tiles else None
    header = {
        "type": "viewer_float_frame",
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
        "byte_length": width * height * 4 * _precision_bytes(precision),
        "cache_hit": cache_hit,
        "evaluate_ms": round(evaluate_ms, 2),
        "resize_ms": round(resize_ms, 2),
        "tile_stream": stream_tiles,
        "tile_height": resolved_tile_height,
        "tile_count": _tile_count(height, resolved_tile_height) if resolved_tile_height else 0,
    }
    if tile_height is not None:
        header["requested_tile_height"] = tile_height
    return header


def _float_preview_payload(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest) -> tuple[dict[str, Any], bytes]:
    entry, cache_hit, evaluate_ms, resize_ms = _float_preview_entry(project, graph, evaluator, payload)
    header = _float_preview_header(payload, entry, cache_hit, evaluate_ms, resize_ms, stream_tiles=False)
    data = _encode_float_rgba(entry.rgba, _float_precision(payload))
    return header, data


def _float_preview_tile_source(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest):
    entry, cache_hit, evaluate_ms, resize_ms = _float_preview_entry(project, graph, evaluator, payload)
    tile_height = _resolved_tile_height(payload, int(entry.rgba.shape[0]))
    header = _float_preview_header(
        payload,
        entry,
        cache_hit,
        evaluate_ms,
        resize_ms,
        stream_tiles=True,
        tile_height=tile_height,
    )
    return header, entry.rgba


def _encode_float_rgba(rgba: np.ndarray, precision: str) -> bytes:
    if precision == "float16":
        return np.ascontiguousarray(np.asarray(rgba, dtype=np.float16)).tobytes(order="C")
    return np.ascontiguousarray(np.asarray(rgba, dtype=np.float32)).tobytes(order="C")


def _encode_float_tile(rgba: np.ndarray, y: int, height: int, precision: str) -> bytes:
    return _encode_float_rgba(rgba[y : y + height], precision)


def _float_precision(payload: FrameRequest) -> str:
    return "float16" if payload.precision == "float16" else "float32"


def _precision_bytes(precision: str) -> int:
    return 2 if precision == "float16" else 4


def _resolved_tile_height(payload: FrameRequest, image_height: int) -> int:
    if payload.tile_height is not None:
        return max(1, min(int(payload.tile_height), max(1, image_height)))
    return max(1, min(128, max(1, image_height)))


def _tile_count(image_height: int, tile_height: int | None) -> int:
    if not tile_height:
        return 0
    return max(0, (max(0, image_height) + tile_height - 1) // tile_height)


def _record_viewer_request_timing(evaluator: GraphEvaluator, payload: FrameRequest, timing: dict[str, Any]) -> None:
    evaluator.record_request_timing(
        {
            "type": "viewer_frame",
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
            **{key: round(value, 2) if isinstance(value, float) else value for key, value in timing.items()},
        }
    )


def _viewer_eval_source(
    graph,
    evaluator: GraphEvaluator,
    node_id: str,
    frame: int,
    viewer_input: str | None,
) -> tuple[str, str | None]:
    node = graph.nodes.get(node_id)
    if node is None:
        raise KeyError(f"Graph does not contain node '{node_id}'.")
    if node.type.lower() != "viewer" or viewer_input is None:
        return node_id, None
    edges = graph.incoming_edges(node_id, str(viewer_input))
    if not edges:
        raise ValueError(f"Viewer input {viewer_input} is not connected.")
    source_node = edges[0].source_node
    if source_node not in graph.nodes:
        raise KeyError(f"Viewer input {viewer_input} references missing node '{source_node}'.")
    return source_node, evaluator.node_signature(graph, source_node, frame)


@router.websocket("/ws/viewer/frame")
async def websocket_viewer_frame(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            payload = FrameRequest.model_validate_json(await websocket.receive_text())
            project = get_project_from_state(websocket.app.state)
            ensure_script_tabs(project)
            evaluator = get_evaluator_from_state(websocket.app.state, project)
            active_graph = get_active_script(project).graph
            render_started = time.perf_counter()
            png_bytes = await asyncio.to_thread(_render_viewer_png, project, active_graph, evaluator, payload)
            render_ms = (time.perf_counter() - render_started) * 1000.0
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
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_text(json.dumps({"type": "error", "detail": str(exc)}))
        await websocket.close(code=1011)


@router.websocket("/ws/viewer/float")
async def websocket_viewer_float(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            payload = FrameRequest.model_validate_json(await websocket.receive_text())
            project = get_project_from_state(websocket.app.state)
            ensure_script_tabs(project)
            evaluator = get_evaluator_from_state(websocket.app.state, project)
            active_graph = get_active_script(project).graph
            render_started = time.perf_counter()
            if payload.stream_tiles:
                header, rgba = await asyncio.to_thread(_float_preview_tile_source, project, active_graph, evaluator, payload)
            else:
                header, data = await asyncio.to_thread(_float_preview_payload, project, active_graph, evaluator, payload)
            render_ms = (time.perf_counter() - render_started) * 1000.0
            send_started = time.perf_counter()
            await websocket.send_text(json.dumps(header))
            sent_bytes = 0
            if payload.stream_tiles:
                precision = _float_precision(payload)
                tile_height = max(1, int(header.get("tile_height") or 128))
                height = int(header["height"])
                tile_index = 0
                for y in range(0, height, tile_height):
                    current_height = min(tile_height, height - y)
                    tile_data = await asyncio.to_thread(_encode_float_tile, rgba, y, current_height, precision)
                    tile_header = {
                        "type": "viewer_float_tile",
                        "index": tile_index,
                        "x": 0,
                        "y": y,
                        "width": int(header["width"]),
                        "height": current_height,
                        "byte_length": len(tile_data),
                    }
                    await websocket.send_text(json.dumps(tile_header))
                    await websocket.send_bytes(tile_data)
                    sent_bytes += len(tile_data)
                    tile_index += 1
                await websocket.send_text(json.dumps({"type": "viewer_float_tiles_done", "tiles": tile_index}))
            else:
                await websocket.send_bytes(data)
                sent_bytes = len(data)
            send_ms = (time.perf_counter() - send_started) * 1000.0
            _record_viewer_request_timing(
                evaluator,
                payload,
                {
                    "transport": f"websocket-{header['dtype']}{'-tiles' if payload.stream_tiles else ''}",
                    "total_ms": render_ms + send_ms,
                    "backend_render_ms": render_ms,
                    "send_ms": send_ms,
                    "bytes": sent_bytes,
                    "tile_count": header.get("tile_count", 0),
                    "tile_height": header.get("tile_height"),
                    "float_cache_hit": header.get("cache_hit", False),
                },
            )
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_text(json.dumps({"type": "error", "detail": str(exc)}))
        await websocket.close(code=1011)


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
