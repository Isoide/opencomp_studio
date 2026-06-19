from __future__ import annotations

from pathlib import Path

from opencomp.core.models import Edge, Node, Project, ProjectGraph, ProjectSettings, ScriptTab

REFERENCE_SEQUENCE_PATH = (
    r"E:\Windows-Shortcuts\Downloads\opencomp_studio_codex_docs\LAL_101_101_0010_####.exr"
)


def create_default_project() -> Project:
    read_path = REFERENCE_SEQUENCE_PATH
    if not Path(read_path.replace("####", "1001")).exists():
        read_path = "builtin://gradient"

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
    script_tab = ScriptTab(id="main", name="Comp 1", graph=graph, kind="comp")
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
