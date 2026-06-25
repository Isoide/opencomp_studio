"""Project, graph, script, and scripting routes for the OpenComp backend.

This module owns the session-management side of the API: project lifecycle,
graph updates, script-tab management, and backend Python execution. Keeping
these routes separate from viewer/render transport makes the main API surface
easier to scan and test.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response

from opencomp.api.app_state import bump_graph_revision, ensure_runtime_state
from opencomp.api.context import (
    clear_runtime_errors,
    get_active_graph,
    ensure_script_tabs,
    get_active_script,
    get_project,
    install_project_in_state,
)
from opencomp.core.defaults import DEFAULT_PYTHON_SCRIPT, create_default_project
from opencomp.core.models import (
    CreateScriptTabRequest,
    ExportNukeRequest,
    GraphUpdate,
    ImportProjectRequest,
    LoadProjectRequest,
    Project,
    ProjectPreferencesUpdate,
    ProjectSettingsUpdate,
    PythonScriptRequest,
    PythonScriptResponse,
    RenameScriptTabRequest,
    SaveProjectRequest,
    ScriptTab,
    SetActiveScriptTabRequest,
)
from opencomp.core.project_io import (
    export_nuke_project,
    load_project_file,
    normalize_project_for_serialization,
    save_project_file,
)
from opencomp.core.scripting import run_session_script
from opencomp.io.nuke_exporter import build_nuke_script

router = APIRouter()


@router.post("/api/projects/new", response_model=Project)
async def new_project(request: Request) -> Project:
    project = create_default_project()
    return install_project_in_state(request.app.state, project)


@router.get("/api/projects/current", response_model=Project)
async def current_project(request: Request) -> Project:
    project = get_project(request)
    ensure_script_tabs(project)
    return project


@router.post("/api/projects/save", response_model=Project)
async def save_project(request: Request, payload: SaveProjectRequest) -> Project:
    project = payload.project or get_project(request)
    normalize_project_for_serialization(project)
    ensure_runtime_state(request.app.state).project = project
    if payload.path:
        save_project_file(project, payload.path)
    return project


@router.post("/api/projects/load", response_model=Project)
async def load_project(request: Request, payload: LoadProjectRequest) -> Project:
    try:
        project = await asyncio.to_thread(load_project_file, payload.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return install_project_in_state(request.app.state, project)


@router.post("/api/projects/import", response_model=Project)
async def import_project(request: Request, payload: ImportProjectRequest) -> Project:
    return install_project_in_state(request.app.state, payload.project)


@router.post("/api/projects/export-nuke")
async def export_nuke(request: Request, payload: ExportNukeRequest):
    project = payload.project or get_project(request)
    ensure_script_tabs(project)
    try:
        output_path = await asyncio.to_thread(export_nuke_project, project, payload.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "exported",
        "path": str(output_path),
        "message": "Nuke .nk export written with v1 OpenComp node mappings.",
    }


@router.post("/api/projects/export-nuke/content")
async def export_nuke_content(request: Request, payload: ExportNukeRequest):
    project = payload.project or get_project(request)
    normalize_project_for_serialization(project)
    try:
        nuke_text = await asyncio.to_thread(build_nuke_script, project, payload.path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=nuke_text, media_type="text/plain; charset=utf-8")


@router.get("/api/projects/settings")
async def get_project_settings(request: Request):
    return get_project(request).settings


@router.put("/api/projects/settings")
async def put_project_settings(request: Request, payload: ProjectSettingsUpdate):
    project = get_project(request)
    ensure_script_tabs(project)
    project.settings = payload.settings
    ensure_runtime_state(request.app.state).project = project
    clear_runtime_errors(request.app.state)
    return project.settings


@router.get("/api/projects/preferences")
async def get_project_preferences(request: Request):
    return get_project(request).preferences


@router.put("/api/projects/preferences")
async def put_project_preferences(request: Request, payload: ProjectPreferencesUpdate):
    project = get_project(request)
    project.preferences = payload.preferences
    ensure_runtime_state(request.app.state).project = project
    return project.preferences


@router.get("/api/graph")
async def get_graph(request: Request):
    project = get_project(request)
    ensure_script_tabs(project)
    return get_active_graph(project)


@router.put("/api/graph")
async def put_graph(request: Request, payload: GraphUpdate):
    project = get_project(request)
    ensure_script_tabs(project)
    active_script = get_active_script(project)
    active_script.graph = payload.graph
    project.graph = payload.graph
    ensure_runtime_state(request.app.state).project = project
    clear_runtime_errors(request.app.state)
    bump_graph_revision(request.app.state)
    return project.graph


@router.get("/api/scripts", response_model=list[ScriptTab])
async def get_scripts(request: Request) -> list[ScriptTab]:
    project = get_project(request)
    ensure_script_tabs(project)
    return project.script_tabs


@router.post("/api/scripts", response_model=Project)
async def create_script(request: Request, payload: CreateScriptTabRequest) -> Project:
    project = get_project(request)
    ensure_script_tabs(project)
    index = len(project.script_tabs) + 1
    script_name = payload.name or f"Comp {index}"
    base_id = "".join(character if character.isalnum() else "-" for character in script_name.lower()).strip("-") or "comp"
    script_id = base_id
    existing_ids = {tab.id for tab in project.script_tabs}
    suffix = 2
    while script_id in existing_ids:
        script_id = f"{base_id}-{suffix}"
        suffix += 1
    script_graph = create_default_project().graph
    script_code = DEFAULT_PYTHON_SCRIPT if payload.kind == "comp" else ""
    script = ScriptTab(id=script_id, name=script_name, graph=script_graph, code=script_code, kind=payload.kind)
    project.script_tabs.append(script)
    project.active_script_id = script.id
    project.graph = script.graph
    ensure_runtime_state(request.app.state).project = project
    clear_runtime_errors(request.app.state)
    bump_graph_revision(request.app.state)
    return project


@router.put("/api/scripts/active", response_model=Project)
async def set_active_script(request: Request, payload: SetActiveScriptTabRequest) -> Project:
    project = get_project(request)
    ensure_script_tabs(project)
    if not any(tab.id == payload.script_id for tab in project.script_tabs):
        raise HTTPException(status_code=404, detail=f"Unknown script tab: {payload.script_id}")
    project.active_script_id = payload.script_id
    project.graph = get_active_graph(project)
    ensure_runtime_state(request.app.state).project = project
    clear_runtime_errors(request.app.state)
    bump_graph_revision(request.app.state)
    return project


@router.patch("/api/scripts/{script_id}", response_model=Project)
async def rename_script(request: Request, script_id: str, payload: RenameScriptTabRequest) -> Project:
    project = get_project(request)
    ensure_script_tabs(project)
    for tab in project.script_tabs:
        if tab.id == script_id:
            tab.name = payload.name
            ensure_runtime_state(request.app.state).project = project
            return project
    raise HTTPException(status_code=404, detail=f"Unknown script tab: {script_id}")


@router.post("/api/python/run", response_model=PythonScriptResponse)
async def run_python(request: Request, payload: PythonScriptRequest) -> PythonScriptResponse:
    project = get_project(request)
    ensure_script_tabs(project)
    active_script = get_active_script(project)
    active_script.code = payload.code
    result = await asyncio.to_thread(run_session_script, project, payload.code)
    ensure_script_tabs(project)
    ensure_runtime_state(request.app.state).project = project
    if result.changed:
        bump_graph_revision(request.app.state)
    return PythonScriptResponse(
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
        error=result.error,
        traceback=result.traceback,
        changed=result.changed,
        project=project,
    )
