"""Unit tests for typed backend app-state helpers."""

from __future__ import annotations

from types import SimpleNamespace

from opencomp.api import app_state
from opencomp.api.app_state import OpenCompRuntimeState, bump_graph_revision, ensure_runtime_state, install_runtime_state
from opencomp.core.defaults import create_default_project


class _FakeEvaluator:
    def __init__(self, **_: object) -> None:
        self.cache_limits: tuple[int, int] | None = None

    def set_cache_limits(self, max_cache_bytes: int, max_preview_cache_bytes: int) -> None:
        self.cache_limits = (max_cache_bytes, max_preview_cache_bytes)


def test_ensure_runtime_state_creates_runtime_once(monkeypatch) -> None:
    monkeypatch.setattr(app_state, "GraphEvaluator", _FakeEvaluator)
    state = SimpleNamespace()

    runtime = ensure_runtime_state(state)

    assert isinstance(runtime, OpenCompRuntimeState)
    assert ensure_runtime_state(state) is runtime


def test_install_runtime_state_replaces_project_and_bumps_revision(monkeypatch) -> None:
    monkeypatch.setattr(app_state, "GraphEvaluator", _FakeEvaluator)
    state = SimpleNamespace()
    first = ensure_runtime_state(state)
    bump_graph_revision(state)
    project = create_default_project()
    project.project_name = "Replacement"

    installed = install_runtime_state(state, project)

    assert installed.project.project_name == "Replacement"
    assert installed.graph_revision == first.graph_revision + 1
