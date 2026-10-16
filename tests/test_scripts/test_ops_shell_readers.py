"""The operations scripts' swept readers keep their meaning without a pipe.

Plan step **balance:X-dm**, rulings **R-BAL252** (sweep every early-exiting
reader under ``pipefail``) and **R-BAL254** (the running-check has ONE home).
``deploy/shekel-deploy.sh``'s migration check read a listed revision as
absent because ``grep -q`` left its pipe while ``printf`` was still writing
(finding BAL-618).  The same shape sat in the scripts graded here, none with
a test until now:

* ``scripts/_docker_lib.sh``'s ``container_running``, the shell scripts'
  one home of "is this container running?", sourced by
  ``scripts/_backup_lib.sh`` and by the deploy script (whose own module
  grades its side) -- and, since plan step **balance:X-do**, its one
  spelling of an image digest's shape (finding **BAL-623**), which the
  reconcile and self-hosted deploy readers below now read;
* ``scripts/restore.sh``'s ``start_app``, whose wait for the app container
  asks it through that same check;
* ``scripts/reconcile_prod_to_canonical.sh``'s ``extract_env_value`` and
  ``compose_image_digest``;
* ``scripts/deploy.sh``'s ``cosign_resolve_digest``;
* since plan step balance:X-do, every script that reads the lib: each
  production container default it takes, and its refusal of a lib lacking
  any function it reads.

**Neither the reconcile script nor the restore script is run here.**  One
writes production's ``/opt/docker/shekel`` (a hardcoded ``PROD_ROOT``), the
other drops a database, so each test reads ONE function's text out of its
script and runs that, against a temporary tree or a stub ``docker``.  The
expected values are worked by hand from what the old spellings printed.  The
reconcile pipelines keep grep, now ending in a reader that drains, and both
carried ``|| true``, so their race only ever cost a status nobody read: these
tests grade the meaning, not a race.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKER_LIB = _REPO_ROOT / "scripts" / "_docker_lib.sh"
_BACKUP_LIB = _REPO_ROOT / "scripts" / "_backup_lib.sh"
_RECONCILE = _REPO_ROOT / "scripts" / "reconcile_prod_to_canonical.sh"
_RESTORE = _REPO_ROOT / "scripts" / "restore.sh"
_SELF_HOSTED_DEPLOY = _REPO_ROOT / "scripts" / "deploy.sh"

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


def _function_text(script: Path, name: str) -> str:
    """Return shell function *name*'s definition from *script*, verbatim.

    A definition opens with ``name() {`` at the start of a line and closes
    with the first ``}`` alone at the start of a line, which is how every
    top-level function in these scripts is written (shfmt enforces it).

    Args:
        script: The script holding the function.
        name: The function's name.

    Returns:
        The definition, from its first line through its closing brace.
    """
    found = re.findall(
        rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
        script.read_text(encoding="utf-8"), flags=re.S | re.M,
    )
    assert len(found) == 1, (
        f"expected one definition of {name}() in {script}, found {len(found)}"
    )
    return found[0]


def _fake_docker(bin_dir: Path, *, stdout: str, status: int) -> Path:
    """Install a ``docker`` stub that prints *stdout* and exits *status*.

    Every invocation's arguments are appended to ``docker.log`` beside it.

    Args:
        bin_dir: Directory placed first on ``PATH``.
        stdout: What the stub prints, verbatim.
        status: The stub's exit status.

    Returns:
        The invocation log's path.
    """
    bin_dir.mkdir(exist_ok=True)
    log = bin_dir / "docker.log"
    answer = bin_dir / "docker.out"
    answer.write_text(stdout, encoding="utf-8")
    stub = bin_dir / "docker"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >>'{log}'
        cat '{answer}'
        exit {status}
        """), encoding="utf-8")
    stub.chmod(0o755)
    return log


def _bash(program: str, *args: str, path_prefix: Path | None = None,
          ) -> subprocess.CompletedProcess:
    """Run *program* under the swept scripts' own ``set -euo pipefail``.

    A function its script calls inside a command substitution is called the
    same way here, since bash runs that subshell WITHOUT errexit: grading it
    bare under ``set -e`` would grade a context its callers never give it.

    Args:
        program: Bash source, reading its inputs from ``$1``, ``$2`` ...
        args: The positional parameters.
        path_prefix: A directory to put first on ``PATH`` (the stubs).

    Returns:
        The completed process, stdout and stderr captured.
    """
    env = None
    if path_prefix is not None:
        env = {"PATH": f"{path_prefix}:/usr/bin:/bin"}
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{program}", "_", *args],
        capture_output=True, text=True, timeout=30, check=False, env=env,
    )


class TestTheRunningCheckHasOneHome:
    """Ruling R-BAL254: ``container_running`` answers for every caller."""

    @pytest.mark.parametrize(
        ("answer", "status", "expected"),
        [
            ("true\n", 0, 0),
            ("false\n", 0, 1),
            ("", 1, 1),
        ],
        ids=["running", "stopped", "docker-cannot-find-it"],
    )
    def test_it_answers_what_docker_reports(
        self, tmp_path, answer, status, expected,
    ):
        """Running is 0; stopped, or unknown to docker, is 1."""
        log = _fake_docker(tmp_path / "bin", stdout=answer, status=status)
        result = _bash(
            'source "$1"; container_running "$2" || exit "$?"',
            str(_DOCKER_LIB), "probe-db", path_prefix=tmp_path / "bin",
        )
        assert result.returncode == expected, result.stderr
        assert log.read_text(encoding="utf-8").splitlines() == [
            "inspect --format {{.State.Running}} probe-db",
        ]

    def test_the_backup_helper_asks_through_it(self, tmp_path):
        """``require_db_container`` refuses a stopped container, by name."""
        _fake_docker(tmp_path / "bin", stdout="false\n", status=0)
        result = _bash(
            'source "$1"; require_db_container "$2" || exit "$?"',
            str(_BACKUP_LIB), "probe-db", path_prefix=tmp_path / "bin",
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Database container 'probe-db' is not running" in result.stdout

    def test_the_backup_helper_passes_a_running_container(self, tmp_path):
        """And lets a running one through, saying nothing."""
        _fake_docker(tmp_path / "bin", stdout="true\n", status=0)
        result = _bash(
            'source "$1"; require_db_container "$2" || exit "$?"',
            str(_BACKUP_LIB), "probe-db", path_prefix=tmp_path / "bin",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == ""


def _restore_stub(bin_dir: Path, *, health: str | None, running: str,
                  status: int) -> Path:
    """Install the ``docker`` and ``sleep`` stubs ``start_app`` meets.

    The app container exists and starts.  Its healthcheck reports *health*,
    or, for ``None``, it has none (that inspect fails, so ``start_app``
    reads ``none``).  The running-check is answered with *running* and
    *status*.  ``sleep`` returns at once, so thirty retries cost nothing.

    Args:
        bin_dir: Directory placed first on ``PATH``.
        health: What the ``{{.State.Health.Status}}`` inspect prints.
        running: What the ``{{.State.Running}}`` inspect prints.
        status: That inspect's exit status.

    Returns:
        The invocation log's path.
    """
    bin_dir.mkdir()
    log = bin_dir / "docker.log"
    (bin_dir / "health.out").write_text(health or "", encoding="utf-8")
    (bin_dir / "running.out").write_text(running, encoding="utf-8")
    health_arm = (
        "exit 1" if health is None else f"cat '{bin_dir}/health.out'; exit 0"
    )
    (bin_dir / "docker").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >>'{log}'
        case "$*" in
            *State.Health.Status*) {health_arm} ;;
            *State.Running*) cat '{bin_dir}/running.out'; exit {status} ;;
        esac
        exit 0
        """), encoding="utf-8")
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n",
                                   encoding="utf-8")
    for name in ("docker", "sleep"):
        (bin_dir / name).chmod(0o755)
    return log


class TestRestoreWaitsForTheAppThroughTheSharedCheck:
    """``scripts/restore.sh``'s ``start_app`` asks ``container_running``.

    Ruling R-BAL254's one home: the wait for an app container with no
    healthcheck spelled its own ``docker inspect``, compared to ``true``.
    The recorder tests put a RECORDER in ``container_running``'s place: the
    first sees ``start_app`` ask it (a spelling of its own would leave the
    recorder unasked and send docker the question instead), the second that
    a healthcheck still starting asks it nothing.
    """

    #: Stands in for ``container_running`` once the real one is sourced:
    #: records each question and answers with ``$RECORDED_ANSWER``.
    _RECORDER = textwrap.dedent("""\
        container_running() {
            printf 'asked %s\\n' "$1" >>"$RECORDER_LOG"
            return "$RECORDED_ANSWER"
        }
        """)

    def _start_app(self, tmp_path: Path, *, health: str | None = None,
                   running: str = "true\n", status: int = 0,
                   recorded_answer: int | None = None,
                   ) -> tuple[subprocess.CompletedProcess, str, str]:
        """Run ``start_app`` from its own text, as ``main`` calls it.

        Args:
            tmp_path: pytest's per-test temporary directory.
            health: The healthcheck's report; ``None`` for no healthcheck.
            running: What the running-check's inspect prints.
            status: That inspect's exit status.
            recorded_answer: When given, ``container_running`` is replaced
                by the recorder, answering with this status.

        Returns:
            The completed process, the docker stub's invocation log and the
            recorder's log.
        """
        log = _restore_stub(
            tmp_path / "bin", health=health, running=running, status=status,
        )
        recorder_log = tmp_path / "asked.log"
        recorder_log.write_text("", encoding="utf-8")
        recorder = "" if recorded_answer is None else self._RECORDER
        result = _bash(
            'source "$1"; eval "$2"; APP_CONTAINER=probe-app; '
            'RECORDER_LOG="$3"; RECORDED_ANSWER="$4"; '
            'eval "$5"; eval "$6"; start_app',
            str(_BACKUP_LIB), recorder, str(recorder_log),
            str(recorded_answer or 0),
            _function_text(_RESTORE, "app_container_exists"),
            _function_text(_RESTORE, "start_app"),
            path_prefix=tmp_path / "bin",
        )
        return (
            result, log.read_text(encoding="utf-8"),
            recorder_log.read_text(encoding="utf-8"),
        )

    @pytest.mark.parametrize(
        ("answer", "ready", "asked"), [(0, True, 1), (1, False, 30)],
        ids=["it-says-running", "it-says-not-running"],
    )
    def test_with_no_healthcheck_it_asks_the_shared_check(
        self, tmp_path, answer, ready, asked,
    ):
        """Each look asks ``container_running``; docker is never asked ``State.Running``."""
        result, calls, recorded = self._start_app(
            tmp_path, recorded_answer=answer,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert recorded.splitlines() == ["asked probe-app"] * asked, recorded
        assert "State.Running" not in calls, calls
        assert ("Application container is running" in result.stdout) is ready
        assert ("did not become ready within 60s" in result.stdout) is not ready

    def test_with_a_healthcheck_still_starting_it_asks_nothing_of_it(
        self, tmp_path,
    ):
        """A healthcheck decides; "running" alone is never ready then."""
        result, calls, recorded = self._start_app(
            tmp_path, health="starting\n", recorded_answer=0,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert recorded == "", recorded
        assert "Application container is running" not in result.stdout
        assert "did not become ready within 60s" in result.stdout
        assert calls.count("{{.State.Health.Status}} probe-app") == 30, calls

    @pytest.mark.parametrize(
        ("running", "status", "ready"),
        [("true\n", 0, True), ("false\n", 0, False), ("", 1, False)],
        ids=["running", "stopped", "docker-cannot-find-it"],
    )
    def test_the_real_check_answers_through_docker(
        self, tmp_path, running, status, ready,
    ):
        """End to end with the real helper: its one inspect, its answer."""
        result, calls, _ = self._start_app(
            tmp_path, running=running, status=status,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert ("Application container is running" in result.stdout) is ready
        asked = calls.splitlines().count(
            "inspect --format {{.State.Running}} probe-app"
        )
        assert asked == (1 if ready else 30), calls


def _extract_env_value(env_file_text: str | None, key: str, tmp_path: Path,
                       ) -> subprocess.CompletedProcess:
    """Run reconcile's ``extract_env_value`` against a temporary ``.env``.

    Args:
        env_file_text: The ``.env`` to write, or ``None`` for no file.
        key: The key asked for.
        tmp_path: The temporary ``PROD_ROOT``.

    Returns:
        The completed process.
    """
    if env_file_text is not None:
        (tmp_path / ".env").write_text(env_file_text, encoding="utf-8")
    return _bash(
        'PROD_ROOT="$1"; eval "$2"; value=$(extract_env_value "$3"); '
        'printf "%s" "$value"',
        str(tmp_path), _function_text(_RECONCILE, "extract_env_value"), key,
    )


class TestReconcileReadsTheFirstLineOfAKey:
    """``extract_env_value``: the first ``KEY=`` line's value, or nothing."""

    @pytest.mark.parametrize(
        ("env", "key", "value"),
        [
            ("A=1\nKEY=first\nKEY=second\n", "KEY", "first"),
            ("KEY=a=b=c\n", "KEY", "a=b=c"),
            ("KEY_OLD=old\nKEY=new\n", "KEY", "new"),
            ("  KEY=indented\nKEY=flush\n", "KEY", "flush"),
            ("A=1\nKEY=no-newline", "KEY", "no-newline"),
            ("KEY=\n", "KEY", ""),
            ("A=1\n", "KEY", ""),
        ],
        ids=[
            "first-of-two", "value-holds-equals", "a-longer-key-is-not-it",
            "an-indented-line-is-not-it", "last-line-unterminated",
            "empty-value", "absent",
        ],
    )
    def test_it_prints_the_value(self, tmp_path, env, key, value):
        """The value after the first ``=``, of the first matching line."""
        result = _extract_env_value(env, key, tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == value, result.stdout

    def test_a_missing_file_is_an_empty_value_not_a_failure(self, tmp_path):
        """Never a non-zero status (OPS/SH-12): the callers' own dies explain."""
        result = _extract_env_value(None, "KEY", tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    def test_an_unreadable_file_is_an_empty_value_not_a_failure(self, tmp_path):
        """A directory where ``.env`` should be reads as no value, too."""
        (tmp_path / ".env").mkdir()
        result = _extract_env_value(None, "KEY", tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""


def _compose_image_digest(compose_text: str | None, tmp_path: Path,
                          ) -> subprocess.CompletedProcess:
    """Run reconcile's ``compose_image_digest`` against a temporary compose file.

    Args:
        compose_text: The ``docker-compose.yml`` to write, or ``None``.
        tmp_path: The temporary ``PROD_ROOT``.

    Returns:
        The completed process.
    """
    if compose_text is not None:
        (tmp_path / "docker-compose.yml").write_text(
            compose_text, encoding="utf-8",
        )
    return _bash(
        'source "$3"; PROD_ROOT="$1"; eval "$2"; '
        'digest=$(compose_image_digest); printf "%s" "$digest"',
        str(tmp_path), _function_text(_RECONCILE, "compose_image_digest"),
        str(_DOCKER_LIB),
    )


class TestReconcileFindsTheLegacyInlineDigest:
    """``compose_image_digest``: the first digest on a SHEKEL image line."""

    @pytest.mark.parametrize(
        ("compose", "digest"),
        [
            (
                f"  db:\n    image: postgres:16@{_DIGEST_A}\n"
                f"  app:\n    image: ghcr.io/saltyreformed/shekel@{_DIGEST_B}\n",
                _DIGEST_B,
            ),
            (
                "    image: ghcr.io/saltyreformed/shekel:latest\n"
                f"    image: ghcr.io/saltyreformed/shekel@{_DIGEST_C}\n",
                _DIGEST_C,
            ),
            (
                f"    image: ghcr.io/saltyreformed/shekel@{_DIGEST_B} "
                f"# was {_DIGEST_C}\n",
                _DIGEST_B,
            ),
            ("    image: ghcr.io/saltyreformed/shekel:latest\n", ""),
            (
                f"    image: ghcr.io/saltyreformed/shekel@{_DIGEST_B}0\n"
                f"    image: ghcr.io/saltyreformed/shekel@{_DIGEST_C}\n",
                "",
            ),
        ],
        ids=[
            "not-the-postgres-pin", "first-shekel-line-with-a-digest",
            "leftmost-on-its-line", "none", "an-over-long-pin-is-not-cut",
        ],
    )
    def test_it_prints_the_digest(self, tmp_path, compose, digest):
        """Postgres's pin is never it; the first shekel digest is.

        A pin one hex digit too long is no digest, and is not cut to 64 into
        one: the answer is empty, so the caller's die runs.  The search does
        not go past it to a later line (plan step balance:X-do).
        """
        result = _compose_image_digest(compose, tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == digest, result.stdout

    def test_a_missing_file_is_an_empty_digest_not_a_failure(self, tmp_path):
        """The caller's own die names both places it looked."""
        result = _compose_image_digest(None, tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    def test_an_unreadable_file_is_an_empty_digest_not_a_failure(self, tmp_path):
        """A directory where the compose file should be: no digest, status 0."""
        (tmp_path / "docker-compose.yml").mkdir()
        result = _compose_image_digest(None, tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""


class TestTheSelfHostedDeployResolvesItsDigest:
    """``scripts/deploy.sh``'s ``cosign_resolve_digest``: first line, or empty."""

    @pytest.mark.parametrize(
        ("answer", "status", "digest"),
        [
            (f"{_DIGEST_A}\n", 0, _DIGEST_A),
            (f"{'a' * 64}\n", 0, _DIGEST_A),
            (f"{_DIGEST_A}\n{_DIGEST_B}\n", 0, _DIGEST_A),
            ("", 1, ""),
        ],
        ids=["prefixed", "bare-id-gets-the-prefix", "first-line", "failed"],
    )
    def test_it_prints_the_image_id(self, tmp_path, answer, status, digest):
        """A failed inspect is EMPTY with status 0, which the callers refuse."""
        _fake_docker(tmp_path / "bin", stdout=answer, status=status)
        result = _bash(
            'source "$2"; IMAGE_REF=probe:latest; eval "$1"; '
            'digest=$(cosign_resolve_digest); printf "%s" "$digest"',
            _function_text(_SELF_HOSTED_DEPLOY, "cosign_resolve_digest"),
            str(_DOCKER_LIB), path_prefix=tmp_path / "bin",
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == digest, result.stdout

    def test_an_id_that_is_no_digest_resolves_to_nothing(self, tmp_path):
        """Not a whole digest, with or without its algorithm: EMPTY, which both callers refuse.

        Plan step balance:X-do, finding BAL-623: the bare-hex normaliser
        tested only the ``sha256:`` prefix, so a short or padded ID went to
        cosign as a digest.  The shared helper's shape now decides.
        """
        for answer in (f"sha256:{'a' * 63}\n", f"{'a' * 63}\n",
                       f"{_DIGEST_A}0\n", f"sha256:{'A' * 64}\n"):
            _fake_docker(tmp_path / "bin", stdout=answer, status=0)
            result = _bash(
                'source "$2"; IMAGE_REF=probe:latest; eval "$1"; '
                'digest=$(cosign_resolve_digest); printf "%s" "$digest"',
                _function_text(_SELF_HOSTED_DEPLOY, "cosign_resolve_digest"),
                str(_DOCKER_LIB), path_prefix=tmp_path / "bin",
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout == "", (answer, result.stdout)


class TestTheDigestShapeHasOneHome:
    """``scripts/_docker_lib.sh``: the one spelling of an image digest's shape.

    Plan step balance:X-do, finding BAL-623: five spellings in three
    scripts, two of them prefix-only.  Both readers PRINT their answer or
    nothing and return 0, so a caller assigns it and refuses an empty one;
    every run here is under ``set -euo pipefail``, so a non-zero status would
    fail the run.
    """

    @pytest.mark.parametrize(
        ("value", "digest"),
        [
            (_DIGEST_A, _DIGEST_A),
            ("sha256:", ""),
            (f"sha256:{'a' * 63}", ""),
            (f"{_DIGEST_A}0", ""),
            (f"sha256:{'A' * 64}", ""),
            ("a" * 64, ""),
            (f"{_DIGEST_A} x", ""),
            (f"x{_DIGEST_A}", ""),
            ("", ""),
        ],
        ids=["whole", "bare-prefix", "one-short", "one-long", "upper-case",
             "no-algorithm", "text-after", "text-before", "empty"],
    )
    def test_image_digest_or_empty_reads_only_a_whole_digest(
        self, value, digest,
    ):
        """The whole value is a digest, or the answer is empty."""
        result = _bash(
            'source "$1"; out=$(image_digest_or_empty "$2"); printf "%s" "$out"',
            str(_DOCKER_LIB), value,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == digest, result.stdout

    @pytest.mark.parametrize(
        ("text", "digest"),
        [
            (f"r@{_DIGEST_A}\nr@{_DIGEST_B}\n", _DIGEST_A),
            (f"image@{_DIGEST_B} # was {_DIGEST_C}", _DIGEST_B),
            (f"r:latest\nr@sha256:{'a' * 12}\nr@{_DIGEST_C}\n", ""),
            ("r:latest\n", ""),
            ("", ""),
            (f"r@{_DIGEST_A}0\nr@{_DIGEST_B}\n", ""),
            (f"r@{_DIGEST_A}{'0' * 40} r@{_DIGEST_B}", ""),
            (f"r@{_DIGEST_A}F r@{_DIGEST_B}", ""),
            (f"r@{_DIGEST_A}_x r@{_DIGEST_B}", ""),
            (f"r@{_DIGEST_A}-x r@{_DIGEST_B}", ""),
            (f"r@{_DIGEST_A}.0 r@{_DIGEST_B}", ""),
            (f'image: "r@{_DIGEST_A}"\n', _DIGEST_A),
            (f"image: 'r@{_DIGEST_A}' # pinned\n", _DIGEST_A),
        ],
        ids=["first-line", "leftmost-on-its-line", "a-short-first-one-is-empty",
             "none", "empty", "one-long-is-empty-not-cut",
             "a-long-run-is-empty-not-cut", "an-upper-case-run-on-is-empty",
             "an-underscore-run-on-is-empty", "a-hyphen-run-on-is-empty",
             "a-dot-run-on-is-empty", "a-double-quoted-one-is-read",
             "a-single-quoted-one-is-read"],
    )
    def test_first_image_digest_reads_the_leftmost_whole_one(
        self, text, digest,
    ):
        """The first ``sha256:`` token decides: its digest, or nothing.

        The token runs on through every image-reference character, so one
        too short, too long, or run on by ``_``, ``-`` or ``.`` answers EMPTY
        rather than being cut to 64 digits or replaced by a later digest.  A
        digest ended by a quote, a space or the line's end is read whole.
        """
        result = _bash(
            'source "$1"; out=$(first_image_digest "$2"); printf "%s" "$out"',
            str(_DOCKER_LIB), text,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == digest, result.stdout


def _carried_image_digest(env_text: str | None, compose_text: str | None,
                          tmp_path: Path) -> subprocess.CompletedProcess:
    """Run reconcile's ``carried_image_digest`` against a temporary PROD_ROOT.

    Args:
        env_text: The ``.env`` to write, or ``None`` for no file.
        compose_text: The ``docker-compose.yml`` to write, or ``None``.
        tmp_path: The temporary ``PROD_ROOT``.

    Returns:
        The completed process.
    """
    if env_text is not None:
        (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    if compose_text is not None:
        (tmp_path / "docker-compose.yml").write_text(
            compose_text, encoding="utf-8",
        )
    return _bash(
        'source "$1"; PROD_ROOT="$2"; eval "$3"; eval "$4"; eval "$5"; '
        'eval "$6"; digest=$(carried_image_digest); printf "%s" "$digest"',
        str(_DOCKER_LIB), str(tmp_path),
        _function_text(_RECONCILE, "die"),
        _function_text(_RECONCILE, "extract_env_value"),
        _function_text(_RECONCILE, "compose_image_digest"),
        _function_text(_RECONCILE, "carried_image_digest"),
    )


class TestReconcileCarriesOnlyAWholeDigest:
    """``carried_image_digest``: the pin written into the new ``.env``.

    Plan step balance:X-do: the value read from ``.env`` was written back
    unchecked, though shekel-deploy refuses any pin but a whole digest.  It
    is refused now, before step 1 writes anything, and the run ends there.
    """

    def test_it_is_checked_before_step_1_writes_anything(self):
        """The digest is read before the first step, so a refusal changes nothing.

        Steps 1-3 write the certificate, the secrets directory and four secret
        files, and none of them writes ``.env`` or the compose file, so the
        value read first is the value step 4 would have read.  Found after
        them, a malformed pin left all of that written.
        """
        lines = _RECONCILE.read_text(encoding="utf-8").splitlines()
        checked = lines.index("current_digest=$(carried_image_digest)")
        first_step = lines.index('log "Step 1: Postgres TLS cert"')
        assert checked < first_step, (checked, first_step)

    def test_the_env_pin_is_carried_when_it_is_whole(self, tmp_path):
        """A whole pin in ``.env``: carried, the compose file never needed."""
        result = _carried_image_digest(
            f"SHEKEL_IMAGE_DIGEST={_DIGEST_A}\n", None, tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == _DIGEST_A

    def test_the_legacy_inline_digest_is_carried_without_an_env_pin(
        self, tmp_path,
    ):
        """No ``.env`` pin: the shekel image line's digest."""
        result = _carried_image_digest(
            "OTHER=1\n",
            f"    image: ghcr.io/saltyreformed/shekel@{_DIGEST_B}\n", tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == _DIGEST_B

    @pytest.mark.parametrize(
        "pin", ["sha256:abc", f"{_DIGEST_A}0", "latest", f"sha256:{'A' * 64}"],
        ids=["short", "too-long", "not-a-digest", "upper-case"],
    )
    def test_a_pin_that_is_no_whole_digest_ends_the_run(self, tmp_path, pin):
        """Refused, naming it -- even with a good inline digest to fall back on."""
        result = _carried_image_digest(
            f"SHEKEL_IMAGE_DIGEST={pin}\n",
            f"    image: ghcr.io/saltyreformed/shekel@{_DIGEST_B}\n", tmp_path,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert result.stdout == ""
        assert (
            f"the image digest to carry forward is not sha256:<64 hex digits> "
            f"(got '{pin}')" in result.stderr
        ), result.stderr

    def test_no_digest_anywhere_ends_the_run(self, tmp_path):
        """Neither place holds one: the refusal names both."""
        result = _carried_image_digest(
            "OTHER=1\n", "    image: ghcr.io/saltyreformed/shekel:latest\n",
            tmp_path,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert result.stdout == ""
        assert "could not determine the image digest" in result.stderr, (
            result.stderr
        )


#: The two production container defaults (plan step balance:X-do, the shell
#: half of finding BAL-644).
_APP_DEFAULT = "shekel_prod_app_container"
_DB_DEFAULT = "shekel_prod_db_container"

#: Every assignment of a container name from the lib: the script, its
#: variable, and which container that variable names.  The pairing is the
#: specification, not a copy of a name.
_DEFAULT_SITES = (
    ("deploy/shekel-deploy.sh", "CONTAINER_NAME", _APP_DEFAULT),
    ("deploy/shekel-deploy.sh", "DB_CONTAINER", _DB_DEFAULT),
    ("deploy/verify_prod_deploy.sh", "CONTAINER", _APP_DEFAULT),
    ("scripts/deploy.sh", "APP_CONTAINER", _APP_DEFAULT),
    ("scripts/deploy.sh", "app_container_default", _APP_DEFAULT),
    ("scripts/backup.sh", "DB_CONTAINER", _DB_DEFAULT),
    ("scripts/restore.sh", "DB_CONTAINER", _DB_DEFAULT),
    ("scripts/restore.sh", "APP_CONTAINER", _APP_DEFAULT),
    ("scripts/verify_backup.sh", "DB_CONTAINER", _DB_DEFAULT),
    ("scripts/verify_backup.sh", "APP_CONTAINER", _APP_DEFAULT),
    ("scripts/generate_pg_cert.sh", "PROD_DB_CONTAINER", _DB_DEFAULT),
    ("scripts/reconcile_prod_to_canonical.sh", "app_container", _APP_DEFAULT),
)

#: Every override a default stands behind, unset before each default is read.
_OVERRIDES = "SHEKEL_CONTAINER_NAME SHEKEL_DB_CONTAINER APP_CONTAINER DB_CONTAINER"


def _shell_scripts() -> list[Path]:
    """Return every shell script under ``deploy/`` and ``scripts/`` but the lib."""
    return sorted(
        path for folder in ("deploy", "scripts")
        for path in (_REPO_ROOT / folder).glob("*.sh") if path != _DOCKER_LIB
    )


def _lib_functions() -> list[str]:
    """Return the names of the functions ``scripts/_docker_lib.sh`` defines."""
    return re.findall(
        r"^(\w+)\(\) \{", _DOCKER_LIB.read_text(encoding="utf-8"), flags=re.M,
    )


def _load_block(lines: list[str]) -> tuple[int, int] | None:
    """Return the first and last line index of a script's lib-load block.

    The block opens at the top-level ``_docker_lib=`` assignment and closes
    at the first ``fi`` alone on a line after it, which is how every loader
    writes it (shfmt holds the layout).

    Args:
        lines: The script's lines.

    Returns:
        The block's span, or ``None`` when the script loads no lib.
    """
    starts = [i for i, line in enumerate(lines) if line.startswith("_docker_lib=")]
    if not starts:
        return None
    assert len(starts) == 1, f"two lib loads: lines {starts}"
    return starts[0], lines.index("fi", starts[0])


def _lib_calls(script: Path) -> set[str]:
    """Return the lib functions *script* names outside comments and its load.

    Args:
        script: The script to read.

    Returns:
        The names, each a whole word on some line that is not a comment and
        not inside the script's own load block (whose refusal lists them).
    """
    lines = script.read_text(encoding="utf-8").splitlines()
    span = _load_block(lines) or (-1, -1)
    body = [
        line for i, line in enumerate(lines)
        if not span[0] <= i <= span[1] and not line.lstrip().startswith("#")
    ]
    return {
        name for name in _lib_functions()
        if any(re.search(rf"\b{name}\b", line) for line in body)
    }


def _loaders() -> list[Path]:
    """Return every script that loads the lib itself."""
    return [
        script for script in _shell_scripts()
        if _load_block(script.read_text(encoding="utf-8").splitlines())
    ]


def _backup_family() -> list[Path]:
    """Return the scripts that reach the lib through ``scripts/_backup_lib.sh``."""
    return [
        script for script in _shell_scripts()
        if re.search(r"^\s*source .*_backup_lib\.sh",
                     script.read_text(encoding="utf-8"), flags=re.M)
    ]


def _reads(loader: Path) -> list[str]:
    """Return the lib functions *loader*'s load must find, sorted.

    The backup lib loads for its whole family, so it must find what any of
    them calls as well as what it calls itself.

    Args:
        loader: A script that loads the lib.

    Returns:
        The names.
    """
    names = _lib_calls(loader)
    if loader == _BACKUP_LIB:
        for member in _backup_family():
            names |= _lib_calls(member)
    return sorted(names)


def _run_load_block(loader: Path, lib: Path) -> subprocess.CompletedProcess:
    """Run *loader*'s lib-load block alone, aimed at *lib*, then ``echo loaded``.

    The block's first line is re-aimed at *lib*, and every function the
    script defines ABOVE the block is defined first, verbatim, so a refusal
    calling one the script has not yet defined fails as it would for real.
    Only ``_self``, which two refusals print and which the scripts set from
    ``readlink -f`` of themselves, is a stub.

    Args:
        loader: The script whose block runs.
        lib: The lib it loads.

    Returns:
        The completed process.
    """
    lines = loader.read_text(encoding="utf-8").splitlines()
    start, end = _load_block(lines)
    defined_above = re.findall(
        r"^\w+\(\) \{(?:.*\}$|(?s:\n.*?^\}$))",
        "\n".join(lines[:start]), flags=re.M,
    )
    return _bash(
        "\n".join(defined_above) + "\n_self=probe\n"
        + "\n".join(['_docker_lib="$1"', *lines[start + 1:end + 1]])
        + "\necho loaded",
        str(lib),
    )


class TestEachDefaultIsTheLibsOwnName:
    """With its override unset, every script names the container the lib names.

    Plan step balance:X-do, the shell half of finding BAL-644, and its
    review's M3: the deploy behaviour tests always set both overrides, so no
    test read a default, and a db default reading the APP's function would
    have aimed every nightly backup at the app.  Each site's assignment is
    run alone with the lib loaded, as ``_function_text`` runs a function:
    the scripts that drop or write production are never run here.

    **The known limit:** the expected name is the lib's own answer, so a
    wrong NAME in the lib itself passes here.  No test can see that without
    a second spelling of the name to compare against, which is what plan
    step balance:X-ec removes.  Until then a wrong db name that names NO
    container stops a deploy, its dry run included, at the migration
    pre-flight, which reads the stamp through it before anything is written;
    one that names ANOTHER postgres on the host holding the same user and
    database (``shekel-dev-db`` does) passes the pre-flight, and the deploy's
    dump and the nightly backup then read that database.  A wrong app name
    is NOT caught before the write: the deploy rewrites the pin and
    recreates, finds no container by that name to stop or to report
    healthy, and takes its failure path with the stamp read no longer
    guaranteed final (ruling R-BAL115).
    """

    def test_every_default_site_is_graded(self):
        """A new default read from the lib must join the table.

        The census reads in ONE direction: it finds an ``X=...`` assignment,
        top-level or indented, that calls a name function.  A default spelled
        any other way (``local X=...``, through ``declare``, or as a literal
        name) is invisible to it.  The ledger's census markers on BAL-644 count a
        re-spelled literal in ``scripts/*.sh`` and a ``:-`` default of one in
        ``deploy/*.sh``, not a bare literal there; a site reaching the lib in
        another form is wiring no test here checks.
        """
        found = {
            (str(script.relative_to(_REPO_ROOT)), match)
            for script in _shell_scripts()
            for match in re.findall(
                r"^\s*(\w+)=.*\bshekel_prod_(?:app|db)_container\b",
                script.read_text(encoding="utf-8"), flags=re.M,
            )
        }
        assert found == {(script, variable) for script, variable, _ in _DEFAULT_SITES}

    @pytest.mark.parametrize(
        ("script", "variable", "function"), _DEFAULT_SITES,
        ids=[f"{script.split('/')[-1]}-{variable}" for script, variable, _ in _DEFAULT_SITES],
    )
    def test_with_no_override_it_is_the_lib_s_name(self, script, variable, function):
        """The assignment, run alone, holds what the lib's function prints."""
        lines = re.findall(
            rf"^\s*{variable}=.*$",
            (_REPO_ROOT / script).read_text(encoding="utf-8"), flags=re.M,
        )
        assert len(lines) == 1, lines
        expected = _bash('source "$1"; "$2"', str(_DOCKER_LIB), function)
        assert expected.returncode == 0 and expected.stdout.strip(), expected
        result = _bash(
            f'unset {_OVERRIDES}; source "$1"; eval "$2"; printf "%s" "${{!3}}"',
            str(_DOCKER_LIB), lines[0], variable,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected.stdout.rstrip("\n"), result.stdout


class TestEachLoaderRefusesALibLackingWhatItReads:
    """Every script that loads the lib refuses one lacking any function it reads.

    Plan step balance:X-do, finding BAL-625: a lib without
    ``container_running`` reached the dump, where "command not found" read
    as "the database container is not running".  Each loader names the
    functions it reads with ``declare -F``; this grades those lists name by
    name against what each script (and, for the backup lib, its family)
    actually calls, by running each load block alone against a copy of the
    lib with exactly one function removed.
    """

    def test_every_script_that_calls_the_lib_loads_it(self):
        """No script calls a lib function without a load that checks for it."""
        loaded = set(_loaders()) | set(_backup_family())
        unchecked = [
            str(script) for script in _shell_scripts()
            if _lib_calls(script) and script not in loaded
        ]
        assert unchecked == []

    @pytest.mark.parametrize(
        ("loader", "name"),
        [(loader, name) for loader in _loaders() for name in _reads(loader)],
        ids=[
            f"{loader.name}-{name}"
            for loader in _loaders() for name in _reads(loader)
        ],
    )
    def test_a_lib_lacking_one_function_is_refused(self, tmp_path, loader, name):
        """Exit 1 at the load, the lib's path named, nothing after it run."""
        stale = tmp_path / "_docker_lib.sh"
        stale.write_text(
            _DOCKER_LIB.read_text(encoding="utf-8") + f"\nunset -f {name}\n",
            encoding="utf-8",
        )
        result = _run_load_block(loader, stale)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "loaded" not in result.stdout, result.stdout
        assert str(stale) in result.stdout + result.stderr, (
            result.stdout + result.stderr
        )

    @pytest.mark.parametrize("loader", _loaders(), ids=lambda path: path.name)
    def test_the_repository_s_lib_loads(self, loader):
        """The control: the real lib passes every load block."""
        result = _run_load_block(loader, _DOCKER_LIB)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.endswith("loaded\n"), result.stdout
