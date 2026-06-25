"""Viewer request and warm-up helpers for the backend API layer.

This module owns the non-transport orchestration used by viewer HTTP and
websocket routes: request-id handling, preview request construction, viewer
input resolution, and background warm/preload planning. Keeping those rules out
of the route module makes the API layer easier to scan and unit test.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from opencomp.api.viewer_context import resolved_viewer_display_view, viewer_proxy_size
from opencomp.core.evaluator import GraphEvaluator, _viewer_input_edges
from opencomp.core.models import FrameRequest, Project, ReadWarmRequest, ViewerWarmRequest
from opencomp.core.preview_renderer import (
    PreviewRequest,
    ViewerProcess,
    get_float_preview,
    render_difference_preview,
    render_standard_preview,
    warm_viewer_input_previews,
)
from opencomp.core.render_contract import RenderROI, RenderRequest


def preview_dimensions(project: Project) -> tuple[int | None, int | None]:
    """Return active preview proxy bounds for a project."""

    return viewer_proxy_size(project.settings)


def viewer_process(payload: FrameRequest) -> ViewerProcess:
    """Convert viewer-process parameters from a transport payload into a typed object."""

    return ViewerProcess(gain=payload.gain, saturation=payload.saturation, fstop=payload.fstop)


def ensure_frame_request_id(payload: FrameRequest) -> str:
    """Ensure a frame request has a stable request id and return it."""

    if not payload.request_id:
        payload.request_id = uuid.uuid4().hex
    return payload.request_id


def viewer_request_scope(payload: FrameRequest) -> str:
    """Return the scheduler scope used to deduplicate viewer requests."""

    return ":".join(
        [
            payload.node_id,
            str(payload.viewer_input or ""),
            payload.channel or "rgba",
            payload.compare_mode,
        ]
    )


def render_request_from_frame(
    payload: FrameRequest,
    *,
    node_id: str,
    frame: int | None = None,
    storage: str | None = None,
) -> RenderRequest:
    """Translate a viewer frame payload into the shared render-request model."""

    request_id = ensure_frame_request_id(payload)
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


def viewer_eval_source(
    graph,
    evaluator: GraphEvaluator,
    node_id: str,
    frame: int,
    viewer_input: str | None,
    channel: str | None = None,
) -> tuple[str, str | None]:
    """Resolve the graph node and signature that should drive viewer evaluation."""

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


def build_preview_request(
    project: Project,
    graph,
    evaluator: GraphEvaluator,
    payload: FrameRequest,
    viewer_input: str | None,
) -> PreviewRequest:
    """Build the preview-render request used by PNG and float viewer paths."""

    max_width, max_height = preview_dimensions(project)
    resolved_display, resolved_view = resolved_viewer_display_view(project.settings, payload.display, payload.view)
    eval_node_id, output_signature = viewer_eval_source(
        graph,
        evaluator,
        payload.node_id,
        payload.frame,
        viewer_input,
        payload.channel or "rgba",
    )
    evaluator.execution_plan_for(
        graph,
        render_request_from_frame(payload, node_id=payload.node_id, storage="frontend"),
        eval_node_id=eval_node_id,
        output_signature=output_signature,
    )
    return PreviewRequest(
        cache_node_id=payload.node_id,
        eval_node_id=eval_node_id,
        frame=payload.frame,
        display=resolved_display,
        view=resolved_view,
        channel=payload.channel or "rgba",
        max_width=max_width,
        max_height=max_height,
        ocio_config=project.settings.ocio_config,
        output_signature=output_signature,
        viewer_process=viewer_process(payload),
        roi=payload.roi,
    )


def render_viewer_png(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest) -> bytes:
    """Render one viewer PNG payload, including difference-mode support."""

    preview_request = build_preview_request(project, graph, evaluator, payload, payload.viewer_input)
    if payload.compare_mode == "difference":
        if payload.compare_input is None:
            raise ValueError("Difference mode requires compare_input.")
        return render_difference_preview(
            evaluator,
            graph,
            preview_request,
            build_preview_request(project, graph, evaluator, payload, payload.compare_input),
        )
    return render_standard_preview(evaluator, graph, preview_request)


def schedule_viewer_warm(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest) -> None:
    """Queue background preview warming for viewer inputs related to one request."""

    node = graph.nodes.get(payload.node_id)
    if node is None or node.type.lower() != "viewer":
        return
    max_width, max_height = preview_dimensions(project)
    input_sockets = viewer_warm_inputs(graph, payload)
    if not input_sockets:
        return
    resolved_display, resolved_view = resolved_viewer_display_view(project.settings, payload.display, payload.view)
    warm_graph = graph.model_copy(deep=True)
    asyncio.create_task(
        asyncio.to_thread(
            warm_viewer_input_previews_scoped,
            evaluator,
            warm_graph,
            payload.node_id,
            payload.frame,
            resolved_display,
            resolved_view,
            payload.channel or "rgba",
            max_width,
            max_height,
            project.settings.ocio_config,
            viewer_process(payload),
            input_sockets,
        )
    )


def warm_viewer_input_previews_scoped(
    evaluator: GraphEvaluator,
    graph,
    viewer_id: str,
    frame: int,
    display: str | None,
    view: str | None,
    channel: str | None,
    max_width: int | None,
    max_height: int | None,
    ocio_config: str | None,
    preview_process,
    input_sockets: set[str] | None,
) -> None:
    """Run preview warming inside a background evaluator activity scope."""

    with evaluator.activity_scope("background"):
        warm_viewer_input_previews(
            evaluator,
            graph,
            viewer_id,
            frame,
            display,
            view,
            channel,
            max_width,
            max_height,
            ocio_config,
            preview_process,
            input_sockets,
        )


def warm_viewer_float_frames(
    project: Project,
    graph,
    evaluator: GraphEvaluator,
    payload: ViewerWarmRequest,
    frames: list[int],
) -> None:
    """Warm float preview entries for a list of frames in background mode."""

    with evaluator.activity_scope("background"):
        resolved_display, resolved_view = resolved_viewer_display_view(project.settings, payload.display, payload.view)
        for frame in frames:
            request = FrameRequest(
                node_id=payload.node_id,
                frame=frame,
                display=resolved_display,
                view=resolved_view,
                channel=payload.channel or "rgba",
                viewer_input=payload.viewer_input,
                precision="float16",
                stream_tiles=False,
            )
            try:
                float_preview_entry(project, graph, evaluator, request)
            except Exception as exc:
                evaluator.record_phase_timing(
                    payload.node_id,
                    "viewer.warm_failed",
                    0.0,
                    {"frame": frame, "error": str(exc)},
                )


def warm_read_frames(
    project: Project,
    graph,
    evaluator: GraphEvaluator,
    payload: ReadWarmRequest,
    frames: list[int],
) -> None:
    """Warm upstream read-node caches for one viewer or graph branch."""

    target_node_id = read_preload_target_node(graph, payload.node_id, payload.viewer_input)
    if target_node_id is None:
        return
    read_node_ids = upstream_read_nodes(graph, target_node_id)
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
        with evaluator.activity_scope("background"):
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


def read_preload_node_ids(graph, node_id: str, viewer_input: str | None = None) -> list[str]:
    """Return read nodes that would be warmed for one preload request."""

    target = read_preload_target_node(graph, node_id, viewer_input)
    if target is None:
        return []
    return upstream_read_nodes(graph, target)


def read_preload_target_node(graph, node_id: str, viewer_input: str | None = None) -> str | None:
    """Resolve the branch root used when warming upstream read nodes."""

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


def upstream_read_nodes(graph, node_id: str) -> list[str]:
    """Return sorted upstream read nodes reachable from one graph node."""

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


def viewer_warm_inputs(graph, payload: FrameRequest) -> set[str]:
    """Return viewer input sockets that should be warmed for one request."""

    if payload.compare_mode == "difference":
        return {str(value) for value in (payload.viewer_input, payload.compare_input) if value is not None}
    if payload.viewer_input is not None:
        return {str(payload.viewer_input)}
    node = graph.nodes.get(payload.node_id)
    if node is not None and node.type.lower() == "viewer":
        return {str(node.params.get("active_input", "0"))}
    return set()


def float_preview_entry(project: Project, graph, evaluator: GraphEvaluator, payload: FrameRequest):
    """Return the cached-or-rendered float preview entry for a viewer request."""

    if payload.compare_mode != "none":
        raise ValueError("Float streaming supports one viewer input per request. Request A and B separately for GPU wipe.")
    preview_request = build_preview_request(project, graph, evaluator, payload, payload.viewer_input)
    return get_float_preview(evaluator, graph, preview_request)
