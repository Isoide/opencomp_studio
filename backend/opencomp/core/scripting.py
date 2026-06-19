from __future__ import annotations

import contextlib
import io
import re
import traceback as traceback_module
from dataclasses import dataclass
from typing import Any

from opencomp.core.models import Edge, Node, Project, ProjectGraph, ScriptTab
from opencomp.nodes import NODE_DEFINITIONS


NODE_TYPES = {definition.type.lower(): definition for definition in NODE_DEFINITIONS}
NODE_PARAM_ALIASES = {
    "file": "path",
    "first": "frame_start",
    "last": "frame_end",
    "first_frame": "frame_start",
    "last_frame": "frame_end",
}
ROOT_SETTING_ALIASES = {
    "name": "project_name",
    "first": "frame_start",
    "last": "frame_end",
    "first_frame": "frame_start",
    "last_frame": "frame_end",
}


@dataclass(slots=True)
class ScriptRunResult:
    success: bool
    stdout: str
    stderr: str
    error: str | None
    traceback: str | None
    changed: bool


def run_session_script(project: Project, code: str) -> ScriptRunResult:
    session = OpenCompSession(project)
    stdout = io.StringIO()
    stderr = io.StringIO()
    namespace = {
        "__name__": "__opencomp_script__",
        "opencomp": session,
        "root": session.root_node,
    }

    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(code, namespace, namespace)
    except Exception as exc:
        return ScriptRunResult(
            success=False,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback_module.format_exc(),
            changed=session.changed,
        )
    return ScriptRunResult(
        success=True,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
        error=None,
        traceback=None,
        changed=session.changed,
    )


class OpenCompSession:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.changed = False

    @property
    def root_node(self) -> "RootHandle":
        return RootHandle(self)

    @property
    def graph(self) -> ProjectGraph:
        return _active_script(self.project).graph

    def node(self, name: str) -> "NodeHandle | RootHandle":
        if name.lower() == "root":
            return self.root_node
        for node in self.graph.nodes.values():
            if node.id == name or node.name == name:
                return NodeHandle(self, node.id)
        inferred_type = _node_type_from_name(name)
        if inferred_type is not None:
            return self.create_node(inferred_type, name=name)
        raise KeyError(f"Unknown node: {name}")

    def create_node(
        self,
        node_type: str,
        name: str | None = None,
        position: tuple[float, float] | list[float] | None = None,
        **params: Any,
    ) -> "NodeHandle":
        definition = _node_definition(node_type)
        node_id = _unique_node_id(self.graph, name or definition.type)
        node = Node(
            id=node_id,
            type=definition.type,
            name=name or definition.type,
            position=tuple(position or _next_node_position(self.graph)),
            params=dict(params),
            outputs={output: "ImageFrame" for output in definition.outputs},
        )
        self.graph.nodes[node_id] = node
        self._mark_changed()
        return NodeHandle(self, node_id)

    def nodes(self, node_type: str | None = None) -> list["NodeHandle"]:
        if node_type is None:
            return [NodeHandle(self, node_id) for node_id in self.graph.nodes]
        normalized = node_type.lower()
        return [
            NodeHandle(self, node.id)
            for node in self.graph.nodes.values()
            if node.type.lower() == normalized
        ]

    def connect(
        self,
        source: str | "NodeHandle",
        target: str | "NodeHandle",
        input: str = "in",
        output: str = "out",
    ) -> None:
        source_node = self._node_id(source)
        target_node = self._node_id(target)
        self.graph.edges = [
            edge
            for edge in self.graph.edges
            if not (edge.target_node == target_node and edge.target_socket == str(input))
        ]
        self.graph.edges.append(
            Edge(
                id=_edge_id(source_node, target_node, str(input)),
                source_node=source_node,
                source_socket=str(output),
                target_node=target_node,
                target_socket=str(input),
            )
        )
        self._mark_changed()

    def disconnect(self, target: str | "NodeHandle", input: str = "in") -> None:
        target_node = self._node_id(target)
        before = len(self.graph.edges)
        self.graph.edges = [
            edge
            for edge in self.graph.edges
            if not (edge.target_node == target_node and edge.target_socket == str(input))
        ]
        if len(self.graph.edges) != before:
            self._mark_changed()

    def _node_id(self, node: str | "NodeHandle") -> str:
        if isinstance(node, NodeHandle):
            return node.id
        return self.node(node).id  # type: ignore[return-value]

    def _mark_changed(self) -> None:
        self.changed = True
        active_script = _active_script(self.project)
        active_script.graph = self.graph
        self.project.graph = active_script.graph


class RootHandle:
    def __init__(self, session: OpenCompSession) -> None:
        self._session = session
        self.id = "root"
        self.name = "root"
        self.type = "Root"

    def value(self, name: str) -> "ValueHandle":
        return ValueHandle(self._session, self, name)

    knob = value

    def __repr__(self) -> str:
        return "<RootHandle root>"


class NodeHandle:
    def __init__(self, session: OpenCompSession, node_id: str) -> None:
        self._session = session
        self.id = node_id

    @property
    def node(self) -> Node:
        return self._session.graph.nodes[self.id]

    @property
    def type(self) -> str:
        return self.node.type

    @property
    def name(self) -> str:
        return self.node.name or self.node.id

    def value(self, name: str) -> "ValueHandle":
        return ValueHandle(self._session, self, name)

    knob = value

    def setPosition(self, x: float, y: float) -> "NodeHandle":
        self.node.position = (float(x), float(y))
        self._session._mark_changed()
        return self

    def xpos(self) -> float:
        return float(self.node.position[0])

    def ypos(self) -> float:
        return float(self.node.position[1])

    def setInput(self, input: str | int, source: str | "NodeHandle", output: str = "out") -> "NodeHandle":
        self._session.connect(source, self, input=str(input), output=output)
        return self

    connectInput = setInput

    def delete(self) -> None:
        graph = self._session.graph
        graph.edges = [edge for edge in graph.edges if edge.source_node != self.id and edge.target_node != self.id]
        graph.nodes.pop(self.id, None)
        self._session._mark_changed()

    def __repr__(self) -> str:
        return f"<NodeHandle {self.id} type={self.type}>"


class ValueHandle:
    def __init__(self, session: OpenCompSession, owner: NodeHandle | RootHandle, name: str) -> None:
        self._session = session
        self._owner = owner
        self.name = name

    def getValue(self) -> Any:
        if isinstance(self._owner, RootHandle):
            return self._get_root_value()
        key = _node_param_key(self.name)
        if key == "name":
            return self._owner.node.name
        if key in {"xpos", "x"}:
            return self._owner.node.position[0]
        if key in {"ypos", "y"}:
            return self._owner.node.position[1]
        return self._owner.node.params.get(key)

    def setValue(self, value: Any) -> NodeHandle | RootHandle:
        if isinstance(self._owner, RootHandle):
            self._set_root_value(value)
            return self._owner

        key = _node_param_key(self.name)
        if key == "name":
            self._owner.node.name = str(value)
        elif key in {"xpos", "x"}:
            self._owner.node.position = (float(value), self._owner.node.position[1])
        elif key in {"ypos", "y"}:
            self._owner.node.position = (self._owner.node.position[0], float(value))
        else:
            self._owner.node.params[key] = value
        self._session._mark_changed()
        return self._owner

    def _get_root_value(self) -> Any:
        key = _root_key(self.name)
        if key == "project_name":
            return self._session.project.project_name
        if hasattr(self._session.project.settings, key):
            return getattr(self._session.project.settings, key)
        if hasattr(self._session.project.preferences, key):
            return getattr(self._session.project.preferences, key)
        return None

    def _set_root_value(self, value: Any) -> None:
        key = _root_key(self.name)
        if key == "project_name":
            self._session.project.project_name = str(value)
        elif hasattr(self._session.project.settings, key):
            setattr(self._session.project.settings, key, value)
        elif hasattr(self._session.project.preferences, key):
            setattr(self._session.project.preferences, key, value)
        else:
            raise KeyError(f"Unknown root value: {self.name}")
        self._session._mark_changed()

    def __repr__(self) -> str:
        return f"<ValueHandle {self._owner.id}.{self.name}={self.getValue()!r}>"


def _active_script(project: Project) -> ScriptTab:
    if not project.script_tabs:
        project.script_tabs.append(ScriptTab(id="main", name="Comp 1", graph=project.graph, kind="comp"))
        project.active_script_id = "main"
    for tab in project.script_tabs:
        if tab.id == project.active_script_id:
            project.graph = tab.graph
            return tab
    project.active_script_id = project.script_tabs[0].id
    project.graph = project.script_tabs[0].graph
    return project.script_tabs[0]


def _node_definition(node_type: str):
    definition = NODE_TYPES.get(node_type.lower())
    if definition is None:
        raise KeyError(f"Unknown node type: {node_type}")
    return definition


def _node_type_from_name(name: str) -> str | None:
    match = re.match(r"^([A-Za-z]+)\d+$", name)
    if not match:
        return None
    node_type = match.group(1)
    return NODE_TYPES[node_type.lower()].type if node_type.lower() in NODE_TYPES else None


def _node_param_key(name: str) -> str:
    return NODE_PARAM_ALIASES.get(name, name)


def _root_key(name: str) -> str:
    return ROOT_SETTING_ALIASES.get(name, name)


def _unique_node_id(graph: ProjectGraph, base: str) -> str:
    normalized = "".join(character for character in base if character.isalnum() or character == "_") or "Node"
    if normalized not in graph.nodes:
        return normalized
    suffix = 2
    while f"{normalized}{suffix}" in graph.nodes:
        suffix += 1
    return f"{normalized}{suffix}"


def _next_node_position(graph: ProjectGraph) -> tuple[float, float]:
    if not graph.nodes:
        return (120.0, 120.0)
    max_y = max(float(node.position[1]) for node in graph.nodes.values())
    return (120.0, max_y + 140.0)


def _edge_id(source: str, target: str, input_socket: str) -> str:
    return f"script-{source}-{target}-{input_socket}"
