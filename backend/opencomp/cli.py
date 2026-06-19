from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from opencomp.core.defaults import create_default_project
from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Project, ProjectGraph
from opencomp.core.project_io import (
    export_nuke_project,
    get_active_script,
    load_project_file,
    save_project_file,
)
from opencomp.core.scripting import run_session_script


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        project = _load_or_create_project(args)
        _set_active_script(project, args.active_script)
        _run_scripts(project, args.run_script, args.eval_code)
        _apply_overrides(project, args.set_value)

        output: dict[str, Any] = {
            "project": _project_summary(project),
            "actions": [],
        }

        if args.validate:
            _validate_project(project)
            output["actions"].append({"type": "validate", "status": "ok"})

        if args.list_scripts:
            output["actions"].append({"type": "scripts", "scripts": _script_summaries(project)})

        if args.list_nodes:
            output["actions"].append({"type": "nodes", "nodes": _node_summaries(get_active_script(project).graph)})

        frames = parse_frame_range(args.frame_range, project.settings.frame_start, project.settings.frame_end)

        if args.metadata:
            output["actions"].append(
                {
                    "type": "metadata",
                    "node": args.metadata,
                    "frame": frames[0],
                    "metadata": _evaluate_metadata(project, args.metadata, frames[0], args),
                }
            )

        render_nodes = _render_node_list(project, args.render_nodes, args.render_all_writes)
        if render_nodes:
            output["actions"].append({"type": "render", "frames": frames, "results": _render(project, render_nodes, frames, args)})

        if args.export_nuke is not None:
            export_path = export_nuke_project(project, args.export_nuke or None)
            output["actions"].append(
                {
                    "type": "export-nuke",
                    "status": "exported",
                    "path": str(export_path),
                    "message": "Nuke .nk export written with v1 OpenComp node mappings.",
                }
            )

        if args.save is not None:
            save_path = args.save or project.settings.project_path or args.project
            if not save_path:
                raise ValueError("Use --save PATH when running without an existing project path.")
            saved_path = save_project_file(project, save_path)
            output["actions"].append({"type": "save", "path": str(saved_path)})

        if not output["actions"]:
            output["actions"].append({"type": "summary", "nodes": _node_summaries(get_active_script(project).graph)})

        _print_output(output, args.json_output)
        return 0
    except Exception as exc:
        if args.json_output:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2), file=sys.stderr)
        else:
            print(f"opencomp: error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencomp",
        description="Headless OpenComp Studio project, script, and render CLI.",
    )
    parser.add_argument("project", nargs="?", help="Path to a .opencomp project/script file.")
    parser.add_argument("--new", action="store_true", help="Start from the default project instead of loading a file.")
    parser.add_argument("--save", nargs="?", const="", help="Save the project as .opencomp, optionally to PATH.")
    parser.add_argument("--active-script", help="Script tab id to operate on before rendering or listing.")
    parser.add_argument("--list-nodes", action="store_true", help="List nodes in the active script graph.")
    parser.add_argument("--list-scripts", action="store_true", help="List script tabs stored in the project.")
    parser.add_argument("--validate", action="store_true", help="Validate that the active graph references known render nodes.")
    parser.add_argument("--run-script", action="append", default=[], help="Run a Python script file against the project.")
    parser.add_argument("--eval", dest="eval_code", action="append", default=[], help="Run inline Python against the project.")
    parser.add_argument(
        "--set",
        dest="set_value",
        action="append",
        default=[],
        metavar="TARGET=VALUE",
        help="Set project or node values, e.g. frame_start=1001 or Read1.path=plate.####.exr.",
    )
    parser.add_argument("--render", dest="render_nodes", help="Comma-separated Write node ids/names to render.")
    parser.add_argument("--render-all-writes", action="store_true", help="Render every Write node in the active graph.")
    parser.add_argument(
        "--range",
        dest="frame_range",
        help="Frame range: 1001, 1001-1005, 1001,1003,1005, or 1001-1010x2.",
    )
    parser.add_argument("--metadata", help="Evaluate one node and print metadata for the first requested frame.")
    parser.add_argument(
        "--export-nuke",
        nargs="?",
        const="",
        help="Write a v1 Nuke .nk export, optionally to PATH.",
    )
    parser.add_argument("--cache-mb", type=int, default=None, help="Node cache size in MB for this CLI run.")
    parser.add_argument("--no-cache", action="store_true", help="Disable in-memory node cache for this CLI run.")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print machine-readable JSON.")
    return parser


def parse_frame_range(spec: str | None, default_start: int, default_end: int) -> list[int]:
    if not spec:
        return [int(default_start)]
    frames: list[int] = []
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" not in part:
            frames.append(int(part))
            continue
        range_part, step_part = (part.split("x", 1) + ["1"])[:2] if "x" in part else (part, "1")
        start_text, end_text = range_part.split("-", 1)
        start = int(start_text)
        end = int(end_text)
        step = max(1, abs(int(step_part)))
        direction = 1 if end >= start else -1
        stop = end + direction
        frames.extend(range(start, stop, step * direction))
    if not frames:
        raise ValueError("Frame range did not contain any frames.")
    return frames


def _load_or_create_project(args: argparse.Namespace) -> Project:
    if args.new:
        return create_default_project()
    if not args.project:
        raise ValueError("Provide a .opencomp project path, or use --new.")
    return load_project_file(args.project)


def _set_active_script(project: Project, script_id: str | None) -> None:
    if not script_id:
        return
    if not any(tab.id == script_id for tab in project.script_tabs):
        raise KeyError(f"Unknown script tab: {script_id}")
    project.active_script_id = script_id
    project.graph = get_active_script(project).graph


def _run_scripts(project: Project, script_paths: Iterable[str], eval_blocks: Iterable[str]) -> None:
    for script_path in script_paths:
        code = Path(script_path).expanduser().read_text(encoding="utf-8")
        _run_script_block(project, code, script_path)
    for index, code in enumerate(eval_blocks, start=1):
        _run_script_block(project, code, f"--eval #{index}")


def _run_script_block(project: Project, code: str, label: str) -> None:
    result = run_session_script(project, code)
    if not result.success:
        raise RuntimeError(f"Python script failed ({label}): {result.error}\n{result.traceback or ''}".rstrip())


def _apply_overrides(project: Project, overrides: Iterable[str]) -> None:
    graph = get_active_script(project).graph
    for override in overrides:
        if "=" not in override:
            raise ValueError(f"Invalid --set value, expected KEY=VALUE: {override}")
        target, raw_value = override.split("=", 1)
        target = target.strip()
        value = _parse_scalar(raw_value.strip())
        if "." in target:
            node_id, param_name = target.split(".", 1)
            node = _resolve_node(graph, node_id)
            if param_name in {"name", "label"}:
                node.name = str(value)
            elif param_name in {"x", "xpos"}:
                node.position = (float(value), node.position[1])
            elif param_name in {"y", "ypos"}:
                node.position = (node.position[0], float(value))
            else:
                node.params[param_name] = value
            continue
        if target == "project_name":
            project.project_name = str(value)
        elif hasattr(project.settings, target):
            setattr(project.settings, target, value)
        elif hasattr(project.preferences, target):
            setattr(project.preferences, target, value)
        else:
            raise KeyError(f"Unknown project setting or node parameter target: {target}")


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _validate_project(project: Project) -> None:
    graph = get_active_script(project).graph
    missing_sources = [edge.source_node for edge in graph.edges if edge.source_node not in graph.nodes]
    missing_targets = [edge.target_node for edge in graph.edges if edge.target_node not in graph.nodes]
    if missing_sources or missing_targets:
        raise ValueError(f"Graph has dangling edges: sources={missing_sources}, targets={missing_targets}")


def _render_node_list(project: Project, render_nodes: str | None, render_all_writes: bool) -> list[str]:
    graph = get_active_script(project).graph
    if render_all_writes:
        return [node.id for node in graph.nodes.values() if node.type.lower() == "write"]
    if not render_nodes:
        return []
    return [_resolve_node(graph, item.strip()).id for item in render_nodes.split(",") if item.strip()]


def _render(project: Project, node_ids: list[str], frames: list[int], args: argparse.Namespace) -> list[dict[str, Any]]:
    graph = get_active_script(project).graph
    evaluator = _make_evaluator(project, args)
    results: list[dict[str, Any]] = []
    for node_id in node_ids:
        node = _resolve_node(graph, node_id)
        if node.type.lower() != "write":
            raise ValueError(f"CLI rendering expects Write nodes, got {node.id} ({node.type}).")
        for frame in frames:
            started = time.perf_counter()
            image = evaluator.evaluate_node(graph, node.id, int(frame))
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            results.append(
                {
                    "node": node.id,
                    "frame": int(frame),
                    "elapsed_ms": elapsed_ms,
                    "output": image.metadata.get("write/filename"),
                    "skipped": image.metadata.get("write/skipped"),
                    "width": image.width,
                    "height": image.height,
                }
            )
    return results


def _evaluate_metadata(project: Project, node_id: str, frame: int, args: argparse.Namespace) -> dict[str, Any]:
    graph = get_active_script(project).graph
    node = _resolve_node(graph, node_id)
    image = _make_evaluator(project, args).evaluate_node(graph, node.id, int(frame))
    return {
        "width": image.width,
        "height": image.height,
        "channels": image.channels,
        "pixel_aspect": image.pixel_aspect,
        "colorspace": image.colorspace,
        "format_bbox": image.format_bbox,
        "data_window": image.data_window,
        "metadata": image.metadata,
    }


def _make_evaluator(project: Project, args: argparse.Namespace) -> GraphEvaluator:
    cache_mb = 0 if args.no_cache else args.cache_mb
    if cache_mb is None:
        cache_mb = project.preferences.cache_memory_limit_mb
    cache_bytes = max(0, int(cache_mb)) * 1024 * 1024
    return GraphEvaluator(
        settings=project.settings,
        max_cache_bytes=cache_bytes,
        max_preview_cache_bytes=0,
        max_float_preview_cache_bytes=0,
    )


def _resolve_node(graph: ProjectGraph, node_id_or_name: str):
    if node_id_or_name in graph.nodes:
        return graph.nodes[node_id_or_name]
    for node in graph.nodes.values():
        if node.name == node_id_or_name:
            return node
    raise KeyError(f"Unknown node: {node_id_or_name}")


def _project_summary(project: Project) -> dict[str, Any]:
    return {
        "name": project.project_name,
        "path": project.settings.project_path,
        "active_script": project.active_script_id,
        "frame_start": project.settings.frame_start,
        "frame_end": project.settings.frame_end,
        "fps": project.settings.fps,
    }


def _script_summaries(project: Project) -> list[dict[str, Any]]:
    return [
        {
            "id": tab.id,
            "name": tab.name,
            "kind": tab.kind,
            "nodes": len(tab.graph.nodes),
            "edges": len(tab.graph.edges),
            "active": tab.id == project.active_script_id,
        }
        for tab in project.script_tabs
    ]


def _node_summaries(graph: ProjectGraph) -> list[dict[str, Any]]:
    return [
        {
            "id": node.id,
            "name": node.name,
            "type": node.type,
            "position": node.position,
            "params": node.params,
            "inputs": {
                edge.target_socket: edge.source_node
                for edge in graph.edges
                if edge.target_node == node.id
            },
        }
        for node in graph.nodes.values()
    ]


def _print_output(output: dict[str, Any], json_output: bool) -> None:
    if json_output:
        print(json.dumps(_json_safe(output), indent=2))
        return
    project = output["project"]
    print(f"OpenComp project: {project['name']} [{project['active_script']}]")
    print(f"Frames: {project['frame_start']}-{project['frame_end']} @ {project['fps']} fps")
    for action in output["actions"]:
        action_type = action["type"]
        if action_type == "nodes":
            for node in action["nodes"]:
                print(f"{node['id']:<18} {node['type']:<14} {node['name'] or ''}")
        elif action_type == "scripts":
            for script in action["scripts"]:
                active = "*" if script["active"] else " "
                print(f"{active} {script['id']:<16} {script['name']} ({script['nodes']} nodes)")
        elif action_type == "render":
            for result in action["results"]:
                output_path = result["output"] or result["skipped"] or "<no output>"
                print(f"render {result['node']} frame {result['frame']}: {result['elapsed_ms']:.1f}ms -> {output_path}")
        elif action_type == "metadata":
            metadata = action["metadata"]
            print(
                f"metadata {action['node']} frame {action['frame']}: "
                f"{metadata['width']}x{metadata['height']} {metadata['channels']}"
            )
            print(json.dumps(_json_safe(metadata["metadata"]), indent=2))
        elif action_type == "save":
            print(f"saved {action['path']}")
        elif action_type == "export-nuke":
            print(f"exported {action['path']}")
        elif action_type == "validate":
            print("project validation ok")
        elif action_type == "summary":
            print(f"{len(action['nodes'])} nodes")


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
