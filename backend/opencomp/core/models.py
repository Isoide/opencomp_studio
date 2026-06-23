from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from opencomp.core.bbox import normalize_bbox


class Edge(BaseModel):
    id: str
    source_node: str
    source_socket: str = "out"
    target_node: str
    target_socket: str = "in"


class ExpressionBinding(BaseModel):
    source: str = ""
    enabled: bool = True
    compiled_cache_key: str | None = None


class Node(BaseModel):
    id: str
    type: str
    name: str | None = None
    position: tuple[float, float] = (0.0, 0.0)
    params: dict[str, Any] = Field(default_factory=dict)
    param_expressions: dict[str, ExpressionBinding] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=lambda: {"out": "ImageFrame"})

    @field_validator("type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Node type cannot be empty.")
        return value.strip()


class ProjectGraph(BaseModel):
    nodes: dict[str, Node] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)

    def incoming_edges(self, node_id: str, socket: str | None = None) -> list[Edge]:
        edges = [edge for edge in self.edges if edge.target_node == node_id]
        if socket is not None:
            edges = [edge for edge in edges if edge.target_socket == socket]
        return edges


class ProjectSettings(BaseModel):
    fps: float = 24.0
    frame_start: int = 1001
    frame_end: int = 1001
    width: int = 1920
    height: int = 1080
    working_colorspace: str = "ACEScg"
    ocio_config: str | None = None
    viewer_display: str | None = None
    viewer_view: str | None = None
    proxy_enabled: bool = True
    viewer_max_width: int = 1280
    viewer_max_height: int = 720
    project_path: str | None = None
    default_output_path: str = "renders/output.####.png"
    cache_enabled: bool = True
    auto_refresh: bool = True
    tile_rendering_enabled: bool = True
    tile_height: int = 64
    tile_workers: int = 4
    render_workers: int = 4
    read_workers: int = 4
    viewer_tile_lanes: int = 3


class HotkeyPreferences(BaseModel):
    add_read: str = "r"
    add_write: str = "w"
    add_merge: str = "m"
    add_shuffle: str = "s"
    add_group: str = "g"
    toggle_disable: str = "d"
    refresh_viewer: str = "u"
    fit_viewer: str = "f"


class PathSubstitution(BaseModel):
    source: str = ""
    target: str = ""


class ProjectPreferences(BaseModel):
    autosave_seconds: int = 300
    idle_autosave_seconds: int = 5
    cache_memory_limit_mb: int = 1024
    viewer_zoom_speed: float = 1.1
    wheel_zoom_enabled: bool = True
    auto_connect_new_nodes: bool = True
    playback_transfer_mode: Literal["hybrid-preview", "always-float", "fast-display"] = "hybrid-preview"
    viewer_transfer_precision: Literal["float32", "float16", "rgb10a2", "uint8"] = "float16"
    read_preload_enabled: bool = True
    read_preload_max_frames: int = 6
    default_read_colorspace: str = "ACES2065-1"
    custom_init_scripts: list[str] = Field(default_factory=list)
    path_substitutions: list[PathSubstitution] = Field(default_factory=list)
    hotkeys: HotkeyPreferences = Field(default_factory=HotkeyPreferences)


class ScriptTab(BaseModel):
    id: str
    name: str
    graph: ProjectGraph = Field(default_factory=ProjectGraph)
    path: str | None = None
    startup_scripts: list[str] = Field(default_factory=list)
    kind: str = "comp"


class Project(BaseModel):
    schema_version: str = "0.1.0"
    project_name: str = "Untitled OpenComp Project"
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
    graph: ProjectGraph = Field(default_factory=ProjectGraph)
    script_tabs: list[ScriptTab] = Field(default_factory=list)
    active_script_id: str = "main"
    preferences: ProjectPreferences = Field(default_factory=ProjectPreferences)
    plugin_menu: list[dict[str, Any]] = Field(default_factory=list)
    startup_scripts: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class ImageFrame:
    width: int
    height: int
    data: np.ndarray
    channels: list[str] = field(default_factory=lambda: ["R", "G", "B", "A"])
    channel_data: dict[str, np.ndarray] = field(default_factory=dict)
    pixel_aspect: float = 1.0
    colorspace: str = "scene-linear"
    frame: int = 1001
    metadata: dict[str, Any] = field(default_factory=dict)
    format_bbox: dict[str, int] | None = None
    data_window: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.data.ndim != 3 or self.data.shape[2] != 4:
            raise ValueError("ImageFrame data must have shape H x W x 4.")
        if self.data.dtype != np.float32:
            self.data = self.data.astype(np.float32, copy=False)
        self.data = np.ascontiguousarray(self.data)
        self.height, self.width = int(self.data.shape[0]), int(self.data.shape[1])
        self.pixel_aspect = float(self.pixel_aspect or 1.0)
        self.format_bbox = normalize_bbox(self.format_bbox, self.width, self.height)
        self.data_window = normalize_bbox(self.data_window, self.width, self.height)
        self.channel_data = {
            name: np.ascontiguousarray(np.asarray(value, dtype=np.float32))
            for name, value in self.channel_data.items()
            if np.asarray(value).shape[:2] == (self.height, self.width)
        }

    def copy_channel_data(self) -> dict[str, np.ndarray]:
        return {name: value.copy() for name, value in self.channel_data.items()}


@dataclass(frozen=True, slots=True)
class TileWindow:
    x: int
    y: int
    width: int
    height: int


class ViewerViewport(BaseModel):
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0


class FrameROI(BaseModel):
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


class FrameRequest(BaseModel):
    node_id: str
    frame: int = 1001
    display: str | None = None
    view: str | None = None
    channel: str | None = None
    viewer_input: str | None = None
    compare_input: str | None = None
    compare_mode: Literal["none", "difference"] = "none"
    gain: float = 1.0
    saturation: float = 1.0
    fstop: float = 0.0
    precision: Literal["float32", "float16", "rgb10a2", "uint8"] = "float32"
    stream_tiles: bool = False
    transfer_mode: Literal[
        "float32-rgba",
        "float16-rgba",
        "float16-rgb",
        "single-channel-float16",
        "rgb10a2",
        "uint8-rgba",
        "display-preview",
    ] = "float16-rgba"
    viewport: ViewerViewport | None = None
    zoom: float | None = None
    tile_width: int | None = None
    tile_height: int | None = None
    tile_lanes: int | None = None
    tile_lane: int | None = None
    request_id: str | None = None
    roi: FrameROI | None = None
    render_scale: float = 1.0
    mipmap_level: int = 0
    channels: list[str] = Field(default_factory=list)
    layers: list[str] = Field(default_factory=list)
    storage: Literal["ram", "gpu", "frontend", "disk"] = "frontend"
    priority: Literal["interactive", "playback", "background", "render"] = "interactive"
    cache_policy: Literal["read-through", "refresh", "bypass", "write-through"] = "read-through"
    cancel_before: str | None = None


class ViewerWarmRequest(BaseModel):
    node_id: str
    frames: list[int] = Field(default_factory=list)
    viewer_input: str | None = None
    display: str | None = None
    view: str | None = None
    channel: str | None = None


class ReadWarmRequest(BaseModel):
    node_id: str
    frames: list[int] = Field(default_factory=list)
    viewer_input: str | None = None
    channel: str | None = None


class CryptomattePickRequest(BaseModel):
    node_id: str
    frame: int = 1001
    layer: str | None = None
    x: int
    y: int


class CryptomatteMatteRequest(BaseModel):
    node_id: str
    frame: int = 1001
    layer: str | None = None
    matte_ids: list[str] = Field(default_factory=list)
    max_width: int | None = None
    max_height: int | None = None


class GraphUpdate(BaseModel):
    graph: ProjectGraph


class ProjectSettingsUpdate(BaseModel):
    settings: ProjectSettings


class ProjectPreferencesUpdate(BaseModel):
    preferences: ProjectPreferences


class CreateScriptTabRequest(BaseModel):
    name: str = "Comp"
    kind: str = "comp"


class SetActiveScriptTabRequest(BaseModel):
    script_id: str


class RenameScriptTabRequest(BaseModel):
    name: str


class SaveProjectRequest(BaseModel):
    path: str | None = None
    project: Project | None = None


class LoadProjectRequest(BaseModel):
    path: str


class ImportProjectRequest(BaseModel):
    project: Project


class ExportNukeRequest(BaseModel):
    path: str | None = None
    project: Project | None = None


class PythonScriptRequest(BaseModel):
    code: str


class PythonScriptResponse(BaseModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    traceback: str | None = None
    changed: bool = False
    project: Project


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    app: str = "OpenComp Studio"


class NodeCatalogItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    label: str
    category: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=lambda: ["out"])
