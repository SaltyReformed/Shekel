"""The fixtures the tests of :mod:`tools.ci` share: the ONE reader of the live ``ci.yml``.

A conftest, not a module of the layer, because parsing YAML takes PyYAML, and the layer's
modules import the standard library alone (``test_bottom_layer.py``); a conftest runs after
the install, as the tests do.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest
import yaml

from tools.ci import ci_yml


@pytest.fixture(name="read_workflow", scope="session")
def _read_workflow() -> Callable[[], dict]:
    """The one reader of :data:`tools.ci.ci_yml.PATH`.  Each call parses the file afresh, so
    no test can hand another a workflow it changed; a fixture of any scope can take it."""

    def read() -> dict:
        return yaml.safe_load(ci_yml.PATH.read_text(encoding="utf-8"))

    return read


@pytest.fixture(name="workflow")
def _workflow(read_workflow: Callable[[], dict]) -> dict:
    """The live workflow, parsed for one test."""
    return read_workflow()
