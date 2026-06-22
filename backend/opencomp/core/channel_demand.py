from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from opencomp.core.models import ProjectGraph

BASE_READ_CHANNELS = ("RGBA", "R", "G", "B", "A")

_BASE_ALIASES = {
    "rgba",
    "rgb",
    "r",
    "g",
    "b",
    "a",
    "red",
    "green",
    "blue",
    "alpha",
    "rgba.red",
    "rgba.green",
    "rgba.blue",
    "rgba.alpha",
    "luma",
    "luminance",
}
_NONE_ALIASES = {"", "none", "disabled", "black", "white", "zero", "one", "0", "1"}
_VECTOR_COMPONENTS = {"x", "y", "w"}
_ALL_ALIASES = {"all", "*"}
_CHANNEL_PARAM_KEYS = {
    "channel",
    "channels",
    "channels2",
    "channels3",
    "channels4",
    "read_channels",
    "channels_to_load",
    "layer_copy",
    "mask",
    "mask_channel",
    "a_channel",
    "b_channel",
    "A",
    "B",
    "output",
    "from",
    "to",
    "input_layer",
    "in_layer",
    "output_layer",
    "out_layer",
    "a_channels",
    "b_channels",
    "Achannels",
    "Bchannels",
    "also_merge",
}
_CHANNEL_PREFIXES = ("from", "to", "out_")
_TOKEN_SPLIT = re.compile(r"[\s,;]+")


@dataclass(frozen=True, slots=True)
class ChannelDemand:
    channels: tuple[str, ...]
    load_all: bool = False

    def cache_key(self) -> str:
        if self.load_all:
            return "all"
        return ",".join(_normalized_channel_key(channel) for channel in self.channels)


def base_channel_demand() -> ChannelDemand:
    return ChannelDemand(_dedupe(BASE_READ_CHANNELS))


def channel_demand_for_graph(
    graph: ProjectGraph,
    target_node_id: str,
    requested_channel: str | None = None,
) -> ChannelDemand:
    channels: list[str] = list(BASE_READ_CHANNELS)
    load_all = False

    def add_token(value: object, *, allow_plain: bool = True) -> None:
        nonlocal load_all
        for token in _channel_tokens(value):
            normalized = _normalize_token(token)
            if normalized in _ALL_ALIASES:
                load_all = True
                continue
            channel = _channel_or_none(token, allow_plain=allow_plain)
            if channel is not None:
                channels.append(channel)

    add_token(requested_channel, allow_plain=True)

    for node_id in _upstream_node_ids(graph, target_node_id):
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        node_type = node.type.lower()
        if node_type == "cryptomatte":
            layer = str(node.params.get("layer") or "").strip()
            if layer:
                channels.extend([layer, f"{layer}*"])
            else:
                channels.append("*cryptomatte*")
        for key, value in node.params.items():
            if _is_channel_param_key(str(key)):
                add_token(value, allow_plain=True)

    if load_all:
        return ChannelDemand((), load_all=True)
    return ChannelDemand(_dedupe([*BASE_READ_CHANNELS, *channels]))


def _upstream_node_ids(graph: ProjectGraph, target_node_id: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    def walk(node_id: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        ordered.append(node_id)
        node = graph.nodes.get(node_id)
        if node is None:
            return
        input_edges = graph.incoming_edges(node_id)
        if node.type.lower() == "viewer":
            active_socket = str(node.params.get("active_input", "0"))
            active_edges = graph.incoming_edges(node_id, active_socket)
            input_edges = active_edges or graph.incoming_edges(node_id, "in")
        for edge in input_edges:
            walk(edge.source_node)

    walk(target_node_id)
    return ordered


def _is_channel_param_key(key: str) -> bool:
    if key in _CHANNEL_PARAM_KEYS:
        return True
    return key.startswith(_CHANNEL_PREFIXES)


def _channel_tokens(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_channel_tokens(item))
        return result
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(_channel_tokens(item))
        return result
    return [token for token in _TOKEN_SPLIT.split(str(value).strip()) if token]


def _channel_or_none(token: str, *, allow_plain: bool) -> str | None:
    value = _strip_input_prefix(token.strip())
    normalized = _normalize_token(value)
    if normalized in _NONE_ALIASES or normalized in _BASE_ALIASES:
        return None
    if normalized in _VECTOR_COMPONENTS:
        return None
    if not allow_plain and "." not in value and "*" not in value:
        return None
    return value


def _strip_input_prefix(value: str) -> str:
    if len(value) > 2 and value[1] in {".", ":"} and value[0].lower() in {"a", "b"}:
        return value[2:]
    return value


def _normalize_token(value: str) -> str:
    return _strip_input_prefix(value).strip().lower()


def _normalized_channel_key(value: str) -> str:
    return _normalize_token(value)


def _dedupe(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip()
        if not cleaned:
            continue
        key = _normalized_channel_key(cleaned)
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return tuple(result)
