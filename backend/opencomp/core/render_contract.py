from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from opencomp.core.models import ProjectGraph, TileWindow


RenderStorage = Literal["ram", "gpu", "frontend", "disk"]
RenderPriority = Literal["interactive", "playback", "background", "render"]
RenderCachePolicy = Literal["read-through", "refresh", "bypass", "write-through"]
RenderPrecision = Literal["float32", "float16", "rgb10a2", "uint8"]


class RenderROI(BaseModel):
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    def to_tile_window(self) -> TileWindow:
        return TileWindow(
            x=int(self.x),
            y=int(self.y),
            width=max(0, int(self.width)),
            height=max(0, int(self.height)),
        )


class RenderRequest(BaseModel):
    node_id: str
    frame: int = 1001
    view: str | None = None
    roi: RenderROI | None = None
    render_scale: float = 1.0
    mipmap_level: int = 0
    channels: list[str] = Field(default_factory=lambda: ["rgba"])
    layers: list[str] = Field(default_factory=list)
    precision: RenderPrecision = "float32"
    storage: RenderStorage = "ram"
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    priority: RenderPriority = "interactive"
    cache_policy: RenderCachePolicy = "read-through"

    @property
    def channel_key(self) -> str:
        return ",".join(sorted(channel.strip().lower() for channel in self.channels if channel.strip())) or "rgba"

    @property
    def layer_key(self) -> str:
        return ",".join(sorted(layer.strip().lower() for layer in self.layers if layer.strip()))

    @property
    def normalized_scale(self) -> float:
        return max(0.0001, float(self.render_scale or 1.0))

    def plan_identity_payload(self) -> dict[str, object]:
        roi = self.roi.model_dump() if self.roi is not None else None
        return {
            "node_id": self.node_id,
            "frame": self.frame,
            "view": self.view or "",
            "roi": roi,
            "render_scale": round(self.normalized_scale, 8),
            "mipmap_level": max(0, int(self.mipmap_level or 0)),
            "channels": self.channel_key,
            "layers": self.layer_key,
            "precision": self.precision,
            "storage": self.storage,
            "cache_policy": self.cache_policy,
        }


@dataclass(frozen=True, slots=True)
class RenderTile:
    node_id: str
    frame: int
    window: TileWindow
    precision: str
    colorspace: str
    format_bbox: dict[str, int]
    data_window: dict[str, int]
    channels: tuple[str, ...] = ("rgba",)
    layers: tuple[str, ...] = ()
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class ImagePlaneTile:
    node_id: str
    frame: int
    layer: str
    channel: str
    window: TileWindow
    precision: str
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionPlanNode:
    node_id: str
    node_type: str
    input_nodes: tuple[str, ...]
    disabled: bool
    bypass_socket: str | None
    tile_native: bool


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    request: RenderRequest
    graph_hash: str
    output_signature: str | None
    eval_node_id: str
    upstream_nodes: tuple[str, ...]
    fallback_nodes: tuple[str, ...]
    tile_native: bool
    nodes: tuple[ExecutionPlanNode, ...]
    cache_key: str
    build_ms: float
    cache_hit: bool = False

    def as_metrics(self) -> dict[str, object]:
        return {
            "request_id": self.request.request_id,
            "eval_node_id": self.eval_node_id,
            "cache_key": self.cache_key,
            "cache_hit": self.cache_hit,
            "build_ms": round(self.build_ms, 2),
            "upstream_count": len(self.upstream_nodes),
            "fallback_count": len(self.fallback_nodes),
            "tile_native": self.tile_native,
            "channel_key": self.request.channel_key,
            "precision": self.request.precision,
            "priority": self.request.priority,
            "storage": self.request.storage,
        }


def graph_hash(graph: ProjectGraph) -> str:
    payload = graph.model_dump(mode="json")
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def execution_plan_cache_key(graph: ProjectGraph, request: RenderRequest) -> str:
    payload = {"graph": graph_hash(graph), "request": request.plan_identity_payload()}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def build_execution_plan(
    graph: ProjectGraph,
    request: RenderRequest,
    *,
    output_signature: str | None,
    eval_node_id: str | None = None,
    tile_native_types: set[str] | None = None,
) -> ExecutionPlan:
    started = time.perf_counter()
    resolved_eval_node = eval_node_id or request.node_id
    native_types = tile_native_types or set()
    graph_digest = graph_hash(graph)
    plan_nodes: list[ExecutionPlanNode] = []
    upstream: list[str] = []
    fallback: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node_id: str) -> bool:
        if node_id in visited:
            return all(item.node_id != node_id or item.tile_native for item in plan_nodes)
        if node_id in visiting:
            return False
        node = graph.nodes.get(node_id)
        if node is None:
            return False
        visiting.add(node_id)
        disabled, bypass_socket = _disabled_and_bypass_socket(graph, node_id)
        input_edges = graph.incoming_edges(node_id)
        if node.type.lower() == "viewer":
            input_edges = _viewer_edges_for_plan(graph, node_id)
        if disabled and bypass_socket is not None:
            input_edges = [edge for edge in input_edges if edge.target_socket == bypass_socket]
        input_nodes = tuple(edge.source_node for edge in input_edges if edge.source_node in graph.nodes)
        child_tile_native = True
        for child_id in input_nodes:
            child_tile_native = walk(child_id) and child_tile_native
        tile_native = node.type.lower() in native_types and child_tile_native
        if not tile_native:
            fallback.append(node_id)
        upstream.append(node_id)
        plan_nodes.append(
            ExecutionPlanNode(
                node_id=node_id,
                node_type=node.type,
                input_nodes=input_nodes,
                disabled=disabled,
                bypass_socket=bypass_socket,
                tile_native=tile_native,
            )
        )
        visiting.remove(node_id)
        visited.add(node_id)
        return tile_native

    plan_tile_native = walk(resolved_eval_node)
    payload = {
        "graph_hash": graph_digest,
        "request": request.plan_identity_payload(),
        "output_signature": output_signature or "",
        "eval_node_id": resolved_eval_node,
    }
    cache_key = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return ExecutionPlan(
        request=request,
        graph_hash=graph_digest,
        output_signature=output_signature,
        eval_node_id=resolved_eval_node,
        upstream_nodes=tuple(upstream),
        fallback_nodes=tuple(fallback),
        tile_native=plan_tile_native,
        nodes=tuple(plan_nodes),
        cache_key=cache_key,
        build_ms=(time.perf_counter() - started) * 1000.0,
    )


def _disabled_and_bypass_socket(graph: ProjectGraph, node_id: str) -> tuple[bool, str | None]:
    node = graph.nodes[node_id]
    value = node.params.get("disabled", node.params.get("disable", False))
    if isinstance(value, bool):
        disabled = value
    elif isinstance(value, (int, float)):
        disabled = value != 0
    elif isinstance(value, str):
        disabled = value.strip().lower() in {"1", "true", "yes", "on", "disabled", "disable"}
    else:
        disabled = bool(value)
    if not disabled:
        return False, None
    edges = graph.incoming_edges(node_id)
    if not edges:
        return True, None
    ordered = sorted(
        edges,
        key=lambda edge: (_socket_priority(edge.target_socket, node.type), str(edge.target_socket), edge.source_node),
    )
    return True, ordered[0].target_socket


def _viewer_edges_for_plan(graph: ProjectGraph, node_id: str):
    node = graph.nodes[node_id]
    active_socket = str(node.params.get("active_input", "0"))
    active_edges = graph.incoming_edges(node_id, active_socket)
    if active_edges:
        return active_edges
    legacy_edges = graph.incoming_edges(node_id, "in")
    if legacy_edges:
        return legacy_edges
    return []


def _socket_priority(socket: str, node_type: str | None = None) -> int:
    priority = ("b", "bg", "a", "in", "input", "0", "mask") if str(node_type or "").lower() == "merge" else ("a", "in", "input", "0", "b", "mask")
    normalized = str(socket).strip().lower()
    if normalized in priority:
        return priority.index(normalized)
    return len(priority)
