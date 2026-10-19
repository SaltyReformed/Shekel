"""The test runner gives every run a private cluster and leaves nothing behind.

Steps ``balance:X-br-2`` (the cluster), ``X-br-3`` (the daemon and the socket)
and ``X-br-4`` (it stopped being optional).  The suite clones a per-worker
database from ``shekel_test_template`` for every test, and that template used
to live on ONE container shared by every worktree -- which is why the repo
carried a suite-slot lock, a hygiene-restart flag, a live-backend probe,
``TEST_DB_PREFIX`` and ``TEST_TEMPLATE_DATABASE``.  ``X-br-4`` deleted all
five and the shared path with them, so there is no flag here to grade any
more: a container per run is what the wrapper does.

**None of the per-run block was graded when it was written**, and a review
said so.  What that review found is what these tests pin, and one of its two
findings has since been deleted rather than fixed:

* A signal to the wrapper could not be serviced while pytest ran in the
  FOREGROUND, so ``timeout`` stopped working and the SIGKILL escalation left
  a live container plus an orphaned pytest.  pytest is backgrounded and
  ``wait``ed now, because ``wait`` is interruptible.
* ``TEST_DB_PER_RUN`` was INHERITED by pytest, so a child that re-invoked the
  wrapper started two more containers nested inside the suite.  The wrapper
  scrubbed the flag before handing the environment on.  With the flag gone
  that scrub is gone too -- there is no shared path left for a child to be
  pushed onto, because there is no shared path at all.

The cluster's own life -- its socket directory, ``docker run``, readiness and
teardown -- is ``scripts/private_cluster.py``'s since step X-cx's K1a, which
moved it out of the wrapper; its own cases (the socket path's byte budget
among them) are ``test_private_cluster.py``.  The cases here drive it THROUGH
the wrapper, which makes its token, arms the traps and hands pytest the DSNs.

These drive a stub ``docker`` and a stub ``pytest`` on ``PATH``, so they need
no daemon, no image and no database, and they run in CI.  The suite-scale
behaviour they stand in for was measured separately, on 2026-09-05 at
``balance:X-br-2``: 13,156 passed in 342.8 s in a private cluster against
356.4 s on the shared one.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest
from tests._repo_root import REPO_ROOT

_TEST_RUNNER = REPO_ROOT / "scripts/test.sh"
# The wrapper's start of its cluster through the module, in one command as
# :func:`_code_lines` gives it.  The ``--`` before the arguments is optional
# HERE because the dash-image case grades it, so each property has one case.
_STARTS_THE_CLUSTER = re.compile(
    r'scripts/private_cluster\.py"\s+start (?:-- )?"\$_run_container" "\$_token" "\$_image"')


def _stub_tree(
    tmp_path: Path, *, rootless: bool = True, make_socket: bool = True,
    survives: int = 0, pytest_status: int = 0,
) -> Path:
    """Write a stub ``docker`` and ``pytest`` into a directory for ``PATH``.

    The docker stub answers only what the per-run branch asks and records
    each subcommand.  The pytest stub records the ENVIRONMENT it was handed,
    which is what the flag-scrub assertion reads.

    Args:
        tmp_path: pytest-provided scratch directory.
        rootless: Whether the stub daemon reports itself rootless.  Since
            ``balance:X-br-3`` the wrapper asks before starting anything, so
            a stub that answers nothing is refused -- which is the point.
        make_socket: Whether the stub container leaves a socket behind.  A
            container can report ready and still not have one; the cluster's
            start refuses to hand out a socket that is not there.
        survives: How many removals the stub container survives: the file
            ``stub-container``, which ``run`` makes holding its ``--label``,
            ``ps`` lists while its label is the filter's, and each ``rm`` of
            its id after that many removes.  While a file ``other-container``
            (ANOTHER run's container) exists, ``run`` refuses as docker
            refuses a name in use; a file ``run-kills-the-start`` makes
            ``run`` SIGKILL its start, ``after making`` the container or not.
        pytest_status: The status the stub pytest exits with, which the
            wrapper must exit with too.

    Returns:
        The directory to prepend to ``PATH``.
    """
    binaries = tmp_path / "bin"
    binaries.mkdir()
    security_options = (
        "[name=seccomp,profile=builtin name=rootless]"
        if rootless
        else "[name=seccomp,profile=builtin]"
    )
    socket_arm = "            listener.bind(os.path.join(host, '.s.PGSQL.5432'))"
    if not make_socket:
        socket_arm = "            pass"
    calls = tmp_path / "docker-calls"
    held = tmp_path / "stub-container"
    other = tmp_path / "other-container"
    kills = tmp_path / "run-kills-the-start"
    env_dump = tmp_path / "pytest-env.json"

    (binaries / "docker").write_text(
        "#!/usr/bin/env python3\n"
        "import os, signal, socket, sys\n"
        "args = sys.argv[1:]\n"
        f"open({str(calls)!r}, 'a').write(' '.join(args) + chr(10))\n"
        "cmd = args[0] if args else ''\n"
        "if cmd == 'info':\n"
        f"    print({security_options!r})\n"
        f"elif cmd == 'run' and os.path.exists({str(kills)!r}):\n"
        f"    if open({str(kills)!r}).read() == 'after making':\n"
        f"        open({str(held)!r}, 'w').write(args[args.index('--label') + 1])\n"
        "    os.kill(os.getppid(), signal.SIGKILL)\n"
        f"elif cmd == 'run' and os.path.exists({str(other)!r}):\n"
        "    print('Conflict. The container name is already in use', file=sys.stderr)\n"
        "    sys.exit(125)\n"
        "elif cmd == 'run':\n"
        # The wrapper mounts the run's socket directory at /sockets; a real
        # postgres would create the socket there, so the stub must too.
        f"    open({str(held)!r}, 'w').write(args[args.index('--label') + 1])\n"
        "    mounts = [args[i + 1] for i, a in enumerate(args) if a == '-v']\n"
        "    for mount in mounts:\n"
        "        if mount.endswith(':/sockets'):\n"
        "            host = mount.rsplit(':', 1)[0]\n"
        "            os.makedirs(host, exist_ok=True)\n"
        "            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        # Bound and deliberately NOT unlinked: the file must outlive the stub.
        f"{socket_arm}\n"
        "    print('stubcontainer')\n"
        f"elif cmd == 'rm' and 'stubcontainer' in args and os.path.exists({str(held)!r}):\n"
        # This removal is already recorded, so it is counted too.
        f"    removals = sum(line.startswith('rm ') for line in open({str(calls)!r}))\n"
        f"    if removals > {survives!r}:\n"
        f"        os.remove({str(held)!r})\n"
        f"elif cmd == 'ps' and os.path.exists({str(held)!r}):\n"
        "    wanted = args[args.index('--filter') + 1].split('=', 1)[1]\n"
        f"    if open({str(held)!r}).read() == wanted:\n"
        "        print('stubcontainer')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    (binaries / "pytest").write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"json.dump(dict(os.environ), open({str(env_dump)!r}, 'w'))\n"
        f"sys.exit({pytest_status})\n",
        encoding="utf-8",
    )
    for name in ("docker", "pytest"):
        (binaries / name).chmod(0o755)
    return binaries


def _run_wrapper(
    binaries: Path, env_extra: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Invoke the wrapper with the stubs first on ``PATH``.

    ``XDG_RUNTIME_DIR`` defaults to a SHORT throwaway directory because the
    per-run socket lives under it and a unix socket path cannot exceed 107
    bytes -- pytest's own ``tmp_path`` is already most of that budget, so a
    test that let it through would be asserting on the wrapper's length
    refusal rather than on the behaviour it means to grade.

    ``DOCKER_HOST``, ``CI`` and ``SHEKEL_ALLOW_HOST_DOCKER`` are all scrubbed
    unless the caller sets them, so no case in this module can depend on which
    daemon the RUNNING SUITE happens to be using, nor on whether it is running
    in CI.  Each of the three has already been observed inverting a case.

    ``env_extra`` is often empty now: since ``balance:X-br-4`` the wrapper
    needs no flag to take the per-run path, so the premise of most cases here
    is simply that it was invoked.

    Args:
        binaries: Directory holding the stubs.
        env_extra: Environment overrides.

    Returns:
        The completed process.
    """
    runtime = env_extra.get("XDG_RUNTIME_DIR")
    scratch = None
    if runtime is None:
        scratch = tempfile.mkdtemp(prefix="skrt", dir="/tmp")
        runtime = scratch
    try:
        return subprocess.run(
            [str(_TEST_RUNNER), "tests/does_not_matter"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={
                **os.environ,
                "PATH": f"{binaries}:{os.environ['PATH']}",
                "TEST_DB_IMAGE": "stub-image:test",
                "XDG_RUNTIME_DIR": runtime,
                # Every variable the daemon gate reads is scrubbed unless a
                # case sets it deliberately, so each case states its own
                # premise instead of inheriting one.  CI is the load-bearing
                # one: GitHub always sets CI=true, which SANCTIONS a
                # non-rootless daemon, so the two refusal cases would have
                # asserted the opposite of what happens and taken the
                # branch-protected merge gate red with them.
                # SHEKEL_ALLOW_HOST_DOCKER does the same for a developer who
                # followed docs/testing-standards.md and exported it.
                "CI": "",
                "SHEKEL_ALLOW_HOST_DOCKER": "",
                # DOCKER_HOST is scrubbed unless a case sets it deliberately.
                # The wrapper EXPORTS it in per-run mode -- correctly, since
                # that is what un-skips tests/test_deploy -- so when the suite
                # itself runs per-run, pytest inherits one and a child wrapper
                # sees it already set.  The auto-selection branch then never
                # runs, and the case grading it silently asserts on the
                # parent's value.  Caught by the full suite; every targeted
                # run passed, because a developer shell has no DOCKER_HOST.
                "DOCKER_HOST": "",
                **env_extra,
            },
            check=False,
        )
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def _code_lines() -> list[str]:
    """The wrapper's commands as bash reads them, one per entry, in order.

    A line ending in a backslash is joined to the next, as bash joins one
    outside single quotes, and blank and comment lines are dropped.  Read one line at a time, a
    container command split by a continuation (``docker \\`` and then
    ``rm -fv "$_run_container"``) slipped past the wiring check: delta
    review 2's LOW-C.

    Returns:
        The commands of ``scripts/test.sh``.
    """
    commands: list[str] = []
    pending = ""
    for line in _TEST_RUNNER.read_text(encoding="utf-8").splitlines():
        if not pending and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1]
            continue
        commands.append(pending + line)
        pending = ""
    return commands


class TestTheWrapperItself:
    """Properties of the script that every other case here presupposes."""

    def test_the_wrapper_is_valid_bash(self) -> None:
        """``scripts/test.sh`` parses. Nothing else in the repo checks this.

        Kept from the deleted container-state module, which is where it was
        earned: that module stripped shell quoting from the state
        classifier's arm patterns, so its canonical spelling of the paused arm
        was ``*(Paused)*`` -- precisely the spelling that is a SYNTAX ERROR in
        the shell.  Removing the quotes at the source was measured to leave
        every test there green while ``bash -n scripts/test.sh`` failed and
        the wrapper refused to run at all, taking every local test invocation
        with it.  shellcheck and shfmt both accept that form, so without this
        assertion the breakage reaches a developer's terminal before any gate.
        The classifier went with the shared path; a syntax error in this file
        still stops the whole repository's testing, so the check outlives what
        motivated it.
        """
        result = subprocess.run(
            ["bash", "-n", str(_TEST_RUNNER)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, (
            f"scripts/test.sh is not valid bash: {result.stderr.strip()}. "
            "Every test invocation in this repo goes through that wrapper, "
            "so this breaks all local testing."
        )

    def test_the_cluster_is_started_and_stopped_through_its_one_home(
        self,
    ) -> None:
        """The wrapper starts and stops its cluster through
        ``scripts/private_cluster.py`` and spells no container command of its own.

        Step X-cx's K1a moved the cluster's life into that module so the
        release rehearsal kit starts its cluster the same way: one ``docker
        run``, one readiness rule, one teardown.  A wrapper that spelled its
        own ``docker run`` again would pass every end-to-end case below, since
        the stub docker cannot tell who called it; this reads the SOURCE for
        that reason, as the trap-arm case does, a command at a time
        (:func:`_code_lines`).
        """
        code = _code_lines()

        assert any('scripts/private_cluster.py" stop "$_token"' in line
                   for line in code), "the teardown no longer stops through the module"
        assert any(_STARTS_THE_CLUSTER.search(line) for line in code), (
            "the cluster is no longer started through the module")
        # Global flags may come before the subcommand (``docker -H <host> rm``,
        # ``docker --context=x run``); the delta review's D17 slipped one past.
        spelled = [line for line in code if re.search(
            r"\bdocker\s+(-\S+\s+(?:[^-\s]\S*\s+)?)*(container\s+)?"
            r"(run|create|start|stop|kill|rm|exec|inspect)\b",
            line)]
        assert not spelled, (
            f"scripts/test.sh spells a container command of its own again: {spelled}"
        )

    @pytest.mark.parametrize("image", ["--help", "-x"])
    def test_an_image_beginning_with_a_dash_reaches_the_start_as_the_image(
        self, tmp_path: Path, image: str
    ) -> None:
        """A ``TEST_DB_IMAGE`` beginning with ``-`` is handed to the cluster's
        start as the image, never read as one of its options.

        The review of 668b53c99's nit: with no ``--`` ending the start's
        options, ``-x`` was read as an option, and the start refused for want
        of an image (exit 2); ``--help`` printed the start's usage and exited
        0, and the wrapper handed pytest that usage as the socket directory
        (measured on stubs, 2026-10-18).  The stub docker starts any image; a
        real one reads such an image as a flag of its own and refuses the
        run, which the start names as an error (measured the same day on the
        rootless daemon, from both images: exit 2, nothing left).

        Args:
            tmp_path: scratch directory.
            image: The image the wrapper is given.
        """
        binaries = _stub_tree(tmp_path)
        result = _run_wrapper(binaries, {"TEST_DB_IMAGE": image})
        seen = (tmp_path / "docker-calls").read_text(encoding="utf-8").splitlines()
        started = [line.split() for line in seen if line.startswith("run ")]

        assert started, f"nothing was started from {image}; docker saw {seen}: {result.stderr}"
        assert started[0][started[0].index("-v") + 2] == image, started[0]
        assert result.returncode == 0, result.stderr


class TestTheClusterTheChildTalksTo:
    """pytest is handed DSNs naming THIS run's socket, and nothing else.

    The wrapper used to read ``TEST_DATABASE_URL`` out of ``.env`` -- naming
    the shared container on ``localhost:5433`` -- and the per-run branch
    overwrote it afterwards.  ``balance:X-br-4`` deleted that read along with
    the shared path, so the only DSNs that can reach pytest are the ones built
    from the socket directory this invocation made.  Restoring a dotenv read
    would point the suite back at a cluster other runs share, which is the
    defect this arc exists to remove; these assertions are what would notice.
    """

    def test_both_dsns_name_this_run_s_own_socket(self, tmp_path: Path) -> None:
        """The admin and the application DSN both reach the private cluster.

        Args:
            tmp_path: scratch directory.
        """
        binaries = _stub_tree(tmp_path)
        result = _run_wrapper(binaries, {})
        env_dump = tmp_path / "pytest-env.json"

        assert env_dump.exists(), (
            f"the stub pytest was never reached: rc={result.returncode} "
            f"stderr={result.stderr.strip()!r}"
        )
        handed = json.loads(env_dump.read_text(encoding="utf-8"))

        for required in ("TEST_ADMIN_DATABASE_URL", "TEST_DATABASE_URL"):
            assert required in handed, f"{required} was not handed to pytest"
            # The socket DIRECTORY, and this RUN's: a bare ``?host=/`` would
            # pass for any socket-shaped DSN, a dotenv's included.  The
            # cluster's module names the directory after the run's token.
            assert "?host=" in handed[required], (
                f"{required} does not name a unix socket at all, so it is not "
                f"this run's private cluster: {handed[required]}"
            )
            assert "/shekel-cluster-" in handed[required], (
                f"{required} does not name a directory this invocation made "
                f"-- it may be coming from somewhere shared: {handed[required]}"
            )


class TestThePrivateClusterIsCleanedUp:
    """The container is removed with its volume, on every path."""

    @pytest.mark.parametrize("signal_name", ["EXIT", "INT", "TERM", "HUP"])
    def test_teardown_is_armed_for_every_signal_that_can_end_a_run(
        self, signal_name: str
    ) -> None:
        """An untrapped fatal signal skips the EXIT trap and leaks the cluster.

        Bash runs the EXIT trap on a normal exit and on a signal it has a
        handler for -- and NOT on an untrapped fatal signal, which terminates
        the shell outright.  So each of these needs its own arm, and each has
        a way of arriving: INT is Ctrl-C at the terminal, TERM is what
        ``timeout`` and process supervisors send, and HUP is the terminal
        closing on a running suite.  HUP was the one missing, while the
        wrapper's own header claimed every exit path was covered.

        A leaked container is not merely untidy: it keeps its postgres running
        on the daemon and its socket directory on the tmpfs, and nothing else
        will ever stop either.

        This reads the SOURCE rather than delivering real signals, as the
        wiring case does -- the behaviour needs a live container and a race,
        and what actually regresses is an arm being dropped from the trap
        list.

        Args:
            signal_name: The signal whose arm must be present.
        """
        source = _TEST_RUNNER.read_text(encoding="utf-8")

        armed = re.findall(r"^trap\s+'[^']*'\s+([A-Z]+)$", source, re.M)

        assert signal_name in armed, (
            f"scripts/test.sh does not trap {signal_name}; it traps {armed}. "
            "A run ended that way leaks its postgres container and its socket directory."
        )

    def test_every_trap_is_armed_before_the_cluster_is_started(self) -> None:
        """The traps come BEFORE the start, so a run killed during the start is
        stopped by name.

        A start ended by a signal it holds stops what it made itself, but one
        killed outright cannot: only the trap's stop, which finds the cluster
        by the token the wrapper made, removes it.  The review of step X-cx's K1a
        measured the traps moved after the start: a TERM to the whole group
        during the start left the container and its socket directory behind.
        Read from the SOURCE, as the wiring case is (:func:`_code_lines`).
        """
        code = _code_lines()
        started = next(index for index, line in enumerate(code)
                       if _STARTS_THE_CLUSTER.search(line))
        armed = {match.group(1): index for index, line in enumerate(code)
                 if (match := re.match(r"trap\s+'_teardown_run_container[^']*'\s+([A-Z]+)$",
                                       line))}

        assert set(armed) == {"EXIT", "INT", "TERM", "HUP"}, armed
        late = sorted(name for name, index in armed.items() if index > started)
        assert not late, f"armed only after the cluster is started: {late}"

    def test_a_container_that_survives_teardown_is_named_and_pytest_s_status_kept(
        self, tmp_path: Path
    ) -> None:
        """A survivor is named on stderr, and the wrapper still exits with
        pytest's status.

        The module's stop exits 1 when something survives.  Under ``set -e``,
        the teardown at the end of the wrapper would then end the run with
        that 1 instead of pytest's status, unless it is told not to (``||
        true``); what survived is reported by the WARNING, not by the exit.
        The stub pytest exits 3, a status nothing else here gives, so a
        wrapper that dropped pytest's status for any other would be seen: the
        delta review's MEDIUM-2 measured ``exit 0`` passing a stub exiting 0.
        """
        result = _run_wrapper(
            _stub_tree(tmp_path, survives=1, pytest_status=3), {})

        assert result.returncode == 3, result.stderr
        assert "survived teardown" in result.stderr, result.stderr

    def test_a_name_another_container_holds_is_left_to_it(
        self, tmp_path: Path
    ) -> None:
        """A container ANOTHER run made, holding the name this run's start
        asks for, is left alone by the start and by the wrapper.

        The coordinator's MEDIUM-1, held where the ruling on commit B put it:
        docker refuses the run ("Conflict"), the start exits 2, and the EXIT
        trap stops by THIS run's token, which that container does not carry.
        A stop by NAME removed it (a leaked cluster of a reused PID, or a live
        one of a run in another PID namespace on the same daemon).
        """
        other = tmp_path / "other-container"
        binaries = _stub_tree(tmp_path)
        other.write_text("", encoding="utf-8")

        result = _run_wrapper(binaries, {})
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8")

        assert result.returncode == 2, result.stderr
        assert "Conflict" in result.stderr, result.stderr
        assert other.exists(), f"the other run's container was removed; docker saw:\n{calls}"
        assert not [line for line in calls.splitlines() if line.startswith("rm ")], calls

    @pytest.mark.parametrize(("kills", "other", "calls"), [
        ("after making", False, ["info", "run", "ps", "rm", "ps"]),
        ("before making", True, ["info", "run", "ps"]),
    ], ids=["after its run made the container",
            "before it made anything, another run's container holding the name"])
    def test_a_start_killed_outright_is_stopped_by_the_trap_s_token(
        self, tmp_path: Path, kills: str, other: bool, calls: list[str]
    ) -> None:
        """A start killed outright (SIGKILL from its ``docker run``) runs no
        stop of its own; the EXIT trap stops by the token the wrapper made.
        After its run made the container, that stop alone removes it and the
        socket directory (the review of 668b53c99 measured a wrapper that
        disarmed on every failed start passing every case).  BEFORE it made
        anything, with another run's container holding the name (S4 of the
        commit B design), the stop finds nothing carrying this run's token;
        a stop by name removed the other run's container.

        Args:
            tmp_path: scratch directory.
            kills: when the stub ``run`` kills the start.
            other: whether another run's container holds the name.
            calls: the docker commands the run gives, in order.
        """
        runtime = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
        binaries = _stub_tree(tmp_path)
        (tmp_path / "run-kills-the-start").write_text(kills, encoding="utf-8")
        if other:
            (tmp_path / "other-container").write_text("", encoding="utf-8")
        try:
            result = _run_wrapper(binaries, {"XDG_RUNTIME_DIR": str(runtime)})
            left = list(runtime.glob("shekel-cluster-*"))
        finally:
            shutil.rmtree(runtime, ignore_errors=True)
        seen = (tmp_path / "docker-calls").read_text(encoding="utf-8").splitlines()

        assert result.returncode == 2, result.stderr
        assert [line.split()[0] for line in seen] == calls, seen
        assert not (tmp_path / "stub-container").exists(), (
            f"the killed start's container was left; docker saw: {seen}")
        assert not left, f"the killed start's socket directory was left: {left}"
        assert (tmp_path / "other-container").exists() is other, (
            f"the other run's container was removed; docker saw: {seen}")

    def test_a_container_a_failed_start_could_not_remove_is_removed_by_the_trap(
        self, tmp_path: Path
    ) -> None:
        """A start that exits 2 naming a container its own stop could not
        remove has it removed by the EXIT trap, by the token the wrapper made.

        Delta review 2's LOW-B: nothing graded that the trap still stops after
        a start's exit 2; a wrapper that disarmed then passed every case.  The
        trap's stop is the only cover once the start's own stop has missed
        something.  Here the start fails (its container leaves no socket) and
        the stub container survives the start's removal and not the trap's.

        Args:
            tmp_path: scratch directory.
        """
        held = tmp_path / "stub-container"
        result = _run_wrapper(_stub_tree(tmp_path, make_socket=False, survives=1), {})
        seen = (tmp_path / "docker-calls").read_text(encoding="utf-8").splitlines()

        assert result.returncode == 2, result.stderr
        assert "left no socket" in result.stderr, result.stderr
        assert "Stopping it left something behind" in result.stderr, result.stderr
        assert [line.split()[0] for line in seen] == [
            "info", "run", "exec", "ps", "rm", "ps", "ps", "rm", "ps"], seen
        assert not held.exists(), f"the container the start left was left; docker saw: {seen}"

    def test_the_container_is_removed_with_its_volume(
        self, tmp_path: Path
    ) -> None:
        """Teardown uses ``rm -fv``, not ``rm -f``.

        The baked image inherits the base image's
        ``VOLUME /var/lib/postgresql`` declaration even though PGDATA lives
        outside it, so a plain ``docker rm -f`` left an anonymous volume
        behind on every run -- measured as 101 of 112 volumes dangling on
        the development daemon, in pairs matching this script's own
        invocations.
        """
        binaries = _stub_tree(tmp_path)
        _run_wrapper(binaries, {})
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8")

        removals = [line for line in calls.splitlines() if line.startswith("rm ")]
        assert removals, f"nothing was removed; docker saw:\n{calls}"
        assert all("-fv" in line for line in removals), (
            f"a removal did not take the volume with it: {removals}"
        )

    def test_the_run_container_is_started_non_durably(
        self, tmp_path: Path
    ) -> None:
        """The cluster runs with fsync off, which is the whole cost argument.

        Measured: the full suite took 753 s in a per-run container against
        356 s on the shared cluster -- 2.1x -- purely because the baked image
        runs with docker's default durability while the shared test-db is
        given these three.  Per-test drop-and-reclone is thousands of DDL
        cycles, priced at 1618 ms with fsync on against 31 ms with it off.
        With them the same suite is 342.8 s, marginally FASTER than shared.
        """
        binaries = _stub_tree(tmp_path)
        _run_wrapper(binaries, {})
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8")

        run_lines = [line for line in calls.splitlines() if line.startswith("run ")]
        assert run_lines, f"no container was started; docker saw:\n{calls}"
        started = run_lines[0]
        for knob in ("fsync=off", "synchronous_commit=off", "full_page_writes=off"):
            assert knob in started, (
                f"{knob} is missing, so the private cluster runs durably and "
                f"the suite pays roughly double: {started}"
            )


class TestPerRunModeRefusesTheProductionDaemon:
    """Step ``balance:X-br-3``: the harness gets a daemon of its own.

    A container per run is affordable only somewhere it is not noticed.  The
    system daemon on the maintainer's box runs the production database and is
    watched by wud/cadvisor/alloy, so per-run mode asks the daemon what it is
    and refuses a non-rootless one.  Fail-closed on purpose: an absent
    rootless daemon stops the run with instructions rather than silently
    falling back to the daemon the whole step exists to get off.

    The check reads ``docker info``, not the socket path, because "rootless"
    is a property of a daemon and a path is only a guess about one -- a
    ``tcp://`` endpoint aimed at a socket-proxy container reads as isolated
    while being the production daemon at a different address.
    """

    def test_a_non_rootless_daemon_is_refused(self, tmp_path: Path) -> None:
        """The default socket must not receive a container per run."""
        result = _run_wrapper(
            _stub_tree(tmp_path, rootless=False),
            {"DOCKER_HOST": "unix:///var/run/docker.sock"},
        )

        assert result.returncode == 2, result.stderr
        assert "is not rootless" in result.stderr
        assert "SHEKEL_ALLOW_HOST_DOCKER=1" in result.stderr, (
            "a refusal must name the way past it"
        )

    def test_nothing_is_started_on_a_refused_daemon(self, tmp_path: Path) -> None:
        """Refusing after starting the container would defeat the point."""
        binaries = _stub_tree(tmp_path, rootless=False)
        _run_wrapper(
            binaries,
            {"DOCKER_HOST": "unix:///var/run/docker.sock"},
        )
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8").split("\n")

        started = [line for line in calls if line.startswith("run ")]
        assert not started, f"a container was started on a refused daemon: {started}"

    @pytest.mark.parametrize(
        "sanction", [{"SHEKEL_ALLOW_HOST_DOCKER": "1"}, {"CI": "true"}]
    )
    def test_a_sanctioned_run_proceeds(
        self, tmp_path: Path, sanction: dict[str, str]
    ) -> None:
        """CI's daemon is a throwaway; the override is a deliberate choice.

        Without this arm the refusal could be unconditional and the two tests
        above would not notice.
        """
        binaries = _stub_tree(tmp_path, rootless=False)
        result = _run_wrapper(
            binaries,
            {
                "DOCKER_HOST": "unix:///var/run/docker.sock",
                **sanction,
            },
        )

        assert result.returncode == 0, result.stderr
        assert "is not rootless" not in result.stderr

    @pytest.mark.parametrize("value", ["false", "0", "", "no"])
    def test_a_falsy_ci_does_not_sanction_the_production_daemon(
        self, tmp_path: Path, value: str
    ) -> None:
        """``CI`` decides by VOCABULARY, not by presence.

        A bare ``[ -n "$CI" ]`` made ``CI=false`` and ``CI=0`` sanction a
        container per run on the daemon that runs production -- the heaviest
        consequence in this file, reached by the reading of an environment
        variable that this same file refuses to guess at everywhere else.
        """
        result = _run_wrapper(
            _stub_tree(tmp_path, rootless=False),
            {
                "DOCKER_HOST": "unix:///var/run/docker.sock",
                "CI": value,
            },
        )

        assert result.returncode == 2, (
            f"CI={value!r} sanctioned the production daemon: {result.stderr}"
        )

    def test_an_unreachable_daemon_is_not_reported_as_non_rootless(
        self, tmp_path: Path
    ) -> None:
        """A stopped daemon is a different fact from a wrong one.

        The socket file outlives the daemon behind it, so the wrapper can
        select a genuinely rootless endpoint and then fail to reach it. Saying
        "is not rootless" there asserts a cause never established, about the
        one endpoint that would have been correct.
        """
        binaries = _stub_tree(tmp_path, rootless=False)
        # A docker whose `info` fails outright, rather than answering.
        (binaries / "docker").write_text(
            "#!/usr/bin/env bash\n"
            'case "${1:-}" in\n'
            "    info) exit 1 ;;\n"
            "    inspect) exit 1 ;;\n"
            "esac\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (binaries / "docker").chmod(0o755)

        result = _run_wrapper(binaries, {})

        assert result.returncode == 2, result.stderr
        assert "could not ask" in result.stderr, result.stderr
        assert "is not rootless" not in result.stderr, (
            "an unreachable daemon was diagnosed as a non-rootless one: "
            f"{result.stderr}"
        )

    def test_an_unset_endpoint_selects_the_rootless_socket(
        self, tmp_path: Path
    ) -> None:
        """The operator should not have to remember to export it."""
        # Short, because the per-run socket lives under this directory and
        # the kernel caps a unix socket path at 107 bytes.
        runtime_dir = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
        sock_path = runtime_dir / "docker.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(sock_path))
            binaries = _stub_tree(tmp_path)
            result = _run_wrapper(
                binaries,
                {"XDG_RUNTIME_DIR": str(runtime_dir)},
            )
            assert result.returncode == 0, result.stderr
            handed = json.loads(
                (tmp_path / "pytest-env.json").read_text(encoding="utf-8")
            )
            assert handed.get("DOCKER_HOST") == f"unix://{sock_path}"
        finally:
            listener.close()
            shutil.rmtree(runtime_dir, ignore_errors=True)


class TestThePrivateClusterHasNoPort:
    """Step ``balance:X-br-3``: the cluster is reached by socket, not TCP.

    A port bought nothing -- one process on this machine talks to it -- and
    cost a whole failure class.  On a rootless daemon ``dockerd`` picks the
    port inside the container's network namespace and rootlesskit binds that
    number on the HOST, where a live outbound connection often owns it;
    ``ip_local_port_range`` is 32768-60999, byte-identical to docker's
    publish band.  Measured 5 of 12 starts failing, and still 3 of 12 with no
    port ever recycled, which rules out a release race.  A socket has no
    allocator and no shared namespace to contend for.
    """

    def test_no_port_is_published(self, tmp_path: Path) -> None:
        """A published port re-opens the collision class outright."""
        binaries = _stub_tree(tmp_path)
        _run_wrapper(binaries, {})
        calls = (tmp_path / "docker-calls").read_text(encoding="utf-8")

        started = [ln for ln in calls.splitlines() if ln.startswith("run ")]
        assert started, f"no container was started; docker saw:\n{calls}"
        assert " -p " not in started[0], (
            f"the private cluster publishes a port again: {started[0]}"
        )

    def test_the_cluster_has_no_network_and_no_listener(
        self, tmp_path: Path
    ) -> None:
        """No port implies no network, which also drops the net driver.

        The rootless daemon falls back to the experimental gvisor-tap-vsock
        driver when slirp4netns is absent; a cluster on ``--network=none``
        does not care which driver is in use.
        """
        binaries = _stub_tree(tmp_path)
        _run_wrapper(binaries, {})
        started = [
            ln
            for ln in (tmp_path / "docker-calls")
            .read_text(encoding="utf-8")
            .splitlines()
            if ln.startswith("run ")
        ][0]

        assert "--network=none" in started.split(), started
        # The TOKEN, not a prefix of it: `listen_addresses=` is an empty
        # value and `listen_addresses=*` -- the opposite of what this test
        # names -- would satisfy a substring check.
        assert "listen_addresses=" in started.split(), (
            f"the cluster opens a TCP listener after all: {started}"
        )

    def test_the_socket_is_not_mounted_over_the_entrypoint_s_directory(
        self, tmp_path: Path
    ) -> None:
        """``/sockets``, never ``/var/run/postgresql``.

        The postgres entrypoint chowns its default socket directory to a
        user that rootless maps into the subuid range and sets the sticky
        bit, after which the invoking user cannot unlink the socket -- five
        cleanup attempts out of five failed against that path, and succeeded
        against ``/sockets``.
        """
        binaries = _stub_tree(tmp_path)
        _run_wrapper(binaries, {})
        started = [
            ln
            for ln in (tmp_path / "docker-calls")
            .read_text(encoding="utf-8")
            .splitlines()
            if ln.startswith("run ")
        ][0]

        assert any(tok.endswith(":/sockets") for tok in started.split()), started
        assert "unix_socket_directories=/sockets" in started.split(), started
        assert "/var/run/postgresql" not in started, (
            "mounting over the entrypoint's own socket directory leaves "
            f"files this user cannot delete: {started}"
        )

    def test_the_socket_directory_is_removed_afterwards(
        self, tmp_path: Path
    ) -> None:
        """It sits on a tmpfs, so a leak is bounded only by the next reboot."""
        runtime = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
        try:
            binaries = _stub_tree(tmp_path)
            result = _run_wrapper(
                binaries,
                {"XDG_RUNTIME_DIR": str(runtime)},
            )
            assert result.returncode == 0, result.stderr
            leftovers = list(runtime.glob("shekel-cluster-*"))
            assert not leftovers, f"socket directories survived: {leftovers}"
        finally:
            shutil.rmtree(runtime, ignore_errors=True)

    def test_an_over_long_socket_path_is_refused_at_the_door(
        self, tmp_path: Path
    ) -> None:
        """107 bytes is a kernel limit, not a preference.

        Left to surface on its own it arrives as a connection error from
        inside the suite, pointing at the database rather than at the path.
        """
        runtime = Path(tempfile.mkdtemp(prefix="skrt" + "x" * 90, dir="/tmp"))
        try:
            binaries = _stub_tree(tmp_path)
            result = _run_wrapper(
                binaries,
                {"XDG_RUNTIME_DIR": str(runtime)},
            )

            assert result.returncode == 2, result.stdout + result.stderr
            assert "107 bytes" in result.stderr, result.stderr
        finally:
            shutil.rmtree(runtime, ignore_errors=True)


class TestAReadyContainerWithNoSocketIsRefused:
    """Readiness and a usable socket are two facts, not one.

    A mutation harness caught this hole rather than a reviewer: deleting the
    guard entirely left every case green, so nothing graded it. Without it the
    wrapper hands pytest a DSN naming a socket that does not exist, and the
    failure surfaces as a connection error from inside the suite -- pointing
    at the database rather than at the container that never made one.
    """

    def test_the_run_stops_rather_than_handing_out_a_dead_dsn(
        self, tmp_path: Path
    ) -> None:
        """`pg_isready` answering is not proof the socket is on the host."""
        binaries = _stub_tree(tmp_path, make_socket=False)

        result = _run_wrapper(binaries, {})

        assert result.returncode == 2, (
            "a DSN naming a nonexistent socket was handed to pytest: "
            f"{result.stdout}{result.stderr}"
        )
        assert "left no socket" in result.stderr, result.stderr


class TestThePeerNoteNamesEveryRunningPytest:
    """The note naming other live pytest runs reads each peer's whole argv.

    Plan step ``balance:X-dm``.  It asked ``printf '%s\\n' "$_peer_argv" |
    grep -qx ...`` under pipefail, so a ``grep`` that matched and left before
    ``printf`` finished writing could read a running pytest as absent and
    drop it from the note: the race that made ``deploy/shekel-deploy.sh``
    read a listed migration as absent (finding BAL-618).  Each peer here is a
    real process whose argv names a pytest, found by the wrapper's real
    ``pgrep`` and ``/proc``; other pytest runs on the host may be named too,
    and nothing here depends on them.
    """

    @staticmethod
    def _peer(*extra: str) -> subprocess.Popen[bytes]:
        """Start a stand-in peer whose argv names ``/xdm/bin/pytest``.

        ``bash -c`` keeps its own argv, so ``/proc/<pid>/cmdline`` reads
        ``bash``, ``-c``, the script, ``/xdm/bin/pytest`` and then *extra*.
        It leads a session of its own, so the whole group can be stopped.

        Args:
            extra: Further arguments to give it.

        Returns:
            The running process.
        """
        return subprocess.Popen(
            ["bash", "-c", "sleep 60; :", "/xdm/bin/pytest", *extra],
            start_new_session=True,
        )

    @staticmethod
    def _stop(peer: subprocess.Popen[bytes]) -> None:
        """Stop a stand-in peer and the ``sleep`` it started.

        Args:
            peer: A process :meth:`_peer` started.
        """
        os.killpg(peer.pid, signal.SIGTERM)
        peer.wait(timeout=10)

    def test_a_running_pytest_is_named_and_a_collect_only_one_is_not(
        self, tmp_path: Path
    ) -> None:
        """Both questions, one wrapper run: is it pytest, does it collect only."""
        binaries = _stub_tree(tmp_path)
        running = self._peer("-n", "12")
        collecting = self._peer("--collect-only", "-q")
        try:
            result = _run_wrapper(binaries, {})
        finally:
            self._stop(running)
            self._stop(collecting)

        assert result.returncode == 0, result.stderr
        assert f"pid {running.pid}  " in result.stderr, result.stderr
        assert f"pid {collecting.pid}  " not in result.stderr, result.stderr

    def test_a_python_dash_m_pytest_peer_is_named(self, tmp_path: Path) -> None:
        """A bare ``pytest`` argument names a peer as surely as a path does.

        ``python3 -m pytest`` leaves ``pytest`` alone on its line, found by
        the wrapper's ``pgrep -x python3``; this stand-in carries the same
        argument without running pytest.
        """
        python3 = shutil.which("python3")
        assert python3 is not None, "the stand-in peer needs a python3"
        binaries = _stub_tree(tmp_path)
        with subprocess.Popen(
            [python3, "-c", "import time; time.sleep(60)", "pytest"],
            start_new_session=True,
        ) as peer:
            try:
                result = _run_wrapper(binaries, {})
            finally:
                self._stop(peer)

        assert result.returncode == 0, result.stderr
        assert f"pid {peer.pid}  " in result.stderr, result.stderr

    def test_a_peer_whose_argv_outgrows_a_pipe_is_still_named(
        self, tmp_path: Path
    ) -> None:
        """1.3 MB of argv after the pytest line: it is named, every run.

        The pytest line comes early and twenty 64 KiB arguments follow it, so
        the replaced ``printf ... | grep -q`` had far more to write than a
        pipe holds when ``grep`` left, and dropped this peer every run.
        """
        binaries = _stub_tree(tmp_path)
        peer = self._peer(*["x" * 65536] * 20)
        try:
            result = _run_wrapper(binaries, {})
        finally:
            self._stop(peer)

        assert result.returncode == 0, result.stderr
        assert f"pid {peer.pid}  " in result.stderr, result.stderr
