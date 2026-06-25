"""Unit tests for the local setup and launcher helpers.

These tests cover the pure helper functions that generate runner scripts and
resolve runtime ports. They keep the startup plumbing testable without needing
to spawn the full app stack for every change.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup_opencomp_support.py"
SPEC = importlib.util.spec_from_file_location("opencomp_setup_support", MODULE_PATH)
assert SPEC and SPEC.loader
SETUP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SETUP
SPEC.loader.exec_module(SETUP)


def test_choose_runtime_port_prefers_requested_port(monkeypatch):
    monkeypatch.setattr(SETUP, "tcp_port_is_available", lambda host, port: port == 8000)
    port, changed = SETUP.choose_runtime_port(8000, 5, reserved_ports=set())
    assert port == 8000
    assert changed is False


def test_choose_runtime_port_scans_forward_and_skips_reserved(monkeypatch):
    monkeypatch.setattr(SETUP, "tcp_port_is_available", lambda host, port: port == 8002)
    port, changed = SETUP.choose_runtime_port(8000, 5, reserved_ports={8001})
    assert port == 8002
    assert changed is True


def test_choose_runtime_port_falls_back_to_ephemeral(monkeypatch):
    monkeypatch.setattr(SETUP, "tcp_port_is_available", lambda host, port: False)
    monkeypatch.setattr(SETUP, "ephemeral_free_port", lambda host, reserved_ports: 49123)
    port, changed = SETUP.choose_runtime_port(8000, 1, reserved_ports=set())
    assert port == 49123
    assert changed is True


def test_frontend_runner_lines_wire_backend_api_for_windows():
    lines = SETUP.frontend_runner_lines(5173, "windows")
    text = "\n".join(lines)
    assert 'set "ROOT=%~dp0.."' in text
    assert 'OPENCOMP_FRONTEND_PORT' in text
    assert 'OPENCOMP_BACKEND_PORT' in text
    assert 'VITE_OPENCOMP_API=http://127.0.0.1:%OPENCOMP_BACKEND_PORT%' in text
    assert 'npm.cmd run dev -- --port %OPENCOMP_FRONTEND_PORT%' in text


def test_frontend_runner_lines_wire_backend_api_for_posix():
    lines = SETUP.frontend_runner_lines(5173, "linux")
    text = "\n".join(lines)
    assert 'ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)' in text
    assert 'OPENCOMP_FRONTEND_PORT' in text
    assert 'OPENCOMP_BACKEND_PORT' in text
    assert 'VITE_OPENCOMP_API="http://127.0.0.1:${OPENCOMP_BACKEND_PORT}"' in text
    assert 'npm run dev -- --port "$OPENCOMP_FRONTEND_PORT"' in text


def test_backend_runner_lines_use_platform_specific_python_path():
    windows_lines = "\n".join(SETUP.backend_runner_lines(Path("C:/repo/.venv"), 8000, "windows"))
    linux_lines = "\n".join(SETUP.backend_runner_lines(Path("/repo/.venv"), 8000, "linux"))
    assert "Scripts" in windows_lines
    assert "%OPENCOMP_BACKEND_PORT%" in windows_lines
    assert "bin\\python" in linux_lines or "bin/python" in linux_lines
    assert '"$OPENCOMP_BACKEND_PORT"' in linux_lines


def test_app_runner_lines_preserve_env_port_overrides():
    lines = "\n".join(SETUP.app_runner_lines(Path("/repo/.venv"), 8000, 5173, "linux"))
    assert 'OPENCOMP_BACKEND_PORT:=8000' in lines
    assert 'OPENCOMP_FRONTEND_PORT:=5173' in lines
    assert '--backend-port "$OPENCOMP_BACKEND_PORT"' in lines
    assert '--frontend-port "$OPENCOMP_FRONTEND_PORT"' in lines


def test_runner_path_reference_prefers_root_relative_paths():
    path = SETUP.runner_path_reference(SETUP.ROOT / "backend", "linux")
    assert path == '"$ROOT/backend"'


def test_runner_path_reference_keeps_external_paths_absolute():
    external = Path("/tmp/external-tool")
    path = SETUP.runner_path_reference(external, "linux")
    assert path == SETUP.sh_quote(external)
