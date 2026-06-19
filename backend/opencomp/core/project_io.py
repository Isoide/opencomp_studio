from __future__ import annotations

from pathlib import Path

from opencomp.core.models import Project, ScriptTab
from opencomp.io.nuke_exporter import export_nuke_script

OPENCOMP_EXTENSION = ".opencomp"


def with_opencomp_extension(path: str | Path) -> Path:
    resolved = Path(path).expanduser()
    if resolved.suffix.lower() == OPENCOMP_EXTENSION:
        return resolved
    return resolved.with_suffix(OPENCOMP_EXTENSION)


def resolve_project_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser()
    if resolved.exists() or resolved.suffix:
        return resolved
    return resolved.with_suffix(OPENCOMP_EXTENSION)


def ensure_project_scripts(project: Project) -> None:
    if not project.script_tabs:
        project.script_tabs.append(ScriptTab(id="main", name="Comp 1", graph=project.graph, kind="comp"))
        project.active_script_id = "main"
    if not any(tab.id == project.active_script_id for tab in project.script_tabs):
        project.active_script_id = project.script_tabs[0].id
    project.graph = get_active_script(project).graph


def get_active_script(project: Project) -> ScriptTab:
    if not project.script_tabs:
        project.script_tabs.append(ScriptTab(id="main", name="Comp 1", graph=project.graph, kind="comp"))
        project.active_script_id = "main"
    for tab in project.script_tabs:
        if tab.id == project.active_script_id:
            return tab
    project.active_script_id = project.script_tabs[0].id
    return project.script_tabs[0]


def normalize_project(project: Project) -> Project:
    ensure_project_scripts(project)
    return project


def normalize_project_for_serialization(project: Project) -> Project:
    if project.script_tabs:
        get_active_script(project).graph = project.graph
    ensure_project_scripts(project)
    return project


def save_project_file(project: Project, path: str | Path) -> Path:
    normalize_project_for_serialization(project)
    output_path = with_opencomp_extension(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    project.settings.project_path = str(output_path)
    output_path.write_text(project.model_dump_json(indent=2), encoding="utf-8")
    return output_path


def load_project_file(path: str | Path) -> Project:
    input_path = resolve_project_path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"OpenComp script not found: {input_path}")
    project = Project.model_validate_json(input_path.read_text(encoding="utf-8"))
    project.settings.project_path = str(input_path)
    return normalize_project(project)


def export_nuke_project(project: Project, path: str | Path | None = None) -> Path:
    normalize_project_for_serialization(project)
    output_path = _default_nuke_path(project, path)
    return export_nuke_script(project, output_path)


def export_nuke_placeholder(project: Project, path: str | Path | None = None) -> Path:
    return export_nuke_project(project, path)


def _default_nuke_path(project: Project, path: str | Path | None) -> Path:
    if path:
        resolved = Path(path).expanduser()
    elif project.settings.project_path:
        resolved = Path(project.settings.project_path).expanduser().with_suffix(".nk")
    else:
        resolved = Path(_safe_filename(project.project_name) or "opencomp_project").with_suffix(".nk")
    if resolved.suffix.lower() != ".nk":
        resolved = resolved.with_suffix(".nk")
    return resolved


def _safe_filename(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value).strip("_")
