"""The stale test-db image prune keeps every live image and forces nothing.

``scripts/prune_test_db_images.py`` runs after each bake and removes old
``shekel-test-db`` images.  The developer's ruling (2026-09-29) fixes what it
may touch: every live worktree's current image is kept, anything under 24
hours old is kept, removal is plain so an image a running suite uses is
refused rather than forced, and if any worktree's current image cannot be
worked out nothing is removed at all.

Every test here drives the prune through :class:`_FakeHost`, which stands in
for git, each worktree's builder and docker.  No test reaches a real daemon or
a real worktree: the autouse fixture replaces the module's only command
runner with that fake for every test.  The fake starts out HEALTHY (three live
worktrees, their tags, four old images, every removal succeeding), so each
test scripts only the fault it is about; a command the fake does not know
raises ``AssertionError``, and a ``--print-tag`` in an unscripted directory
raises ``KeyError``.  Only :class:`TestTheRunner` calls the real runner, with
``subprocess.run`` itself replaced.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import prune_test_db_images as _MODULE

# The real runner, taken before the autouse fixture replaces it.
# Pylint: ``protected-access`` -- ``_run`` is the one place the prune's
# subprocess arguments (the hang guard among them) are set; no public
# function reaches it without also reaching git and docker.
_real_run = _MODULE._run  # pylint: disable=protected-access

_REPO = "shekel-test-db"
_ROOT = Path("/wt/main")

# What the fake answers a command with: a result, or the exception it raises.
_Answer = subprocess.CompletedProcess[str] | OSError | subprocess.SubprocessError

# Three live worktrees and one git marks prunable.  ``main`` and ``twin`` are
# checked out at the same commit, so they share a key: the ordinary case this
# host has.
_PORCELAIN = (
    "worktree /wt/main\n"
    "HEAD 1111111111111111111111111111111111111111\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /wt/twin\n"
    "HEAD 1111111111111111111111111111111111111111\n"
    "branch refs/heads/dev\n"
    "\n"
    "worktree /wt/lane\n"
    "HEAD 2222222222222222222222222222222222222222\n"
    "branch refs/heads/feat/lane\n"
    "locked claude agent lane (pid 1 start 2)\n"
    "\n"
    "worktree /wt/gone\n"
    "HEAD 3333333333333333333333333333333333333333\n"
    "detached\n"
    "prunable gitdir file points to non-existent location\n"
    "\n"
)
_MAIN_TAG = f"{_REPO}:aaaaaaaaaaaa"
_LANE_TAG = f"{_REPO}:bbbbbbbbbbbb"
_STALE = (f"{_REPO}:cccccccccccc", f"{_REPO}:dddddddddddd")


def _done(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    """Return a finished process with the given result.

    Args:
        stdout: What it printed.
        returncode: Its exit status.
        stderr: What it printed on stderr.

    Returns:
        The completed process.
    """
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class _FakeHost:
    """Answers each command the prune runs from a script, and records them all."""

    def __init__(self) -> None:
        """Script a healthy host: every live worktree's tag known, two stale images."""
        self.worktrees: _Answer = _done(_PORCELAIN)
        self.tags: dict[str, _Answer] = {
            "/wt/main": _done(f"{_MAIN_TAG}\n"),
            "/wt/twin": _done(f"{_MAIN_TAG}\n"),
            "/wt/lane": _done(f"{_LANE_TAG}\n"),
        }
        self.listing: _Answer = _done("\n".join((_MAIN_TAG, _LANE_TAG, *_STALE)) + "\n")
        self.refused: set[str] = set()
        self.rmi_error: OSError | subprocess.SubprocessError | None = None
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(
        self, command: list[str], cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Answer one command.

        Args:
            command: The argv the prune ran.
            cwd: The directory it ran it in.

        Returns:
            The scripted result.

        Raises:
            OSError: When the script says this command cannot start.
            subprocess.SubprocessError: When the script says it times out.
            AssertionError: For a command the prune has no business running.
        """
        self.calls.append((list(command), cwd))
        if command[:4] == ["git", "-C", str(_ROOT), "worktree"]:
            answer = self.worktrees
        elif command[-1] == "--print-tag":
            answer = self.tags[str(cwd)]
        elif command[:3] == ["docker", "image", "ls"]:
            answer = self.listing
        elif command[:2] == ["docker", "rmi"]:
            # The first removal succeeds; ``rmi_error`` fails the ones after.
            if self.rmi_error is not None and len(self.removals()) > 1:
                answer = self.rmi_error
            else:
                answer = _done(returncode=1 if command[-1] in self.refused else 0)
        else:
            raise AssertionError(f"the prune ran an unexpected command: {command}")
        if isinstance(answer, (OSError, subprocess.SubprocessError)):
            raise answer
        return answer

    def removals(self) -> list[list[str]]:
        """Return every ``docker rmi`` the prune ran, in order.

        Returns:
            Their argvs.
        """
        return [command for command, _cwd in self.calls if command[:2] == ["docker", "rmi"]]


@pytest.fixture(name="host", autouse=True)
def _host_fixture(monkeypatch: pytest.MonkeyPatch) -> _FakeHost:
    """Route every command the prune runs to a fresh :class:`_FakeHost`.

    Args:
        monkeypatch: Used to replace the module's command runner.

    Returns:
        The host, for the test to script and inspect.
    """
    fake = _FakeHost()
    monkeypatch.setattr(_MODULE, "_run", fake)
    return fake


class TestTheKeepSet:
    """Every live worktree's current image survives, however old it is."""

    def test_every_live_worktree_s_image_is_kept_even_when_old(
        self, host: _FakeHost, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Both live keys are over 24 hours old and neither is removed.

        The listing names all four images as older than 24 hours; only the
        two no worktree names are removed.
        """
        _MODULE.prune_stale_images(_REPO, _ROOT)

        assert host.removals() == [["docker", "rmi", tag] for tag in _STALE]
        assert (
            "removed 2, kept 2 (a live worktree's current image), refused 0"
            in capsys.readouterr().out
        )

    def test_each_worktree_s_tag_comes_from_its_own_builder(
        self, host: _FakeHost
    ) -> None:
        """Another tree's key is never computed with this tree's code.

        Each live worktree's own ``scripts/build_test_db_image.py`` is run,
        inside that worktree, with the interpreter running the prune.
        """
        _MODULE.prune_stale_images(_REPO, _ROOT)

        asked = [(command, cwd) for command, cwd in host.calls if command[-1] == "--print-tag"]
        assert asked == [
            ([sys.executable, f"{tree}/scripts/build_test_db_image.py", "--print-tag"],
             Path(tree))
            for tree in ("/wt/main", "/wt/twin", "/wt/lane")
        ]

    def test_a_prunable_worktree_is_skipped_rather_than_aborting(
        self, host: _FakeHost, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A worktree whose directory is gone has no image, so it cannot block.

        ``/wt/gone`` has no scripted tag: asking for one would raise
        ``KeyError`` in the fake, so passing proves it was never asked.
        """
        _MODULE.prune_stale_images(_REPO, _ROOT)

        assert not any(cwd == Path("/wt/gone") for _command, cwd in host.calls)
        assert host.removals(), "a prunable worktree stopped the prune"
        assert "removed 2" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("answer", "reported"),
        [
            pytest.param(
                _done(returncode=1, stderr="Traceback\nBuildError: boom"),
                "removed nothing: could not work out the current image of /wt/lane",
                id="exits-1",
            ),
            pytest.param(
                _done(f"{_LANE_TAG}\n", returncode=1),
                "--print-tag exited 1, printed 'shekel-test-db:bbbbbbbbbbbb'",
                id="exits-1-after-printing-a-tag",
            ),
            pytest.param(
                _done(returncode=2, stderr="can't open file 'scripts/build_test_db_image.py'"),
                "--print-tag exited 2",
                id="no-builder",
            ),
            pytest.param(_done(""), "printed ''", id="prints-nothing"),
            pytest.param(
                _done("usage: build_test_db_image.py [-h]\n"), "printed 'usage", id="prints-no-tag"
            ),
            pytest.param(
                _done(f"{_LANE_TAG}\nsomething else\n"), "removed nothing", id="prints-two-lines"
            ),
            pytest.param(
                _done("other-repo:bbbbbbbbbbbb\n"), "printed 'other-repo", id="another-repo"
            ),
            pytest.param(
                FileNotFoundError(2, "No such file or directory", "/wt/lane"),
                "stopped after removing 0: [Errno 2] No such file or directory: '/wt/lane'",
                id="directory-gone",
            ),
            pytest.param(
                subprocess.TimeoutExpired(["build_test_db_image.py", "--print-tag"], 60),
                "stopped after removing 0: Command",
                id="hangs",
            ),
        ],
    )
    def test_one_worktree_without_a_tag_means_nothing_is_removed(
        self,
        host: _FakeHost,
        capsys: pytest.CaptureFixture[str],
        answer: _Answer,
        reported: str,
    ) -> None:
        """The ruling: a worktree's image that cannot be worked out stops it all.

        A keep set missing one worktree would remove the image that worktree
        is about to run, so the prune removes NOTHING and says why.

        Args:
            host: The scripted host.
            capsys: Captures the one line the prune reports.
            answer: How ``/wt/lane``'s builder fails to give a tag.
            reported: What the prune's line must say.
        """
        host.tags["/wt/lane"] = answer

        _MODULE.prune_stale_images(_REPO, _ROOT)

        assert not host.removals()
        captured = capsys.readouterr()
        assert reported in captured.err
        assert len(captured.err.splitlines()) == 1, captured.err
        assert not captured.out

    @pytest.mark.parametrize(
        ("listing", "reported"),
        [
            pytest.param(
                _done(returncode=128, stderr="fatal: not a git repository"),
                "removed nothing: git worktree list exited 128: fatal: not a git repository",
                id="git-fails",
            ),
            pytest.param(
                _done(""), "removed nothing: git worktree list printed ''", id="prints-nothing"
            ),
            pytest.param(
                _done("HEAD 1111111111111111111111111111111111111111\n"),
                "removed nothing: git worktree list printed 'HEAD 1111",
                id="record-without-its-worktree-line",
            ),
        ],
    )
    def test_no_trustworthy_worktree_list_means_nothing_is_removed(
        self,
        host: _FakeHost,
        capsys: pytest.CaptureFixture[str],
        listing: _Answer,
        reported: str,
    ) -> None:
        """Without the list of worktrees there is no keep set to trust.

        An empty listing must refuse rather than yield an empty keep set, which
        would remove every live worktree's image once it was a day old.

        Args:
            host: The scripted host.
            capsys: Captures the one line the prune reports.
            listing: What ``git worktree list --porcelain`` answers.
            reported: What the prune's line must say.
        """
        host.worktrees = listing

        _MODULE.prune_stale_images(_REPO, _ROOT)

        assert [command[0] for command, _cwd in host.calls] == ["git"]
        assert reported in capsys.readouterr().err


class TestTheCandidates:
    """Only images docker itself calls older than 24 hours are considered."""

    def test_the_listing_asks_docker_for_images_over_24_hours_old(
        self, host: _FakeHost
    ) -> None:
        """"Anything under 24 hours old" is kept by docker's own ``until`` filter."""
        _MODULE.prune_stale_images(_REPO, _ROOT)

        listings = [
            command for command, _cwd in host.calls if command[:3] == ["docker", "image", "ls"]
        ]
        assert len(listings) == 1
        pairs = list(zip(listings[0], listings[0][1:]))
        assert ("--filter", "until=24h") in pairs
        assert ("--filter", f"reference={_REPO}") in pairs

    def test_an_untagged_image_is_never_removed(self, host: _FakeHost) -> None:
        """A ``<none>`` row names no tag, so it is left alone."""
        host.listing = _done(f"{_REPO}:<none>\n{_STALE[0]}\n")

        _MODULE.prune_stale_images(_REPO, _ROOT)

        assert host.removals() == [["docker", "rmi", _STALE[0]]]


class TestTheRemoval:
    """Removal is plain, tolerates refusal, and never fails the caller."""

    def test_removal_is_plain_and_never_forced(self, host: _FakeHost) -> None:
        """No ``-f``: docker's refusal is what protects a running suite's image."""
        _MODULE.prune_stale_images(_REPO, _ROOT)

        removals = host.removals()
        assert removals, "nothing was removed, so nothing was checked"
        for command in removals:
            assert command == ["docker", "rmi", command[-1]], command
            assert "-f" not in command and "--force" not in command

    def test_a_refused_removal_is_counted_and_the_rest_still_go(
        self, host: _FakeHost, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An image in use is refused; the prune counts it and carries on."""
        host.refused = {_STALE[0]}

        _MODULE.prune_stale_images(_REPO, _ROOT)

        assert host.removals() == [["docker", "rmi", tag] for tag in _STALE]
        assert (
            "removed 1, kept 2 (a live worktree's current image), refused 1"
            in capsys.readouterr().out
        )

    @pytest.mark.parametrize(
        ("listing", "reported"),
        [
            pytest.param(
                _done(returncode=1, stderr="Cannot connect to the Docker daemon"),
                "removed nothing: docker image ls exited 1: Cannot connect to the Docker daemon",
                id="daemon-unreachable",
            ),
            pytest.param(
                FileNotFoundError(2, "No such file or directory: 'docker'"),
                "stopped after removing 0: [Errno 2] No such file or directory: 'docker'",
                id="no-docker",
            ),
            pytest.param(
                subprocess.TimeoutExpired(["docker", "image", "ls"], 60),
                "stopped after removing 0: Command '['docker', 'image', 'ls']' timed out",
                id="daemon-hangs",
            ),
        ],
    )
    def test_a_docker_failure_does_not_raise(
        self,
        host: _FakeHost,
        capsys: pytest.CaptureFixture[str],
        listing: _Answer,
        reported: str,
    ) -> None:
        """A prune problem must never fail the build, so none escapes.

        Args:
            host: The scripted host.
            capsys: Captures the one line the prune reports.
            listing: How the image listing fails.
            reported: What the prune's line must say.
        """
        host.listing = listing

        _MODULE.prune_stale_images(_REPO, _ROOT)

        assert not host.removals()
        assert reported in capsys.readouterr().err

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(OSError(12, "Cannot allocate memory"), id="cannot-start"),
            pytest.param(subprocess.TimeoutExpired(["docker", "rmi"], 60), id="hangs"),
        ],
    )
    def test_a_failure_part_way_through_says_how_far_it_got(
        self,
        host: _FakeHost,
        capsys: pytest.CaptureFixture[str],
        error: OSError | subprocess.SubprocessError,
    ) -> None:
        """An error after one removal is reported with that removal counted.

        Args:
            host: The scripted host.
            capsys: Captures the one line the prune reports.
            error: What the second removal raises.
        """
        host.rmi_error = error

        _MODULE.prune_stale_images(_REPO, _ROOT)

        assert host.removals() == [["docker", "rmi", tag] for tag in _STALE]
        assert "stopped after removing 1" in capsys.readouterr().err


class TestTheRunner:
    """The real runner: output captured, status left to the caller, a hang cut off."""

    def test_every_command_is_bounded_and_never_raises_on_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wedged daemon or git must not hold the build, and the test run, forever.

        ``subprocess.run`` is replaced, so nothing is executed.

        Args:
            monkeypatch: Used to replace ``subprocess.run``.
        """
        seen: dict[str, object] = {}

        def _record(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            """Record the arguments the runner passes."""
            seen["command"] = command
            seen.update(kwargs)
            return _done()

        monkeypatch.setattr(_MODULE.subprocess, "run", _record)

        _real_run(["docker", "image", "ls"], cwd=Path("/wt/lane"))

        assert seen["command"] == ["docker", "image", "ls"]
        assert seen["cwd"] == Path("/wt/lane")
        assert isinstance(seen["timeout"], int) and seen["timeout"] > 0
        assert seen["check"] is False
        assert seen["errors"] == "replace"
        assert "shell" not in seen
