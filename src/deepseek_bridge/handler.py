"""DeepSeekProxyHandler — redirected to handler/ package.

The handler class has been refactored into the handler/ package for
modularity.  This module exists as a redirect so that all existing
imports (``from deepseek_bridge.handler import DeepSeekProxyHandler``)
continue to work without change.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_pkg_dir = Path(__file__).parent / "handler"
_spec = importlib.util.spec_from_file_location(
    "deepseek_bridge.handler",
    _pkg_dir / "__init__.py",
    submodule_search_locations=[str(_pkg_dir)],
)
if _spec is None or _spec.loader is None:
    msg = (
        f"Could not load handler package from {_pkg_dir / '__init__.py'}. "
        "Make sure handler/__init__.py exists."
    )
    raise ImportError(msg)

_mod = importlib.util.module_from_spec(_spec)
sys.modules["deepseek_bridge.handler"] = _mod
_spec.loader.exec_module(_mod)

DeepSeekProxyHandler = _mod.DeepSeekProxyHandler
