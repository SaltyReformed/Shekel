"""Loading a module of ``side/`` by path, the one way the kit's tests reach the side probe.

Side code is run by path and never imported by name (the package docstring says why, and
``test_imports.py`` holds the rule), so a test that grades a side module loads it as the probe
runs it: from its file, under a name no package holds, registered in ``sys.modules`` before
it runs, since its dataclasses look their module up there.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_SIDE = Path(__file__).resolve().parent / "side"


def load(name: str) -> ModuleType:
    """``side/<name>.py``, loaded by path as ``rehearsal_side_<name>``."""
    spec = importlib.util.spec_from_file_location(f"rehearsal_side_{name}",
                                                  _SIDE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
