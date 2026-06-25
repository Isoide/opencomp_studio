"""Pytest collection helpers for OpenComp backend tests.

This file assigns stable `unit` and `integration` markers during collection so
the suite can be filtered consistently without repeating marks in every test
module. The mapping is intentionally explicit to keep test intent visible.
"""

from __future__ import annotations

from pathlib import Path

import pytest


INTEGRATION_TEST_FILES = {
    "test_composite_nodes.py",
    "test_cryptomatte.py",
    "test_exr_channel_metadata.py",
    "test_oiio_backend.py",
    "test_preview.py",
    "test_project_io_and_cli.py",
    "test_python_scripting.py",
    "test_render_contract.py",
    "test_script_tabs.py",
    "test_slapcomp.py",
    "test_time_color_nodes.py",
    "test_viewer_endpoint.py",
    "test_viewer_inputs.py",
    "test_viewer_scene_linear.py",
    "test_viewer_streaming.py",
    "test_vulkan_backend.py",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Assign `unit` or `integration` markers to collected tests."""

    for item in items:
        filename = Path(str(item.fspath)).name
        if filename in INTEGRATION_TEST_FILES:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)
