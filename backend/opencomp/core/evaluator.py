from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

import numpy as np

from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.core.models import ImageFrame, ProjectGraph, ProjectSettings
from opencomp.io.image_reader import image_source_fingerprint
from opencomp.nodes import NODE_REGISTRY
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError

CacheKey = tuple[str, int, str]
PreviewCacheKey = tuple[str, int, str, str, str, str, int | None, int | None, str | None]
FloatPreviewCacheKey = tuple[str, int, str, str, int | None, int | None]


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
    cache_hits: int = 0
    cache_misses: int = 0
    preview_cache_hits: int = 0
    preview_cache_misses: int = 0
    float_preview_cache_hits: int = 0
    float_preview_cache_misses: int = 0
    cache_memory_bytes: int = 0
    preview_cache_memory_bytes: int = 0
    float_preview_cache_memory_bytes: int = 0
    active_nodes: set[str] = field(default_factory=set)
    active_node_counts: dict[str, int] = field(default_factory=dict)
    node_timings: dict[str, dict[str, Any]] = field(default_factory=dict)
    preview_timings: dict[str, dict[str, Any]] = field(default_factory=dict)
    phase_timings: list[dict[str, Any]] = field(default_factory=list)
    request_timings: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        if self.ocio is None:
            self.ocio = OCIOColorEngine(self.settings.ocio_config)

    def evaluate_node(self, graph: ProjectGraph, node_id: str, frame: int) -> ImageFrame:
        if node_id not in graph.nodes:
            raise KeyError(f"Graph does not contain node '{node_id}'.")
        return self._evaluate(graph, node_id, frame, visiting=set())

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
        self.cache_misses += 1

        node = graph.nodes[node_id]
        operation = NODE_REGISTRY.get(node.type.lower())
        if operation is None:
            raise UnknownNodeTypeError(f"Unsupported node type '{node.type}' on node '{node_id}'.")

        self._begin_node(node.id)
        started = time.perf_counter()
        visiting.add(node_id)
        try:
            input_edges = graph.incoming_edges(node_id)
            if node.type.lower() == "viewer":
                input_edges = _viewer_input_edges(graph, node_id)

            inputs: dict[str, ImageFrame] = {}
            for edge in input_edges:
                inputs[edge.target_socket] = self._evaluate(graph, edge.source_node, frame, visiting)
            visiting.remove(node_id)

            context = EvaluationContext(
                frame=frame,
                settings=self.settings,
                ocio=self.ocio,
                metrics=lambda metric_node_id, phase, duration_ms, details=None: self.record_phase_timing(
                    metric_node_id,
                    phase,
                    duration_ms,
                    details,
                ),
            )
            result = operation.evaluate(node, inputs, context)
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

    def clear_cache(self) -> None:
        with self._lock:
            self.cache.clear()
            self.preview_cache.clear()
            self.float_preview_cache.clear()
            self.cache_hits = 0
            self.cache_misses = 0
            self.preview_cache_hits = 0
            self.preview_cache_misses = 0
            self.float_preview_cache_hits = 0
            self.float_preview_cache_misses = 0
            self.cache_memory_bytes = 0
            self.preview_cache_memory_bytes = 0
            self.float_preview_cache_memory_bytes = 0
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
            self.max_preview_cache_bytes = max(0, int(max_preview_cache_bytes))
            self.max_float_preview_cache_bytes = max(
                0,
                int(max_float_preview_cache_bytes if max_float_preview_cache_bytes is not None else max_preview_cache_bytes),
            )
            self._prune_cache()

    def node_signature(self, graph: ProjectGraph, node_id: str, frame: int) -> str:
        return _node_signature(graph, node_id, frame, visiting=set())

    def output_signature(self, graph: ProjectGraph, node_id: str, frame: int) -> str:
        node = graph.nodes[node_id]
        if node.type.lower() == "viewer":
            input_edges = _viewer_input_edges(graph, node_id)
            if input_edges:
                return self.node_signature(graph, input_edges[0].source_node, frame)
        return self.node_signature(graph, node_id, frame)

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
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "preview_hits": self.preview_cache_hits,
                "preview_misses": self.preview_cache_misses,
                "float_preview_hits": self.float_preview_cache_hits,
                "float_preview_misses": self.float_preview_cache_misses,
                "memory_bytes": self.cache_memory_bytes,
                "preview_memory_bytes": self.preview_cache_memory_bytes,
                "float_preview_memory_bytes": self.float_preview_cache_memory_bytes,
                "max_memory_bytes": self.max_cache_bytes,
                "max_preview_memory_bytes": self.max_preview_cache_bytes,
                "max_float_preview_memory_bytes": self.max_float_preview_cache_bytes,
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

    def _prune_cache(self) -> None:
        if self.max_cache_bytes <= 0:
            self.cache.clear()
            self.cache_memory_bytes = 0
        while self.max_cache_bytes > 0 and self.cache_memory_bytes > self.max_cache_bytes and self.cache:
            _key, entry = self.cache.popitem(last=False)
            self.cache_memory_bytes -= entry.bytes
        self._prune_preview_cache()
        self._prune_float_preview_cache()

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
    payload = {
        "id": node.id,
        "type": node.type,
        "params": node.params,
        "source": _source_signature(node.type, node.params, frame),
        "inputs": incoming,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


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
