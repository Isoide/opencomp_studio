"""Node metadata, bindings, and Cryptomatte API routes.

This router keeps node-introspection and Cryptomatte-specific endpoints out of
the transport-heavy viewer route module. It owns request validation, node/image
evaluation for metadata reads, and lightweight response shaping around the
evaluator and Cryptomatte helpers.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response

from opencomp.api.context import ensure_script_tabs, get_active_graph, get_evaluator, get_project, json_safe, resolved_frame_number
from opencomp.api.viewer_context import viewer_context_payload, viewer_proxy_limits
from opencomp.core.evaluator import GraphCycleError, UnknownNodeTypeError
from opencomp.core.models import CryptomatteMatteRequest, CryptomattePickRequest, NodeCatalogItem
from opencomp.core.preview_renderer import render_cryptomatte_preview
from opencomp.io.cryptomatte import cryptomatte_layer_payload, cryptomatte_layers, pick_cryptomatte_id
from opencomp.nodes import NODE_DEFINITIONS
from opencomp.nodes.base import NodeEvaluationError

router = APIRouter()


@router.get("/api/nodes/catalog", response_model=list[NodeCatalogItem])
async def node_catalog() -> list[NodeCatalogItem]:
    """Return the available node catalog for the frontend palette."""

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


@router.get("/api/nodes/{node_id}/metadata")
async def node_metadata(request: Request, node_id: str, frame: int | None = None):
    """Return rendered metadata and resolved parameter state for one node/frame."""

    project, active_graph, evaluator, frame_number = _resolved_node_context(request, node_id, frame)
    image = await _evaluate_node_image(evaluator, active_graph, node_id, frame_number)
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
        "viewer_context": viewer_context_payload(project.settings, image),
    }


@router.get("/api/nodes/{node_id}/bindings")
async def node_bindings(request: Request, node_id: str, frame: int | None = None):
    """Return expression-readable outputs and current expression errors for a node."""

    _, active_graph, evaluator, frame_number = _resolved_node_context(request, node_id, frame)
    return {
        "node_id": node_id,
        "frame": frame_number,
        "bindable_outputs": json_safe(evaluator.bindable_outputs(active_graph, node_id, frame_number)),
        "expression_errors": json_safe(evaluator.expression_errors(active_graph, node_id, frame_number)),
    }


@router.get("/api/nodes/{node_id}/cryptomatte")
async def node_cryptomatte(request: Request, node_id: str, frame: int | None = None):
    """Return discovered Cryptomatte layers for one rendered node/frame."""

    _, active_graph, evaluator, frame_number = _resolved_node_context(request, node_id, frame)
    image = await _evaluate_node_image(evaluator, active_graph, node_id, frame_number)
    return {
        "node_id": node_id,
        "frame": frame_number,
        "layers": [cryptomatte_layer_payload(layer) for layer in cryptomatte_layers(image)],
    }


@router.post("/api/cryptomatte/pick")
async def cryptomatte_pick(request: Request, payload: CryptomattePickRequest):
    """Pick a Cryptomatte id from one rendered pixel."""

    _, active_graph, evaluator, _ = _resolved_node_context(request, payload.node_id, payload.frame)
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
    """Render a Cryptomatte matte preview as PNG bytes."""

    project, active_graph, evaluator, _ = _resolved_node_context(request, payload.node_id, payload.frame)
    max_width, max_height = viewer_proxy_limits(project.settings, payload.max_width, payload.max_height)
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


def _resolved_node_context(request: Request, node_id: str, frame: int | None):
    """Return the shared project/graph/evaluator context for node-based routes."""

    project = get_project(request)
    ensure_script_tabs(project)
    active_graph = get_active_graph(project)
    if node_id not in active_graph.nodes:
        raise HTTPException(status_code=404, detail=f"Unknown node: {node_id}")
    evaluator = get_evaluator(request, project)
    return project, active_graph, evaluator, resolved_frame_number(project, frame)


async def _evaluate_node_image(evaluator, active_graph, node_id: str, frame_number: int):
    """Evaluate a node image and convert common evaluator errors into HTTP 400s."""

    try:
        return await asyncio.to_thread(evaluator.evaluate_node, active_graph, node_id, frame_number)
    except (GraphCycleError, UnknownNodeTypeError, NodeEvaluationError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
