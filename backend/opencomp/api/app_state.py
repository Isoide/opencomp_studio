"""Typed runtime-state helpers for the FastAPI app container.

This module keeps the backend's mutable request-shared services behind one
structured state object instead of scattering raw ``app.state`` attributes
through route handlers. Routes can then ask for project/evaluator/scheduler
services directly without repeating dynamic attribute lookups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opencomp.core.defaults import create_default_project
from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Project
from opencomp.core.render_scheduler import RenderScheduler

RUNTIME_STATE_KEY = "opencomp_runtime"


@dataclass(slots=True)
class OpenCompRuntimeState:
    """Mutable backend services shared across requests for one app instance."""

    project: Project
    evaluator: GraphEvaluator
    evaluator_settings_key: str
    render_scheduler: RenderScheduler = field(default_factory=RenderScheduler)
    render_jobs: dict[str, Any] = field(default_factory=dict)
    graph_revision: int = 0


def viewer_preview_cache_bytes(max_cache_bytes: int) -> int:
    """Return the preview-cache budget derived from the project cache budget."""

    if max_cache_bytes <= 0:
        return 0
    return max(64 * 1024 * 1024, max_cache_bytes // 2)


def build_runtime_state(project: Project | None = None) -> OpenCompRuntimeState:
    """Create a runtime-state object for a project, allocating evaluator services."""

    project = project or create_default_project()
    max_cache_bytes = max(0, int(project.preferences.cache_memory_limit_mb)) * 1024 * 1024
    preview_cache_bytes = viewer_preview_cache_bytes(max_cache_bytes)
    evaluator = GraphEvaluator(
        settings=project.settings,
        max_cache_bytes=max_cache_bytes,
        max_preview_cache_bytes=preview_cache_bytes,
        max_float_preview_cache_bytes=preview_cache_bytes,
    )
    return OpenCompRuntimeState(
        project=project,
        evaluator=evaluator,
        evaluator_settings_key=project.settings.model_dump_json(),
    )


def ensure_runtime_state(state: Any) -> OpenCompRuntimeState:
    """Return the typed runtime state, creating a default one when missing."""

    runtime = getattr(state, RUNTIME_STATE_KEY, None)
    if runtime is None:
        runtime = build_runtime_state()
        setattr(state, RUNTIME_STATE_KEY, runtime)
    return runtime


def install_runtime_state(state: Any, project: Project) -> OpenCompRuntimeState:
    """Replace the runtime state for a newly loaded/imported project."""

    runtime = build_runtime_state(project)
    previous = getattr(state, RUNTIME_STATE_KEY, None)
    if previous is not None:
        runtime.graph_revision = previous.graph_revision + 1
    setattr(state, RUNTIME_STATE_KEY, runtime)
    return runtime


def bump_graph_revision(state: Any) -> int:
    """Increment and return the active graph revision counter."""

    runtime = ensure_runtime_state(state)
    runtime.graph_revision += 1
    return runtime.graph_revision
