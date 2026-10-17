"""The self-hosted deploy checks health where compose published nginx.

Plan step **balance:X-do**, finding **BAL-661**.  ``docker-compose.yml``
publishes the bundled nginx on ``${NGINX_PORT:-80}``, and its comment tells
the operator to move the port in ``.env``, which compose reads.
``scripts/deploy.sh`` never reads ``.env``: it spelled the same default a
second time (``NGINX_PORT="${NGINX_PORT:-80}"``) and built its health URL from
its own environment, so a port moved the documented way left a good deploy
polling ``:80`` until it timed out and rolled back, or let whatever else
answered on ``:80`` speak for a broken one.

The script now asks compose for nginx's published address, so compose's own
``.env`` read is the one home of the port.  What compose 5.6.0 answers was
measured on the rootless daemon (2026-10-16): ONE line, the IPv4 binding
``<host-ip>:<port>`` (``0.0.0.0`` for an any-address binding, the bound IP for
a host-IP one), and exit 1 with a message when the service never started, is
stopped, or does not publish the port.  The script resolves the address
BEFORE it pulls, backs up, builds or swaps anything: a deploy recreates only
the app (``up -d --no-deps --force-recreate app``), so nginx's binding is the
same before the swap and after it, and a stopped nginx is refused with
nothing touched.

**The deploy script is not run here.**  Each test reads the functions it
grades out of ``scripts/deploy.sh`` and runs them against a ``docker`` stub,
taking the script's own compose file list and nginx port from the script.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from tests.test_scripts._shell_harness import fake_docker, function_text, run_bash

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO_ROOT / "scripts" / "deploy.sh"

#: Every step ``main`` takes after its canonical-stack guard, in order.  Each
#: is replaced by a recorder, so the ordering test runs no real step.
_STEPS = (
    "check_prerequisites", "resolve_image_ref", "pull_latest", "backup_database",
    "tag_previous_image", "build_image", "sign_image",
    "verify_image_signature", "restart_app", "wait_for_health", "rollback",
)


def _assignment(name: str) -> str:
    """Return ``scripts/deploy.sh``'s one top-level assignment of *name*.

    Args:
        name: The variable's name.

    Returns:
        The assignment line, verbatim.
    """
    found = re.findall(
        rf"^{re.escape(name)}=.*$", _DEPLOY.read_text(encoding="utf-8"),
        flags=re.M,
    )
    assert len(found) == 1, (
        f"expected one top-level assignment of {name} in {_DEPLOY}, "
        f"found {len(found)}"
    )
    return found[0]


def _config() -> str:
    """Return the two settings the reader takes from the script itself."""
    return "\n".join((
        _assignment("COMPOSE_FILES"), _assignment("NGINX_CONTAINER_PORT"),
    ))


def _compose_stub(tmp_path: Path, *, answer: str, status: int) -> Path:
    """Install :func:`fake_docker` behind a wrapper that records ``$PWD``.

    The script names its compose files by RELATIVE path, so the directory
    compose runs in decides which files it reads.

    Returns:
        The log of the directories ``docker`` was run from, one per call.
    """
    fake_docker(tmp_path / "fake", stdout=answer, status=status)
    cwd_log = tmp_path / "cwd.log"
    wrapper = tmp_path / "bin" / "docker"
    wrapper.parent.mkdir(exist_ok=True)
    wrapper.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$PWD" >>'{cwd_log}'
        exec '{tmp_path / "fake" / "docker"}' "$@"
        """), encoding="utf-8")
    wrapper.chmod(0o755)
    return cwd_log


def _health_url(tmp_path: Path, *, answer: str, status: int,
                overrides: str = ""):
    """Run ``nginx_health_url`` against a stub compose *answer* and *status*.

    The reader runs inside a command substitution, as its caller runs it.

    Args:
        tmp_path: The test's directory.
        answer: What the stub ``docker`` prints.
        status: Its exit status.
        overrides: Assignments evaluated after the script's own settings.

    Returns:
        The completed process and the stub's invocation log.
    """
    _compose_stub(tmp_path, answer=answer, status=status)
    result = run_bash(
        'eval "$1"; eval "$2"; eval "$3"; eval "$4"; '
        'url=$(nginx_health_url); printf "%s" "$url"',
        _config(), overrides, function_text(_DEPLOY, "log"),
        function_text(_DEPLOY, "nginx_health_url"),
        path_prefix=tmp_path / "bin",
    )
    return result, tmp_path / "fake" / "docker.log"


class TestTheHealthUrlIsComposesPublishedAddress:
    """``nginx_health_url``: compose's one IPv4 line, or nothing."""

    @pytest.mark.parametrize(
        ("answer", "url"),
        [
            ("0.0.0.0:80\n", "http://localhost:80/health"),
            ("0.0.0.0:8081\n", "http://localhost:8081/health"),
            ("127.0.0.1:18080\n", "http://127.0.0.1:18080/health"),
            ("192.168.10.5:80\n", "http://192.168.10.5:80/health"),
        ],
        ids=["default-port", "port-moved-in-env", "loopback-binding",
             "host-ip-binding"],
    )
    def test_it_is_the_published_address(self, tmp_path, answer, url):
        """The any-address form becomes localhost; a bound IP is kept."""
        result, _ = _health_url(tmp_path, answer=answer, status=0)
        assert result.returncode == 0, result.stderr
        assert result.stdout == url, result.stdout

    def test_it_asks_compose_for_nginx_s_port(self, tmp_path):
        """One question, about the nginx service, with the settings it is given.

        Both settings are replaced after the script's own are read, so a
        reader that spells either instead of reading it asks the wrong
        question and fails here.
        """
        result, log = _health_url(
            tmp_path, answer="0.0.0.0:80\n", status=0,
            overrides="COMPOSE_FILES=(-f probe-a.yml -f probe-b.yml); "
                      "NGINX_CONTAINER_PORT=9443",
        )
        assert result.returncode == 0, result.stderr
        assert log.read_text(encoding="utf-8").splitlines() == [
            "compose -f probe-a.yml -f probe-b.yml port nginx 9443",
        ]

    @pytest.mark.parametrize(
        "answer",
        ['service "nginx" is not running\n',
         "no port 8080/tcp for container shekel-prod-nginx: 80/tcp\n", "",
         "0.0.0.0:80\n"],
        ids=["stopped-or-never-up", "port-not-published", "silent",
             "an-address-but-a-failure"],
    )
    def test_compose_s_refusal_is_an_empty_url(self, tmp_path, answer):
        """Compose exiting 1 answers NOTHING, with status 0, which the caller refuses.

        Even an answer of the right shape: the exit status decides first.
        """
        result, _ = _health_url(tmp_path, answer=answer, status=1)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "", result.stdout

    @pytest.mark.parametrize(
        "answer",
        ["", "[::]:80\n", "0.0.0.0:80\n0.0.0.0:81\n", "0.0.0.0:\n", ":80\n",
         "localhost:80\n", "0.0.0.0:80x\n", "0.0.0.0:123456\n",
         "0.0.0:80\n"],
        ids=["empty", "ipv6", "two-lines", "no-port", "no-host", "a-name",
             "port-run-on", "port-too-long", "three-octets"],
    )
    def test_an_answer_that_is_not_one_ipv4_line_is_an_empty_url(
            self, tmp_path, answer):
        """Only the measured shape is read; anything else answers nothing.

        The answer it rejected is shown on stderr, so the refusal that
        follows is not read as "nginx is not running".
        """
        result, _ = _health_url(tmp_path, answer=answer, status=0)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "", (answer, result.stdout)
        assert f"Compose answered '{answer.rstrip(chr(10))}'" in result.stderr, (
            result.stderr)


def _run_main(tmp_path: Path, *, answer: str, status: int):
    """Run ``main`` with every step a recorder and compose answering *answer*.

    ``--force-self-hosted`` passes the canonical-stack guard on a host that
    holds ``/opt/docker/shekel``; the guard is not what is graded here.

    The test process runs from the repository root, never the deploy
    directory, and every compose call must have run in the deploy directory.
    ``main``'s first step, the retired-setting check, runs for real: neither
    setting is in the harness's environment.

    Returns:
        The completed process and the steps taken, in order.
    """
    cwd_log = _compose_stub(tmp_path, answer=answer, status=status)
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    steps = tmp_path / "steps"
    recorders = "\n".join(
        f'{step}() {{ printf "%s %s\\n" {step} "${{HEALTH_URL:-}}" >>"$STEPS"; }}'
        for step in _STEPS
    )
    result = run_bash(
        'STEPS="$1"; SHEKEL_REPO_ROOT="$2"; eval "$3"; eval "$4"; eval "$5"; '
        'eval "$6"; eval "$7"; eval "$8"; eval "$9"; '
        'SKIP_PULL=false; SKIP_BACKUP=false; SKIP_COSIGN=false; '
        'FORCE_SELF_HOSTED=false; APP_CONTAINER=probe-app; '
        'main --force-self-hosted',
        str(steps), str(deploy_dir), _config(), recorders,
        function_text(_DEPLOY, "log"),
        function_text(_DEPLOY, "refuse_retired_settings"),
        function_text(_DEPLOY, "nginx_health_url"),
        function_text(_DEPLOY, "resolve_health_url"),
        function_text(_DEPLOY, "main"),
        path_prefix=tmp_path / "bin",
    )
    taken = (steps.read_text(encoding="utf-8").splitlines()
             if steps.exists() else [])
    ran_in = set(cwd_log.read_text(encoding="utf-8").splitlines())
    assert ran_in == {str(deploy_dir)}, (
        f"compose ran in {sorted(ran_in)}, not the deploy directory "
        f"{deploy_dir}: its relative -f paths would name other files"
    )
    return result, taken


class TestADeployWithoutNginxsAddressTouchesNothing:
    """``main`` resolves the address before any step that touches the app."""

    def test_a_stopped_nginx_is_refused_by_name_before_any_step(self, tmp_path):
        """Nothing pulled, backed up, built or swapped; the refusal names nginx."""
        result, taken = _run_main(
            tmp_path, answer='service "nginx" is not running\n', status=1,
        )
        assert result.returncode == 1, result.stdout
        assert [line.split()[0] for line in taken] == ["check_prerequisites"]
        errors = [line for line in result.stdout.splitlines()
                  if "[ERROR]" in line]
        port = run_bash('eval "$1"; printf "%s" "$NGINX_CONTAINER_PORT"',
                        _config()).stdout
        assert any(f"nginx's port {port}" in line for line in errors), errors
        start = run_bash(
            'eval "$1"; printf "%s" "docker compose ${COMPOSE_FILES[*]} up -d"',
            _config(),
        ).stdout
        assert any(start in line for line in errors), errors

    def test_the_health_check_polls_the_published_address(self, tmp_path):
        """Every step runs, in order, and the health wait reads compose's address."""
        result, taken = _run_main(tmp_path, answer="0.0.0.0:8081\n", status=0)
        assert result.returncode == 0, result.stdout + result.stderr
        assert [line.split()[0] for line in taken] == [
            "check_prerequisites", "resolve_image_ref", "pull_latest", "backup_database",
            "tag_previous_image", "build_image", "sign_image",
            "verify_image_signature", "restart_app", "wait_for_health",
        ]
        assert taken[-1] == "wait_for_health http://localhost:8081/health"
