"""The self-hosted deploy takes its checkout and its image's name from where they live.

Plan step **balance:X-do**, finding **BAL-664**.  ``scripts/deploy.sh`` read two
values no operator needs to set:

* ``DEPLOY_DIR``, the checkout to deploy.  Its default was the checkout the
  script is in, and its libraries load from there whatever it says, so an
  override drove one revision's script against another checkout, that
  checkout's ``scripts/backup.sh`` included.  The script now deploys the
  checkout it is in (``SHEKEL_REPO_ROOT``, computed once by
  ``scripts/_settings_lib.sh``), and ``DEPLOY_DIR`` set at all is refused,
  naming the fix.
* ``IMAGE_REF``, the reference cosign signs and verifies.  Its default was a
  copy of the tag compose's build writes, so an override signed and verified
  one image while the deploy ran another.  The script now asks compose for
  the app service's image (``config --format json app``, measured on compose
  5.6.0 on 2026-10-17: ``config --images app`` lists the images of the
  services app depends on too), and ``IMAGE_REF`` set at all is refused.
  Rollback restores the previous image under that same reference, where it
  used to ask compose a second time and spell ``:latest`` itself.

**The deploy script is run whole only with ``--help``**, which the refusals
precede and which otherwise exits before any step; the other tests read the
functions they grade out of the script and run them against a ``docker``
stub.
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.test_scripts._shell_harness import fake_docker, function_text, run_bash

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO_ROOT / "scripts" / "deploy.sh"

#: A compose file list no script spells, so a reader that spells its own fails.
_PROBE_FILES = "COMPOSE_FILES=(-f probe-a.yml -f probe-b.yml)"
_PROBE_FILE_ARGS = "-f probe-a.yml -f probe-b.yml"


def _logging_stubs(bin_dir: Path, *names: str) -> dict[str, Path]:
    """Install each of *names* as a stub that logs its arguments and exits 1.

    Returns:
        Each stub's invocation log, by name.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    logs = {}
    for name in names:
        logs[name] = bin_dir / f"{name}.log"
        stub = bin_dir / name
        stub.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >>'{logs[name]}'
            exit 1
            """), encoding="utf-8")
        stub.chmod(0o755)
    return logs


class TestARetiredSettingIsRefused:
    """``DEPLOY_DIR`` or ``IMAGE_REF`` set at all ends the run before anything else.

    ``--help`` is refused too: the refusal comes before the arguments are
    read, so it is the first thing the script does after loading its libs.
    """

    @pytest.mark.parametrize(
        ("name", "value", "fix"),
        [
            ("DEPLOY_DIR", "/srv/another-checkout",
             "run that checkout's own scripts/deploy.sh"),
            ("DEPLOY_DIR", "", "run that checkout's own scripts/deploy.sh"),
            ("IMAGE_REF", "probe.example/shekel:probe",
             "the image compose names for the app service"),
            ("IMAGE_REF", "", "the image compose names for the app service"),
        ],
        ids=["deploy-dir", "deploy-dir-empty", "image-ref", "image-ref-empty"],
    )
    def test_it_is_refused_by_name(self, tmp_path, name, value, fix):
        """Exit 1, the setting and what replaced it named, no usage, no docker or git."""
        logs = _logging_stubs(tmp_path / "bin", "docker", "git")
        result = _run_help(tmp_path, {name: value})
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Usage:" not in result.stdout, result.stdout
        errors = [line for line in result.stdout.splitlines() if "[ERROR]" in line]
        assert any(f"{name} is set, but this script no longer reads it" in line
                   for line in errors), errors
        assert any(fix in line for line in errors), errors
        assert any(f"Remove {name} from wherever it is set" in line
                   for line in errors), errors
        assert not any(log.exists() for log in logs.values())

    def test_deploy_dir_s_refusal_names_the_checkout_it_deploys(self, tmp_path):
        """The checkout the script is in, so the operator sees what it would deploy."""
        _logging_stubs(tmp_path / "bin", "docker", "git")
        result = _run_help(tmp_path, {"DEPLOY_DIR": "/srv/another-checkout"})
        assert result.returncode == 1, result.stdout + result.stderr
        assert str(_REPO_ROOT) in result.stdout, result.stdout

    def test_both_set_are_both_named(self, tmp_path):
        """One run names every retired setting it finds, then ends."""
        _logging_stubs(tmp_path / "bin", "docker", "git")
        result = _run_help(tmp_path, {"DEPLOY_DIR": "/srv/x", "IMAGE_REF": "x:y"})
        assert result.returncode == 1, result.stdout + result.stderr
        for name in ("DEPLOY_DIR", "IMAGE_REF"):
            assert f"{name} is set, but this script no longer reads it" in result.stdout


def _run_help(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run ``scripts/deploy.sh --help`` whole with *env*, the stubs first on ``PATH``."""
    return subprocess.run(
        ["bash", str(_DEPLOY), "--help"], capture_output=True, text=True,
        timeout=30, check=False,
        env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", **env},
    )


class TestThePrerequisitesNamePython3:
    """``check_prerequisites`` refuses a host without ``python3``.

    ``resolve_image_ref`` reads compose's answer with it before anything is
    touched; without this check its absence would end the run there with a
    bare ``python3: command not found`` (exit 127), not a refusal naming it.
    """

    def test_a_host_without_python3_is_refused_by_name(self, tmp_path):
        """docker, git and curl present, python3 absent: exit 1, python3 named."""
        bin_dir = tmp_path / "bin"
        _logging_stubs(bin_dir, "docker", "git", "curl")
        (bin_dir / "date").symlink_to("/usr/bin/date")
        result = run_bash(
            'eval "$1"; eval "$2"; SHEKEL_REPO_ROOT="$3"; PATH="$4"; '
            'check_prerequisites; echo reached',
            function_text(_DEPLOY, "log"), function_text(_DEPLOY, "check_prerequisites"),
            str(tmp_path), str(bin_dir),
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "reached" not in result.stdout
        assert "[ERROR] python3 command not found" in result.stdout, result.stdout


def _compose(tmp_path: Path, *, answer: str, status: int) -> tuple[Path, Path]:
    """Install a ``docker`` answering *answer* with *status*, recording ``$PWD`` too.

    The script names its compose files by RELATIVE path, so the directory
    compose runs in decides which files it reads.

    Returns:
        The stub's argument log and its directory log.
    """
    log = fake_docker(tmp_path / "fake", stdout=answer, status=status)
    cwd_log = tmp_path / "cwd.log"
    wrapper = tmp_path / "bin" / "docker"
    wrapper.parent.mkdir(exist_ok=True)
    wrapper.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$PWD" >>'{cwd_log}'
        exec '{tmp_path / "fake" / "docker"}' "$@"
        """), encoding="utf-8")
    wrapper.chmod(0o755)
    return log, cwd_log


def _resolve_image_ref(tmp_path: Path, *, answer: str, status: int):
    """Run ``resolve_image_ref`` in a checkout of its own, compose answering *answer*.

    Returns:
        The completed process (``IMAGE_REF`` printed after it returns), the
        stub's argument log, its directory log and the checkout.
    """
    log, cwd_log = _compose(tmp_path, answer=answer, status=status)
    root = tmp_path / "checkout"
    root.mkdir()
    result = run_bash(
        f'SHEKEL_REPO_ROOT="$1"; {_PROBE_FILES}; eval "$2"; eval "$3"; '
        'resolve_image_ref; printf "IMAGE_REF=%s\\n" "${IMAGE_REF}"',
        str(root), function_text(_DEPLOY, "log"),
        function_text(_DEPLOY, "resolve_image_ref"), path_prefix=tmp_path / "bin",
    )
    return result, log, cwd_log, root


class TestTheImageRefIsComposesName:
    """``resolve_image_ref``: the app service's image as compose resolves it, or a refusal."""

    def test_it_is_the_app_service_s_image(self, tmp_path):
        """One question, over the script's compose files, in its checkout; app's image only."""
        # The app service is not the first, so a reader taking the first
        # service's image fails here.
        answer = json.dumps({"services": {
            "db": {"image": "postgres:probe"},
            "app": {"image": "probe.example/shekel:probe"},
        }})
        result, log, cwd_log, root = _resolve_image_ref(tmp_path, answer=answer, status=0)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines()[-1] == "IMAGE_REF=probe.example/shekel:probe"
        assert log.read_text(encoding="utf-8").splitlines() == [
            f"compose {_PROBE_FILE_ARGS} config --format json app",
        ]
        assert cwd_log.read_text(encoding="utf-8").splitlines() == [str(root)]

    @pytest.mark.parametrize(
        ("answer", "status"),
        [
            ("required variable SECRET_KEY is missing a value\n", 1),
            (json.dumps({"services": {"app": {"image": "x:y"}}}), 1),
            ("not json\n", 0),
            ("[]", 0),
            (json.dumps({"services": {}}), 0),
            (json.dumps({"services": {"app": {}}}), 0),
            (json.dumps({"services": {"app": {"image": ""}}}), 0),
            (json.dumps({"services": {"app": {"image": None}}}), 0),
        ],
        ids=["compose-refuses", "an-answer-but-a-failure", "not-json", "not-an-object",
             "no-app", "no-image", "empty-image", "null-image"],
    )
    def test_no_name_is_refused(self, tmp_path, answer, status):
        """Exit 1, naming the app service's image, before the caller goes on."""
        result, _, _, _ = _resolve_image_ref(tmp_path, answer=answer, status=status)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "IMAGE_REF=" not in result.stdout, result.stdout
        errors = [line for line in result.stdout.splitlines() if "[ERROR]" in line]
        assert any("no image for the app service" in line for line in errors), errors


class TestRollbackRestoresTheImageRef:
    """``rollback`` puts the previous image back under the reference compose runs."""

    def test_it_tags_the_previous_image_as_the_image_ref_then_recreates(self, tmp_path):
        """No second compose question, no ``:latest`` of its own: the tag, then ``up``."""
        log, cwd_log = _compose(tmp_path, answer="", status=0)
        root = tmp_path / "checkout"
        root.mkdir()
        result = run_bash(
            f'SHEKEL_REPO_ROOT="$1"; {_PROBE_FILES}; eval "$2"; eval "$3"; '
            'APP_CONTAINER=probe-app; IMAGE_REF=probe.example/shekel:probe; '
            'wait_for_health() { return 0; }; rollback',
            str(root), function_text(_DEPLOY, "log"), function_text(_DEPLOY, "rollback"),
            path_prefix=tmp_path / "bin",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert log.read_text(encoding="utf-8").splitlines() == [
            "image inspect probe-app:previous",
            f"compose {_PROBE_FILE_ARGS} stop app",
            "tag probe-app:previous probe.example/shekel:probe",
            f"compose {_PROBE_FILE_ARGS} up -d --no-deps --force-recreate app",
        ]
        # The inspect needs no directory; each compose call names its files
        # relatively, so it must run in the checkout.
        assert cwd_log.read_text(encoding="utf-8").splitlines()[1:] == [str(root)] * 3
