"""Fail-closed collection guard for the ``docker``-marked deploy tests.

The tests marked ``@pytest.mark.docker`` in this package stand up real
containers (or drive ``docker compose`` / ``docker run``) against
whatever daemon the ``docker`` CLI points at.  On the maintainer's
homelab box the default daemon IS the production daemon that the
wud/cadvisor/alloy observability stack watches, so running these tests
there spams that stack with ephemeral ``shekel-test-*`` container events.

``scripts/test.sh`` already default-excludes them with ``-m "not
docker"``, but that only protects runs that go through the wrapper.  This
conftest is the fail-closed backstop for a *bare* ``pytest`` -- an IDE
test runner, ``pytest tests/test_deploy`` from muscle memory, or the
explicit ``PYTEST_MARKER_EXPR=docker`` opt-in: it skips the
``docker``-marked tests whenever the active Docker endpoint is the host's
system socket, UNLESS the run is sanctioned.  A run is sanctioned when
either

  * it is CI -- an ephemeral throwaway daemon that no observability stack
    watches, where these integration tests SHOULD run (this is the case
    the naive "skip on the system socket" guard would have wrongly
    stripped from the merge gate); or
  * ``DOCKER_HOST`` already points at an isolated daemon (a rootless
    socket, ``tcp://``, ``ssh://`` -- anything other than the default
    system socket); or
  * the operator explicitly set ``SHEKEL_ALLOW_HOST_DOCKER=1`` to accept
    the churn (e.g. a quick local check before rootless isolation is set
    up).

See docs/test-harness-isolation.md.
"""
from __future__ import annotations

import os

import pytest

# ``DOCKER_HOST`` values that resolve to the host's default (system)
# daemon.  Any other value -- a rootless socket, ``tcp://``, ``ssh://`` --
# is treated as an isolated endpoint the docker tests may safely use.
# ``docker context`` is deliberately not consulted: the documented
# isolation workflow selects the rootless daemon via ``DOCKER_HOST``, and
# parsing contexts would add cost for a path this project does not use.
_SYSTEM_DOCKER_ENDPOINTS = frozenset(
    {
        "",
        "unix:///var/run/docker.sock",
        "unix:///run/docker.sock",
    }
)

# Env var an operator sets to deliberately accept running the docker
# tests against the system daemon.
_HOST_DOCKER_OVERRIDE_ENV = "SHEKEL_ALLOW_HOST_DOCKER"


def _running_against_system_daemon() -> bool:
    """Return True when the active ``docker`` endpoint is the host's
    default system daemon rather than an isolated one.
    """
    return os.environ.get("DOCKER_HOST", "") in _SYSTEM_DOCKER_ENDPOINTS


def _host_docker_allowed() -> bool:
    """Return True when running the docker tests on the system daemon is
    sanctioned: either this is CI (its daemon is an ephemeral throwaway
    that no observability stack watches) or the operator set the override.
    """
    if os.environ.get("CI"):
        return True
    return os.environ.get(_HOST_DOCKER_OVERRIDE_ENV) == "1"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip ``docker``-marked tests that would otherwise execute against
    the host's system Docker daemon.

    No-op when the active endpoint is already isolated (a non-default
    ``DOCKER_HOST``) or when running on the system daemon is sanctioned
    (CI or the ``SHEKEL_ALLOW_HOST_DOCKER=1`` override).  When it does
    apply, the tests are marked skipped -- not deselected -- so the reason
    is visible in the report.
    """
    if not _running_against_system_daemon() or _host_docker_allowed():
        return
    skip_marker = pytest.mark.skip(
        reason=(
            "docker-marked test refuses to run against the host system "
            "Docker daemon (would spawn shekel-test-* containers on the "
            "production daemon the homelab stack watches). Point "
            "DOCKER_HOST at an isolated daemon, or export "
            "SHEKEL_ALLOW_HOST_DOCKER=1 to accept the churn. See "
            "docs/test-harness-isolation.md."
        )
    )
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip_marker)
