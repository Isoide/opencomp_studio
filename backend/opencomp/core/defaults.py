"""Default project fixtures and startup graph construction for OpenComp.

This module defines the initial session graph used when the app boots without a
project file. It prefers a known reference sequence when available and falls
back to a builtin generator, keeping first-launch behavior stable across hosts.
"""

from __future__ import annotations

import os

from opencomp.core.models import Edge, Node, Project, ProjectGraph, ProjectSettings, ScriptTab
from opencomp.io.path_utils import path_exists

REFERENCE_SEQUENCE_ENV = "OPENCOMP_REFERENCE_SEQUENCE"

DEFAULT_PYTHON_SCRIPT = """node = opencomp.node("Read2")
node.value("path").setValue(r"<path>")
node.value("first_frame").setValue(1001)
node.value("last_frame").setValue(1010)
node.setPosition(280, 120)

root = opencomp.node("root")
root.value("name").setValue("test")
"""


def configured_reference_sequence_path() -> str | None:
    """Return the configured reference sequence path used for first-launch projects.

    Hosts may opt into a studio-local startup plate through
    ``OPENCOMP_REFERENCE_SEQUENCE``. When unset, OpenComp deliberately avoids
    shipping a hard-coded network path and falls back to builtin imagery.
    """

    override = os.environ.get(REFERENCE_SEQUENCE_ENV, "").strip()
    return override or None


def default_read_source_path(frame: int = 1001) -> str:
    """Return the default Read source, falling back to a builtin image when unavailable."""

    reference_path = configured_reference_sequence_path()
    if reference_path and path_exists(reference_path, frame):
        return reference_path
    return "builtin://gradient"


def create_default_project() -> Project:
    read_path = default_read_source_path()

    graph = ProjectGraph(
        nodes={
            "Read1": Node(
                id="Read1",
                type="Read",
                name="Read",
                position=(120, 80),
                params={
                    "path": read_path,
                    "colorspace": "ACES2065-1",
                    "localization_policy": "from auto-localize path",
                    "proxy": "",
                    "proxy_format": "root.proxy_format",
                    "frame_start": 1001,
                    "frame_end": 1010,
                    "before": "hold",
                    "after": "hold",
                    "frame_mode": "expression",
                    "frame": "frame",
                    "missing_frames": "error",
                    "input_transform": "default (linear)",
                    "premultiplied": False,
                    "raw_data": False,
                    "auto_alpha": False,
                    "edge_pixels": "plate detect",
                },
            ),
            "Grade1": Node(
                id="Grade1",
                type="Grade",
                name="Grade",
                position=(120, 220),
                params={"gain": 1.0, "offset": 0.0, "gamma": 1.0},
            ),
            "Viewer1": Node(
                id="Viewer1",
                type="Viewer",
                name="Viewer",
                position=(120, 380),
                params={"active_input": "0"},
            ),
        },
        edges=[
            Edge(id="e-read-grade", source_node="Read1", target_node="Grade1"),
            Edge(id="e-grade-viewer-0", source_node="Grade1", target_node="Viewer1", target_socket="0"),
        ],
    )
    script_tab = ScriptTab(id="main", name="Comp 1", graph=graph, code=DEFAULT_PYTHON_SCRIPT, kind="comp")
    return Project(
        project_name="OpenComp Studio Session",
        settings=ProjectSettings(
            frame_start=1001,
            frame_end=1010,
            working_colorspace="ACES2065-1",
            viewer_display=None,
            viewer_view=None,
        ),
        graph=graph,
        script_tabs=[script_tab],
        active_script_id=script_tab.id,
        plugin_menu=[
            {"label": "Run Python Script", "command": "python_script", "path": ""},
            {"label": "Reload Menu Plugins", "command": "reload_plugins"},
        ],
    )
