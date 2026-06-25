"""Shared backend API context and service access helpers.

This module keeps route handlers small by centralizing how the API reaches the
current project, evaluator, scheduler, active script, and common JSON/error
shaping helpers. It is intentionally transport-adjacent so multiple route
modules can share one consistent backend access pattern.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from opencomp.api.app_state import (
    ensure_runtime_state,
    install_runtime_state,
    viewer_preview_cache_bytes,
)
from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Project, ProjectGraph, ScriptTab
from opencomp.core.project_io import ensure_project_scripts, get_active_script as project_io_get_active_script
from opencomp.core.render_scheduler import RenderScheduler
from opencomp.nodes.base import NodeEvaluationError


def get_project_from_state(state: Any) -> Project:
    """Return the current project stored on the shared app runtime."""

    return ensure_runtime_state(state).project


def get_project(request: Request) -> Project:
    """Return the current project for a request."""

    return get_project_from_state(request.app.state)


def ensure_script_tabs(project: Project) -> None:
    """Ensure the project exposes a stable script-tab structure."""

    ensure_project_scripts(project)


def get_active_script(project: Project) -> ScriptTab:
    """Return the active script tab for a project."""

    return project_io_get_active_script(project)


def get_active_graph(project: Project) -> ProjectGraph:
    """Return the active graph for a project without repeating script traversal."""

    return get_active_script(project).graph


def resolved_frame_number(project: Project, frame: int | None = None) -> int:
    """Return an explicit frame number, defaulting to the project start frame."""

    return frame if frame is not None else project.settings.frame_start


def get_evaluator_from_state(state: Any, project: Project) -> GraphEvaluator:
    """Return a cache-budget-aware evaluator matching the current project settings."""

    runtime = ensure_runtime_state(state)
    settings_key = project.settings.model_dump_json()
    max_cache_bytes = max(0, int(project.preferences.cache_memory_limit_mb)) * 1024 * 1024
    max_preview_cache_bytes = viewer_preview_cache_bytes(max_cache_bytes)
    if runtime.evaluator_settings_key != settings_key:
        runtime.evaluator = GraphEvaluator(
            settings=project.settings,
            max_cache_bytes=max_cache_bytes,
            max_preview_cache_bytes=max_preview_cache_bytes,
            max_float_preview_cache_bytes=max_preview_cache_bytes,
        )
        runtime.evaluator_settings_key = settings_key
    else:
        runtime.evaluator.set_cache_limits(max_cache_bytes, max_preview_cache_bytes)
    return runtime.evaluator


def get_evaluator(request: Request, project: Project) -> GraphEvaluator:
    """Return the current evaluator for a request/project pair."""

    return get_evaluator_from_state(request.app.state, project)


def get_render_scheduler_from_state(state: Any) -> RenderScheduler:
    """Return the shared render scheduler for the backend app."""

    return ensure_runtime_state(state).render_scheduler


def install_project_in_state(state: Any, project: Project) -> Project:
    """Replace the runtime project and rebuild request-shared services for it."""

    ensure_script_tabs(project)
    install_runtime_state(state, project)
    return project


def json_safe(value: Any) -> Any:
    """Convert arbitrary metadata structures into JSON-safe values."""

    return json.loads(json.dumps(value, default=str))


def node_error_payload(exc: Exception) -> dict[str, Any]:
    """Shape structured node errors for HTTP and WebSocket responses."""

    if isinstance(exc, NodeEvaluationError):
        return {
            "detail": str(exc),
            "node_id": exc.node_id,
            "message": exc.message,
            "kind": "node_evaluation_error",
        }
    return {
        "detail": str(exc),
        "node_id": None,
        "message": str(exc),
        "kind": exc.__class__.__name__,
    }


def clear_runtime_errors(state: Any) -> None:
    """Clear stored evaluator runtime errors after graph or settings changes."""

    ensure_runtime_state(state).evaluator.clear_runtime_errors()
