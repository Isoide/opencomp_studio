from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Iterator

import numpy as np

from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.core.channel_demand import ChannelDemand, channel_demand_for_graph
from opencomp.core.models import Edge, ImageFrame, Node, ProjectGraph, ProjectSettings, TileWindow
from opencomp.core.render_contract import (
    ExecutionPlan,
    RenderRequest,
    RenderROI,
    build_execution_plan,
    execution_plan_cache_key,
)
from opencomp.io.image_reader import image_source_fingerprint
from opencomp.nodes import NODE_REGISTRY
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError
from opencomp.nodes.read import _mapped_frame, _range_frame, _read_channels

CacheKey = tuple[str, int, str]
PreviewCacheKey = tuple[str, int, str, str, str, str, int | None, int | None, str | None]
FloatPreviewCacheKey = tuple[str, int, str, str, int | None, int | None]
TileCacheKey = tuple[str, int, str, int, int, int, int]
PlanCacheKey = str
SourceCacheKey = str

TILE_FULL_WIDTH = "tile/full_width"
TILE_FULL_HEIGHT = "tile/full_height"
TILE_X = "tile/x"
TILE_Y = "tile/y"
TILE_LOCAL_NODE_TYPES = {
    "addchannels",
    "channelmerge",
    "clamp",
    "colorspace",
    "comparemetadata",
    "constant",
    "copy",
    "copymetadata",
    "crop",
    "exposure",
    "grade",
    "group",
    "invert",
    "merge",
    "modifymetadata",
    "premult",
    "read",
    "reformat",
    "remove",
    "saturation",
    "shuffle",
    "unpremult",
    "viewer",
    "viewmetadata",
}
BYPASS_SOCKET_PRIORITY = ("a", "in", "input", "0", "b", "mask")
MERGE_BYPASS_SOCKET_PRIORITY = ("b", "bg", "a", "in", "input", "0", "mask")


class GraphCycleError(RuntimeError):
    pass


class UnknownNodeTypeError(RuntimeError):
    pass


@dataclass(slots=True)
class CacheEntry:
    image: ImageFrame
    bytes: int
    signature: str


@dataclass(slots=True)
class PreviewCacheEntry:
    data: bytes
    bytes: int


@dataclass(slots=True)
class FloatPreviewCacheEntry:
    rgba: np.ndarray
    apply_ocio: bool
    colorspace: str
    source_width: int
    source_height: int
    pixel_aspect: float
    format_bbox: dict[str, int]
    data_window: dict[str, int]
    bytes: int


@dataclass(slots=True)
class TileCacheEntry:
    image: ImageFrame
    bytes: int
    signature: str


@dataclass(slots=True)
class SourceCacheEntry:
    image: ImageFrame
    bytes: int
    signature: str


@dataclass
class GraphEvaluator:
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    ocio: OCIOColorEngine | None = None
    max_cache_bytes: int = 1024 * 1024 * 1024
    max_preview_cache_bytes: int = 512 * 1024 * 1024
    max_float_preview_cache_bytes: int = 512 * 1024 * 1024
    cache: OrderedDict[CacheKey, CacheEntry] = field(default_factory=OrderedDict)
    preview_cache: OrderedDict[PreviewCacheKey, PreviewCacheEntry] = field(default_factory=OrderedDict)
    float_preview_cache: OrderedDict[FloatPreviewCacheKey, FloatPreviewCacheEntry] = field(default_factory=OrderedDict)
    tile_cache: OrderedDict[TileCacheKey, TileCacheEntry] = field(default_factory=OrderedDict)
    source_cache: OrderedDict[SourceCacheKey, SourceCacheEntry] = field(default_factory=OrderedDict)
    execution_plan_cache: OrderedDict[PlanCacheKey, ExecutionPlan] = field(default_factory=OrderedDict)
    cache_hits: int = 0
    cache_misses: int = 0
    preview_cache_hits: int = 0
    preview_cache_misses: int = 0
    float_preview_cache_hits: int = 0
    float_preview_cache_misses: int = 0
    tile_cache_hits: int = 0
    tile_cache_misses: int = 0
    source_cache_hits: int = 0
    source_cache_misses: int = 0
    execution_plan_hits: int = 0
    execution_plan_misses: int = 0
    cache_memory_bytes: int = 0
    preview_cache_memory_bytes: int = 0
    float_preview_cache_memory_bytes: int = 0
    tile_cache_memory_bytes: int = 0
    source_cache_memory_bytes: int = 0
    max_tile_cache_bytes: int | None = None
    max_source_cache_bytes: int | None = None
    active_nodes: set[str] = field(default_factory=set)
    active_node_counts: dict[str, int] = field(default_factory=dict)
    node_timings: dict[str, dict[str, Any]] = field(default_factory=dict)
    preview_timings: dict[str, dict[str, Any]] = field(default_factory=dict)
    phase_timings: list[dict[str, Any]] = field(default_factory=list)
    request_timings: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _inflight: dict[CacheKey, Future[ImageFrame]] = field(default_factory=dict)
    _tile_inflight: dict[TileCacheKey, Future[ImageFrame]] = field(default_factory=dict)
    _source_inflight: dict[SourceCacheKey, Future[ImageFrame]] = field(default_factory=dict)
    _executor: ThreadPoolExecutor | None = field(default=None, init=False, repr=False)
    _thread_state: threading.local = field(default_factory=threading.local, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.ocio is None:
            self.ocio = OCIOColorEngine(self.settings.ocio_config)
        if self.max_tile_cache_bytes is None:
            self.max_tile_cache_bytes = self.max_cache_bytes
        if self.max_source_cache_bytes is None:
            self.max_source_cache_bytes = self.max_cache_bytes
        self._executor = ThreadPoolExecutor(
            max_workers=self._executor_worker_count(),
            thread_name_prefix="opencomp-eval",
        )

    def evaluate_node(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        requested_channel: str | None = None,
        channel_demand: ChannelDemand | None = None,
    ) -> ImageFrame:
        if node_id not in graph.nodes:
            raise KeyError(f"Graph does not contain node '{node_id}'.")
        demand = self._evaluation_channel_demand(graph, node_id, requested_channel, channel_demand)
        with self.channel_demand_scope(demand):
            output_signature = self.output_signature(graph, node_id, frame)
            self.execution_plan_for(
                graph,
                RenderRequest(
                    node_id=node_id,
                    frame=frame,
                    channels=[requested_channel or "rgba"],
                    precision="float32",
                    storage="ram",
                ),
                eval_node_id=node_id,
                output_signature=output_signature,
            )
            return self._evaluate(graph, node_id, frame, visiting=set())

    def evaluate_node_tile(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        window: TileWindow,
        requested_channel: str | None = None,
        channel_demand: ChannelDemand | None = None,
    ) -> ImageFrame:
        if node_id not in graph.nodes:
            raise KeyError(f"Graph does not contain node '{node_id}'.")
        normalized = TileWindow(
            x=int(window.x),
            y=int(window.y),
            width=max(0, int(window.width)),
            height=max(0, int(window.height)),
        )
        demand = self._evaluation_channel_demand(graph, node_id, requested_channel, channel_demand)
        with self.channel_demand_scope(demand):
            output_signature = self.output_signature(graph, node_id, frame)
            self.execution_plan_for(
                graph,
                RenderRequest(
                    node_id=node_id,
                    frame=frame,
                    roi=RenderROI(x=normalized.x, y=normalized.y, width=normalized.width, height=normalized.height),
                    channels=[requested_channel or "rgba"],
                    precision="float32",
                    storage="ram",
                ),
                eval_node_id=node_id,
                output_signature=output_signature,
            )
            return self._evaluate_tile_cached(graph, node_id, frame, normalized, visiting=set())

    def evaluate_render_request(self, graph: ProjectGraph, request: RenderRequest) -> ImageFrame:
        if request.node_id not in graph.nodes:
            raise KeyError(f"Graph does not contain node '{request.node_id}'.")
        requested_channel = request.channels[0] if request.channels else "rgba"
        demand = self._evaluation_channel_demand(graph, request.node_id, requested_channel, None)
        with self.channel_demand_scope(demand):
            output_signature = self.output_signature(graph, request.node_id, request.frame)
            self.execution_plan_for(graph, request, eval_node_id=request.node_id, output_signature=output_signature)
            if request.roi is not None:
                return self._evaluate_tile_cached(graph, request.node_id, request.frame, request.roi.to_tile_window(), visiting=set())
            return self._evaluate(graph, request.node_id, request.frame, visiting=set())

    def execution_plan_for(
        self,
        graph: ProjectGraph,
        request: RenderRequest,
        *,
        eval_node_id: str | None = None,
        output_signature: str | None = None,
    ) -> ExecutionPlan:
        resolved_eval_node = eval_node_id or request.node_id
        base_key = execution_plan_cache_key(graph, request)
        cache_payload = {
            "base": base_key,
            "eval_node_id": resolved_eval_node,
            "output_signature": output_signature or "",
        }
        cache_key = hashlib.sha1(json.dumps(cache_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        with self._lock:
            cached = self.execution_plan_cache.get(cache_key)
            if cached is not None:
                self.execution_plan_cache.move_to_end(cache_key)
                self.execution_plan_hits += 1
                plan = replace(cached, cache_hit=True, build_ms=0.0)
                self.record_phase_timing(
                    resolved_eval_node,
                    "graph.execution_plan",
                    0.0,
                    plan.as_metrics(),
                )
                return plan
            self.execution_plan_misses += 1

        plan = build_execution_plan(
            graph,
            request,
            output_signature=output_signature,
            eval_node_id=resolved_eval_node,
            tile_native_types=TILE_LOCAL_NODE_TYPES,
        )
        with self._lock:
            self.execution_plan_cache[cache_key] = plan
            while len(self.execution_plan_cache) > 256:
                self.execution_plan_cache.popitem(last=False)
        self.record_phase_timing(
            resolved_eval_node,
            "graph.execution_plan",
            plan.build_ms,
            plan.as_metrics(),
        )
        return plan

    def _evaluate(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        visiting: set[str],
    ) -> ImageFrame:
        if node_id in visiting:
            chain = " -> ".join([*visiting, node_id])
            raise GraphCycleError(f"Node graph contains a cycle: {chain}")

        signature = self.node_signature(graph, node_id, frame)
        cache_key = (node_id, frame, signature)
        cached = self._get_cached(cache_key)
        if cached is not None:
            self._record_node_timing(graph.nodes[node_id].id, graph.nodes[node_id].type, 0.0, cache_hit=True)
            return cached
        owner = False
        wait_future: Future[ImageFrame] | None = None
        with self._lock:
            self.cache_misses += 1
            wait_future = self._inflight.get(cache_key)
            if wait_future is None:
                wait_future = Future()
                self._inflight[cache_key] = wait_future
                owner = True

        if not owner:
            wait_started = time.perf_counter()
            try:
                result = wait_future.result()
            except Exception:
                raise
            finally:
                self.record_phase_timing(
                    node_id,
                    "cache.inflight_wait",
                    (time.perf_counter() - wait_started) * 1000.0,
                    {"frame": frame},
                )
            self._record_node_timing(graph.nodes[node_id].id, graph.nodes[node_id].type, 0.0, cache_hit=True)
            return result

        try:
            result = self._evaluate_uncached(graph, node_id, frame, visiting, signature, cache_key)
        except Exception as exc:
            wait_future.set_exception(exc)
            raise
        else:
            wait_future.set_result(result)
            return result
        finally:
            with self._lock:
                if self._inflight.get(cache_key) is wait_future:
                    self._inflight.pop(cache_key, None)

    def _evaluate_uncached(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        visiting: set[str],
        signature: str,
        cache_key: CacheKey,
    ) -> ImageFrame:

        node = graph.nodes[node_id]
        operation = NODE_REGISTRY.get(node.type.lower())
        if operation is None and not _node_disabled(node):
            raise UnknownNodeTypeError(f"Unsupported node type '{node.type}' on node '{node_id}'.")

        self._begin_node(node.id)
        started = time.perf_counter()
        visiting.add(node_id)
        try:
            input_edges = graph.incoming_edges(node_id)
            if node.type.lower() == "viewer":
                input_edges = _viewer_input_edges(graph, node_id)
            bypass_edge = _preferred_bypass_edge(input_edges, node.type) if _node_disabled(node) else None
            if bypass_edge is not None:
                input_edges = [bypass_edge]
            elif operation is None:
                raise UnknownNodeTypeError(f"Unsupported node type '{node.type}' on node '{node_id}'.")

            inputs = self._evaluate_inputs(graph, frame, input_edges, visiting)
            visiting.remove(node_id)

            context = EvaluationContext(
                frame=frame,
                settings=self.settings,
                ocio=self.ocio,
                requested_channels=self._current_channel_demand(),
                metrics=lambda metric_node_id, phase, duration_ms, details=None: self.record_phase_timing(
                    metric_node_id,
                    phase,
                    duration_ms,
                    details,
                ),
            )
            result = (
                _bypass_frame(node, inputs)
                if bypass_edge is not None
                else self._evaluate_operation(node, operation, inputs, context)
            )
        except NodeEvaluationError:
            raise
        except Exception as exc:
            raise NodeEvaluationError(node.id, str(exc)) from exc
        finally:
            visiting.discard(node_id)
            self._end_node(node.id, node.type, started)
        if node.type.lower() != "viewer":
            self._store_cached(cache_key, result, signature)
        return result

    def _evaluate_operation(
        self,
        node: Node,
        operation: Any,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        if node.type.lower() != "read" or inputs:
            return operation.evaluate(node, inputs, context)
        source_signature = self._read_source_signature(node, context)
        if source_signature is None:
            return operation.evaluate(node, inputs, context)
        return self._evaluate_read_source_cached(node, operation, context, source_signature)

    def _evaluate_read_source_cached(
        self,
        node: Node,
        operation: Any,
        context: EvaluationContext,
        source_signature: str,
    ) -> ImageFrame:
        cached = self._get_cached_source(source_signature)
        if cached is not None:
            self.record_phase_timing(
                node.id,
                "read.source_cache",
                0.0,
                {"frame": context.frame, "hit": True},
            )
            return cached

        owner = False
        wait_future: Future[ImageFrame] | None = None
        with self._lock:
            self.source_cache_misses += 1
            wait_future = self._source_inflight.get(source_signature)
            if wait_future is None:
                wait_future = Future()
                self._source_inflight[source_signature] = wait_future
                owner = True

        if not owner:
            wait_started = time.perf_counter()
            try:
                result = wait_future.result()
            finally:
                self.record_phase_timing(
                    node.id,
                    "read.source_inflight_wait",
                    (time.perf_counter() - wait_started) * 1000.0,
                    {"frame": context.frame},
                )
            return result

        try:
            result = operation.evaluate(node, {}, context)
        except Exception as exc:
            wait_future.set_exception(exc)
            raise
        else:
            self._store_cached_source(source_signature, result, source_signature)
            wait_future.set_result(result)
            return result
        finally:
            with self._lock:
                if self._source_inflight.get(source_signature) is wait_future:
                    self._source_inflight.pop(source_signature, None)

    def _evaluate_inputs(
        self,
        graph: ProjectGraph,
        frame: int,
        input_edges,
        visiting: set[str],
    ) -> dict[str, ImageFrame]:
        edges = list(input_edges)
        worker_count = self._parallel_worker_count(edges, graph)
        if len(edges) <= 1 or getattr(self._thread_state, "in_worker", False) or worker_count <= 1:
            return {edge.target_socket: self._evaluate(graph, edge.source_node, frame, visiting) for edge in edges}

        started = time.perf_counter()
        futures: list[tuple[Any, Future[ImageFrame]]] = []
        executor = self._executor
        if executor is None:
            return {edge.target_socket: self._evaluate(graph, edge.source_node, frame, visiting) for edge in edges}
        channel_demand = self._current_channel_demand()
        for edge in edges:
            child_visiting = set(visiting)
            futures.append(
                (
                    edge,
                    executor.submit(
                        self._evaluate_input_worker,
                        graph,
                        edge.source_node,
                        frame,
                        child_visiting,
                        channel_demand,
                    ),
                )
            )

        inputs: dict[str, ImageFrame] = {}
        try:
            for edge, future in futures:
                inputs[edge.target_socket] = future.result()
        except Exception:
            for _edge, future in futures:
                future.cancel()
            raise
        finally:
            self.record_phase_timing(
                "graph",
                "inputs.parallel",
                (time.perf_counter() - started) * 1000.0,
                {"frame": frame, "branches": len(edges), "workers": worker_count},
            )
        return inputs

    def _evaluate_input_worker(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        visiting: set[str],
        channel_demand: ChannelDemand | None,
    ) -> ImageFrame:
        previous = getattr(self._thread_state, "in_worker", False)
        previous_demand = self._current_channel_demand()
        self._thread_state.in_worker = True
        self._thread_state.channel_demand = channel_demand
        try:
            return self._evaluate(graph, node_id, frame, visiting)
        finally:
            self._thread_state.in_worker = previous
            self._set_current_channel_demand(previous_demand)

    def _evaluate_tile_cached(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        window: TileWindow,
        visiting: set[str],
    ) -> ImageFrame:
        if window.width <= 0 or window.height <= 0:
            return _empty_tile(window, frame, self.settings.working_colorspace)
        if node_id not in graph.nodes:
            raise KeyError(f"Graph does not contain node '{node_id}'.")

        signature = self.node_signature(graph, node_id, frame)
        cache_key: TileCacheKey = (node_id, frame, signature, window.x, window.y, window.width, window.height)
        cached = self._get_cached_tile(cache_key)
        if cached is not None:
            self._record_node_timing(graph.nodes[node_id].id, graph.nodes[node_id].type, 0.0, cache_hit=True)
            return cached

        if not self.settings.cache_enabled:
            return self._evaluate_tile(graph, node_id, frame, window, visiting)

        owner = False
        wait_future: Future[ImageFrame] | None = None
        with self._lock:
            self.tile_cache_misses += 1
            wait_future = self._tile_inflight.get(cache_key)
            if wait_future is None:
                wait_future = Future()
                self._tile_inflight[cache_key] = wait_future
                owner = True

        if not owner:
            wait_started = time.perf_counter()
            try:
                result = wait_future.result()
            finally:
                self.record_phase_timing(
                    node_id,
                    "tile.cache.inflight_wait",
                    (time.perf_counter() - wait_started) * 1000.0,
                    {"frame": frame, "tile": _tile_details(window)},
                )
            self._record_node_timing(graph.nodes[node_id].id, graph.nodes[node_id].type, 0.0, cache_hit=True)
            return result

        try:
            result = self._evaluate_tile(graph, node_id, frame, window, visiting)
        except Exception as exc:
            wait_future.set_exception(exc)
            raise
        else:
            if graph.nodes[node_id].type.lower() != "viewer":
                self._store_cached_tile(cache_key, result, signature)
            wait_future.set_result(result)
            return result
        finally:
            with self._lock:
                if self._tile_inflight.get(cache_key) is wait_future:
                    self._tile_inflight.pop(cache_key, None)

    def _evaluate_tile(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        window: TileWindow,
        visiting: set[str],
    ) -> ImageFrame:
        if node_id in visiting:
            chain = " -> ".join([*visiting, node_id])
            raise GraphCycleError(f"Node graph contains a cycle: {chain}")
        if window.width <= 0 or window.height <= 0:
            return _empty_tile(window, frame, self.settings.working_colorspace)

        node = graph.nodes[node_id]
        node_type = node.type.lower()
        if node_type == "viewer":
            input_edges = _viewer_input_edges(graph, node_id)
            if not input_edges:
                return self._evaluate_full_tile(graph, node_id, frame, window, visiting, "viewer.unconnected")
            return self._evaluate_tile_cached(graph, input_edges[0].source_node, frame, window, set(visiting))
        if node_type == "read":
            full = self._evaluate(graph, node_id, frame, visiting)
            return _crop_frame_to_tile(full, window)
        if node_type == "constant":
            return _constant_tile(node.params, window, frame, self.settings.working_colorspace)
        if node_type == "reformat":
            return self._evaluate_reformat_tile(graph, node_id, frame, window, visiting)
        if node_type == "crop" and _truthy(node.params.get("reformat", False)):
            return self._evaluate_full_tile(graph, node_id, frame, window, visiting, "crop.reformat")
        if node_type == "transform":
            return self._evaluate_full_tile(graph, node_id, frame, window, visiting, "transform.affine")
        can_tile_bypass = _node_disabled(node) and _preferred_bypass_edge(graph.incoming_edges(node_id), node.type) is not None
        if node_type not in TILE_LOCAL_NODE_TYPES and not can_tile_bypass:
            return self._evaluate_full_tile(graph, node_id, frame, window, visiting, "unsupported")

        node = graph.nodes[node_id]
        operation = NODE_REGISTRY.get(node_type)
        if operation is None and not can_tile_bypass:
            raise UnknownNodeTypeError(f"Unsupported node type '{node.type}' on node '{node_id}'.")

        self._begin_node(node.id)
        started = time.perf_counter()
        visiting.add(node_id)
        try:
            input_edges = graph.incoming_edges(node_id)
            bypass_edge = _preferred_bypass_edge(input_edges, node.type) if _node_disabled(node) else None
            if bypass_edge is not None:
                input_edges = [bypass_edge]
            elif operation is None:
                raise UnknownNodeTypeError(f"Unsupported node type '{node.type}' on node '{node_id}'.")
            inputs = self._evaluate_tile_inputs(graph, frame, input_edges, window, visiting)
            visiting.remove(node_id)
            context = EvaluationContext(
                frame=frame,
                settings=self.settings,
                ocio=self.ocio,
                requested_channels=self._current_channel_demand(),
                metrics=lambda metric_node_id, phase, duration_ms, details=None: self.record_phase_timing(
                    metric_node_id,
                    phase,
                    duration_ms,
                    details,
                ),
            )
            result = (
                _bypass_frame(node, inputs)
                if bypass_edge is not None
                else operation.evaluate(node, inputs, context)
            )
            return _annotate_tile_frame(result, window, _tile_full_width(inputs), _tile_full_height(inputs))
        except NodeEvaluationError:
            raise
        except Exception as exc:
            raise NodeEvaluationError(node.id, str(exc)) from exc
        finally:
            visiting.discard(node_id)
            self._end_node(node.id, node.type, started)

    def _evaluate_reformat_tile(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        window: TileWindow,
        visiting: set[str],
    ) -> ImageFrame:
        node = graph.nodes[node_id]
        input_edges = graph.incoming_edges(node_id)
        bypass_edge = _preferred_bypass_edge(input_edges, node.type) if _node_disabled(node) else None
        if bypass_edge is not None:
            input_edges = [bypass_edge]
        if not input_edges:
            return self._evaluate_full_tile(graph, node_id, frame, window, visiting, "reformat.unconnected")
        if len(input_edges) > 1:
            return self._evaluate_full_tile(graph, node_id, frame, window, visiting, "reformat.multi_input")

        self._begin_node(node.id)
        started = time.perf_counter()
        visiting.add(node_id)
        try:
            source_tile = self._evaluate_tile_cached(graph, input_edges[0].source_node, frame, window, set(visiting))
            source_full_width = int(source_tile.metadata.get(TILE_FULL_WIDTH) or source_tile.width)
            source_full_height = int(source_tile.metadata.get(TILE_FULL_HEIGHT) or source_tile.height)
            target_width = int(node.params.get("width") or source_full_width)
            target_height = int(node.params.get("height") or source_full_height)
            if target_width != source_full_width or target_height != source_full_height:
                fallback_visiting = set(visiting)
                fallback_visiting.discard(node_id)
                return self._evaluate_full_tile(graph, node_id, frame, window, fallback_visiting, "reformat.resize")

            preserve_channels = bool(node.params.get("preserve_channels", node.params.get("resize_channels", False)))
            return ImageFrame(
                width=source_tile.width,
                height=source_tile.height,
                data=source_tile.data,
                channels=source_tile.channels,
                channel_data=source_tile.copy_channel_data() if preserve_channels else {},
                pixel_aspect=source_tile.pixel_aspect,
                colorspace=source_tile.colorspace,
                frame=frame,
                metadata={
                    **source_tile.metadata,
                    "reformat": [target_width, target_height],
                    "reformat/preserve_channels": preserve_channels,
                    TILE_FULL_WIDTH: target_width,
                    TILE_FULL_HEIGHT: target_height,
                    TILE_X: window.x,
                    TILE_Y: window.y,
                },
                format_bbox=source_tile.format_bbox,
                data_window=source_tile.data_window,
            )
        finally:
            visiting.discard(node_id)
            self._end_node(node.id, node.type, started)

    def _evaluate_tile_inputs(
        self,
        graph: ProjectGraph,
        frame: int,
        input_edges,
        window: TileWindow,
        visiting: set[str],
    ) -> dict[str, ImageFrame]:
        edges = list(input_edges)
        worker_count = self._parallel_worker_count(edges, graph)
        if len(edges) <= 1 or getattr(self._thread_state, "in_worker", False) or worker_count <= 1:
            return {
                edge.target_socket: self._evaluate_tile_cached(graph, edge.source_node, frame, window, visiting)
                for edge in edges
            }

        started = time.perf_counter()
        futures: list[tuple[Any, Future[ImageFrame]]] = []
        executor = self._executor
        if executor is None:
            return {
                edge.target_socket: self._evaluate_tile_cached(graph, edge.source_node, frame, window, visiting)
                for edge in edges
            }
        channel_demand = self._current_channel_demand()
        for edge in edges:
            child_visiting = set(visiting)
            futures.append(
                (
                    edge,
                    executor.submit(
                        self._evaluate_tile_input_worker,
                        graph,
                        edge.source_node,
                        frame,
                        window,
                        child_visiting,
                        channel_demand,
                    ),
                )
            )

        inputs: dict[str, ImageFrame] = {}
        try:
            for edge, future in futures:
                inputs[edge.target_socket] = future.result()
        except Exception:
            for _edge, future in futures:
                future.cancel()
            raise
        finally:
            self.record_phase_timing(
                "graph",
                "tiles.inputs.parallel",
                (time.perf_counter() - started) * 1000.0,
                {"frame": frame, "branches": len(edges), "workers": worker_count, "tile": _tile_details(window)},
            )
        return inputs

    def _evaluate_tile_input_worker(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        window: TileWindow,
        visiting: set[str],
        channel_demand: ChannelDemand | None,
    ) -> ImageFrame:
        previous = getattr(self._thread_state, "in_worker", False)
        previous_demand = self._current_channel_demand()
        self._thread_state.in_worker = True
        self._thread_state.channel_demand = channel_demand
        try:
            return self._evaluate_tile_cached(graph, node_id, frame, window, visiting)
        finally:
            self._thread_state.in_worker = previous
            self._set_current_channel_demand(previous_demand)

    def _evaluate_full_tile(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        window: TileWindow,
        visiting: set[str],
        reason: str,
    ) -> ImageFrame:
        started = time.perf_counter()
        full = self._evaluate(graph, node_id, frame, set(visiting))
        tile = _crop_frame_to_tile(full, window)
        self.record_phase_timing(
            node_id,
            "tile.full_frame_fallback",
            (time.perf_counter() - started) * 1000.0,
            {"frame": frame, "reason": reason, "tile": _tile_details(window), "width": full.width, "height": full.height},
        )
        return tile

    def _render_worker_count(self) -> int:
        cpu_count = os.cpu_count() or 1
        requested = int(getattr(self.settings, "render_workers", 4) or 1)
        return max(1, min(requested, cpu_count))

    def _read_worker_count(self) -> int:
        cpu_count = os.cpu_count() or 1
        requested = int(getattr(self.settings, "read_workers", 4) or 1)
        return max(1, min(requested, cpu_count))

    def _executor_worker_count(self) -> int:
        return max(self._render_worker_count(), self._read_worker_count())

    def _parallel_worker_count(self, edges, graph: ProjectGraph) -> int:
        if any(graph.nodes.get(edge.source_node) and graph.nodes[edge.source_node].type.lower() == "read" for edge in edges):
            return max(self._render_worker_count(), self._read_worker_count())
        return self._render_worker_count()

    def _read_source_signature(self, node: Node, context: EvaluationContext) -> str | None:
        path = str(node.params.get("path") or node.params.get("file") or "builtin://gradient")
        read_frame = _range_frame(node, _mapped_frame(node, context.frame))
        if read_frame is None:
            return None
        colorspace = str(node.params.get("colorspace") or context.settings.working_colorspace)
        read_channels = _read_channels(node, context)
        source = image_source_fingerprint(path, read_frame)
        missing_policy = str(node.params.get("missing_frames") or node.params.get("on_error") or "error").lower()
        if source.get("exists") is False and missing_policy in {"nearest", "nearest frame", "nearest_frame"}:
            return None
        payload = {
            "kind": "read_source",
            "source": source,
            "path_expression": path,
            "read_frame": read_frame,
            "colorspace": colorspace,
            "read_channels": "all" if read_channels is None else [str(channel) for channel in read_channels],
            "missing_policy": missing_policy,
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def clear_cache(self) -> None:
        with self._lock:
            self.cache.clear()
            self.preview_cache.clear()
            self.float_preview_cache.clear()
            self.tile_cache.clear()
            self.source_cache.clear()
            self.execution_plan_cache.clear()
            self._inflight.clear()
            self._tile_inflight.clear()
            self._source_inflight.clear()
            self.cache_hits = 0
            self.cache_misses = 0
            self.preview_cache_hits = 0
            self.preview_cache_misses = 0
            self.float_preview_cache_hits = 0
            self.float_preview_cache_misses = 0
            self.tile_cache_hits = 0
            self.tile_cache_misses = 0
            self.source_cache_hits = 0
            self.source_cache_misses = 0
            self.execution_plan_hits = 0
            self.execution_plan_misses = 0
            self.cache_memory_bytes = 0
            self.preview_cache_memory_bytes = 0
            self.float_preview_cache_memory_bytes = 0
            self.tile_cache_memory_bytes = 0
            self.source_cache_memory_bytes = 0
            self.phase_timings.clear()
            self.request_timings.clear()

    def set_cache_limits(
        self,
        max_cache_bytes: int,
        max_preview_cache_bytes: int,
        max_float_preview_cache_bytes: int | None = None,
    ) -> None:
        with self._lock:
            self.max_cache_bytes = max(0, int(max_cache_bytes))
            self.max_tile_cache_bytes = self.max_cache_bytes
            self.max_source_cache_bytes = self.max_cache_bytes
            self.max_preview_cache_bytes = max(0, int(max_preview_cache_bytes))
            self.max_float_preview_cache_bytes = max(
                0,
                int(max_float_preview_cache_bytes if max_float_preview_cache_bytes is not None else max_preview_cache_bytes),
            )
            self._prune_cache()

    def node_signature(self, graph: ProjectGraph, node_id: str, frame: int) -> str:
        signature = _node_signature(graph, node_id, frame, visiting=set())
        demand = self._current_channel_demand() or channel_demand_for_graph(graph, node_id)
        return _signature_with_channel_demand(signature, demand)

    def output_signature(self, graph: ProjectGraph, node_id: str, frame: int) -> str:
        node = graph.nodes[node_id]
        if node.type.lower() == "viewer":
            input_edges = _viewer_input_edges(graph, node_id)
            if input_edges:
                return self.node_signature(graph, input_edges[0].source_node, frame)
        return self.node_signature(graph, node_id, frame)

    def channel_demand_for(
        self,
        graph: ProjectGraph,
        node_id: str,
        requested_channel: str | None = None,
    ) -> ChannelDemand:
        return channel_demand_for_graph(graph, node_id, requested_channel)

    @contextmanager
    def channel_demand_scope(self, demand: ChannelDemand | None) -> Iterator[None]:
        previous = self._current_channel_demand()
        self._set_current_channel_demand(demand)
        try:
            yield
        finally:
            self._set_current_channel_demand(previous)

    def _evaluation_channel_demand(
        self,
        graph: ProjectGraph,
        node_id: str,
        requested_channel: str | None,
        channel_demand: ChannelDemand | None,
    ) -> ChannelDemand | None:
        if channel_demand is not None:
            return channel_demand
        current = self._current_channel_demand()
        if current is not None and requested_channel is None:
            return current
        return channel_demand_for_graph(graph, node_id, requested_channel)

    def _current_channel_demand(self) -> ChannelDemand | None:
        return getattr(self._thread_state, "channel_demand", None)

    def _set_current_channel_demand(self, demand: ChannelDemand | None) -> None:
        if demand is None:
            if hasattr(self._thread_state, "channel_demand"):
                delattr(self._thread_state, "channel_demand")
            return
        self._thread_state.channel_demand = demand

    def preview_cache_key(
        self,
        graph: ProjectGraph,
        node_id: str,
        frame: int,
        display: str | None,
        view: str | None,
        channel: str | None,
        max_width: int | None,
        max_height: int | None,
        ocio_config: str | None,
    ) -> PreviewCacheKey:
        return self.preview_cache_key_for_signature(
            node_id,
            frame,
            self.output_signature(graph, node_id, frame),
            display,
            view,
            channel,
            max_width,
            max_height,
            ocio_config,
        )

    def preview_cache_key_for_signature(
        self,
        node_id: str,
        frame: int,
        output_signature: str,
        display: str | None,
        view: str | None,
        channel: str | None,
        max_width: int | None,
        max_height: int | None,
        ocio_config: str | None,
    ) -> PreviewCacheKey:
        return (
            node_id,
            frame,
            output_signature,
            display or "",
            view or "",
            channel or "rgba",
            max_width,
            max_height,
            ocio_config,
        )

    def get_cached_preview(self, cache_key: PreviewCacheKey) -> bytes | None:
        if not self.settings.cache_enabled:
            return None
        with self._lock:
            entry = self.preview_cache.get(cache_key)
            if entry is None:
                self.preview_cache_misses += 1
                return None
            self.preview_cache.move_to_end(cache_key)
            self.preview_cache_hits += 1
            return entry.data

    def store_cached_preview(self, cache_key: PreviewCacheKey, data: bytes) -> None:
        if not self.settings.cache_enabled:
            return
        with self._lock:
            entry_bytes = len(data)
            if self.max_preview_cache_bytes <= 0 or entry_bytes > self.max_preview_cache_bytes:
                return
            existing = self.preview_cache.pop(cache_key, None)
            if existing is not None:
                self.preview_cache_memory_bytes -= existing.bytes
            self.preview_cache[cache_key] = PreviewCacheEntry(data=data, bytes=entry_bytes)
            self.preview_cache_memory_bytes += entry_bytes
            self._prune_preview_cache()

    def has_cached_preview(self, cache_key: PreviewCacheKey) -> bool:
        with self._lock:
            return cache_key in self.preview_cache

    def float_preview_cache_key_for_signature(
        self,
        node_id: str,
        frame: int,
        output_signature: str,
        channel: str | None,
        max_width: int | None,
        max_height: int | None,
    ) -> FloatPreviewCacheKey:
        return (node_id, frame, output_signature, channel or "rgba", max_width, max_height)

    def get_cached_float_preview(self, cache_key: FloatPreviewCacheKey) -> FloatPreviewCacheEntry | None:
        if not self.settings.cache_enabled:
            return None
        with self._lock:
            entry = self.float_preview_cache.get(cache_key)
            if entry is None:
                self.float_preview_cache_misses += 1
                return None
            self.float_preview_cache.move_to_end(cache_key)
            self.float_preview_cache_hits += 1
            return entry

    def store_cached_float_preview(self, cache_key: FloatPreviewCacheKey, entry: FloatPreviewCacheEntry) -> None:
        if not self.settings.cache_enabled:
            return
        with self._lock:
            if self.max_float_preview_cache_bytes <= 0 or entry.bytes > self.max_float_preview_cache_bytes:
                return
            existing = self.float_preview_cache.pop(cache_key, None)
            if existing is not None:
                self.float_preview_cache_memory_bytes -= existing.bytes
            self.float_preview_cache[cache_key] = entry
            self.float_preview_cache_memory_bytes += entry.bytes
            self._prune_float_preview_cache()

    def has_cached_float_preview(self, cache_key: FloatPreviewCacheKey) -> bool:
        with self._lock:
            return cache_key in self.float_preview_cache

    def cached_frame_numbers(self, node_ids: set[str] | None = None) -> dict[str, list[int]]:
        with self._lock:
            node_frames = {
                key[1]
                for key in self.cache.keys()
                if node_ids is None or key[0] in node_ids
            }
            preview_frames = {
                key[1]
                for key in self.preview_cache.keys()
                if node_ids is None or key[0] in node_ids
            }
            float_preview_frames = {
                key[1]
                for key in self.float_preview_cache.keys()
                if node_ids is None or key[0] in node_ids
            }
            return {
                "node_frames": sorted(node_frames),
                "preview_frames": sorted(preview_frames | float_preview_frames),
                "final_preview_frames": sorted(preview_frames),
                "float_preview_frames": sorted(float_preview_frames),
                "all_frames": sorted(node_frames | preview_frames | float_preview_frames),
            }

    def cache_snapshot(self, node_ids: set[str] | None = None) -> dict[str, Any]:
        with self._lock:
            scoped_frames = self.cached_frame_numbers(node_ids)
            all_frames = self.cached_frame_numbers()
            return {
                "enabled": self.settings.cache_enabled,
                "entries": len(self.cache),
                "preview_entries": len(self.preview_cache),
                "float_preview_entries": len(self.float_preview_cache),
                "tile_cache_entries": len(self.tile_cache),
                "source_cache_entries": len(self.source_cache),
                "execution_plan_entries": len(self.execution_plan_cache),
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "preview_hits": self.preview_cache_hits,
                "preview_misses": self.preview_cache_misses,
                "float_preview_hits": self.float_preview_cache_hits,
                "float_preview_misses": self.float_preview_cache_misses,
                "tile_cache_hits": self.tile_cache_hits,
                "tile_cache_misses": self.tile_cache_misses,
                "source_cache_hits": self.source_cache_hits,
                "source_cache_misses": self.source_cache_misses,
                "execution_plan_hits": self.execution_plan_hits,
                "execution_plan_misses": self.execution_plan_misses,
                "memory_bytes": self.cache_memory_bytes,
                "preview_memory_bytes": self.preview_cache_memory_bytes,
                "float_preview_memory_bytes": self.float_preview_cache_memory_bytes,
                "tile_cache_memory_bytes": self.tile_cache_memory_bytes,
                "source_cache_memory_bytes": self.source_cache_memory_bytes,
                "max_memory_bytes": self.max_cache_bytes,
                "max_preview_memory_bytes": self.max_preview_cache_bytes,
                "max_float_preview_memory_bytes": self.max_float_preview_cache_bytes,
                "max_tile_cache_memory_bytes": self.max_tile_cache_bytes or 0,
                "max_source_cache_memory_bytes": self.max_source_cache_bytes or 0,
                "cached_frames": scoped_frames["preview_frames"],
                "cached_final_preview_frames": scoped_frames["final_preview_frames"],
                "cached_float_preview_frames": scoped_frames["float_preview_frames"],
                "cached_node_frames": scoped_frames["node_frames"],
                "cached_all_frames": all_frames["all_frames"],
                "active_nodes": sorted(self.active_nodes),
                "node_timings": dict(self.node_timings),
                "preview_timings": dict(self.preview_timings),
                "phase_timings": list(self.phase_timings),
                "request_timings": list(self.request_timings),
                "last_request_timing": self.request_timings[-1] if self.request_timings else None,
            }

    @contextmanager
    def node_runtime(self, node_id: str, node_type: str) -> Iterator[None]:
        self._begin_node(node_id)
        started = time.perf_counter()
        try:
            yield
        finally:
            self._end_node(node_id, node_type, started)

    def mark_node_cache_hit(self, node_id: str, node_type: str) -> None:
        self._record_node_timing(node_id, node_type, 0.0, cache_hit=True)

    def record_preview_timing(self, node_id: str, timing: dict[str, Any]) -> None:
        with self._lock:
            self.preview_timings[node_id] = {**timing, "timestamp": time.time()}

    def record_phase_timing(
        self,
        node_id: str,
        phase: str,
        duration_ms: float,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self.phase_timings.append(
                {
                    "node_id": node_id,
                    "phase": phase,
                    "duration_ms": round(duration_ms, 2),
                    "details": details or {},
                    "timestamp": time.time(),
                }
            )
            del self.phase_timings[:-160]

    def record_request_timing(self, timing: dict[str, Any]) -> None:
        with self._lock:
            self.request_timings.append({**timing, "timestamp": time.time()})
            del self.request_timings[:-80]

    def _get_cached(self, cache_key: CacheKey) -> ImageFrame | None:
        if not self.settings.cache_enabled:
            return None
        with self._lock:
            entry = self.cache.get(cache_key)
            if entry is None:
                return None
            self.cache.move_to_end(cache_key)
            self.cache_hits += 1
            return entry.image

    def _get_cached_tile(self, cache_key: TileCacheKey) -> ImageFrame | None:
        if not self.settings.cache_enabled:
            return None
        with self._lock:
            entry = self.tile_cache.get(cache_key)
            if entry is None:
                return None
            self.tile_cache.move_to_end(cache_key)
            self.tile_cache_hits += 1
            return entry.image

    def _get_cached_source(self, cache_key: SourceCacheKey) -> ImageFrame | None:
        if not self.settings.cache_enabled:
            return None
        with self._lock:
            entry = self.source_cache.get(cache_key)
            if entry is None:
                return None
            self.source_cache.move_to_end(cache_key)
            self.source_cache_hits += 1
            return entry.image

    def _store_cached(self, cache_key: CacheKey, image: ImageFrame, signature: str) -> None:
        if not self.settings.cache_enabled:
            return
        with self._lock:
            entry_bytes = _estimate_image_bytes(image)
            if self.max_cache_bytes <= 0 or entry_bytes > self.max_cache_bytes:
                return
            existing = self.cache.pop(cache_key, None)
            if existing is not None:
                self.cache_memory_bytes -= existing.bytes
            self.cache[cache_key] = CacheEntry(image=image, bytes=entry_bytes, signature=signature)
            self.cache_memory_bytes += entry_bytes
            self._prune_cache()

    def _store_cached_tile(self, cache_key: TileCacheKey, image: ImageFrame, signature: str) -> None:
        if not self.settings.cache_enabled:
            return
        with self._lock:
            entry_bytes = _estimate_image_bytes(image)
            max_bytes = int(self.max_tile_cache_bytes if self.max_tile_cache_bytes is not None else self.max_cache_bytes)
            if max_bytes <= 0 or entry_bytes > max_bytes:
                return
            existing = self.tile_cache.pop(cache_key, None)
            if existing is not None:
                self.tile_cache_memory_bytes -= existing.bytes
            self.tile_cache[cache_key] = TileCacheEntry(image=image, bytes=entry_bytes, signature=signature)
            self.tile_cache_memory_bytes += entry_bytes
            self._prune_tile_cache()

    def _store_cached_source(self, cache_key: SourceCacheKey, image: ImageFrame, signature: str) -> None:
        if not self.settings.cache_enabled:
            return
        with self._lock:
            entry_bytes = _estimate_image_bytes(image)
            max_bytes = int(self.max_source_cache_bytes if self.max_source_cache_bytes is not None else self.max_cache_bytes)
            if max_bytes <= 0 or entry_bytes > max_bytes:
                return
            existing = self.source_cache.pop(cache_key, None)
            if existing is not None:
                self.source_cache_memory_bytes -= existing.bytes
            self.source_cache[cache_key] = SourceCacheEntry(image=image, bytes=entry_bytes, signature=signature)
            self.source_cache_memory_bytes += entry_bytes
            self._prune_source_cache()

    def _prune_cache(self) -> None:
        if self.max_cache_bytes <= 0:
            self.cache.clear()
            self.cache_memory_bytes = 0
        while self.max_cache_bytes > 0 and self.cache_memory_bytes > self.max_cache_bytes and self.cache:
            _key, entry = self.cache.popitem(last=False)
            self.cache_memory_bytes -= entry.bytes
        self._prune_tile_cache()
        self._prune_source_cache()
        self._prune_preview_cache()
        self._prune_float_preview_cache()

    def _prune_tile_cache(self) -> None:
        max_bytes = int(self.max_tile_cache_bytes if self.max_tile_cache_bytes is not None else self.max_cache_bytes)
        if max_bytes <= 0:
            self.tile_cache.clear()
            self.tile_cache_memory_bytes = 0
            return
        while self.tile_cache_memory_bytes > max_bytes and self.tile_cache:
            _key, entry = self.tile_cache.popitem(last=False)
            self.tile_cache_memory_bytes -= entry.bytes

    def _prune_source_cache(self) -> None:
        max_bytes = int(self.max_source_cache_bytes if self.max_source_cache_bytes is not None else self.max_cache_bytes)
        if max_bytes <= 0:
            self.source_cache.clear()
            self.source_cache_memory_bytes = 0
            return
        while self.source_cache_memory_bytes > max_bytes and self.source_cache:
            _key, entry = self.source_cache.popitem(last=False)
            self.source_cache_memory_bytes -= entry.bytes

    def _prune_preview_cache(self) -> None:
        if self.max_preview_cache_bytes <= 0:
            self.preview_cache.clear()
            self.preview_cache_memory_bytes = 0
            return
        while self.preview_cache_memory_bytes > self.max_preview_cache_bytes and self.preview_cache:
            _key, entry = self.preview_cache.popitem(last=False)
            self.preview_cache_memory_bytes -= entry.bytes

    def _prune_float_preview_cache(self) -> None:
        if self.max_float_preview_cache_bytes <= 0:
            self.float_preview_cache.clear()
            self.float_preview_cache_memory_bytes = 0
            return
        while self.float_preview_cache_memory_bytes > self.max_float_preview_cache_bytes and self.float_preview_cache:
            _key, entry = self.float_preview_cache.popitem(last=False)
            self.float_preview_cache_memory_bytes -= entry.bytes

    def _begin_node(self, node_id: str) -> None:
        with self._lock:
            self.active_node_counts[node_id] = self.active_node_counts.get(node_id, 0) + 1
            self.active_nodes.add(node_id)

    def _end_node(self, node_id: str, node_type: str, started: float) -> None:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        with self._lock:
            remaining = self.active_node_counts.get(node_id, 1) - 1
            if remaining <= 0:
                self.active_node_counts.pop(node_id, None)
                self.active_nodes.discard(node_id)
            else:
                self.active_node_counts[node_id] = remaining
            self._record_node_timing(node_id, node_type, elapsed_ms, cache_hit=False)

    def _record_node_timing(self, node_id: str, node_type: str, elapsed_ms: float, cache_hit: bool) -> None:
        with self._lock:
            self.node_timings[node_id] = {
                "type": node_type,
                "duration_ms": round(elapsed_ms, 2),
                "cache_hit": cache_hit,
                "timestamp": time.time(),
            }


def evaluate_node(graph: ProjectGraph, node_id: str, frame: int) -> ImageFrame:
    return GraphEvaluator().evaluate_node(graph, node_id, frame)


def _crop_frame_to_tile(frame: ImageFrame, window: TileWindow) -> ImageFrame:
    output = np.zeros((window.height, window.width, 4), dtype=np.float32)
    src_x0 = max(0, window.x)
    src_y0 = max(0, window.y)
    src_x1 = min(frame.width, window.x + window.width)
    src_y1 = min(frame.height, window.y + window.height)
    dst_x0 = max(0, -window.x)
    dst_y0 = max(0, -window.y)
    if src_x1 > src_x0 and src_y1 > src_y0:
        dst_x1 = dst_x0 + (src_x1 - src_x0)
        dst_y1 = dst_y0 + (src_y1 - src_y0)
        output[dst_y0:dst_y1, dst_x0:dst_x1] = frame.data[src_y0:src_y1, src_x0:src_x1]

    channel_data: dict[str, np.ndarray] = {}
    for name, value in frame.channel_data.items():
        tile_value = _crop_aux_plane(value, window, frame.width, frame.height)
        if tile_value is not None:
            channel_data[name] = tile_value
    return ImageFrame(
        width=window.width,
        height=window.height,
        data=output,
        channels=frame.channels,
        channel_data=channel_data,
        pixel_aspect=frame.pixel_aspect,
        colorspace=frame.colorspace,
        frame=frame.frame,
        metadata={
            **frame.metadata,
            TILE_FULL_WIDTH: frame.width,
            TILE_FULL_HEIGHT: frame.height,
            TILE_X: window.x,
            TILE_Y: window.y,
        },
        format_bbox=frame.format_bbox,
        data_window=frame.data_window,
    )


def _crop_aux_plane(value: np.ndarray, window: TileWindow, full_width: int, full_height: int) -> np.ndarray | None:
    if value.ndim not in {2, 3}:
        return None
    shape_tail = value.shape[2:] if value.ndim == 3 else ()
    output_shape = (window.height, window.width, *shape_tail)
    output = np.zeros(output_shape, dtype=np.float32)
    src_x0 = max(0, window.x)
    src_y0 = max(0, window.y)
    src_x1 = min(full_width, window.x + window.width)
    src_y1 = min(full_height, window.y + window.height)
    dst_x0 = max(0, -window.x)
    dst_y0 = max(0, -window.y)
    if src_x1 > src_x0 and src_y1 > src_y0:
        dst_x1 = dst_x0 + (src_x1 - src_x0)
        dst_y1 = dst_y0 + (src_y1 - src_y0)
        output[dst_y0:dst_y1, dst_x0:dst_x1] = value[src_y0:src_y1, src_x0:src_x1]
    return np.ascontiguousarray(output)


def _constant_tile(params: dict[str, Any], window: TileWindow, frame: int, working_colorspace: str) -> ImageFrame:
    full_width = int(params.get("width") or 1920)
    full_height = int(params.get("height") or 1080)
    color = [
        float(params.get("r", 0.0)),
        float(params.get("g", 0.0)),
        float(params.get("b", 0.0)),
        float(params.get("a", 1.0)),
    ]
    data = np.zeros((window.height, window.width, 4), dtype=np.float32)
    data[:, :] = color
    return ImageFrame(
        width=window.width,
        height=window.height,
        data=data,
        colorspace=str(params.get("colorspace") or working_colorspace),
        frame=frame,
        metadata={
            "generated": "constant",
            "color": color,
            TILE_FULL_WIDTH: full_width,
            TILE_FULL_HEIGHT: full_height,
            TILE_X: window.x,
            TILE_Y: window.y,
        },
    )


def _empty_tile(window: TileWindow, frame: int, colorspace: str) -> ImageFrame:
    return ImageFrame(
        width=max(0, window.width),
        height=max(0, window.height),
        data=np.zeros((max(0, window.height), max(0, window.width), 4), dtype=np.float32),
        colorspace=colorspace,
        frame=frame,
        metadata={TILE_FULL_WIDTH: max(0, window.width), TILE_FULL_HEIGHT: max(0, window.height), TILE_X: window.x, TILE_Y: window.y},
    )


def _annotate_tile_frame(frame: ImageFrame, window: TileWindow, full_width: int, full_height: int) -> ImageFrame:
    metadata = {
        **frame.metadata,
        TILE_FULL_WIDTH: full_width,
        TILE_FULL_HEIGHT: full_height,
        TILE_X: window.x,
        TILE_Y: window.y,
    }
    return ImageFrame(
        width=frame.width,
        height=frame.height,
        data=frame.data,
        channels=frame.channels,
        channel_data=frame.channel_data,
        pixel_aspect=frame.pixel_aspect,
        colorspace=frame.colorspace,
        frame=frame.frame,
        metadata=metadata,
        format_bbox=frame.format_bbox,
        data_window=frame.data_window,
    )


def _tile_full_width(inputs: dict[str, ImageFrame]) -> int:
    for frame in inputs.values():
        return int(frame.metadata.get(TILE_FULL_WIDTH) or frame.width)
    return 0


def _tile_full_height(inputs: dict[str, ImageFrame]) -> int:
    for frame in inputs.values():
        return int(frame.metadata.get(TILE_FULL_HEIGHT) or frame.height)
    return 0


def _tile_details(window: TileWindow) -> dict[str, int]:
    return {"x": window.x, "y": window.y, "width": window.width, "height": window.height}


def _node_disabled(node: Node) -> bool:
    value = node.params.get("disabled", node.params.get("disable", False))
    return _truthy(value, truthy_strings={"1", "true", "yes", "on", "disabled", "disable"})


def _truthy(value: object, truthy_strings: set[str] | None = None) -> bool:
    strings = truthy_strings or {"1", "true", "yes", "on"}
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in strings
    return bool(value)


def _preferred_bypass_edge(edges: list[Edge], node_type: str | None = None) -> Edge | None:
    if not edges:
        return None
    ordered = sorted(edges, key=lambda item: (_socket_priority(item.target_socket, node_type), item.target_socket, item.source_node))
    return ordered[0]


def _preferred_bypass_input(inputs: dict[str, ImageFrame], node_type: str | None = None) -> ImageFrame | None:
    if not inputs:
        return None
    socket = sorted(inputs.keys(), key=lambda item: (_socket_priority(item, node_type), item))[0]
    return inputs[socket]


def _socket_priority(socket: str, node_type: str | None = None) -> int:
    normalized = str(socket).strip().lower()
    priorities = MERGE_BYPASS_SOCKET_PRIORITY if str(node_type or "").lower() == "merge" else BYPASS_SOCKET_PRIORITY
    if normalized in priorities:
        return priorities.index(normalized)
    return len(priorities)


def _bypass_frame(node: Node, inputs: dict[str, ImageFrame]) -> ImageFrame:
    source = _preferred_bypass_input(inputs, node.type)
    if source is None:
        raise NodeEvaluationError(node.id, "Disabled node has no input to bypass.")
    metadata = {
        **source.metadata,
        "node/bypassed": node.id,
        "node/bypassed_type": node.type,
    }
    return ImageFrame(
        width=source.width,
        height=source.height,
        data=source.data,
        channels=list(source.channels),
        channel_data=dict(source.channel_data),
        pixel_aspect=source.pixel_aspect,
        colorspace=source.colorspace,
        frame=source.frame,
        metadata=metadata,
        format_bbox=dict(source.format_bbox or {}),
        data_window=dict(source.data_window or {}),
    )


def _params_hash(params: dict) -> str:
    payload = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _node_signature(graph: ProjectGraph, node_id: str, frame: int, visiting: set[str]) -> str:
    if node_id in visiting:
        chain = " -> ".join([*visiting, node_id])
        raise GraphCycleError(f"Node graph contains a cycle: {chain}")
    node = graph.nodes[node_id]
    visiting.add(node_id)
    incoming = []
    input_edges = graph.incoming_edges(node_id)
    if node.type.lower() == "viewer":
        input_edges = _viewer_input_edges(graph, node_id)
    bypass_edge = _preferred_bypass_edge(input_edges, node.type) if _node_disabled(node) else None
    if bypass_edge is not None:
        input_edges = [bypass_edge]
    for edge in sorted(input_edges, key=lambda item: (item.target_socket, item.source_node)):
        incoming.append(
            {
                "source_node": edge.source_node,
                "source_socket": edge.source_socket,
                "target_socket": edge.target_socket,
                "signature": _node_signature(graph, edge.source_node, frame, visiting),
            }
        )
    visiting.remove(node_id)
    if bypass_edge is not None:
        payload = {
            "id": node.id,
            "type": node.type,
            "disabled": True,
            "bypass_socket": bypass_edge.target_socket,
            "inputs": incoming,
        }
    else:
        payload = {
            "id": node.id,
            "type": node.type,
            "params": node.params,
            "source": _source_signature(node.type, node.params, frame),
            "inputs": incoming,
        }
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _signature_with_channel_demand(signature: str, demand: ChannelDemand | None) -> str:
    if demand is None:
        return signature
    payload = {"signature": signature, "read_channel_demand": demand.cache_key()}
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _viewer_input_edges(graph: ProjectGraph, node_id: str):
    node = graph.nodes[node_id]
    active_socket = str(node.params.get("active_input", "0"))
    active_edges = graph.incoming_edges(node_id, active_socket)
    if active_edges:
        return active_edges
    legacy_edges = graph.incoming_edges(node_id, "in")
    if legacy_edges:
        return legacy_edges
    return []


def _source_signature(node_type: str, params: dict, frame: int) -> dict[str, object] | None:
    if node_type.lower() != "read":
        return None
    return image_source_fingerprint(str(params.get("path") or "builtin://gradient"), frame)


def _estimate_image_bytes(image: ImageFrame) -> int:
    return int(image.data.nbytes + sum(channel.nbytes for channel in image.channel_data.values()))
