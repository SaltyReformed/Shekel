"""The rehearsal kit's test configuration: the ``cluster`` marker.

A test that starts a real private cluster carries it.  CI's every-scope step deselects
those (``-m "not cluster"``), since a registry-only change set cannot change the kit
(the coordinator's K1 leaf-plan ruling, item 3); they run by hand, with ``DOCKER_HOST`` at
the rootless daemon (the bench chooses none, :mod:`.bench` says why):
``DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock pytest tools/rehearsal -c /dev/null -m
cluster``.  The tests run with ``-c /dev/null``, so no ini file registers the marker: this
hook does.
"""
from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register ``cluster``, so pytest does not warn of it as an unknown marker."""
    config.addinivalue_line(
        "markers", "cluster: starts a real private cluster (scripts/private_cluster.py)")
