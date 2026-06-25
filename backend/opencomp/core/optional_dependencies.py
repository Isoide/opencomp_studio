"""Helpers for loading optional runtime integrations consistently.

This module centralizes best-effort imports for host-specific packages such as
OpenColorIO, OpenImageIO, and Vulkan bindings. It keeps optional dependency
checks declarative and avoids repeating small import wrappers across the codebase.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class OptionalDependency:
    """Result of resolving an optional Python package import."""

    module_name: str | None
    module: Any | None

    @property
    def available(self) -> bool:
        return self.module is not None


def import_optional(module_name: str) -> Any | None:
    """Import one optional module and return None when it is unavailable."""

    try:
        return importlib.import_module(module_name)
    except ImportError:
        return None


def import_first_available(*module_names: str) -> OptionalDependency:
    """Import the first available module from an ordered preference list."""

    for module_name in module_names:
        module = import_optional(module_name)
        if module is not None:
            return OptionalDependency(module_name=module_name, module=module)
    return OptionalDependency(module_name=None, module=None)
